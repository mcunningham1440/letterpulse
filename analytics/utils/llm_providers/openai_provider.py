"""
OpenAI Responses API provider adapter.

Translates the orchestrator's call signature into kwargs for
`AsyncOpenAI.responses.{parse, create}` and translates the response into a
`NormalizedResponse`. Wraps retryable SDK exceptions in `ProviderError` so
the orchestrator can decide whether to fall back to Anthropic.
"""

from __future__ import annotations

import json
import logging
import warnings as _warnings
from typing import Any, List, Optional

from openai import (
    APIConnectionError,
    APITimeoutError,
    AsyncOpenAI,
    InternalServerError,
    RateLimitError,
)

from .base import NormalizedResponse, NormalizedUsage, ProviderError

logger = logging.getLogger(__name__)


PROVIDER_NAME = "openai"

# SDK exception classes the orchestrator should retry on the other provider.
# 400/401/403/404/422/413 are client errors — fallback wouldn't fix them and
# silently bouncing would just produce two failed rows in LLMCall instead of
# one, masking the real bug.
_RETRYABLE_EXC = (
    APIConnectionError,
    APITimeoutError,
    RateLimitError,
    InternalServerError,
)


async def make_call(
    *,
    api_key: str,
    function_name: str,
    messages: List[dict],
    model: str,
    reasoning_level: str,
    response_format: Optional[Any] = None,
    tools: Optional[List[dict]] = None,
    tool_choice: Optional[Any] = None,
    store: bool = False,
    previous_response_id: Optional[str] = None,
    prompt_cache_key: Optional[str] = None,
    prompt_cache_retention: Optional[str] = None,
    max_tokens: int = 16384,  # Unused on OpenAI — Responses API uses its own default.
    timeout: float = 90.0,
) -> NormalizedResponse:
    kwargs = {
        "model": model,
        "input": messages,
        "reasoning": {"effort": reasoning_level},
    }
    if tools is not None:
        kwargs["tools"] = tools
    if tool_choice is not None:
        kwargs["tool_choice"] = tool_choice
    if store:
        kwargs["store"] = True
    if previous_response_id is not None:
        kwargs["previous_response_id"] = previous_response_id
    if prompt_cache_key is not None:
        kwargs["prompt_cache_key"] = prompt_cache_key
    if prompt_cache_retention is not None:
        kwargs["prompt_cache_retention"] = prompt_cache_retention

    # max_retries=0 — the orchestrator owns the retry/fallback budget. Letting
    # the SDK auto-retry would silently burn our wall-clock budget before
    # falling back to Anthropic.
    client = AsyncOpenAI(api_key=api_key, timeout=timeout, max_retries=0)
    try:
        try:
            if response_format is not None:
                kwargs["text_format"] = response_format
                response = await client.responses.parse(**kwargs)
            else:
                response = await client.responses.create(**kwargs)
        finally:
            await client.close()
    except _RETRYABLE_EXC as e:
        raise ProviderError(e, is_retryable=True, provider=PROVIDER_NAME) from e
    except Exception as e:
        raise ProviderError(e, is_retryable=False, provider=PROVIDER_NAME) from e

    return _to_normalized(response, model=model, response_format=response_format)


# ---------------------------------------------------------------------------
# Response -> NormalizedResponse
# ---------------------------------------------------------------------------

