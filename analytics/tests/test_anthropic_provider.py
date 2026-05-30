"""
Tests for analytics/utils/llm_providers/anthropic_provider.py — the Anthropic
Messages API adapter.

`AsyncAnthropic` is patched at the provider-module level so no live calls are
made. Response shapes come from real Anthropic API recordings captured by
`analytics/tests/fixtures/_record_phase2.py` and scrubbed by `_scrub.py`. The
fixtures (5 of them, in `analytics/tests/fixtures/anthropic_*.json`) cover:

  - `anthropic_sonnet_structured_200.json`     — messages.parse() with
        output_format=PydanticModel, adaptive thinking on Sonnet
  - `anthropic_sonnet_freeform_200.json`       — messages.create() free-form,
        adaptive thinking on Sonnet
  - `anthropic_haiku_manual_thinking_200.json` — messages.create() free-form,
        manual budget_tokens thinking on Haiku (the one fixture where the API
        actually reports a non-zero thinking-tokens count)
  - `anthropic_sonnet_tool_use_turn1_200.json` — messages.create() with tools
        + forced tool_choice, returns a tool_use block
  - `anthropic_sonnet_tool_use_turn2_200.json` — same conversation continued
        with tool_result fed back, returns final text

Tests cover:
  - Canonical (OpenAI-shaped) message translation -> Claude's system + messages
  - Tool definition translation (OpenAI 'parameters' -> Claude 'input_schema')
  - Reasoning-effort mapping (Sonnet -> adaptive, Haiku -> manual budget)
  - Forced-tool-use forces thinking off (Claude rejects the combination)
  - Prompt-cache-retention 24h maps to cache_control ttl=1h
  - Response translation -> NormalizedResponse with output_items + tool_calls
  - Usage extraction (input + cache_read + cache_creation + output + thinking)
  - Ignored OpenAI-only kwargs (store, previous_response_id, prompt_cache_key)
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from anthropic.types import Message, ParsedMessage
from pydantic import BaseModel
from typing import List

from analytics.utils.llm_providers import anthropic_provider
from analytics.utils.llm_providers.base import ProviderError


FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


class _ExampleSchema(BaseModel):
    foo: str


class _NicheAnalysisResult(BaseModel):
    """Mirrors analytics.utils.niche.NicheAnalysisResult — kept inline so the
    test doesn't import Django utility modules at collection time."""
    niche: str
    content_types: List[str]


def _load_message(name: str) -> Message:
    """Load a recorded Anthropic response JSON and reconstruct a Message."""
    with open(FIXTURES_DIR / name) as f:
        data = json.load(f)
    return Message.model_validate(data)


def _load_parsed_message(name: str, schema) -> ParsedMessage:
    """Load a recorded structured-output JSON as a ParsedMessage[Schema]."""
    with open(FIXTURES_DIR / name) as f:
        data = json.load(f)
    return ParsedMessage[schema].model_validate(data)


@pytest.fixture
def mock_async_anthropic():
    """Patch AsyncAnthropic. Yields (constructor_mock, client_mock, response_mock).

    Default response is one text block "hello" + minimal usage so request-shape
    tests can run without caring about response content. Tests that exercise
    response translation install a fixture-backed Message via `install_fixture`
    on the returned client mock (see _RECORDED_RESPONSES tests below).
    """
    with patch("analytics.utils.llm_providers.anthropic_provider.AsyncAnthropic") as ctor:
        client = MagicMock(name="AsyncAnthropicClient")
        ctor.return_value = client
        response = MagicMock(name="AnthropicMessage")
        text_block = MagicMock()
        text_block.type = "text"
        text_block.text = "hello"
        response.content = [text_block]
        response.usage.input_tokens = 0
        response.usage.cache_read_input_tokens = 0
        response.usage.cache_creation_input_tokens = 0
        response.usage.output_tokens = 0
        response.usage.output_tokens_details.thinking_tokens = 0
        response.id = "msg_test"
        response.parsed_output = None
        client.messages.create = AsyncMock(return_value=response)
        client.messages.parse = AsyncMock(return_value=response)
        client.close = AsyncMock()
        yield ctor, client, response


# ===========================================================================
# Request-shape tests — these validate the kwargs the provider sends TO the
# Anthropic SDK. They use the generic mock response since the response payload
# is irrelevant to these assertions.
# ===========================================================================


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
        assert isinstance(kwargs["system"], list)
        assert kwargs["system"][0]["type"] == "text"
        assert "you are concise" in kwargs["system"][0]["text"]
        roles = [m["role"] for m in kwargs["messages"]]
        assert "system" not in roles
        assert kwargs["messages"][0]["role"] == "user"

    async def test_function_call_translated_to_tool_use_block(self, mock_async_anthropic):
        _, client, _ = mock_async_anthropic
        await anthropic_provider.make_call(
            api_key="k", function_name="f",
            messages=[
                {"role": "user", "content": "search please"},
                {"type": "function_call", "name": "web_search",
                 "arguments": '{"q":"x"}', "call_id": "call_1"},
            ],
            model="claude-sonnet-4-6", reasoning_level="low",
        )
        kwargs = client.messages.create.await_args.kwargs
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
        for m in kwargs["messages"]:
            for block in m.get("content", []):
                assert block.get("type") != "reasoning"


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
        assert not kwargs.get("output_config")


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


