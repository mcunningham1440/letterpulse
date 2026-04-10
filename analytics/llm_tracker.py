"""
Thread-local LLM call tracker for the local dev panel.

Collects detailed data (prompts, token usage, costs, timing) from every
llm_call invocation during a workflow.  All public functions are no-ops
when ENVIRONMENT != 'local', so there is zero overhead in production.

Usage::

    from analytics.llm_tracker import start_tracking, finish_tracking

    start_tracking()
    # ... run workflow that calls llm_call() one or more times ...
    data = finish_tracking()   # dict ready for JSON serialization
"""

import threading
import time

from django.conf import settings

_local = threading.local()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def start_tracking():
    """Begin collecting LLM call data on the current thread."""
    if settings.ENVIRONMENT != 'local':
        return
    _local.calls = []
    _local.start_time = time.time()


def is_tracking():
    """Return True if tracking is active on this thread."""
    if settings.ENVIRONMENT != 'local':
        return False
    return hasattr(_local, 'calls')


def record_call(function_name, model, messages, response, duration):
    """Append one LLM call record.  Called automatically by llm_call()."""
    if not is_tracking():
        return

    system_prompt = _extract_by_role(messages, 'system')
    user_prompt = _extract_by_role(messages, 'user')
    output_text = _extract_output(response)

    input_tokens = response.usage.input_tokens
    cached_tokens = response.usage.input_tokens_details.cached_tokens
    new_input_tokens = input_tokens - cached_tokens
    output_tokens = response.usage.output_tokens
    reasoning_tokens = response.usage.output_tokens_details.reasoning_tokens
    response_tokens = output_tokens - reasoning_tokens

    pricing = settings.LLM_PRICING.get(model, {})
    input_cost = _token_cost(new_input_tokens, pricing.get('input_per_million', 0))
    cached_cost = _token_cost(cached_tokens, pricing.get('cached_input_per_million', 0))
    output_cost = _token_cost(output_tokens, pricing.get('output_per_million', 0))

    _local.calls.append({
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


def finish_tracking():
    """
    Finalize tracking, compute totals, and return the full data dict.
    Clears thread-local state so a new tracking session can begin.
    Returns None if tracking was never started.
    """
    if not is_tracking():
        return None

    wall_clock = round(time.time() - _local.start_time, 3)
    calls = _local.calls

    # Aggregate totals
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

    # Clear state
    del _local.calls
    del _local.start_time

    return data


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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
        # Text output items
        if hasattr(item, 'content'):
            for content_piece in item.content:
                if hasattr(content_piece, 'text'):
                    parts.append(content_piece.text)
        # Function/tool call outputs
        elif hasattr(item, 'type') and item.type == 'function_call':
            name = getattr(item, 'name', '')
            args = getattr(item, 'arguments', '')
            parts.append(f"[tool_call: {name}] {args}")
    return '\n'.join(parts) if parts else ''
