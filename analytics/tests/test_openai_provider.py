"""
Tests for analytics/utils/llm_providers/openai_provider.py — the OpenAI
Responses API adapter.

`AsyncOpenAI` is patched at the provider-module level so no live calls are
made. Tests cover the request shape (kwarg plumbing) and the response shape
translation (raw SDK response -> NormalizedResponse with output_parsed,
output_items, tool_calls, usage).
"""

from inspect import signature
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import BaseModel

from analytics.utils.llm_providers import openai_provider


class _ExampleSchema(BaseModel):
    foo: str


def _bind(mock_obj, real_func):
    """Bind a mock's most recent call against the real function signature."""
    args, kwargs = mock_obj.call_args
    bound = signature(real_func).bind(*args, **kwargs)
    bound.apply_defaults()
    return bound.arguments


@pytest.fixture
def mock_async_openai():
    """Patch AsyncOpenAI inside the provider module.

    Returns (constructor_mock, client_mock, response_sentinel). The response
    sentinel is a MagicMock with a minimal `usage` attribute so the
    NormalizedResponse builder doesn't choke on int conversions.
    """
    with patch("analytics.utils.llm_providers.openai_provider.AsyncOpenAI") as ctor:
        client = MagicMock(name="AsyncOpenAIClient")
        ctor.return_value = client
        response = MagicMock(name="OpenAIResponse")
        # Minimal usage shape so _extract_usage doesn't error.
        response.usage.input_tokens = 0
        response.usage.output_tokens = 0
        response.usage.input_tokens_details.cached_tokens = 0
        response.usage.output_tokens_details.reasoning_tokens = 0
        response.output = []  # empty -> no output_items
        response.id = "resp_test"
        response.output_parsed = None
        client.responses.parse = AsyncMock(return_value=response)
        client.responses.create = AsyncMock(return_value=response)
        client.close = AsyncMock()
        yield ctor, client, response


# -- Structured-output path -----------------------------------------------

class StructuredOutputTests:

    async def test_response_format_kwarg_is_sent_to_sdk_as_text_format(self, mock_async_openai):
        # The provider accepts `response_format=` (Pydantic model) but the
        # OpenAI Responses SDK expects that under the name `text_format`.
        _, client, _ = mock_async_openai
        await openai_provider.make_call(
            api_key="k", function_name="f",
            messages=[{"role": "user", "content": "hi"}],
            model="gpt-5.4", reasoning_level="low",
            response_format=_ExampleSchema,
        )
        client.responses.parse.assert_awaited_once()
        kwargs = client.responses.parse.await_args.kwargs
        assert kwargs["text_format"] is _ExampleSchema
        assert "response_format" not in kwargs

    async def test_create_is_not_called_when_response_format_provided(self, mock_async_openai):
        _, client, _ = mock_async_openai
        await openai_provider.make_call(
            api_key="k", function_name="f", messages=[], model="m", reasoning_level="low",
            response_format=_ExampleSchema,
        )
        client.responses.create.assert_not_awaited()


# -- Free-form path -------------------------------------------------------

class FreeFormTests:

    async def test_no_response_format_calls_responses_create(self, mock_async_openai):
        _, client, _ = mock_async_openai
        await openai_provider.make_call(
            api_key="k", function_name="f", messages=[], model="m", reasoning_level="low",
        )
        client.responses.create.assert_awaited_once()
        kwargs = client.responses.create.await_args.kwargs
        assert "text_format" not in kwargs
        assert "response_format" not in kwargs

    async def test_parse_not_called_in_free_form_path(self, mock_async_openai):
        _, client, _ = mock_async_openai
        await openai_provider.make_call(
            api_key="k", function_name="f", messages=[], model="m", reasoning_level="low",
        )
        client.responses.parse.assert_not_awaited()


# -- Kwarg forwarding -----------------------------------------------------