class CacheControlTests:

    async def test_default_cache_control_on_last_system_block(self, mock_async_anthropic):
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
        _, client, _ = mock_async_anthropic
        await anthropic_provider.make_call(
            api_key="k", function_name="f", messages=[{"role": "user", "content": "x"}],
            model="claude-sonnet-4-6", reasoning_level="low",
            prompt_cache_key="cf_plan_abc",
        )
        kwargs = client.messages.create.await_args.kwargs
        assert "prompt_cache_key" not in kwargs


# ===========================================================================
# Response-translation tests — these load real recorded API responses, install
# them as the SDK return value, and assert that anthropic_provider's
# normalization produces the expected NormalizedResponse shape.
# ===========================================================================


class StructuredOutputResponseTests:
    """messages.parse(output_format=...) with Sonnet + adaptive thinking.

    Fixture: anthropic_sonnet_structured_200.json
    Real call: classify a newsletter excerpt into niche + content_types.
    """

    async def test_messages_parse_is_used_and_parsed_output_surfaced(self, mock_async_anthropic):
        _, client, _ = mock_async_anthropic
        recorded = _load_parsed_message(
            "anthropic_sonnet_structured_200.json", _NicheAnalysisResult,
        )
        client.messages.parse.return_value = recorded

        result = await anthropic_provider.make_call(
            api_key="k", function_name="niche",
            messages=[{"role": "user", "content": "newsletter excerpt..."}],
            model="claude-sonnet-4-6", reasoning_level="medium",
            response_format=_NicheAnalysisResult,
        )

        client.messages.parse.assert_awaited_once()
        client.messages.create.assert_not_awaited()
        assert isinstance(result.output_parsed, _NicheAnalysisResult)
        assert result.output_parsed.niche == "Artificial Intelligence & Machine Learning"
        # The recording produced 5 content types — the Pydantic validator
        # accepts the schema even though production caps at 5 downstream.
        assert len(result.output_parsed.content_types) == 5
        # Provider/model carried through
        assert result.provider == "anthropic"
        assert result.model == "claude-sonnet-4-6"
        # id is the scrubbed synthetic msg id from the fixture
        assert result.id.startswith("msg_synthetic")

    async def test_structured_output_text_contains_serialized_json(self, mock_async_anthropic):
        # The text block from messages.parse() carries the JSON-encoded
        # version of the parsed object — make sure the normalizer surfaces it.
        _, client, _ = mock_async_anthropic
        recorded = _load_parsed_message(
            "anthropic_sonnet_structured_200.json", _NicheAnalysisResult,
        )
        client.messages.parse.return_value = recorded

        result = await anthropic_provider.make_call(
            api_key="k", function_name="niche",
            messages=[{"role": "user", "content": "x"}],
            model="claude-sonnet-4-6", reasoning_level="medium",
            response_format=_NicheAnalysisResult,
        )
        text_obj = json.loads(result.output_text)
        assert text_obj["niche"] == "Artificial Intelligence & Machine Learning"

    async def test_structured_output_usage_from_recording(self, mock_async_anthropic):
        # Sonnet structured-output call recorded these counts. Adaptive
        # thinking ran but produced 0 thinking-tokens this turn.
        _, client, _ = mock_async_anthropic
        recorded = _load_parsed_message(
            "anthropic_sonnet_structured_200.json", _NicheAnalysisResult,
        )
        client.messages.parse.return_value = recorded

        result = await anthropic_provider.make_call(
            api_key="k", function_name="niche",
            messages=[{"role": "user", "content": "x"}],
            model="claude-sonnet-4-6", reasoning_level="medium",
            response_format=_NicheAnalysisResult,
        )
        assert result.usage.input_tokens == 302
        assert result.usage.cached_input_tokens == 0
        assert result.usage.cache_creation_tokens == 0
        # output 49, thinking 0 -> response_tokens 49, reasoning 0
        assert result.usage.output_tokens == 49
        assert result.usage.reasoning_tokens == 0


