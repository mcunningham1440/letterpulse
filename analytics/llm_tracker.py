"""
Per-call LLM telemetry.

Two things happen on every llm_call:
  1. A row is always enqueued to the DB (LLMCall table) via log_sink.
  2. When dev-panel tracking is active (local mode only), a rich in-memory
     record with prompts + per-call cost is appended for later JSON export.

Context is supplied via contextvars so call sites don't need to thread
user / publication / task / section through every function signature.
asyncio.Task inherits the context at creation, so parallel sections in
asyncio.gather each see their own section_name without cross-talk.
"""

import contextvars
import time
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

_tracker_calls = contextvars.ContextVar('_tracker_calls', default=None)
_tracker_start = contextvars.ContextVar('_tracker_start', default=None)

# Context for DB-side LLMCall rows
_user_id_ctx = contextvars.ContextVar('_llm_user_id', default=None)
_publication_id_ctx = contextvars.ContextVar('_llm_publication_id', default=None)
_task_id_ctx = contextvars.ContextVar('_llm_task_id', default='')
_task_kind_ctx = contextvars.ContextVar('_llm_task_kind', default='')
_additional_info_ctx = contextvars.ContextVar('_llm_additional_info', default=None)


# ---------------------------------------------------------------------------
# Context setters (used by background workers and per-call sites)
# ---------------------------------------------------------------------------

def set_llm_context(*, user_id=None, publication_id=None, task_id='', task_kind=''):
    """Set the user/publication/task context for all subsequent llm_call invocations."""
    if user_id is not None:
        _user_id_ctx.set(user_id)
    if publication_id is not None:
        _publication_id_ctx.set(publication_id)
    _task_id_ctx.set(str(task_id) if task_id else '')
    _task_kind_ctx.set(task_kind or '')


def set_additional_info(info):
    """Set the additional_info dict for LLM calls in the current async context."""
    _additional_info_ctx.set(dict(info) if info else None)


# ---------------------------------------------------------------------------
# Dev panel tracking (local only)
# ---------------------------------------------------------------------------

def start_tracking():
    """Begin collecting dev-panel LLM call data in the current context."""
    if settings.ENVIRONMENT != 'local':
        return
    _tracker_calls.set([])
    _tracker_start.set(time.time())


def seed_tracking(prior_data):
    """
    Resume dev-panel tracking in a new context (e.g. a new background thread)
    by replaying the calls from a previous finish_tracking() dict. The caller
    passes task.dev_panel_data from the prior stage; subsequent llm_call
    invocations accumulate on top of the replayed list.
    """
    if settings.ENVIRONMENT != 'local':
        return
    prior_calls = []
    if isinstance(prior_data, dict):
        prior_calls = list(prior_data.get('calls') or [])
    _tracker_calls.set(prior_calls)
    _tracker_start.set(time.time())


def is_tracking():
    """Return True if dev-panel tracking is active in the current context."""
    if settings.ENVIRONMENT != 'local':
        return False
    return _tracker_calls.get() is not None


def record_call(function_name, model, messages, response, duration, start_ts=None):
    """
    Record a successful LLM call. Always enqueues a DB row; also appends
    to the dev-panel accumulator when local tracking is active.
    """
    input_tokens = response.usage.input_tokens
    cached_tokens = response.usage.input_tokens_details.cached_tokens
    new_input_tokens = input_tokens - cached_tokens
    output_tokens = response.usage.output_tokens
    reasoning_tokens = response.usage.output_tokens_details.reasoning_tokens
    response_tokens = output_tokens - reasoning_tokens

    _enqueue_llm_row(
        function_name=function_name,
        model=model,
        duration=duration,
        start_ts=start_ts,
        success=True,
        input_tokens_cached=cached_tokens,
        input_tokens_new=new_input_tokens,
        output_tokens_reasoning=reasoning_tokens,
        output_tokens_response=response_tokens,
    )

    calls = _tracker_calls.get()
    if calls is None:
        return

    system_prompt = _extract_by_role(messages, 'system')
    user_prompt = _extract_by_role(messages, 'user')
    output_text = _extract_output(response)

    pricing = settings.LLM_PRICING.get(model, {})
    input_cost = _token_cost(new_input_tokens, pricing.get('input_per_million', 0))
    cached_cost = _token_cost(cached_tokens, pricing.get('cached_input_per_million', 0))
    output_cost = _token_cost(output_tokens, pricing.get('output_per_million', 0))

    calls.append({
        'function_name': function_name,
        'model': model,
        'system_prompt': system_prompt,
        'user_prompt': user_prompt,
        'runtime_seconds': round(duration, 3),
        'output_text': output_text,
        'input_usage': {
            'new_tokens': new_input_tokens,
            'cached_tokens': cached_tokens,
            'total_tokens': input_tokens,
            'cost': round(input_cost + cached_cost, 6),
        },
        'output_usage': {
            'reasoning_tokens': reasoning_tokens,
            'response_tokens': response_tokens,
            'total_tokens': output_tokens,
            'cost': round(output_cost, 6),
        },
        'total_cost': round(input_cost + cached_cost + output_cost, 6),
    })


