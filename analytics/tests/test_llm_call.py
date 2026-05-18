"""
Phase 2 tests for analytics/utils/llm.py — the OpenAI Responses API wrapper.

`AsyncOpenAI` is mocked at the class level so no live calls are made. Tests
cover the request shape (kwarg plumbing), the structured-output mapping
(response_format -> text_format), and the success / error tracking hooks.

The captured fixtures in analytics/tests/fixtures/openai_*.json confirm the
real SDK response shapes; tests below construct minimal stand-ins because
the wrapper itself doesn't introspect the response beyond returning it.
"""

from inspect import signature
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import BaseModel

from analytics.llm_tracker import record_call as _record_call_sig
from analytics.llm_tracker import record_error as _record_error_sig
from analytics.utils.llm import llm_call


class _ExampleSchema(BaseModel):
    foo: str


def _bind(mock_obj, real_func):
    """
    Bind a mock's most recent call against the real function's signature.
    Returns a dict mapping parameter name -> value, so assertions can be
    keyed by name and survive positional/keyword refactors of the callee.
    """
    args, kwargs = mock_obj.call_args
    bound = signature(real_func).bind(*args, **kwargs)
    bound.apply_defaults()
    return bound.arguments


@pytest.fixture
def mock_async_openai():
    """Patch AsyncOpenAI. Yields (constructor_mock, client_mock, response_sentinel)."""
    with patch("analytics.utils.llm.AsyncOpenAI") as ctor:
        client = MagicMock(name="AsyncOpenAIClient")
        ctor.return_value = client
        response = MagicMock(name="OpenAIResponse")
        client.responses.parse = AsyncMock(return_value=response)
        client.responses.create = AsyncMock(return_value=response)
        client.close = AsyncMock()
        yield ctor, client, response


@pytest.fixture(autouse=True)
def mock_tracker():
    """Stub record_call / record_error so the wrapper's logging side effects
    don't touch the DB through the logsink queue."""
    with patch("analytics.utils.llm.record_call") as rc, \
         patch("analytics.utils.llm.record_error") as re_:
        yield rc, re_


# -- Structured-output path -----------------------------------------------

class StructuredOutputTests:

    async def test_response_format_kwarg_is_sent_to_sdk_as_text_format(self, mock_async_openai):
        # The wrapper accepts `response_format=` (a Pydantic model) but the
        # OpenAI Responses SDK expects that argument under the name
        # `text_format`. Verify the wire-level kwarg.
        _, client, response = mock_async_openai
        result = await llm_call(
            function_name="f", messages=[{"role": "user", "content": "hi"}],
            model="gpt-5.4", reasoning_level="low",
            response_format=_ExampleSchema,
        )
        client.responses.parse.assert_awaited_once()
        kwargs = client.responses.parse.await_args.kwargs
        assert kwargs["text_format"] is _ExampleSchema
        assert "response_format" not in kwargs
        assert result is response

    async def test_create_is_not_called_when_response_format_provided(self, mock_async_openai):
        _, client, _ = mock_async_openai
        await llm_call(
            function_name="f", messages=[], model="m", reasoning_level="low",
            response_format=_ExampleSchema,
        )
        client.responses.create.assert_not_awaited()


# -- Free-form path -------------------------------------------------------

class FreeFormTests:

    async def test_no_response_format_calls_responses_create(self, mock_async_openai):
        _, client, response = mock_async_openai
        result = await llm_call(
            function_name="f", messages=[], model="m", reasoning_level="low",
        )
        client.responses.create.assert_awaited_once()
        kwargs = client.responses.create.await_args.kwargs
        assert "text_format" not in kwargs
        assert "response_format" not in kwargs
        assert result is response

    async def test_parse_not_called_in_free_form_path(self, mock_async_openai):
        _, client, _ = mock_async_openai
        await llm_call(function_name="f", messages=[], model="m", reasoning_level="low")
        client.responses.parse.assert_not_awaited()


# -- Kwarg forwarding -----------------------------------------------------

