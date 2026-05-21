"""
Shared types and helpers for LLM provider adapters.

Two providers live alongside this module — `openai_provider` and
`anthropic_provider`. Each translates its SDK response into the
`NormalizedResponse` shape so callers (and llm_tracker) see one interface.

Canonical message format is OpenAI Responses-API items — the format the
existing codebase already produces. The Anthropic adapter translates to/from
Claude's native shape at the SDK boundary.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, List, Optional


# ---------------------------------------------------------------------------
# Normalized response shape
# ---------------------------------------------------------------------------

@dataclass
class NormalizedUsage:
    """Token usage normalized across providers.

    `input_tokens` counts NEW input tokens only (not cache reads). To recover
    the gross input total, add `cached_input_tokens + cache_creation_tokens`.
    `cache_creation_tokens` is always 0 for OpenAI (no equivalent concept).
    `reasoning_tokens` covers OpenAI's reasoning items; for Anthropic it is 0
    because Claude does not separately count thinking tokens (they are billed
    inside `output_tokens`).
    """
    input_tokens: int = 0
    cached_input_tokens: int = 0
    cache_creation_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0


@dataclass
class NormalizedResponse:
    """Provider-agnostic response wrapper.

    - `output_parsed`: Pydantic-validated structured output, or None for free-form calls.
    - `output_text`: assistant text. Tool-call items are rendered as
      `[tool_call: name] <args_json>` so dev-panel can show them inline.
    - `output_items`: canonical (OpenAI-shaped) message items for re-feeding
      into the next call's `messages` list. Use this instead of raw SDK output.
    - `tool_calls`: parsed tool-call dicts ({call_id, name, arguments_json}).
    - `usage`: NormalizedUsage.
    - `id`: provider response id (resp_… or msg_…); empty string if unknown.
    - `provider`: 'openai' | 'anthropic'.
    - `model`: model the call ran on (may differ from `model` passed to llm_call
      when fallback fires).
    - `raw`: the underlying SDK response, kept for debugging / provider-specific
      access. Don't depend on it in shared code.
    """
    output_parsed: Optional[Any] = None
    output_text: str = ""
    output_items: List[dict] = field(default_factory=list)
    tool_calls: List[dict] = field(default_factory=list)
    usage: NormalizedUsage = field(default_factory=NormalizedUsage)
    id: str = ""
    provider: str = ""
    model: str = ""
    raw: Any = None


# ---------------------------------------------------------------------------
# Provider error wrapper
# ---------------------------------------------------------------------------

class ProviderError(Exception):
    """Wraps an SDK-level exception with a retryability flag.

    `is_retryable=True` means the orchestrator should try the cross-provider
    fallback. Authentication errors, 400-class client errors, and similar
    non-recoverable conditions get `is_retryable=False` and bubble straight
    up — falling back wouldn't fix them (and might mask the real bug).

    `original` keeps the SDK exception so callers and the tracker can still
    inspect its type / message.
    """
    def __init__(self, original: BaseException, *, is_retryable: bool, provider: str):
        self.original = original
        self.is_retryable = is_retryable
        self.provider = provider
        super().__init__(f"[{provider}] {type(original).__name__}: {original}")


# ---------------------------------------------------------------------------
# Provider selection
# ---------------------------------------------------------------------------

_OPENAI_MODEL_RE = re.compile(r'^gpt[-_]', re.IGNORECASE)
_ANTHROPIC_MODEL_RE = re.compile(r'^claude[-_]', re.IGNORECASE)


def provider_name_for(model: str) -> str:
    """Return 'openai' or 'anthropic' based on the model name's prefix.

    Raises ValueError on an unrecognized prefix so the caller fails loudly
    rather than silently routing to a default provider that may not be
    configured.
    """
    if not model:
        raise ValueError("model name is required")
    if _OPENAI_MODEL_RE.match(model):
        return 'openai'
    if _ANTHROPIC_MODEL_RE.match(model):
        return 'anthropic'
    raise ValueError(
        f"Cannot determine provider for model {model!r}: "
        f"expected a prefix of 'gpt-' or 'claude-'."
    )