class FreeFormSonnetResponseTests:
    """messages.create() free-form on Sonnet + adaptive thinking.

    Fixture: anthropic_sonnet_freeform_200.json
    Real call: ask for a newsletter tagline.
    """

    async def test_messages_create_is_used(self, mock_async_anthropic):
        _, client, _ = mock_async_anthropic
        client.messages.create.return_value = _load_message("anthropic_sonnet_freeform_200.json")

        await anthropic_provider.make_call(
            api_key="k", function_name="tagline",
            messages=[{"role": "user", "content": "tagline pls"}],
            model="claude-sonnet-4-6", reasoning_level="low",
        )
        client.messages.create.assert_awaited_once()
        client.messages.parse.assert_not_awaited()

    async def test_text_block_translated_to_canonical_message_item(self, mock_async_anthropic):
        _, client, _ = mock_async_anthropic
        client.messages.create.return_value = _load_message("anthropic_sonnet_freeform_200.json")

        result = await anthropic_provider.make_call(
            api_key="k", function_name="tagline",
            messages=[{"role": "user", "content": "x"}],
            model="claude-sonnet-4-6", reasoning_level="low",
        )
        # Single text block in this recording.
        assert len(result.output_items) == 1
        item = result.output_items[0]
        assert item["type"] == "message"
        assert item["role"] == "assistant"
        assert item["content"][0]["type"] == "output_text"
        # The model returned a tagline; assert on the prefix to keep the test
        # resilient to small wording shifts if the recording is re-captured.
        assert result.output_text.startswith("**")
        assert "ship" in result.output_text.lower() or "production" in result.output_text.lower()
        assert result.output_parsed is None

    async def test_freeform_sonnet_usage_from_recording(self, mock_async_anthropic):
        _, client, _ = mock_async_anthropic
        client.messages.create.return_value = _load_message("anthropic_sonnet_freeform_200.json")

        result = await anthropic_provider.make_call(
            api_key="k", function_name="tagline",
            messages=[{"role": "user", "content": "x"}],
            model="claude-sonnet-4-6", reasoning_level="low",
        )
        # Recording: input=39, output=59, thinking=0.
        assert result.usage.input_tokens == 39
        assert result.usage.output_tokens == 59
        assert result.usage.reasoning_tokens == 0


class HaikuManualThinkingResponseTests:
    """messages.create() free-form on Haiku + manual budget_tokens thinking.

    Fixture: anthropic_haiku_manual_thinking_200.json
    Real call: ask for three signs of a well-monetized newsletter. This is the
    only recording where the API reports a non-zero thinking-tokens count, so
    it's the ground-truth check that the usage extractor handles real numbers.
    """

    async def test_thinking_block_is_dropped_from_output_items(self, mock_async_anthropic):
        # The recording contains both a thinking block AND a text block.
        # The provider drops the thinking block at the boundary (Claude
        # private state, not round-trippable).
        _, client, _ = mock_async_anthropic
        client.messages.create.return_value = _load_message(
            "anthropic_haiku_manual_thinking_200.json"
        )

        result = await anthropic_provider.make_call(
            api_key="k", function_name="signs",
            messages=[{"role": "user", "content": "x"}],
            model="claude-haiku-4-5", reasoning_level="medium",
        )
        # Only the text block round-trips into output_items.
        assert len(result.output_items) == 1
        assert result.output_items[0]["type"] == "message"
        assert "monetized" in result.output_text.lower() or "revenue" in result.output_text.lower()

    async def test_haiku_thinking_tokens_surface_on_reasoning_tokens(self, mock_async_anthropic):
        # GROUND TRUTH: the recorded Haiku call had thinking_tokens=223 of
        # output_tokens=345. The normalizer must (a) split those out as
        # reasoning_tokens and (b) net them out of output_tokens.
        _, client, _ = mock_async_anthropic
        client.messages.create.return_value = _load_message(
            "anthropic_haiku_manual_thinking_200.json"
        )

        result = await anthropic_provider.make_call(
            api_key="k", function_name="signs",
            messages=[{"role": "user", "content": "x"}],
            model="claude-haiku-4-5", reasoning_level="medium",
        )
        # Recording: input=54, output=345, thinking=223.
        assert result.usage.input_tokens == 54
        assert result.usage.reasoning_tokens == 223
        assert result.usage.output_tokens == 345 - 223  # 122