class KwargForwardingTests:

    async def test_model_and_messages_and_reasoning_forwarded(self, mock_async_openai):
        _, client, _ = mock_async_openai
        msgs = [{"role": "user", "content": "hi"}]
        await openai_provider.make_call(
            api_key="k", function_name="f", messages=msgs, model="gpt-5.4-mini",
            reasoning_level="medium",
        )
        kwargs = client.responses.create.await_args.kwargs
        assert kwargs["model"] == "gpt-5.4-mini"
        assert kwargs["input"] is msgs                # messages -> input rename
        assert kwargs["reasoning"] == {"effort": "medium"}

    async def test_tools_and_tool_choice_forwarded(self, mock_async_openai):
        _, client, _ = mock_async_openai
        tools = [{"type": "function", "name": "web_search"}]
        await openai_provider.make_call(
            api_key="k", function_name="f", messages=[], model="m", reasoning_level="low",
            tools=tools, tool_choice="required",
        )
        kwargs = client.responses.create.await_args.kwargs
        assert kwargs["tools"] is tools
        assert kwargs["tool_choice"] == "required"

    async def test_tools_absent_when_not_provided(self, mock_async_openai):
        _, client, _ = mock_async_openai
        await openai_provider.make_call(
            api_key="k", function_name="f", messages=[], model="m", reasoning_level="low",
        )
        kwargs = client.responses.create.await_args.kwargs
        assert "tools" not in kwargs
        assert "tool_choice" not in kwargs

    async def test_store_true_forwarded(self, mock_async_openai):
        _, client, _ = mock_async_openai
        await openai_provider.make_call(
            api_key="k", function_name="f", messages=[], model="m", reasoning_level="low",
            store=True,
        )
        kwargs = client.responses.create.await_args.kwargs
        assert kwargs["store"] is True

    async def test_store_false_omitted_from_kwargs(self, mock_async_openai):
        _, client, _ = mock_async_openai
        await openai_provider.make_call(
            api_key="k", function_name="f", messages=[], model="m", reasoning_level="low",
        )
        kwargs = client.responses.create.await_args.kwargs
        assert "store" not in kwargs

    async def test_previous_response_id_forwarded(self, mock_async_openai):
        _, client, _ = mock_async_openai
        await openai_provider.make_call(
            api_key="k", function_name="f", messages=[], model="m", reasoning_level="low",
            previous_response_id="resp_xyz",
        )
        kwargs = client.responses.create.await_args.kwargs
        assert kwargs["previous_response_id"] == "resp_xyz"

    async def test_prompt_cache_key_and_retention_forwarded(self, mock_async_openai):
        _, client, _ = mock_async_openai
        await openai_provider.make_call(
            api_key="k", function_name="f", messages=[], model="m", reasoning_level="low",
            prompt_cache_key="cf_plan_abc", prompt_cache_retention="24h",
        )
        kwargs = client.responses.create.await_args.kwargs
        assert kwargs["prompt_cache_key"] == "cf_plan_abc"
        assert kwargs["prompt_cache_retention"] == "24h"

    async def test_timeout_and_api_key_and_no_retries_to_constructor(self, mock_async_openai):
        # The orchestrator owns the retry/fallback budget — the SDK must be
        # set to max_retries=0 so it doesn't silently consume our budget.
        ctor, _, _ = mock_async_openai
        await openai_provider.make_call(
            api_key="my-key", function_name="f", messages=[], model="m", reasoning_level="low",
            timeout=45.0,
        )
        ctor.assert_called_once()
        ctor_kwargs = ctor.call_args.kwargs
        assert ctor_kwargs["timeout"] == 45.0
        assert ctor_kwargs["max_retries"] == 0
        assert ctor_kwargs["api_key"] == "my-key"


# -- NormalizedResponse translation --------------------------------------

class NormalizedResponseTests:

    async def test_returns_normalized_response(self, mock_async_openai):
        _, _, _ = mock_async_openai
        result = await openai_provider.make_call(
            api_key="k", function_name="f", messages=[], model="gpt-5.4",
            reasoning_level="low",
        )
        # Has the public Normalized interface.
        assert hasattr(result, "output_parsed")
        assert hasattr(result, "output_text")
        assert hasattr(result, "output_items")
        assert hasattr(result, "tool_calls")
        assert hasattr(result, "usage")
        assert result.provider == "openai"
        assert result.model == "gpt-5.4"

    async def test_usage_split_into_new_and_cached(self, mock_async_openai):
        _, _, response = mock_async_openai
        # Gross input 100 with 30 cached -> normalized 70 new + 30 cached.
        response.usage.input_tokens = 100
        response.usage.input_tokens_details.cached_tokens = 30
        response.usage.output_tokens = 50
        response.usage.output_tokens_details.reasoning_tokens = 10

        result = await openai_provider.make_call(
            api_key="k", function_name="f", messages=[], model="gpt-5.4",
            reasoning_level="low",
        )
        assert result.usage.input_tokens == 70
        assert result.usage.cached_input_tokens == 30
        assert result.usage.cache_creation_tokens == 0  # OpenAI never sets this
        assert result.usage.output_tokens == 40         # 50 - 10 reasoning
        assert result.usage.reasoning_tokens == 10

    async def test_client_closed_on_success(self, mock_async_openai):
        _, client, _ = mock_async_openai
        await openai_provider.make_call(
            api_key="k", function_name="f", messages=[], model="m", reasoning_level="low",
        )
        client.close.assert_awaited_once()

    async def test_client_closed_on_exception(self, mock_async_openai):
        from analytics.utils.llm_providers.base import ProviderError
        _, client, _ = mock_async_openai
        client.responses.create.side_effect = RuntimeError("boom")
        with pytest.raises(ProviderError):
            await openai_provider.make_call(
                api_key="k", function_name="f", messages=[], model="m", reasoning_level="low",
            )
        client.close.assert_awaited_once()

    async def test_retryable_sdk_error_wraps_as_retryable_provider_error(self, mock_async_openai):
        # openai.APITimeoutError is in the retryable set — the provider must
        # raise ProviderError(is_retryable=True) so the orchestrator falls back.
        from openai import APITimeoutError
        from analytics.utils.llm_providers.base import ProviderError

        _, client, _ = mock_async_openai
        client.responses.create.side_effect = APITimeoutError(request=MagicMock())

        with pytest.raises(ProviderError) as exc_info:
            await openai_provider.make_call(
                api_key="k", function_name="f", messages=[], model="m", reasoning_level="low",
            )
        assert exc_info.value.is_retryable is True
        assert exc_info.value.provider == "openai"
        assert isinstance(exc_info.value.original, APITimeoutError)

    async def test_nonretryable_sdk_error_is_marked_nonretryable(self, mock_async_openai):
        # A plain RuntimeError isn't in the retryable set; the orchestrator
        # must NOT fall back on it (would mask the real bug).
        from analytics.utils.llm_providers.base import ProviderError

        _, client, _ = mock_async_openai
        client.responses.create.side_effect = RuntimeError("client bug")

        with pytest.raises(ProviderError) as exc_info:
            await openai_provider.make_call(
                api_key="k", function_name="f", messages=[], model="m", reasoning_level="low",
            )
        assert exc_info.value.is_retryable is False
