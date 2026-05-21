import asyncio
import hashlib
import json
import logging
from datetime import date, datetime, timedelta
from typing import List

from asgiref.sync import async_to_sync, sync_to_async
from django.conf import settings
from django.utils import timezone
from perplexity import Perplexity
from pydantic import BaseModel

from analytics.llm_tracker import (
    finish_tracking,
    seed_tracking,
    set_additional_info,
    set_llm_context,
    start_tracking,
)
from analytics.models import (
    ContentSearchFeedback,
    Feedback,
    LinkData,
    Section,
)
from analytics.prompts import (
    CONTENT_FINDER_DISPATCH_PROMPT,
    CONTENT_FINDER_OUTPUT_PROMPT,
    CONTENT_FINDER_PLAN_PROMPT,
    CONTENT_FINDER_SEARCH_PROMPT,
)

from .background import refresh_db_connection
from .links import format_link_history
from .llm import llm_call
from .text import html_to_text_with_links

logger = logging.getLogger(__name__)


def perplexity_search(queries, max_results=10, domains=None, max_days_ago=None, historical_urls=None):
    """Call the Perplexity search API with one or more queries and return results as a formatted string.

    If historical_urls is provided, any result whose URL is a substring of
    any historical URL is silently excluded from the output.
    """
    queries = [q for q in queries if q and q.strip()]
    if not queries:
        return "No results found (empty queries)."

    kwargs = {
        "query": queries,
        "max_results": max_results,
        "max_tokens_per_page": 256,
    }
    if domains:
        kwargs["search_domain_filter"] = domains
    if max_days_ago is not None:
        after_date = date.today() - timedelta(days=max_days_ago)
        kwargs["search_after_date_filter"] = after_date.strftime("%m/%d/%Y")

    client = Perplexity(api_key=settings.PERPLEXITY_API_KEY)
    search = client.search.create(**kwargs)

    if not search.results:
        return "No results found."

    # Filter out results whose URL is a substring of any historical URL
    results = search.results
    if historical_urls:
        results = [
            r for r in results
            if not r.url or not any(r.url.rstrip('/') in h_url for h_url in historical_urls)
        ]
        if not results:
            return "No results found."

    today = datetime.now().date()
    lines = []
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. {r.title or '(no title)'}")
        lines.append(f"   URL: {r.url or ''}")
        if getattr(r, "date", None):
            try:
                d = datetime.strptime(r.date, "%Y-%m-%d").date()
                days_ago = (today - d).days
                lines.append(f"   Date: {r.date} ({days_ago} days ago)")
            except ValueError:
                lines.append(f"   Date: {r.date}")
        if getattr(r, "snippet", None):
            lines.append(f"   {r.snippet}")
        lines.append("")

    return "\n".join(lines)


CONTENT_FINDER_SEARCH_TOOL = {
    "type": "function",
    "name": "web_search",
    "description": "Search the web for recent articles, news, tools, and other content. Supports up to 5 queries per call for comprehensive coverage of a topic.",
    "parameters": {
        "type": "object",
        "properties": {
            "queries": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "maxItems": 5,
                "description": "1-5 search queries. Be specific — include topic, context, and recency (e.g. 'open source AI agent frameworks released April 2026'). IMPORTANT: Do NOT use 'site:' prefix in queries. To filter by domain, use the 'domains' parameter instead."
            },
            "domains": {
                "type": "array",
                "items": {"type": "string"},
                "maxItems": 20,
                "description": "Domains to restrict results to (e.g. ['techcrunch.com', 'arxiv.org']). Pass empty array to search the entire web. This is the ONLY way to filter by domain — 'site:' in queries does not work."
            },
            "max_days_ago": {
                "type": "integer",
                "description": "Only return results published within this many days. For example, 7 = past week, 14 = past two weeks, 30 = past month. Omit (set to 0) for no date restriction."
            }
        },
        "required": ["queries", "domains", "max_days_ago"],
        "additionalProperties": False,
    },
    "strict": True,
}


class ContentFinderLink(BaseModel):
    title: str
    source: str
    url: str
    date: str
    description: str
    relevance: str


class ContentFinderAllLinks(BaseModel):
    links: List[ContentFinderLink]


class ContentFinderDispatchList(BaseModel):
    sections: List[str]


def build_content_finder_user_prompt(section, link_history_str, link_count, max_url_len=75):
    """Build the user prompt for a single section's content finder agent."""
    section_text = html_to_text_with_links(section.section_html, max_url_len=max_url_len)

    return f"""<{section.section_name}.content>
{section_text}
</{section.section_name}.content>

<{section.section_name}.historical_link_performance>
The following links have appeared in this section in past issues. Values above 1.0x indicate above-average performance.

{link_history_str}
</{section.section_name}.historical_link_performance>
"""


