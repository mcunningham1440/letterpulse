import asyncio
import json
import logging
import os
import time
from datetime import datetime

from django.utils import timezone as dj_timezone
from dotenv import load_dotenv
from openai import AsyncOpenAI, OpenAI

from analytics.llm_tracker import record_call, record_error

logger = logging.getLogger(__name__)


OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
if not OPENAI_API_KEY:
    load_dotenv()
    OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]

# Support both JSON format (AWS AppRunner) and plain string format (local dev)
try:
    OPENAI_API_KEY = json.loads(OPENAI_API_KEY)["OPENAI_API_KEY"]
except (json.JSONDecodeError, KeyError, TypeError):
    pass  # Already a plain string, use as-is


async def llm_call(function_name, messages, model, reasoning_level, response_format=None, tools=None, tool_choice=None, user=None, store=False, previous_response_id=None, prompt_cache_key=None, prompt_cache_retention=None, timeout=90.0):
    """
    Make an async call to OpenAI API and log the request.

    Args:
        function_name: Name of the function making the call (for logging)
        messages: List of message dicts for the API
        model: Model name (e.g., "gpt-5.1")
        reasoning_level: Reasoning effort level ("low", "medium", "high")
        response_format: Optional Pydantic model for structured output
        tools: Optional list of tools
        tool_choice: Optional tool choice constraint ("auto", "required", or specific tool dict)
        user: Django user object for logging (optional)
        store: If True, OpenAI stores the response server-side for reasoning continuity
        previous_response_id: ID of a stored response to thread reasoning context from
        prompt_cache_key: Routing-stickiness key so requests sharing a long prefix
            land on the same machine and reuse cached KV state.
        prompt_cache_retention: "in_memory" (default) or "24h" for extended caching
            on supported models (gpt-5.x, gpt-4.1).

    Returns:
        OpenAI API response object
    """
    start_datetime = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    start_ts = dj_timezone.now()
    start_time = time.time()

    kwargs = {
        "model": model,
        "input": messages,
        "reasoning": {"effort": reasoning_level}
    }
    if tools is not None:
        kwargs["tools"] = tools
    if tool_choice is not None:
        kwargs["tool_choice"] = tool_choice
    if store:
        kwargs["store"] = True
    if previous_response_id is not None:
        kwargs["previous_response_id"] = previous_response_id
    if prompt_cache_key is not None:
        kwargs["prompt_cache_key"] = prompt_cache_key
    if prompt_cache_retention is not None:
        kwargs["prompt_cache_retention"] = prompt_cache_retention

    try:
        if asyncio.get_event_loop().is_running():
            client = AsyncOpenAI(api_key=OPENAI_API_KEY, timeout=timeout, max_retries=2)
            try:
                if response_format is not None:
                    kwargs["text_format"] = response_format

                    response = await client.responses.parse(**kwargs)
                else:
                    response = await client.responses.create(**kwargs)
            finally:
                await client.close()
        else:
            client = OpenAI(api_key=OPENAI_API_KEY, timeout=timeout, max_retries=2)
            try:
                if response_format is not None:
                    kwargs["text_format"] = response_format

                    response = client.responses.parse(**kwargs)
                else:
                    response = client.responses.create(**kwargs)
            finally:
                client.close()
    except Exception as e:
        logger.exception("llm_call failed")
        record_error(function_name, model, time.time() - start_time, e, start_ts=start_ts)
        raise
    duration = time.time() - start_time

    record_call(function_name, model, messages, response, duration, start_ts=start_ts)

    return response
