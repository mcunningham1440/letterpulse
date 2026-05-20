"""
Anthropic Messages API provider adapter.

Translates canonical (OpenAI Responses-shaped) messages into Claude's
`system` + `messages` format, calls the Anthropic SDK, and translates the
response back into canonical shape so the orchestrator and existing call
sites don't need to know which provider ran.

Notable provider differences handled here:

- **Stateless conversations.** Claude has no `previous_response_id`. The
  `store=True` / `previous_response_id` kwargs from llm_call are silently
  ignored on this path (we log at debug level so it's visible in dev panel
  debugging). Conversation context is replayed in `messages` every call —
  which the codebase already does.
- **Prompt cache key.** No equivalent. `prompt_cache_key` is dropped here;
  Claude's caching is content-prefix-hash based and works without one.
- **Cache retention.** `prompt_cache_retention="24h"` is mapped to Claude's
  `ttl: "1h"` (Claude's max ephemeral TTL). This is a silent downgrade
  vs OpenAI — flagged in the plan; logged at debug level.
- **Reasoning effort.** Sonnet 4.6 uses adaptive thinking with
  `output_config.effort`. Older / smaller models (Haiku) use manual
  `thinking.budget_tokens`. The mapping table is `_MODEL_THINKING_MODE`.
- **Forced tool use + thinking.** Claude returns 400 when adaptive/extended
  thinking is on and `tool_choice` forces a tool. We detect this and
  disable thinking for that single call — behavioral degradation, flagged.
- **Tool input schema.** OpenAI tool dicts get translated to Claude's
  `{name, description, input_schema}` shape via `_translate_tools`.

Token usage: Claude doesn't expose a thinking-tokens count separate from
`output_tokens`, so `reasoning_tokens` is always 0 on this path.
`cache_creation_tokens` (cache writes) is recorded separately.
"""

from __future__ import annotations

import json
import logging
from typing import Any, List, Optional

from anthropic import (
    APIConnectionError,
    APITimeoutError,
    AsyncAnthropic,
    InternalServerError,
    RateLimitError,
)

# Newer 5xx classes that may not exist on every SDK version — pull
# defensively so the import doesn't explode on an older anthropic install.
_extra_retryable = []
for _name in ("OverloadedError", "ServiceUnavailableError", "DeadlineExceededError"):
    try:
        _extra_retryable.append(getattr(__import__('anthropic'), _name))
    except AttributeError:
        pass

from .base import NormalizedResponse, NormalizedUsage, ProviderError

logger = logging.getLogger(__name__)


PROVIDER_NAME = "anthropic"

# Adaptive thinking is available on Sonnet 4.6 / Opus 4.6+ / Mythos. Smaller
# models (Haiku) use manual budget_tokens. Unknown models default to
# 'adaptive' — safe because Sonnet 4.6 is our primary Claude target and
# adaptive is the documented "use this by default" path.
_MODEL_THINKING_MODE = {
    "claude-sonnet-4-6": "adaptive",
    "claude-opus-4-7": "adaptive",
    "claude-opus-4-6": "adaptive",
    "claude-haiku-4-5": "manual",
    "claude-haiku-4-5-20251001": "manual",
}

# Manual-mode budget_tokens for low/medium/high. Picks are approximations
# guided by the docs ("4-5k light, 10k standard"). No official mapping from
# OpenAI effort levels exists, so these may need retuning after observing
# real usage — flagged in the plan.
_MANUAL_BUDGET_BY_EFFORT = {
    "low": 1024,
    "medium": 4096,
    "high": 10000,
}

