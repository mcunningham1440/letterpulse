"""
LLM provider registry.

`get_provider(model)` returns a `Provider` record for the model's provider
(picked by prefix: gpt-* -> openai, claude-* -> anthropic). The orchestrator
in `analytics.utils.llm` calls `provider.make_call(...)`.

Each provider is a thin module — no class hierarchy — exposing:
  - PROVIDER_NAME: str
  - make_call(*, api_key, function_name, messages, model, reasoning_level, ...): NormalizedResponse
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from . import anthropic_provider, openai_provider
from .base import (
    NormalizedResponse,
    NormalizedUsage,
    ProviderError,
    provider_name_for,
)


@dataclass(frozen=True)
class Provider:
    name: str
    make_call: Callable
    api_key_setting: str   # name of the Django setting holding the API key


_PROVIDERS = {
    "openai": Provider(
        name="openai",
        make_call=openai_provider.make_call,
        api_key_setting="OPENAI_API_KEY",
    ),
    "anthropic": Provider(
        name="anthropic",
        make_call=anthropic_provider.make_call,
        api_key_setting="ANTHROPIC_API_KEY",
    ),
}


def get_provider(model: str) -> Provider:
    """Return the Provider record for the given model name."""
    return _PROVIDERS[provider_name_for(model)]


__all__ = [
    "Provider",
    "NormalizedResponse",
    "NormalizedUsage",
    "ProviderError",
    "get_provider",
    "provider_name_for",
]