def build_all_sections_user_prompt(sections, max_links=60, max_url_len=75):
    """Concatenate build_content_finder_user_prompt() output for every section in order."""
    blocks = []
    for section in sections:
        link_history_str, link_count = format_link_history(
            section, max_links=max_links, max_url_len=max_url_len,
        )
        blocks.append(
            build_content_finder_user_prompt(
                section, link_history_str, link_count, max_url_len=max_url_len,
            )
        )
    return "\n".join(blocks)


async def run_plan_stage(task, sections):
    """Stage 1: single LLM call that sees all sections and drafts a search plan.

    Returns (plan_text, plan_messages) where plan_messages is the full list
    [system(PLAN_PROMPT), user(user_prompt), <plan response output items>].
    """
    max_links = settings.CONTENT_FINDER_MAX_LINKS
    max_url_len = settings.CONTENT_FINDER_MAX_URL_LEN

    user_prompt = await sync_to_async(build_all_sections_user_prompt)(
        sections, max_links=max_links, max_url_len=max_url_len,
    )

    messages = [
        {"role": "system", "content": CONTENT_FINDER_PLAN_PROMPT},
        {"role": "user", "content": user_prompt},
    ]

    response = await llm_call(
        "content_finder_plan",
        messages,
        settings.CONTENT_FINDER_PLAN_MODEL,
        settings.CONTENT_FINDER_PLAN_REASONING,
        store=True,
        prompt_cache_key=f"cf_plan_{task.task_id}",
        prompt_cache_retention="24h",
        timeout=90.0,
    )

    plan_messages = messages + response.output_items
    return response.output_text, plan_messages


async def run_dispatch_stage(task):
    """Stage 2: append the user's feedback + DISPATCH_PROMPT to plan_messages and
    ask the model for a structured List[str] of section labels.

    Returns (dispatch_sections, dispatch_messages) where dispatch_messages is
    plan_messages + [user(feedback), system(DISPATCH_PROMPT), <dispatch response output items>].
    """
    feedback = (task.user_feedback or "").strip() or "(no changes)"

    messages = list(task.plan_messages or []) + [
        {"role": "user", "content": feedback},
        {"role": "system", "content": CONTENT_FINDER_DISPATCH_PROMPT},
    ]

    response = await llm_call(
        "content_finder_dispatch",
        messages,
        settings.CONTENT_FINDER_PLAN_MODEL,
        settings.CONTENT_FINDER_PLAN_REASONING,
        response_format=ContentFinderDispatchList,
        store=True,
        prompt_cache_key=f"cf_plan_{task.task_id}",
        prompt_cache_retention="24h",
        timeout=60.0,
    )

    parsed = response.output_parsed
    # Soft fallback: a None/empty parsed output completes the pipeline with
    # zero search agents rather than raising — same behavior as the previous
    # try/except block. Flagged.
    dispatch_sections = list(parsed.sections) if parsed is not None else []
    dispatch_sections = dispatch_sections[:settings.CONTENT_FINDER_DISPATCH_MAX_SECTIONS]
    dispatch_messages = messages + response.output_items
    return dispatch_sections, dispatch_messages


async def run_search_agent(section_name, dispatch_messages, historical_urls, task_id, max_rounds=3):
    """
    Per-section search agent. Starts from the full dispatch_messages list,
    appends user(SEARCH_PROMPT), runs `max_rounds` of tool-call turns (each
    appending the assistant output + tool outputs), then appends user(OUTPUT_PROMPT)
    and emits the final structured output.

    Returns (section_name, parsed_links_or_empty_list).
    """
    set_additional_info({'section_name': section_name})

    model = settings.CONTENT_FINDER_MODEL
    reasoning = settings.CONTENT_FINDER_REASONING
    # Same key across every call this agent makes so its rounds + final output
    # all route to the same machine and reuse the cached prefix from one call to
    # the next. Per-section keys (rather than per-task) keep parallel agents
    # under the ~15 RPM-per-key routing budget. The OpenAI API caps
    # prompt_cache_key at 64 chars; UUID(36) + long section names overflow,
    # so the section component is hashed.
    section_slug = hashlib.md5(section_name.encode('utf-8')).hexdigest()[:8]
    cache_key = f"cf_search_{task_id}_{section_slug}"

    messages = list(dispatch_messages) + [
        {"role": "user", "content": CONTENT_FINDER_SEARCH_PROMPT.format(section_name=section_name)},
    ]

    for round_num in range(max_rounds):
        response = await llm_call(
            "content_finder_search",
            messages,
            model,
            reasoning,
            tools=[CONTENT_FINDER_SEARCH_TOOL],
            tool_choice="required",
            store=True,
            prompt_cache_key=cache_key,
            prompt_cache_retention="24h",
            timeout=45.0,
        )

        # Carry the assistant output (incl. any reasoning items) forward.
        messages = messages + response.output_items

        if not response.tool_calls:
            break

        for call in response.tool_calls:
            try:
                args = json.loads(call.get('arguments_json') or '{}')
            except json.JSONDecodeError:
                # Soft fallback: skip malformed tool call rather than crash.
                # Flagged — would mean the model produced invalid JSON for a
                # strict-schema tool.
                args = {}
            queries = args.get("queries") or []
            domains = args.get("domains") or None
            max_days_ago = args.get("max_days_ago") or None

            result = perplexity_search(
                queries, domains=domains, max_days_ago=max_days_ago,
                historical_urls=historical_urls,
            )
            messages.append({
                "type": "function_call_output",
                "call_id": call.get('call_id', ''),
                "output": result,
            })

    messages = messages + [
        {"role": "user", "content": CONTENT_FINDER_OUTPUT_PROMPT.format(section_name=section_name)},
    ]

    response = await llm_call(
        "content_finder_final_output",
        messages,
        model,
        reasoning,
        response_format=ContentFinderAllLinks,
        store=True,
        prompt_cache_key=cache_key,
        prompt_cache_retention="24h",
        timeout=60.0,
    )

    parsed = response.output_parsed
    links = [link.model_dump() for link in parsed.links] if parsed is not None else []

    return (section_name, links)


