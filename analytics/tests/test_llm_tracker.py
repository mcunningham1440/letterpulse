"""
Unit tests for analytics.llm_tracker.

Covers:
- _token_cost: per-million pricing math.
- _serialize_input_messages: role/content normalization, function_call and
  function_call_output formatting, reasoning items filtered out.
- record_call / finish_tracking: dev-panel accumulator with totals; isolation
  on a per-context basis via asyncio.gather (each task inherits the snapshot
  at create time and mutations don't leak across siblings).

The DB-side LLMCall enqueue is intentionally noop'd by patching
`analytics.llm_tracker.log_sink.put`; this file is about the tracker math,
not the queue plumbing.
"""
from __future__ import annotations

import asyncio
import contextvars
from types import SimpleNamespace
from unittest import mock

import pytest
from django.test import SimpleTestCase, override_settings

from analytics import llm_tracker
from analytics.llm_tracker import (
    _serialize_input_messages,
    _token_cost,
    finish_tracking,
    record_call,
    start_tracking,
)


def _fake_response(input_tokens=0, cached=0, output=0, reasoning=0,
                   output_text=""):
    usage = SimpleNamespace(
        input_tokens=input_tokens,
        cached_input_tokens=cached,
        cache_creation_tokens=0,
        output_tokens=output,
        reasoning_tokens=reasoning,
    )
    return SimpleNamespace(usage=usage, output_text=output_text)


class TokenCostTests(SimpleTestCase):

    def test_one_million_tokens_costs_the_price(self):
        self.assertAlmostEqual(_token_cost(1_000_000, 2.5), 2.5)

    def test_half_million_tokens_is_half_price(self):
        self.assertAlmostEqual(_token_cost(500_000, 4.0), 2.0)

    def test_zero_tokens_is_zero_cost(self):
        self.assertEqual(_token_cost(0, 10.0), 0)


class SerializeInputMessagesTests(SimpleTestCase):

    def test_string_content_round_trips(self):
        out = _serialize_input_messages([
            {"role": "user", "content": "Hello there"},
        ])
        self.assertEqual(out, [{"label": "user", "text": "Hello there"}])

    def test_list_content_concatenates_text_pieces(self):
        out = _serialize_input_messages([
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Hello"},
                    {"type": "input_text", "input_text": "World"},
                ],
            },
        ])
        self.assertEqual(out[0]["label"], "user")
        self.assertIn("Hello", out[0]["text"])
        self.assertIn("World", out[0]["text"])

    def test_function_call_serialized_with_name_and_arguments(self):
        out = _serialize_input_messages([
            {"type": "function_call", "name": "web_search",
             "arguments": '{"q": "anthropic"}'},
        ])
        self.assertEqual(out[0]["label"], "function_call")
        self.assertIn("name: web_search", out[0]["text"])
        self.assertIn('"q": "anthropic"', out[0]["text"])

    def test_function_call_output_uses_output_field(self):
        out = _serialize_input_messages([
            {"type": "function_call_output", "output": "tool result"},
        ])
        self.assertEqual(out[0]["text"], "tool result")

    def test_reasoning_items_dropped(self):
        out = _serialize_input_messages([
            {"type": "reasoning", "content": "internal thought"},
            {"role": "user", "content": "visible"},
        ])
        # Only the user message survives.
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["label"], "user")

    def test_non_dict_falls_back_to_raw(self):
        out = _serialize_input_messages(["just-a-string"])
        self.assertEqual(out[0]["label"], "raw")
        self.assertEqual(out[0]["text"], "just-a-string")

    def test_empty_input_returns_empty_list(self):
        self.assertEqual(_serialize_input_messages([]), [])
        self.assertEqual(_serialize_input_messages(None), [])