class ToolUseRoundTripResponseTests:
    """messages.create() with tools + forced tool_choice, two-turn round trip.

    Fixtures: anthropic_sonnet_tool_use_turn1_200.json (tool_use block),
              anthropic_sonnet_tool_use_turn2_200.json (final text after
              tool_result).
    Real call: "What is the current weather in Tokyo in celsius?"
    """

    async def test_turn1_tool_use_block_becomes_function_call_item(self, mock_async_anthropic):
        _, client, _ = mock_async_anthropic
        client.messages.create.return_value = _load_message(
            "anthropic_sonnet_tool_use_turn1_200.json"
        )

        result = await anthropic_provider.make_call(
            api_key="k", function_name="weather",
            messages=[{"role": "user", "content": "What is the weather in Tokyo?"}],
            model="claude-sonnet-4-6", reasoning_level="medium",
            tools=[{
                "type": "function", "name": "get_current_weather",
                "description": "Get weather",
                "parameters": {"type": "object", "properties": {}},
            }],
            tool_choice="required",
        )
        # The tool_use block should appear in both output_items and tool_calls.
        assert len(result.output_items) == 1
        item = result.output_items[0]
        assert item["type"] == "function_call"
        assert item["name"] == "get_current_weather"
        # Scrubbed synthetic toolu id from the recording.
        assert item["call_id"].startswith("toolu_synthetic")

        assert len(result.tool_calls) == 1
        tc = result.tool_calls[0]
        assert tc["name"] == "get_current_weather"
        # Arguments are the actual model output: city=Tokyo, unit=c.
        args = json.loads(tc["arguments_json"])
        assert args["city"] == "Tokyo"
        assert args["unit"] == "c"

    async def test_turn2_final_text_after_tool_result(self, mock_async_anthropic):
        _, client, _ = mock_async_anthropic
        client.messages.create.return_value = _load_message(
            "anthropic_sonnet_tool_use_turn2_200.json"
        )

        result = await anthropic_provider.make_call(
            api_key="k", function_name="weather",
            # Mirror the recording's input shape: user + assistant tool_use +
            # user tool_result. Tests the message-translation outbound path too.
            messages=[
                {"role": "user", "content": "What is the weather in Tokyo?"},
                {"type": "function_call", "name": "get_current_weather",
                 "arguments": '{"city": "Tokyo", "unit": "c"}',
                 "call_id": "toolu_synthetic00000000000000000001"},
                {"type": "function_call_output",
                 "call_id": "toolu_synthetic00000000000000000001",
                 "output": "Tokyo is 18°C and partly cloudy."},
            ],
            model="claude-sonnet-4-6", reasoning_level="medium",
            tools=[{
                "type": "function", "name": "get_current_weather",
                "description": "Get weather",
                "parameters": {"type": "object", "properties": {}},
            }],
        )
        # Final turn returned a text block; no further tool calls.
        assert result.tool_calls == []
        assert len(result.output_items) == 1
        assert result.output_items[0]["type"] == "message"
        assert "Tokyo" in result.output_text
        assert "18" in result.output_text

    async def test_turn1_call_id_round_trips_through_message_translation(self, mock_async_anthropic):
        # When the canonical output_items from turn 1 are fed back into the
        # next call (as production does), the call_id should land on the
        # tool_use block in the assistant message we send to Claude.
        _, client, _ = mock_async_anthropic
        client.messages.create.return_value = _load_message(
            "anthropic_sonnet_tool_use_turn1_200.json"
        )

        result = await anthropic_provider.make_call(
            api_key="k", function_name="weather",
            messages=[{"role": "user", "content": "weather?"}],
            model="claude-sonnet-4-6", reasoning_level="medium",
            tools=[{
                "type": "function", "name": "get_current_weather",
                "description": "Get weather",
                "parameters": {"type": "object", "properties": {}},
            }],
            tool_choice="required",
        )
        # Feed turn 1's output_items back into a second call's messages list.
        client.messages.create.reset_mock()
        client.messages.create.return_value = _load_message(
            "anthropic_sonnet_tool_use_turn2_200.json"
        )

        round_trip_messages = (
            [{"role": "user", "content": "weather?"}]
            + result.output_items
            + [{"type": "function_call_output",
                "call_id": result.tool_calls[0]["call_id"],
                "output": "Tokyo is 18°C and partly cloudy."}]
        )
        await anthropic_provider.make_call(
            api_key="k", function_name="weather",
            messages=round_trip_messages,
            model="claude-sonnet-4-6", reasoning_level="medium",
        )
        sent_kwargs = client.messages.create.await_args.kwargs
        # The translated outbound messages should contain a tool_use block
        # with the same call_id that came out of turn 1.
        all_blocks = []
        for m in sent_kwargs["messages"]:
            for b in m.get("content", []) or []:
                if isinstance(b, dict):
                    all_blocks.append(b)
        tool_use_blocks = [b for b in all_blocks if b.get("type") == "tool_use"]
        assert len(tool_use_blocks) == 1
        assert tool_use_blocks[0]["id"] == result.tool_calls[0]["call_id"]
        # And there should be a matching tool_result on a user message.
        tool_result_blocks = [b for b in all_blocks if b.get("type") == "tool_result"]
        assert len(tool_result_blocks) == 1
        assert tool_result_blocks[0]["tool_use_id"] == result.tool_calls[0]["call_id"]


# ===========================================================================
# Error classification — unchanged from pre-fixtures; doesn't exercise response
# translation, only SDK error -> ProviderError wrapping.
# ===========================================================================


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