def _to_normalized(response, *, model: str, response_format) -> NormalizedResponse:
    """Translate an OpenAI Responses-API response into our normalized shape."""
    output_items = _serialize_output_items(response)

    text_parts: List[str] = []
    tool_calls: List[dict] = []

    # Use response.output (the SDK object) for text/tool extraction so we get
    # accurate types — output_items is the JSON-dumped form for re-feeding.
    for item in getattr(response, 'output', []) or []:
        item_type = getattr(item, 'type', None)
        if item_type == 'message':
            for piece in getattr(item, 'content', []) or []:
                text = getattr(piece, 'text', None)
                if text:
                    text_parts.append(text)
        elif item_type == 'function_call':
            args = getattr(item, 'arguments', '') or ''
            call_id = getattr(item, 'call_id', '') or ''
            name = getattr(item, 'name', '') or ''
            tool_calls.append({
                'call_id': call_id,
                'name': name,
                'arguments_json': args,
            })
            text_parts.append(f"[tool_call: {name}] {args}")

    parsed = _extract_parsed(response) if response_format is not None else None

    usage = _extract_usage(response)

    return NormalizedResponse(
        output_parsed=parsed,
        output_text='\n'.join(text_parts),
        output_items=output_items,
        tool_calls=tool_calls,
        usage=usage,
        id=getattr(response, 'id', '') or '',
        provider=PROVIDER_NAME,
        model=model,
        raw=response,
    )


def _extract_parsed(response):
    """Pull the Pydantic-parsed object from a `responses.parse()` response.

    `responses.parse()` exposes the parsed object two ways depending on shape:
    `response.output_parsed` (preferred) or
    `response.output[-1].content[0].parsed` (last-message, content-block 0).
    Return whichever is populated. Returns None when neither is present —
    this is a soft fallback so a partial response (e.g. refusal) still
    surfaces to the caller via `output_text` rather than erroring at the
    boundary. The orchestrator only ever calls this when `response_format`
    was set, so a missing `parsed` is genuinely anomalous.
    """
    parsed = getattr(response, 'output_parsed', None)
    if parsed is not None:
        return parsed
    try:
        return response.output[-1].content[0].parsed
    except (AttributeError, IndexError):
        return None


def _serialize_output_items(response) -> List[dict]:
    """Serialize response.output items to canonical (OpenAI-shaped) dicts.

    This is the format the existing codebase appends to `messages` between
    stages. Reasoning items keep their encrypted blob so OpenAI itself can
    pick them up on the next call; the Anthropic adapter drops them at its
    own translation boundary.
    """
    serialized = []
    for item in getattr(response, 'output', []) or []:
        try:
            with _warnings.catch_warnings():
                # responses.parse() attaches a non-API `parsed` field to its
                # ParsedResponseOutputText content items. Dumping it warns
                # noisily even though we strip it below.
                _warnings.filterwarnings(
                    'ignore',
                    message=r'Pydantic serializer warnings:',
                    category=UserWarning,
                )
                data = item.model_dump(mode='json', exclude_none=True)
        except AttributeError:
            data = dict(item) if isinstance(item, dict) else {}

        for content_piece in (data.get('content') or []):
            if isinstance(content_piece, dict):
                content_piece.pop('parsed', None)

        serialized.append(data)
    return serialized


def _extract_usage(response) -> NormalizedUsage:
    """Pull token usage from an OpenAI response.usage object.

    Splits the gross input_tokens into NEW vs cached. Defaults to 0 for any
    missing sub-field rather than raising — older SDK versions or mocked
    responses in tests don't always have the full nested shape.
    """
    u = getattr(response, 'usage', None)
    if u is None:
        return NormalizedUsage()

    input_tokens = int(getattr(u, 'input_tokens', 0) or 0)
    output_tokens = int(getattr(u, 'output_tokens', 0) or 0)

    cached = 0
    in_details = getattr(u, 'input_tokens_details', None)
    if in_details is not None:
        cached = int(getattr(in_details, 'cached_tokens', 0) or 0)

    reasoning = 0
    out_details = getattr(u, 'output_tokens_details', None)
    if out_details is not None:
        reasoning = int(getattr(out_details, 'reasoning_tokens', 0) or 0)

    new_input = max(0, input_tokens - cached)
    response_tokens = max(0, output_tokens - reasoning)

    return NormalizedUsage(
        input_tokens=new_input,
        cached_input_tokens=cached,
        cache_creation_tokens=0,
        output_tokens=response_tokens,
        reasoning_tokens=reasoning,
    )
