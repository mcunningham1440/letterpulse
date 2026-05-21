"""
Tests for analytics/utils/llm.py — the multi-provider orchestrator.

These tests stub out the underlying providers at the orchestrator boundary
(`get_provider`) so we exercise dispatch + tracking without touching real
SDKs. Per-provider tests live in test_openai_provider.py and
test_anthropic_provider.py; fallback orchestration lives in
test_provider_fallback.py.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from analytics.utils.llm import llm_call
from analytics.utils.llm_providers.base import (
    NormalizedResponse,
    NormalizedUsage,
    provider_name_for,
)


def _make_normalized(provider="openai", model="gpt-5.4"):
    """A NormalizedResponse with minimal usage so the tracker doesn't choke."""
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


def _provider_with_make_call(name, response, api_key_setting):
    """Build a stand-in Provider record whose make_call returns `response`."""
    p = MagicMock(name=f"{name}Provider")
    p.name = name
    p.api_key_setting = api_key_setting
    p.make_call = AsyncMock(return_value=response)
    return p


@pytest.fixture(autouse=True)
def _stub_tracker():
    """Tracker writes go to a queue that touches the DB. Stub them out so
    these tests stay hermetic."""
    with patch("analytics.utils.llm.record_call") as rc, \
         patch("analytics.utils.llm.record_error") as re_:
        yield rc, re_


@pytest.fixture
def _seed_keys():
    """Seed the orchestrator's API key cache so _get_api_key short-circuits
    instead of reading os.environ / .env on every test."""
    from analytics.utils import llm as llm_mod
    llm_mod._API_KEY_CACHE["OPENAI_API_KEY"] = "test-openai"
    llm_mod._API_KEY_CACHE["ANTHROPIC_API_KEY"] = "test-anthropic"
    yield
    llm_mod._API_KEY_CACHE.clear()


# -- Provider dispatch by model name -------------------------------------

class ProviderDispatchTests:

    def test_gpt_prefix_routes_to_openai(self):
        assert provider_name_for("gpt-5.4") == "openai"
        assert provider_name_for("gpt-5.4-mini") == "openai"

    def test_claude_prefix_routes_to_anthropic(self):
        assert provider_name_for("claude-sonnet-4-6") == "anthropic"
        assert provider_name_for("claude-haiku-4-5") == "anthropic"

    def test_unknown_prefix_raises_value_error(self):
        with pytest.raises(ValueError):
            provider_name_for("llama-3")

    async def test_gpt_model_invokes_openai_provider(self, _seed_keys):
        response = _make_normalized(provider="openai", model="gpt-5.4")
        openai_p = _provider_with_make_call("openai", response, "OPENAI_API_KEY")

        with patch("analytics.utils.llm.get_provider", return_value=openai_p):
            result = await llm_call(
                "f", [{"role": "user", "content": "x"}], "gpt-5.4", "low",
            )
        openai_p.make_call.assert_awaited_once()
        assert openai_p.make_call.await_args.kwargs["model"] == "gpt-5.4"
        assert result is response

    async def test_claude_model_invokes_anthropic_provider(self, _seed_keys):
        response = _make_normalized(provider="anthropic", model="claude-sonnet-4-6")
        anthropic_p = _provider_with_make_call("anthropic", response, "ANTHROPIC_API_KEY")

        with patch("analytics.utils.llm.get_provider", return_value=anthropic_p):
            result = await llm_call(
                "f", [{"role": "user", "content": "x"}], "claude-sonnet-4-6", "low",
            )
        anthropic_p.make_call.assert_awaited_once()
        assert anthropic_p.make_call.await_args.kwargs["model"] == "claude-sonnet-4-6"
        assert result is response


# -- Tracking integration -------------------------------------------------

class TrackingHookTests:

    async def test_record_call_invoked_on_success_with_provider(self, _seed_keys, _stub_tracker):
        rc, _ = _stub_tracker
        response = _make_normalized(provider="openai", model="gpt-5.4")
        openai_p = _provider_with_make_call("openai", response, "OPENAI_API_KEY")
        with patch("analytics.utils.llm.get_provider", return_value=openai_p):
            await llm_call("my_fn", [{"role": "user", "content": "x"}], "gpt-5.4", "low")
        rc.assert_called_once()
        # `provider` arrives as a kwarg from the orchestrator.
        assert rc.call_args.kwargs.get("provider") == "openai"

    async def test_record_error_invoked_on_nonretryable_then_reraises(
            self, _seed_keys, _stub_tracker):
        from analytics.utils.llm_providers.base import ProviderError
        rc, re_ = _stub_tracker

        underlying = RuntimeError("client bug")
        bad_provider = MagicMock()
        bad_provider.name = "openai"
        bad_provider.api_key_setting = "OPENAI_API_KEY"
        bad_provider.make_call = AsyncMock(side_effect=ProviderError(
            underlying, is_retryable=False, provider="openai",
        ))

        with patch("analytics.utils.llm.get_provider", return_value=bad_provider):
            with pytest.raises(RuntimeError, match="client bug"):
                await llm_call("f", [], "gpt-5.4", "low")
        re_.assert_called_once()
        rc.assert_not_called()


# -- Missing API key short-circuit ---------------------------------------

class MissingApiKeyTests:

    async def test_missing_key_raises_runtime_error_without_attempting_call(
            self, _stub_tracker):
        from analytics.utils import llm as llm_mod
        # Force the cache to report missing for OpenAI specifically.
        llm_mod._API_KEY_CACHE["OPENAI_API_KEY"] = ""
        llm_mod._API_KEY_CACHE["ANTHROPIC_API_KEY"] = "x"
        try:
            response = _make_normalized()
            p = _provider_with_make_call("openai", response, "OPENAI_API_KEY")
            with patch("analytics.utils.llm.get_provider", return_value=p):
                with pytest.raises(RuntimeError, match="OPENAI_API_KEY is not set"):
                    await llm_call("f", [], "gpt-5.4", "low")
            p.make_call.assert_not_awaited()
            # The failure is still recorded so the dev panel / LLMCall table
            # surface the misconfig.
            re_ = _stub_tracker[1]
            re_.assert_called_once()
        finally:
            llm_mod._API_KEY_CACHE.clear()