_RETRYABLE_EXC = tuple([
    APIConnectionError,
    APITimeoutError,
    RateLimitError,
    InternalServerError,
] + _extra_retryable)


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
    store: bool = False,                          # ignored on Anthropic
    previous_response_id: Optional[str] = None,   # ignored on Anthropic
    prompt_cache_key: Optional[str] = None,       # ignored on Anthropic
    prompt_cache_retention: Optional[str] = None,
    max_tokens: int = 16384,
    timeout: float = 90.0,
) -> NormalizedResponse:
    if store:
        logger.debug("anthropic_provider: ignoring store=True (no server-side response storage)")
    if previous_response_id:
        logger.debug("anthropic_provider: ignoring previous_response_id (Claude is stateless)")
    if prompt_cache_key:
        logger.debug("anthropic_provider: ignoring prompt_cache_key (no routing-stickiness equivalent)")

    system_blocks, anthropic_messages = _translate_messages_to_anthropic(messages)

    forcing_tool = _is_forcing_tool(tool_choice)
    thinking_param, output_config = _build_thinking_and_effort(
        model=model, reasoning_level=reasoning_level, forcing_tool=forcing_tool,
    )

    # Attach cache_control to the last system block + last tool def so the
    # system prompt and tool schemas are cached across calls within a flow.
    cache_ttl = "1h" if prompt_cache_retention == "24h" else None
    if cache_ttl:
        logger.debug("anthropic_provider: mapping prompt_cache_retention='24h' -> cache_control ttl='1h'")
    system_blocks = _attach_cache_control_last(system_blocks, ttl=cache_ttl)

    anthropic_tools = _translate_tools(tools)
    anthropic_tools = _attach_cache_control_last(anthropic_tools, ttl=cache_ttl)
    anthropic_tool_choice = _translate_tool_choice(tool_choice)

    kwargs: dict = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": anthropic_messages,
    }
    if system_blocks:
        kwargs["system"] = system_blocks
    if anthropic_tools:
        kwargs["tools"] = anthropic_tools
    if anthropic_tool_choice is not None:
        kwargs["tool_choice"] = anthropic_tool_choice
    if thinking_param is not None:
        kwargs["thinking"] = thinking_param
    if output_config:
        kwargs["output_config"] = output_config

    # max_retries=0 — orchestrator owns the retry/fallback budget.
    client = AsyncAnthropic(api_key=api_key, timeout=timeout, max_retries=0)
    try:
        try:
            if response_format is not None:
                # Claude's first-class structured output. `messages.parse()`
                # validates the response against the schema; on success the
                # parsed object is on `response.parsed_output`.
                kwargs["output_format"] = response_format
                response = await client.messages.parse(**kwargs)
            else:
                response = await client.messages.create(**kwargs)
        finally:
            await client.close()
    except _RETRYABLE_EXC as e:
        raise ProviderError(e, is_retryable=True, provider=PROVIDER_NAME) from e
    except Exception as e:
        raise ProviderError(e, is_retryable=False, provider=PROVIDER_NAME) from e

    return _to_normalized(response, model=model, response_format=response_format)


# ---------------------------------------------------------------------------
# Outbound: canonical (OpenAI-shaped) -> Anthropic
# ---------------------------------------------------------------------------