async def run_all_searches(task):
    """Stage 3: fan out parallel search agents, one per entry in task.dispatch_sections."""
    def _load_historical_urls():
        urls = set(
            url.rstrip('/') for url in
            LinkData.objects.filter(
                user=task.user,
                publication=task.publication,
            ).values_list('raw_url', flat=True)
        )
        urls |= set(
            url.rstrip('/') for url in
            ContentSearchFeedback.objects.filter(
                user=task.user,
                publication=task.publication,
            ).values_list('url', flat=True)
        )
        return urls

    historical_urls = await sync_to_async(_load_historical_urls)()

    max_rounds = settings.CONTENT_FINDER_MAX_ROUNDS
    dispatch_messages = task.dispatch_messages or []

    agents = [
        run_search_agent(
            section_name, dispatch_messages, historical_urls,
            str(task.task_id), max_rounds=max_rounds,
        )
        for section_name in (task.dispatch_sections or [])
    ]
    return await asyncio.gather(*agents)


def run_content_finder_background(task):
    """
    Background work fn for content finder. Branches on task.status: 'planning'
    runs Stage 1 (exits awaiting user feedback); 'dispatching' runs Stage 2 +
    Stage 3. The `spawn_background` wrapper handles claim, error-marking, and
    DB connection cleanup.
    """
    set_llm_context(
        user_id=task.user_id,
        publication_id=task.publication_id,
        task_id=task.task_id,
        task_kind='content_finder',
    )

    if task.status == 'planning':
        if settings.ENVIRONMENT == 'local':
            start_tracking()

        sections = list(
            Section.objects.filter(post=task.post, user=task.user).order_by('start_line')
        )
        if not sections:
            task.mark_complete(result_data=[])
            return

        plan_text, plan_messages = async_to_sync(run_plan_stage)(task, sections)
        refresh_db_connection()

        update_fields = ['plan_text', 'plan_messages', 'status', 'last_heartbeat', 'updated_at']
        task.plan_text = plan_text
        task.plan_messages = plan_messages
        task.status = 'awaiting_feedback'
        task.last_heartbeat = timezone.now()
        if settings.ENVIRONMENT == 'local':
            task.dev_panel_data = finish_tracking() or {}
            update_fields.append('dev_panel_data')
        task.save(update_fields=update_fields)
        return

    if task.status == 'dispatching':
        # Resume dev-panel accumulation from what Stage 1 recorded so the
        # final panel shows plan + dispatch + all parallel searches together.
        if settings.ENVIRONMENT == 'local':
            seed_tracking(task.dev_panel_data)

        dispatch_sections, dispatch_messages = async_to_sync(run_dispatch_stage)(task)
        refresh_db_connection()

        task.dispatch_sections = dispatch_sections
        task.dispatch_messages = dispatch_messages
        task.status = 'searching'
        task.last_heartbeat = timezone.now()
        task.save(update_fields=[
            'dispatch_sections', 'dispatch_messages', 'status',
            'last_heartbeat', 'updated_at',
        ])

        if not dispatch_sections:
            extras = {'result_data': []}
            if settings.ENVIRONMENT == 'local':
                extras['dev_panel_data'] = finish_tracking() or task.dev_panel_data
            task.mark_complete(**extras)
            return

        raw_results = async_to_sync(run_all_searches)(task)
        refresh_db_connection()

        # Preserve dispatch output order. JSONB doesn't guarantee dict key
        # order, so we store an explicit list keyed by `section` instead of
        # a {section_name: links} dict.
        links_by_section = {name: links for name, links in raw_results if links is not None}
        result_data = [
            {'section': name, 'links': links_by_section[name]}
            for name in dispatch_sections
            if name in links_by_section
        ]

        extras = {'result_data': result_data}
        if settings.ENVIRONMENT == 'local':
            extras['dev_panel_data'] = finish_tracking() or task.dev_panel_data
        task.mark_complete(**extras)

        Feedback.objects.get_or_create(
            user=task.user, feature='used_content_finder',
            defaults={'response': 'completed'}
        )
