"""
Multi-provider LLM call orchestrator.

Public entry point: `llm_call(...)`. Picks a provider from the model name
(gpt-* -> openai, claude-* -> anthropic), tries it once, and on a retryable
failure falls back to the cross-provider equivalent in
`settings.LLM_FALLBACK_MAP`. Both attempts are recorded in `LLMCall`.

Callers receive a `NormalizedResponse` (see `llm_providers.base`) with
`output_parsed`, `output_text`, `output_items`, `tool_calls`, and `usage`
attributes — provider-agnostic, so existing code paths work for both
OpenAI and Anthropic without further branching.
"""

import json
import logging
import os
import time

from django.conf import settings
from django.utils import timezone as dj_timezone
from dotenv import load_dotenv

from analytics.llm_tracker import record_call, record_error

from .llm_providers import (
    NormalizedResponse,
    ProviderError,
    get_provider,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# API key loading (supports plain string or JSON-wrapped, the AWS AppRunner format)
# ---------------------------------------------------------------------------

def _load_api_key(env_var: str) -> str:
    val = os.environ.get(env_var, "")
    if not val:
        load_dotenv()
        # `.get` rather than `[]` so a missing fallback-provider key doesn't
        # crash at import time on installs that haven't configured both
        # providers yet. Calls that need the key surface the misconfig at
        # call time, which is the right place to fail loudly.
        val = os.environ.get(env_var, "")
    if not val:
        return ""
    try:
        parsed = json.loads(val)
        if isinstance(parsed, dict) and env_var in parsed:
            return parsed[env_var]
    except (json.JSONDecodeError, TypeError):
        pass
    return val


_API_KEY_CACHE: dict = {}


def _get_api_key(env_var: str) -> str:
    if env_var not in _API_KEY_CACHE:
        _API_KEY_CACHE[env_var] = _load_api_key(env_var)
    return _API_KEY_CACHE[env_var]


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

async def llm_call(
    function_name,
    messages,
    model,
    reasoning_level,
    response_format=None,
    tools=None,
    tool_choice=None,
    user=None,
    store=False,
    previous_response_id=None,
    prompt_cache_key=None,
    prompt_cache_retention=None,
    max_tokens=16384,
    timeout=90.0,
) -> NormalizedResponse:
    """Run an LLM call against the model's provider, falling back on retryable errors.

    Args:
        function_name: Logical function name for logging (e.g. 'content_finder_plan').
        messages: Canonical (OpenAI Responses-shaped) message list.
        model: Model id — its prefix picks the provider (gpt-* / claude-*).
        reasoning_level: 'low' | 'medium' | 'high'. Mapped per-provider.
        response_format: Optional Pydantic model for structured output.
        tools: Optional list of tool defs (OpenAI shape; translated for Claude).
        tool_choice: Optional tool-choice constraint.
        user: Django user object — currently unused at this level, kept for API compat.
        store: OpenAI server-side response storage. Ignored on the Anthropic path.
        previous_response_id: OpenAI threading. Ignored on the Anthropic path.
        prompt_cache_key: OpenAI routing-stickiness key. Ignored on the Anthropic path.
        prompt_cache_retention: '24h' opts into extended cache (Claude maps to 1h ttl).
        max_tokens: Required on Anthropic; ignored on OpenAI Responses API.
        timeout: Per-call HTTP timeout in seconds.

    Returns:
        NormalizedResponse — use .output_parsed / .output_text / .output_items /
        .tool_calls / .usage. Don't reach into .raw unless you really need
        provider-specific state.
    """
    primary = get_provider(model)
    fallback_model = settings.LLM_FALLBACK_MAP.get(model)

    call_kwargs = dict(
        function_name=function_name,
        messages=messages,
        reasoning_level=reasoning_level,
        response_format=response_format,
        tools=tools,
        tool_choice=tool_choice,
        store=store,
        previous_response_id=previous_response_id,
        prompt_cache_key=prompt_cache_key,
        prompt_cache_retention=prompt_cache_retention,
        max_tokens=max_tokens,
        timeout=timeout,
    )

    try:
        return await _single_attempt(primary, model, call_kwargs)
    except ProviderError as e:
        # Non-retryable: fallback wouldn't fix the underlying issue (bad
        # input, missing tool, refused content). Surface the SDK error.
        if not e.is_retryable or fallback_model is None:
            raise e.original
        # Retryable + fallback configured — try the other provider once.
        logger.warning(
            "llm_call: falling back %s(%s) -> %s after %s",
            primary.name, model, fallback_model, type(e.original).__name__,
        )

    fallback = get_provider(fallback_model)
    try:
        return await _single_attempt(
            fallback, fallback_model, call_kwargs,
            extra_info={
                'fell_back_from': model,
                'fell_back_from_provider': primary.name,
            },
        )
    except ProviderError as e2:
        # Both providers exhausted — surface the fallback's SDK error.
        raise e2.original


# ---------------------------------------------------------------------------
# Single-attempt helper
# ---------------------------------------------------------------------------

async def _single_attempt(provider, model, call_kwargs, *, extra_info=None):
    """Run one provider attempt with record_call / record_error bookkeeping.

    Returns the NormalizedResponse on success.
    Raises ProviderError on failure (whether retryable or not — the
    orchestrator decides what to do).

    Missing API key is treated as a configuration error: raises immediately
    after logging an LLMCall row, *without* wrapping in ProviderError, so the
    orchestrator doesn't try to fall back (which would just mask the bug
    if the other side is also misconfigured).
    """
    api_key = _get_api_key(provider.api_key_setting)
    if not api_key:
        start_ts = dj_timezone.now()
        err = RuntimeError(
            f"{provider.api_key_setting} is not set; cannot call {provider.name}"
        )
        record_error(
            call_kwargs['function_name'], model, 0.0, err,
            start_ts=start_ts, provider=provider.name, extra_info=extra_info,
        )
        raise err

    start_ts = dj_timezone.now()
    start_time = time.time()
    try:
        response = await provider.make_call(api_key=api_key, model=model, **call_kwargs)
    except ProviderError as e:
        duration = time.time() - start_time
        record_error(
            call_kwargs['function_name'], model, duration, e.original,
            start_ts=start_ts, provider=provider.name, extra_info=extra_info,
        )
        raise

    duration = time.time() - start_time
    record_call(
        call_kwargs['function_name'], model, call_kwargs['messages'],
        response, duration, start_ts=start_ts,
        provider=provider.name, extra_info=extra_info,
    )
    return response


# ---------------------------------------------------------------------------
# Back-compat re-export
# ---------------------------------------------------------------------------
# Tests that pre-date the multi-provider split patch `analytics.utils.llm.AsyncOpenAI`
# directly. Keep the symbol available here so those patches still work. Production
# code should import AsyncOpenAI from analytics.utils.llm_providers.openai_provider.
from openai import AsyncOpenAI  # noqa: E402,F401