def _translate_messages_to_anthropic(canonical: List[dict]):
    """Translate canonical message items into (system_blocks, anthropic_messages).

    Canonical format covers:
      - {"role": "system" | "user" | "assistant", "content": str}
      - {"type": "message", "role": "assistant", "content": [{"type":"output_text","text":"..."}]}
      - {"type": "function_call", "name":..., "arguments": json-str, "call_id":...}
      - {"type": "function_call_output", "call_id":..., "output": str}
      - {"type": "reasoning", ...}                              # dropped

    System items are collected and joined into Claude's top-level `system`
    list-of-blocks parameter. Everything else is grouped by role
    (function_call_output -> user, function_call/message -> assistant) and
    consecutive same-role items merge into one Anthropic message.
    """
    system_parts: List[str] = []
    out_messages: List[dict] = []

    current_role: Optional[str] = None
    current_blocks: List[dict] = []

    def flush():
        nonlocal current_role, current_blocks
        if not current_blocks:
            return
        # Anthropic requires tool_result blocks first inside a user message.
        if current_role == "user":
            current_blocks.sort(key=lambda b: 0 if b.get("type") == "tool_result" else 1)
        out_messages.append({"role": current_role, "content": current_blocks})
        current_role = None
        current_blocks = []

    for msg in canonical or []:
        role = msg.get("role")
        item_type = msg.get("type")

        # System -> collect, don't emit
        if role == "system":
            content = msg.get("content")
            text = _flatten_text(content)
            if text:
                system_parts.append(text)
            continue

        # Reasoning items are OpenAI-private encrypted state. Claude has no
        # equivalent surface so we drop them at the boundary.
        if item_type == "reasoning":
            continue

        # function_call_output -> tool_result in a user message
        if item_type == "function_call_output":
            block = {
                "type": "tool_result",
                "tool_use_id": msg.get("call_id") or "",
                "content": str(msg.get("output", "") or ""),
            }
            if current_role != "user":
                flush()
                current_role = "user"
            current_blocks.append(block)
            continue

        # function_call -> tool_use in the current assistant message
        if item_type == "function_call":
            args_raw = msg.get("arguments", "{}") or "{}"
            try:
                args = json.loads(args_raw) if isinstance(args_raw, str) else dict(args_raw)
            except (json.JSONDecodeError, TypeError):
                # Malformed args — pass through as a stringified blob under
                # an _raw key rather than dropping the call. Flagged as a soft
                # fallback per global instructions.
                args = {"_raw": str(args_raw)}
            block = {
                "type": "tool_use",
                "id": msg.get("call_id") or "",
                "name": msg.get("name") or "",
                "input": args,
            }
            if current_role != "assistant":
                flush()
                current_role = "assistant"
            current_blocks.append(block)
            continue

        # Plain {"role": "assistant", "content": "..."} or {type:"message", role:"assistant", content:[...]}
        target_role: Optional[str] = None
        if role == "assistant" or item_type == "message":
            target_role = "assistant"
        elif role == "user":
            target_role = "user"

        if target_role is None:
            # Unknown shape — skip rather than fail the call.
            continue

        text = _flatten_text(msg.get("content"))
        if not text:
            continue
        block = {"type": "text", "text": text}
        if current_role != target_role:
            flush()
            current_role = target_role
        current_blocks.append(block)

    flush()

    system_blocks: List[dict] = []
    if system_parts:
        system_blocks = [{"type": "text", "text": "\n\n".join(system_parts)}]

    return system_blocks, out_messages