def record_error(function_name, model, duration, error, start_ts=None):
    """Record a failed LLM call (exception raised before a response was returned)."""
    _enqueue_llm_row(
        function_name=function_name,
        model=model,
        duration=duration,
        start_ts=start_ts,
        success=False,
        error_type=type(error).__name__,
        error_message=str(error)[:5000],
    )


def finish_tracking():
    """
    Finalize dev-panel tracking, compute totals, and return the full data dict.
    Clears context state so a new tracking session can begin.
    Returns None if tracking was never started.
    """
    calls = _tracker_calls.get()
    start_time = _tracker_start.get()
    if calls is None or start_time is None:
        return None

    wall_clock = round(time.time() - start_time, 3)

    tot_new_input = sum(c['input_usage']['new_tokens'] for c in calls)
    tot_cached = sum(c['input_usage']['cached_tokens'] for c in calls)
    tot_input = sum(c['input_usage']['total_tokens'] for c in calls)
    tot_input_cost = sum(c['input_usage']['cost'] for c in calls)

    tot_reasoning = sum(c['output_usage']['reasoning_tokens'] for c in calls)
    tot_response = sum(c['output_usage']['response_tokens'] for c in calls)
    tot_output = sum(c['output_usage']['total_tokens'] for c in calls)
    tot_output_cost = sum(c['output_usage']['cost'] for c in calls)

    tot_cost = sum(c['total_cost'] for c in calls)

    data = {
        'calls': calls,
        'totals': {
            'wall_clock_seconds': wall_clock,
            'input_usage': {
                'new_tokens': tot_new_input,
                'cached_tokens': tot_cached,
                'total_tokens': tot_input,
                'cost': round(tot_input_cost, 6),
            },
            'output_usage': {
                'reasoning_tokens': tot_reasoning,
                'response_tokens': tot_response,
                'total_tokens': tot_output,
                'cost': round(tot_output_cost, 6),
            },
            'total_cost': round(tot_cost, 6),
        },
    }

    _tracker_calls.set(None)
    _tracker_start.set(None)

    return data


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _enqueue_llm_row(*, function_name, model, duration, start_ts, success,
                     input_tokens_cached=0, input_tokens_new=0,
                     output_tokens_reasoning=0, output_tokens_response=0,
                     error_type='', error_message=''):
    """Build an LLMCall entry dict and hand it to log_sink. Never raises."""
    try:
        from .logsink import log_sink
        now = timezone.now()
        ts_start = (now - timedelta(seconds=duration)) if start_ts is None else start_ts
        additional_info = _additional_info_ctx.get() or {}
        log_sink.put({
            '_target': 'LLMCall',
            'ts_start': ts_start,
            'ts_end': now,
            'user_id': _user_id_ctx.get(),
            'publication_id': _publication_id_ctx.get(),
            'function_name': function_name,
            'model': model,
            'input_tokens_cached': input_tokens_cached,
            'input_tokens_new': input_tokens_new,
            'output_tokens_reasoning': output_tokens_reasoning,
            'output_tokens_response': output_tokens_response,
            'success': success,
            'error_type': error_type,
            'error_message': error_message,
            'task_id': _task_id_ctx.get(),
            'task_kind': _task_kind_ctx.get(),
            'additional_info': dict(additional_info),
        })
    except Exception:
        # Telemetry must never break the app.
        pass


def _token_cost(token_count, price_per_million):
    """Compute cost for a given token count and per-million price."""
    return (token_count / 1_000_000) * price_per_million


def _extract_by_role(messages, role):
    """
    Extract and concatenate content from messages matching the given role.
    Handles plain string content and list-of-dicts content.
    """
    parts = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        if msg.get('role') != role:
            continue
        content = msg.get('content', '')
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get('type') == 'text':
                    parts.append(item.get('text', ''))
                elif isinstance(item, str):
                    parts.append(item)
    return '\n\n'.join(parts) if parts else ''


def _extract_output(response):
    """Extract text content from the OpenAI response output."""
    parts = []
    for item in getattr(response, 'output', []):
        if hasattr(item, 'content') and item.content:
            for content_piece in item.content:
                if hasattr(content_piece, 'text'):
                    parts.append(content_piece.text)
        elif hasattr(item, 'type') and item.type == 'function_call':
            name = getattr(item, 'name', '')
            args = getattr(item, 'arguments', '')
            parts.append(f"[tool_call: {name}] {args}")
    return '\n'.join(parts) if parts else ''