class KwargForwardingTests:

    async def test_model_and_messages_and_reasoning_forwarded(self, mock_async_openai):
        _, client, _ = mock_async_openai
        msgs = [{"role": "user", "content": "hi"}]
        await llm_call(
            function_name="f", messages=msgs, model="gpt-5.4-mini",
            reasoning_level="medium",
        )
        kwargs = client.responses.create.await_args.kwargs
        assert kwargs["model"] == "gpt-5.4-mini"
        assert kwargs["input"] is msgs  # messages -> input rename
        # reasoning_level is wrapped in {"effort": ...}
        assert kwargs["reasoning"] == {"effort": "medium"}

    async def test_tools_and_tool_choice_forwarded(self, mock_async_openai):
        _, client, _ = mock_async_openai
        tools = [{"type": "function", "name": "web_search"}]
        await llm_call(
            function_name="f", messages=[], model="m", reasoning_level="low",
            tools=tools, tool_choice="required",
        )
        kwargs = client.responses.create.await_args.kwargs
        assert kwargs["tools"] is tools
        assert kwargs["tool_choice"] == "required"

    async def test_tools_absent_when_not_provided(self, mock_async_openai):
        _, client, _ = mock_async_openai
        await llm_call(function_name="f", messages=[], model="m", reasoning_level="low")
        kwargs = client.responses.create.await_args.kwargs
        assert "tools" not in kwargs
        assert "tool_choice" not in kwargs

    async def test_store_true_forwarded(self, mock_async_openai):
        _, client, _ = mock_async_openai
        await llm_call(
            function_name="f", messages=[], model="m", reasoning_level="low",
            store=True,
        )
        kwargs = client.responses.create.await_args.kwargs
        assert kwargs["store"] is True

    async def test_store_false_omitted_from_kwargs(self, mock_async_openai):
        # Production only adds `store` when truthy; default False is dropped.
        _, client, _ = mock_async_openai
        await llm_call(function_name="f", messages=[], model="m", reasoning_level="low")
        kwargs = client.responses.create.await_args.kwargs
        assert "store" not in kwargs

    async def test_previous_response_id_forwarded(self, mock_async_openai):
        _, client, _ = mock_async_openai
        await llm_call(
            function_name="f", messages=[], model="m", reasoning_level="low",
            previous_response_id="resp_xyz",
        )
        kwargs = client.responses.create.await_args.kwargs
        assert kwargs["previous_response_id"] == "resp_xyz"

    async def test_previous_response_id_omitted_when_none(self, mock_async_openai):
        _, client, _ = mock_async_openai
        await llm_call(function_name="f", messages=[], model="m", reasoning_level="low")
        kwargs = client.responses.create.await_args.kwargs
        assert "previous_response_id" not in kwargs

    async def test_prompt_cache_key_and_retention_forwarded(self, mock_async_openai):
        _, client, _ = mock_async_openai
        await llm_call(
            function_name="f", messages=[], model="m", reasoning_level="low",
            prompt_cache_key="cf_plan_abc", prompt_cache_retention="24h",
        )
        kwargs = client.responses.create.await_args.kwargs
        assert kwargs["prompt_cache_key"] == "cf_plan_abc"
        assert kwargs["prompt_cache_retention"] == "24h"

    async def test_cache_kwargs_omitted_when_none(self, mock_async_openai):
        _, client, _ = mock_async_openai
        await llm_call(function_name="f", messages=[], model="m", reasoning_level="low")
        kwargs = client.responses.create.await_args.kwargs
        assert "prompt_cache_key" not in kwargs
        assert "prompt_cache_retention" not in kwargs

    async def test_timeout_and_api_key_passed_to_constructor(self, mock_async_openai):
        ctor, _, _ = mock_async_openai
        await llm_call(
            function_name="f", messages=[], model="m", reasoning_level="low",
            timeout=45.0,
        )
        ctor.assert_called_once()
        ctor_kwargs = ctor.call_args.kwargs
        assert ctor_kwargs["timeout"] == 45.0
        # max_retries=2 is hard-coded; verify it's set so future SDK upgrades
        # don't silently change behavior.
        assert ctor_kwargs["max_retries"] == 2
        # api_key must be plumbed through (regression guard — without it,
        # the SDK would silently fall back to env-var lookup).
        assert "api_key" in ctor_kwargs
        assert ctor_kwargs["api_key"]


# -- Tracking hooks -------------------------------------------------------

class TrackingTests:

    async def test_record_call_invoked_exactly_once_on_success(
        self, mock_async_openai, mock_tracker
    ):
        rc, _ = mock_tracker
        _, _, response = mock_async_openai
        msgs = [{"role": "user", "content": "x"}]
        await llm_call(
            function_name="my_fn", messages=msgs, model="gpt-5.4",
            reasoning_level="low",
        )
        rc.assert_called_once()
        # Bind by parameter name so positional/kwarg refactors of record_call
        # don't silently break the assertion.
        bound = _bind(rc, _record_call_sig)
        assert bound["function_name"] == "my_fn"
        assert bound["model"] == "gpt-5.4"
        assert bound["messages"] is msgs
        assert bound["response"] is response
        assert isinstance(bound["duration"], float)
        assert bound["duration"] >= 0
        assert bound["start_ts"] is not None

    async def test_record_error_invoked_when_sdk_raises_then_reraises(
        self, mock_async_openai, mock_tracker
    ):
        rc, re_ = mock_tracker
        _, client, _ = mock_async_openai
        client.responses.create.side_effect = RuntimeError("api boom")

        with pytest.raises(RuntimeError, match="api boom"):
            await llm_call(
                function_name="my_fn", messages=[], model="gpt-5.4",
                reasoning_level="low",
            )

        re_.assert_called_once()
        bound = _bind(re_, _record_error_sig)
        assert bound["function_name"] == "my_fn"
        assert bound["model"] == "gpt-5.4"
        assert isinstance(bound["duration"], float)
        assert isinstance(bound["error"], RuntimeError)
        assert bound["start_ts"] is not None
        # And record_call must NOT have been invoked.
        rc.assert_not_called()

    async def test_record_error_invoked_when_parse_raises(
        self, mock_async_openai, mock_tracker
    ):
        rc, re_ = mock_tracker
        _, client, _ = mock_async_openai
        client.responses.parse.side_effect = RuntimeError("parse boom")

        with pytest.raises(RuntimeError):
            await llm_call(
                function_name="f", messages=[], model="m", reasoning_level="low",
                response_format=_ExampleSchema,
            )
        re_.assert_called_once()
        bound = _bind(re_, _record_error_sig)
        assert isinstance(bound["error"], RuntimeError)
        rc.assert_not_called()


# -- Client lifecycle -----------------------------------------------------

class ClientLifecycleTests:

    async def test_client_closed_on_success(self, mock_async_openai):
        _, client, _ = mock_async_openai
        await llm_call(function_name="f", messages=[], model="m", reasoning_level="low")
        client.close.assert_awaited_once()

    async def test_client_closed_on_exception(self, mock_async_openai):
        _, client, _ = mock_async_openai
        client.responses.create.side_effect = RuntimeError("boom")
        with pytest.raises(RuntimeError):
            await llm_call(function_name="f", messages=[], model="m", reasoning_level="low")
        client.close.assert_awaited_once()