def _flatten_text(content) -> str:
    """Return a single text string from either a str or a list of content pieces."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for piece in content:
            if isinstance(piece, dict):
                t = piece.get("text") or piece.get("input_text") or piece.get("output_text") or ""
                if t:
                    parts.append(t)
            elif isinstance(piece, str):
                parts.append(piece)
        return "\n".join(parts)
    return ""


def _translate_tools(openai_tools: Optional[List[dict]]) -> Optional[List[dict]]:
    """Translate OpenAI Responses-API tool defs to Claude's {name, description, input_schema}.

    OpenAI shape:
      {"type": "function", "name": ..., "description": ..., "parameters": {...}, "strict": bool}
    Claude shape:
      {"name": ..., "description": ..., "input_schema": {...}}

    Returns None when there are no tools, so callers can omit the kwarg
    cleanly instead of sending an empty list (which Claude rejects).
    """
    if not openai_tools:
        return None
    out = []
    for t in openai_tools:
        if not isinstance(t, dict):
            continue
        name = t.get("name") or ""
        desc = t.get("description") or ""
        schema = t.get("parameters") or t.get("input_schema") or {"type": "object", "properties": {}}
        anth = {"name": name, "description": desc, "input_schema": schema}
        out.append(anth)
    return out


def _translate_tool_choice(openai_tool_choice):
    """Translate OpenAI's tool_choice to Claude's form.

    - "auto" / None / "none" -> pass through ("none" becomes {"type": "none"})
    - "required" -> {"type": "any"}
    - {"type": "function", "name": "foo"} -> {"type": "tool", "name": "foo"}
    - already-Claude shape -> pass through unchanged
    """
    if openai_tool_choice is None:
        return None
    if openai_tool_choice == "auto":
        return {"type": "auto"}
    if openai_tool_choice == "required":
        return {"type": "any"}
    if openai_tool_choice == "none":
        return {"type": "none"}
    if isinstance(openai_tool_choice, dict):
        # OpenAI: {"type": "function", "name": "foo"}; Claude: {"type": "tool", "name": "foo"}
        if openai_tool_choice.get("type") == "function" and openai_tool_choice.get("name"):
            return {"type": "tool", "name": openai_tool_choice["name"]}
        return openai_tool_choice
    return openai_tool_choice


def _is_forcing_tool(tool_choice) -> bool:
    """True when tool_choice forces a tool — which Claude rejects alongside thinking."""
    if tool_choice == "required":
        return True
    if isinstance(tool_choice, dict):
        t = tool_choice.get("type")
        return t in ("any", "tool", "function")
    return False


def _build_thinking_and_effort(*, model: str, reasoning_level: str, forcing_tool: bool):
    """Build the `thinking` kwarg and `output_config` for a Claude call.

    When the caller forces a tool, thinking must be off (Claude rejects the
    combination). This is a behavioral degradation vs OpenAI — flagged in
    the plan.
    """
    if forcing_tool:
        return ({"type": "disabled"}, {})

    mode = _MODEL_THINKING_MODE.get(model, "adaptive")
    effort = reasoning_level if reasoning_level in ("low", "medium", "high") else "medium"

    if mode == "adaptive":
        return ({"type": "adaptive"}, {"effort": effort})

    # manual
    budget = _MANUAL_BUDGET_BY_EFFORT.get(effort, _MANUAL_BUDGET_BY_EFFORT["medium"])
    return ({"type": "enabled", "budget_tokens": budget}, {})


def _attach_cache_control_last(blocks, *, ttl=None):
    """Mark the last block in a list with cache_control so its prefix is cached.

    Returns the list (mutated in place). No-op when blocks is empty or None.
    Used for system blocks and tools. Stays under Claude's 4-breakpoint
    limit — at most 2 markers in total (one on system, one on tools).
    """
    if not blocks:
        return blocks
    last = blocks[-1]
    if not isinstance(last, dict):
        return blocks
    cc = {"type": "ephemeral"}
    if ttl:
        cc["ttl"] = ttl
    last["cache_control"] = cc
    return blocks


# ---------------------------------------------------------------------------
# Inbound: Anthropic response -> NormalizedResponse
# ---------------------------------------------------------------------------

def _to_normalized(response, *, model: str, response_format) -> NormalizedResponse:
    output_items: List[dict] = []
    text_parts: List[str] = []
    tool_calls: List[dict] = []

    for block in getattr(response, 'content', []) or []:
        bt = getattr(block, 'type', None)
        if bt == "text":
            text = getattr(block, 'text', '') or ''
            if text:
                text_parts.append(text)
                output_items.append({
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": text}],
                })
        elif bt == "tool_use":
            name = getattr(block, 'name', '') or ''
            block_id = getattr(block, 'id', '') or ''
            input_dict = getattr(block, 'input', {}) or {}
            try:
                args_json = json.dumps(input_dict)
            except (TypeError, ValueError):
                args_json = "{}"
            tool_calls.append({
                'call_id': block_id,
                'name': name,
                'arguments_json': args_json,
            })
            output_items.append({
                "type": "function_call",
                "call_id": block_id,
                "name": name,
                "arguments": args_json,
            })
            text_parts.append(f"[tool_call: {name}] {args_json}")
        elif bt == "thinking":
            # Claude-private state. Don't round-trip through canonical
            # format — see module docstring.
            continue

    parsed = None
    if response_format is not None:
        # messages.parse() returns the parsed Pydantic model on .parsed_output
        parsed = getattr(response, 'parsed_output', None)

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


def _extract_usage(response) -> NormalizedUsage:
    """Pull token usage from an Anthropic response.usage object.

    Anthropic reports `input_tokens` *only for tokens after the last cache
    breakpoint*, so the "true" billable new-input count is
    `input_tokens + cache_creation_input_tokens` (cache writes are billed at
    write rates; we report them separately so the dev panel can show them).

    `cache_read_input_tokens` is the count of tokens served from cache.

    There's no per-call thinking-tokens breakdown in the response — thinking
    is billed inside `output_tokens`. We surface 0 in `reasoning_tokens` so
    the LLMCall split is consistent (response_tokens = all output).
    """
    u = getattr(response, 'usage', None)
    if u is None:
        return NormalizedUsage()

    new_input = int(getattr(u, 'input_tokens', 0) or 0)
    cache_read = int(getattr(u, 'cache_read_input_tokens', 0) or 0)
    cache_create = int(getattr(u, 'cache_creation_input_tokens', 0) or 0)
    output = int(getattr(u, 'output_tokens', 0) or 0)

    return NormalizedUsage(
        input_tokens=new_input,
        cached_input_tokens=cache_read,
        cache_creation_tokens=cache_create,
        output_tokens=output,
        reasoning_tokens=0,
    )
