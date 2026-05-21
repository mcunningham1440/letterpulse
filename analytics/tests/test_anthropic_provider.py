"""
Tests for analytics/utils/llm_providers/anthropic_provider.py — the Anthropic
Messages API adapter.

`AsyncAnthropic` is patched at the provider-module level so no live calls are
made. Tests cover:
  - Canonical (OpenAI-shaped) message translation -> Claude's system + messages
  - Tool definition translation (OpenAI 'parameters' -> Claude 'input_schema')
  - Reasoning-effort mapping (Sonnet -> adaptive, Haiku -> manual budget)
  - Forced-tool-use forces thinking off (Claude rejects the combination)
  - Prompt-cache-retention 24h maps to cache_control ttl=1h
  - Response translation -> NormalizedResponse with output_items + tool_calls
  - Usage extraction (input + cache_read + cache_creation + output)
  - Ignored OpenAI-only kwargs (store, previous_response_id, prompt_cache_key)
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import BaseModel

from analytics.utils.llm_providers import anthropic_provider
from analytics.utils.llm_providers.base import ProviderError


class _ExampleSchema(BaseModel):
    foo: str


def _mock_text_block(text):
    """A MagicMock that quacks like a Claude TextBlock."""
    b = MagicMock()
    b.type = "text"
    b.text = text
    return b


def _mock_tool_use_block(id, name, input_dict):
    b = MagicMock()
    b.type = "tool_use"
    b.id = id
    b.name = name
    b.input = input_dict
    return b


@pytest.fixture
def mock_async_anthropic():
    """Patch AsyncAnthropic. Yields (constructor_mock, client_mock, response_mock).

    Response defaults to one text block 'hello' + minimal usage so the
    NormalizedResponse builder has the shape it expects.
    """
    with patch("analytics.utils.llm_providers.anthropic_provider.AsyncAnthropic") as ctor:
        client = MagicMock(name="AsyncAnthropicClient")
        ctor.return_value = client
        response = MagicMock(name="AnthropicMessage")
        response.content = [_mock_text_block("hello")]
        response.usage.input_tokens = 0
        response.usage.cache_read_input_tokens = 0
        response.usage.cache_creation_input_tokens = 0
        response.usage.output_tokens = 0
        response.id = "msg_test"
        response.parsed_output = None
        client.messages.create = AsyncMock(return_value=response)
        client.messages.parse = AsyncMock(return_value=response)
        client.close = AsyncMock()
        yield ctor, client, response


# -- Message translation --------------------------------------------------

class MessageTranslationTests:

    async def test_system_message_lifted_to_top_level_param(self, mock_async_anthropic):
        _, client, _ = mock_async_anthropic
        await anthropic_provider.make_call(
            api_key="k", function_name="f",
            messages=[
                {"role": "system", "content": "you are concise"},
                {"role": "user", "content": "hi"},
            ],
            model="claude-sonnet-4-6", reasoning_level="low",
        )
        kwargs = client.messages.create.await_args.kwargs
        # system is a list of content blocks at top level.
        assert isinstance(kwargs["system"], list)
        assert kwargs["system"][0]["type"] == "text"
        assert "you are concise" in kwargs["system"][0]["text"]
        # And the system block does NOT appear in messages.
        roles = [m["role"] for m in kwargs["messages"]]
        assert "system" not in roles
        assert kwargs["messages"][0]["role"] == "user"

    async def test_function_call_translated_to_tool_use_block(self, mock_async_anthropic):
        _, client, _ = mock_async_anthropic
        await anthropic_provider.make_call(
            api_key="k", function_name="f",
            messages=[
                {"role": "user", "content": "search please"},
                # An OpenAI assistant turn that requested a tool call:
                {"type": "function_call", "name": "web_search",
                 "arguments": '{"q":"x"}', "call_id": "call_1"},
            ],
            model="claude-sonnet-4-6", reasoning_level="low",
        )
        kwargs = client.messages.create.await_args.kwargs
        # The function_call becomes a tool_use block in an assistant message.
        assistant_msgs = [m for m in kwargs["messages"] if m["role"] == "assistant"]
        assert len(assistant_msgs) == 1
        blocks = assistant_msgs[0]["content"]
        tool_blocks = [b for b in blocks if b.get("type") == "tool_use"]
        assert len(tool_blocks) == 1
        assert tool_blocks[0]["id"] == "call_1"
        assert tool_blocks[0]["name"] == "web_search"
        assert tool_blocks[0]["input"] == {"q": "x"}

    async def test_function_call_output_translated_to_tool_result_in_user_message(
            self, mock_async_anthropic):
        _, client, _ = mock_async_anthropic
        await anthropic_provider.make_call(
            api_key="k", function_name="f",
            messages=[
                {"role": "user", "content": "search"},
                {"type": "function_call", "name": "web_search",
                 "arguments": "{}", "call_id": "call_1"},
                {"type": "function_call_output", "call_id": "call_1", "output": "result text"},
            ],
            model="claude-sonnet-4-6", reasoning_level="low",
        )
        kwargs = client.messages.create.await_args.kwargs
        # The tool_result must land in a user message AFTER the assistant turn.
        user_msgs = [m for m in kwargs["messages"] if m["role"] == "user"]
        assert len(user_msgs) >= 2
        tool_results = [b for b in user_msgs[-1]["content"] if b.get("type") == "tool_result"]
        assert len(tool_results) == 1
        assert tool_results[0]["tool_use_id"] == "call_1"
        assert tool_results[0]["content"] == "result text"

    async def test_reasoning_items_are_dropped_at_boundary(self, mock_async_anthropic):
        # OpenAI reasoning items are encrypted blobs only OpenAI can read;
        # passing them to Claude is meaningless and could trigger a 400.
        _, client, _ = mock_async_anthropic
        await anthropic_provider.make_call(
            api_key="k", function_name="f",
            messages=[
                {"role": "user", "content": "hi"},
                {"type": "reasoning", "summary": [], "encrypted_content": "..."},
                {"type": "message", "role": "assistant",
                 "content": [{"type": "output_text", "text": "reply"}]},
            ],
            model="claude-sonnet-4-6", reasoning_level="low",
        )
        kwargs = client.messages.create.await_args.kwargs
        # No assistant block should contain a 'reasoning'-typed block.
        for m in kwargs["messages"]:
            for block in m.get("content", []):
                assert block.get("type") != "reasoning"


# -- Tool def translation -------------------------------------------------

class ToolTranslationTests:

    async def test_openai_tool_def_translated_to_claude_input_schema(self, mock_async_anthropic):
        _, client, _ = mock_async_anthropic
        await anthropic_provider.make_call(
            api_key="k", function_name="f", messages=[{"role": "user", "content": "x"}],
            model="claude-sonnet-4-6", reasoning_level="low",
            tools=[{
                "type": "function",
                "name": "web_search",
                "description": "search the web",
                "parameters": {"type": "object", "properties": {"q": {"type": "string"}}},
                "strict": True,
            }],
            tool_choice="required",
        )
        kwargs = client.messages.create.await_args.kwargs
        assert len(kwargs["tools"]) == 1
        t = kwargs["tools"][0]
        assert t["name"] == "web_search"
        assert t["description"] == "search the web"
        # OpenAI's 'parameters' becomes Claude's 'input_schema'.
        assert t["input_schema"]["properties"] == {"q": {"type": "string"}}

    async def test_tool_choice_required_becomes_any(self, mock_async_anthropic):
        _, client, _ = mock_async_anthropic
        await anthropic_provider.make_call(
            api_key="k", function_name="f", messages=[{"role": "user", "content": "x"}],
            model="claude-sonnet-4-6", reasoning_level="low",
            tools=[{"type": "function", "name": "t", "description": "", "parameters": {}}],
            tool_choice="required",
        )
        kwargs = client.messages.create.await_args.kwargs
        assert kwargs["tool_choice"] == {"type": "any"}

    async def test_forced_tool_use_disables_thinking(self, mock_async_anthropic):
        # Claude returns 400 when adaptive/extended thinking is on and
        # tool_choice forces a tool. The adapter must disable thinking.
        _, client, _ = mock_async_anthropic
        await anthropic_provider.make_call(
            api_key="k", function_name="f", messages=[{"role": "user", "content": "x"}],
            model="claude-sonnet-4-6", reasoning_level="medium",
            tools=[{"type": "function", "name": "t", "description": "", "parameters": {}}],
            tool_choice="required",
        )
        kwargs = client.messages.create.await_args.kwargs
        assert kwargs["thinking"] == {"type": "disabled"}
        # output_config should be empty/absent (no effort when thinking off).
        assert not kwargs.get("output_config")


# -- Reasoning-effort mapping ---------------------------------------------

class ReasoningEffortTests:

    @pytest.mark.parametrize("level", ["low", "medium", "high"])
    async def test_sonnet_uses_adaptive_with_effort(self, mock_async_anthropic, level):
        _, client, _ = mock_async_anthropic
        await anthropic_provider.make_call(
            api_key="k", function_name="f", messages=[{"role": "user", "content": "x"}],
            model="claude-sonnet-4-6", reasoning_level=level,
        )
        kwargs = client.messages.create.await_args.kwargs
        assert kwargs["thinking"] == {"type": "adaptive"}
        assert kwargs["output_config"] == {"effort": level}

    @pytest.mark.parametrize("level,expected_budget", [
        ("low", 1024), ("medium", 4096), ("high", 10000),
    ])
    async def test_haiku_uses_manual_budget_tokens(
            self, mock_async_anthropic, level, expected_budget):
        _, client, _ = mock_async_anthropic
        await anthropic_provider.make_call(
            api_key="k", function_name="f", messages=[{"role": "user", "content": "x"}],
            model="claude-haiku-4-5", reasoning_level=level,
        )
        kwargs = client.messages.create.await_args.kwargs
        assert kwargs["thinking"] == {"type": "enabled", "budget_tokens": expected_budget}


# -- Cache control --------------------------------------------------------

class CacheControlTests:

    async def test_default_cache_control_on_last_system_block(self, mock_async_anthropic):
        # Even without prompt_cache_retention, the adapter enables 5-min
        # caching by marking the last system block.
        _, client, _ = mock_async_anthropic
        await anthropic_provider.make_call(
            api_key="k", function_name="f",
            messages=[
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "x"},
            ],
            model="claude-sonnet-4-6", reasoning_level="low",
        )
        kwargs = client.messages.create.await_args.kwargs
        assert kwargs["system"][-1].get("cache_control") == {"type": "ephemeral"}

    async def test_retention_24h_maps_to_ttl_1h(self, mock_async_anthropic):
        _, client, _ = mock_async_anthropic
        await anthropic_provider.make_call(
            api_key="k", function_name="f",
            messages=[
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "x"},
            ],
            model="claude-sonnet-4-6", reasoning_level="low",
            prompt_cache_retention="24h",
        )
        kwargs = client.messages.create.await_args.kwargs
        assert kwargs["system"][-1].get("cache_control") == {"type": "ephemeral", "ttl": "1h"}


# -- Ignored OpenAI-only kwargs ------------------------------------------

class IgnoredOpenAIOnlyKwargsTests:

    async def test_store_silently_ignored(self, mock_async_anthropic):
        _, client, _ = mock_async_anthropic
        await anthropic_provider.make_call(
            api_key="k", function_name="f", messages=[{"role": "user", "content": "x"}],
            model="claude-sonnet-4-6", reasoning_level="low",
            store=True,
        )
        kwargs = client.messages.create.await_args.kwargs
        assert "store" not in kwargs

    async def test_previous_response_id_silently_ignored(self, mock_async_anthropic):
        _, client, _ = mock_async_anthropic
        await anthropic_provider.make_call(
            api_key="k", function_name="f", messages=[{"role": "user", "content": "x"}],
            model="claude-sonnet-4-6", reasoning_level="low",
            previous_response_id="resp_xyz",
        )
        kwargs = client.messages.create.await_args.kwargs
        assert "previous_response_id" not in kwargs

    async def test_prompt_cache_key_silently_ignored(self, mock_async_anthropic):
        # No routing-stickiness key equivalent on Claude.
        _, client, _ = mock_async_anthropic
        await anthropic_provider.make_call(
            api_key="k", function_name="f", messages=[{"role": "user", "content": "x"}],
            model="claude-sonnet-4-6", reasoning_level="low",
            prompt_cache_key="cf_plan_abc",
        )
        kwargs = client.messages.create.await_args.kwargs
        assert "prompt_cache_key" not in kwargs


# -- Response translation --------------------------------------------------

class ResponseTranslationTests:

    async def test_text_block_becomes_canonical_message_item(self, mock_async_anthropic):
        _, _, response = mock_async_anthropic
        response.content = [_mock_text_block("hi there")]
        result = await anthropic_provider.make_call(
            api_key="k", function_name="f", messages=[{"role": "user", "content": "x"}],
            model="claude-sonnet-4-6", reasoning_level="low",
        )
        assert result.output_text == "hi there"
        # Canonical format: one item of type "message" with output_text content.
        assert len(result.output_items) == 1
        item = result.output_items[0]
        assert item["type"] == "message"
        assert item["role"] == "assistant"
        assert item["content"][0] == {"type": "output_text", "text": "hi there"}

    async def test_tool_use_block_becomes_function_call_item(self, mock_async_anthropic):
        _, _, response = mock_async_anthropic
        response.content = [_mock_tool_use_block("call_42", "web_search", {"q": "abc"})]
        result = await anthropic_provider.make_call(
            api_key="k", function_name="f", messages=[{"role": "user", "content": "x"}],
            model="claude-sonnet-4-6", reasoning_level="low",
            tools=[{"type": "function", "name": "web_search", "description": "", "parameters": {}}],
            tool_choice="required",
        )
        # In canonical output_items: type=function_call with stringified arguments.
        assert len(result.output_items) == 1
        item = result.output_items[0]
        assert item["type"] == "function_call"
        assert item["call_id"] == "call_42"
        assert item["name"] == "web_search"
        # And tool_calls list mirrors it for easy iteration.
        assert result.tool_calls == [
            {"call_id": "call_42", "name": "web_search", "arguments_json": '{"q": "abc"}'},
        ]

    async def test_parsed_output_surfaced_when_response_format_given(self, mock_async_anthropic):
        _, client, response = mock_async_anthropic
        parsed = _ExampleSchema(foo="bar")
        response.parsed_output = parsed
        result = await anthropic_provider.make_call(
            api_key="k", function_name="f", messages=[{"role": "user", "content": "x"}],
            model="claude-sonnet-4-6", reasoning_level="low",
            response_format=_ExampleSchema,
        )
        # Confirm messages.parse() was used (not create) and the parsed
        # object is exposed on the normalized response.
        client.messages.parse.assert_awaited_once()
        client.messages.create.assert_not_awaited()
        assert result.output_parsed is parsed

    async def test_usage_extraction(self, mock_async_anthropic):
        _, _, response = mock_async_anthropic
        response.usage.input_tokens = 50            # new tokens (after last cache breakpoint)
        response.usage.cache_read_input_tokens = 1000
        response.usage.cache_creation_input_tokens = 200
        response.usage.output_tokens = 300

        result = await anthropic_provider.make_call(
            api_key="k", function_name="f", messages=[{"role": "user", "content": "x"}],
            model="claude-sonnet-4-6", reasoning_level="low",
        )
        assert result.usage.input_tokens == 50
        assert result.usage.cached_input_tokens == 1000
        assert result.usage.cache_creation_tokens == 200
        # Claude has no separate thinking-tokens count.
        assert result.usage.output_tokens == 300
        assert result.usage.reasoning_tokens == 0


# -- Error classification -------------------------------------------------

class ErrorClassificationTests:

    async def test_retryable_sdk_error_wraps_as_retryable_provider_error(self, mock_async_anthropic):
        from anthropic import APITimeoutError
        _, client, _ = mock_async_anthropic
        client.messages.create.side_effect = APITimeoutError(request=MagicMock())

        with pytest.raises(ProviderError) as exc_info:
            await anthropic_provider.make_call(
                api_key="k", function_name="f", messages=[{"role": "user", "content": "x"}],
                model="claude-sonnet-4-6", reasoning_level="low",
            )
        assert exc_info.value.is_retryable is True
        assert exc_info.value.provider == "anthropic"
        assert isinstance(exc_info.value.original, APITimeoutError)

    async def test_nonretryable_error_marked_nonretryable(self, mock_async_anthropic):
        _, client, _ = mock_async_anthropic
        client.messages.create.side_effect = RuntimeError("bad input")

        with pytest.raises(ProviderError) as exc_info:
            await anthropic_provider.make_call(
                api_key="k", function_name="f", messages=[{"role": "user", "content": "x"}],
                model="claude-sonnet-4-6", reasoning_level="low",
            )
        assert exc_info.value.is_retryable is False