@override_settings(ENVIRONMENT='local')
class RecordCallAccumulationTests(SimpleTestCase):
    """
    Verify the dev-panel accumulator. We patch log_sink to keep the DB-side
    enqueue out of these tests — they're about per-call math + totals only.
    """

    def setUp(self):
        # Run each test inside its own copied context so contextvars don't
        # leak across tests.
        self._ctx = contextvars.copy_context()
        # Silence the LLMCall enqueue.
        self._patcher = mock.patch(
            "analytics.llm_tracker._enqueue_llm_row"
        )
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()

    def _run(self, fn, *args, **kwargs):
        return self._ctx.run(fn, *args, **kwargs)

    def test_record_call_appends_to_accumulator_with_cost_math(self):
        def body():
            start_tracking()
            resp = _fake_response(
                input_tokens=1_000_000, cached=0, output=500_000, reasoning=0,
                output_text="hi",
            )
            record_call(
                function_name="auto_section",
                model="gpt-5.4",
                messages=[{"role": "user", "content": "test"}],
                response=resp,
                duration=1.234,
                provider="openai",
            )
            return finish_tracking()

        data = self._run(body)
        self.assertIsNotNone(data)
        self.assertEqual(len(data["calls"]), 1)
        call = data["calls"][0]
        self.assertEqual(call["function_name"], "auto_section")
        self.assertEqual(call["provider"], "openai")
        self.assertEqual(call["model"], "gpt-5.4")
        # Input cost: 1M @ $2.50/M = $2.50; cached 0.
        self.assertAlmostEqual(call["input_usage"]["cost"], 2.50)
        # Output: 0.5M @ $15/M = $7.50.
        self.assertAlmostEqual(call["output_usage"]["cost"], 7.50)
        self.assertAlmostEqual(call["total_cost"], 10.00)
        # Totals match the single call.
        self.assertAlmostEqual(data["totals"]["total_cost"], 10.00)
        self.assertEqual(data["totals"]["input_usage"]["new_tokens"], 1_000_000)

    def test_finish_tracking_returns_none_when_not_started(self):
        # No start_tracking() call in this context -> finish returns None.
        def body():
            return finish_tracking()
        self.assertIsNone(self._run(body))

    def test_record_call_without_tracking_does_not_raise(self):
        # When dev-panel tracking is OFF (no start_tracking), record_call
        # should silently skip the accumulator branch.
        def body():
            resp = _fake_response(input_tokens=100, output=50)
            record_call(
                function_name="x", model="gpt-5.4", messages=[],
                response=resp, duration=0.1, provider="openai",
            )
            return finish_tracking()
        # Returns None because tracking never started.
        self.assertIsNone(self._run(body))


@override_settings(ENVIRONMENT='local')
class ContextvarIsolationTests(SimpleTestCase):
    """
    Verify the claim made at llm_tracker.py:11 — asyncio.Task inherits the
    context at creation, so parallel coroutines in asyncio.gather each see
    their own tracker state without cross-talk.
    """

    def setUp(self):
        self._patcher = mock.patch(
            "analytics.llm_tracker._enqueue_llm_row"
        )
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()

    @pytest.mark.asyncio
    async def test_gather_tasks_each_have_isolated_accumulator(self):
        # Each child task starts its own tracking via copy_context so the
        # parent's call list stays empty. Mutations in one child must not
        # appear in another child.
        async def child(label, n_tokens):
            ctx = contextvars.copy_context()
            def run():
                start_tracking()
                resp = _fake_response(input_tokens=n_tokens, output=10,
                                      output_text=label)
                record_call(
                    function_name=label, model="gpt-5.4-mini",
                    messages=[{"role": "user", "content": label}],
                    response=resp, duration=0.1, provider="openai",
                )
                return finish_tracking()
            # Run the sync recording inside the copied context so each
            # gather task gets its own contextvar snapshot.
            return await asyncio.to_thread(ctx.run, run)

        results = await asyncio.gather(
            child("alpha", 1000),
            child("beta", 2000),
            child("gamma", 3000),
        )
        # Each task gets exactly one call, with its own model + tokens.
        self.assertEqual(len(results), 3)
        for d in results:
            self.assertEqual(len(d["calls"]), 1)
        labels = sorted(d["calls"][0]["function_name"] for d in results)
        self.assertEqual(labels, ["alpha", "beta", "gamma"])
        token_totals = sorted(
            d["totals"]["input_usage"]["new_tokens"] for d in results
        )
        self.assertEqual(token_totals, [1000, 2000, 3000])

    @pytest.mark.asyncio
    async def test_finish_tracking_clears_state_for_next_session(self):
        # After finish, a subsequent start should begin from zero.
        ctx = contextvars.copy_context()

        def first():
            start_tracking()
            record_call("a", "gpt-5.4", [], _fake_response(input_tokens=100),
                        duration=0.05, provider="openai")
            return finish_tracking()

        def second():
            # finish_tracking above cleared _tracker_calls (set to None);
            # without start_tracking, record_call no-ops at the accumulator.
            # A fresh start should give us an empty list to append to.
            start_tracking()
            record_call("b", "gpt-5.4", [], _fake_response(input_tokens=200),
                        duration=0.05, provider="openai")
            return finish_tracking()

        first_data = ctx.run(first)
        second_data = ctx.run(second)
        # Second session should reflect only the second call.
        self.assertEqual(len(second_data["calls"]), 1)
        self.assertEqual(second_data["calls"][0]["function_name"], "b")
        # First session unaffected.
        self.assertEqual(first_data["calls"][0]["function_name"], "a")
