"""
Tests for the primary -> fallback flow in analytics/utils/llm.py.

Both providers are stubbed at the get_provider boundary. We exercise the
orchestrator's decisions: retryable vs non-retryable, mapped vs unmapped
fallback model, and the dual-record (primary error + fallback success)
shape that ends up in the tracker.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from analytics.utils.llm import llm_call
from analytics.utils.llm_providers.base import (
    NormalizedResponse,
    NormalizedUsage,
    ProviderError,
)


def _make_normalized(provider, model):
    return NormalizedResponse(
        output_parsed=None,
        output_text="ok",
        output_items=[],
        tool_calls=[],
        usage=NormalizedUsage(),
        id="resp_1",
        provider=provider,
        model=model,
        raw=None,
    )


def _provider(name, *, api_key_setting, make_call):
    p = MagicMock(name=f"{name}Provider")
    p.name = name
    p.api_key_setting = api_key_setting
    p.make_call = make_call
    return p


@pytest.fixture(autouse=True)
def _stub_tracker():
    with patch("analytics.utils.llm.record_call") as rc, \
         patch("analytics.utils.llm.record_error") as re_:
        yield rc, re_


@pytest.fixture(autouse=True)
def _seed_keys():
    from analytics.utils import llm as llm_mod
    llm_mod._API_KEY_CACHE["OPENAI_API_KEY"] = "test-openai"
    llm_mod._API_KEY_CACHE["ANTHROPIC_API_KEY"] = "test-anthropic"
    yield
    llm_mod._API_KEY_CACHE.clear()


@pytest.fixture
def _fallback_map():
    """settings.LLM_FALLBACK_MAP — confirm the gpt <-> claude tier mapping."""
    from django.conf import settings
    return settings.LLM_FALLBACK_MAP


# -- Retryable failure triggers fallback ----------------------------------

class RetryableFallbackTests:

    async def test_retryable_primary_failure_falls_back_and_succeeds(
            self, _stub_tracker, _fallback_map):
        rc, re_ = _stub_tracker
        # gpt-5.4 fails retryably; fallback (claude-sonnet-4-6) succeeds.
        primary_err = TimeoutError("primary timed out")
        primary = _provider(
            "openai", api_key_setting="OPENAI_API_KEY",
            make_call=AsyncMock(side_effect=ProviderError(
                primary_err, is_retryable=True, provider="openai",
            )),
        )
        fallback_response = _make_normalized("anthropic", "claude-sonnet-4-6")
        fallback = _provider(
            "anthropic", api_key_setting="ANTHROPIC_API_KEY",
            make_call=AsyncMock(return_value=fallback_response),
        )

        def get_provider_side(model):
            return primary if model == "gpt-5.4" else fallback

        with patch("analytics.utils.llm.get_provider", side_effect=get_provider_side):
            result = await llm_call("f", [{"role": "user", "content": "x"}], "gpt-5.4", "low")
        assert result is fallback_response

        # Failure was logged for primary, success for fallback.
        re_.assert_called_once()
        rc.assert_called_once()

        # The successful row carries the fell_back_from marker.
        rc_kwargs = rc.call_args.kwargs
        assert rc_kwargs.get("provider") == "anthropic"
        assert rc_kwargs.get("extra_info", {}).get("fell_back_from") == "gpt-5.4"
        assert rc_kwargs.get("extra_info", {}).get("fell_back_from_provider") == "openai"


# -- Non-retryable failure surfaces immediately --------------------------

class NonRetryableTests:

    async def test_nonretryable_primary_failure_does_not_fall_back(self, _stub_tracker):
        rc, re_ = _stub_tracker
        # A 400 / client bug shouldn't get a second chance on the other
        # provider — fallback would just mask the real error.
        primary_err = RuntimeError("bad request")
        primary = _provider(
            "openai", api_key_setting="OPENAI_API_KEY",
            make_call=AsyncMock(side_effect=ProviderError(
                primary_err, is_retryable=False, provider="openai",
            )),
        )
        # If the orchestrator wrongly fell back, this would be hit:
        fallback = _provider(
            "anthropic", api_key_setting="ANTHROPIC_API_KEY",
            make_call=AsyncMock(return_value=_make_normalized("anthropic", "claude-sonnet-4-6")),
        )

        def get_provider_side(model):
            return primary if model == "gpt-5.4" else fallback

        with patch("analytics.utils.llm.get_provider", side_effect=get_provider_side):
            with pytest.raises(RuntimeError, match="bad request"):
                await llm_call("f", [], "gpt-5.4", "low")

        # Fallback was NOT attempted.
        fallback.make_call.assert_not_awaited()
        re_.assert_called_once()
        rc.assert_not_called()


# -- Both providers fail ---------------------------------------------------

class BothFailTests:

    async def test_both_fail_raises_fallback_error(self, _stub_tracker):
        rc, re_ = _stub_tracker
        primary_err = TimeoutError("primary timeout")
        fallback_err = TimeoutError("fallback timeout")
        primary = _provider(
            "openai", api_key_setting="OPENAI_API_KEY",
            make_call=AsyncMock(side_effect=ProviderError(
                primary_err, is_retryable=True, provider="openai",
            )),
        )
        fallback = _provider(
            "anthropic", api_key_setting="ANTHROPIC_API_KEY",
            make_call=AsyncMock(side_effect=ProviderError(
                fallback_err, is_retryable=True, provider="anthropic",
            )),
        )

        def get_provider_side(model):
            return primary if model == "gpt-5.4" else fallback

        with patch("analytics.utils.llm.get_provider", side_effect=get_provider_side):
            with pytest.raises(TimeoutError, match="fallback timeout"):
                await llm_call("f", [], "gpt-5.4", "low")

        # Two failures logged — one per attempt.
        assert re_.call_count == 2
        rc.assert_not_called()


# -- No fallback configured ----------------------------------------------

class NoFallbackTests:

    async def test_retryable_failure_without_fallback_raises_primary_error(self, _stub_tracker):
        rc, re_ = _stub_tracker
        # gpt-7 is not in LLM_FALLBACK_MAP — primary failure must bubble up
        # rather than silently routing to some default.
        primary_err = TimeoutError("primary timed out")
        primary = _provider(
            "openai", api_key_setting="OPENAI_API_KEY",
            make_call=AsyncMock(side_effect=ProviderError(
                primary_err, is_retryable=True, provider="openai",
            )),
        )

        # Patch settings.LLM_FALLBACK_MAP to a dict that doesn't include "gpt-7"
        from django.test import override_settings
        with override_settings(LLM_FALLBACK_MAP={}):
            with patch("analytics.utils.llm.get_provider", return_value=primary):
                with pytest.raises(TimeoutError, match="primary timed out"):
                    await llm_call("f", [], "gpt-7", "low")

        re_.assert_called_once()
        rc.assert_not_called()
