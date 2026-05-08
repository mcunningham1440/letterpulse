"""
Utility functions for the Django analytics app.
Adapted from the original utils.py for Streamlit.
"""

import difflib
import hashlib
import json
import math
import re
from collections import Counter
from openai import AsyncOpenAI, OpenAI
from pydantic import BaseModel
from typing import List, Optional
import pandas as pd
import asyncio
import aiohttp
import os
import logging
from datetime import datetime
import time
import html as html_module
from bs4 import BeautifulSoup, NavigableString
from django.conf import settings
from django.db import transaction
from django.db.models import F
from Levenshtein import distance as levenshtein_distance
from dotenv import load_dotenv
from analytics.prompts import (
    CONTENT_FINDER_PLAN_PROMPT,
    CONTENT_FINDER_DISPATCH_PROMPT,
    CONTENT_FINDER_SEARCH_PROMPT,
    CONTENT_FINDER_OUTPUT_PROMPT,
    IMPROVEMENT_TIP_PROMPT,
    NICHE_ANALYSIS_PROMPT,
)

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


# Timezone choices for user preferences - comprehensive list covering all major timezones
TIMEZONE_CHOICES = [
    # North America
    ('America/New_York', 'Eastern Time (ET) - New York'),
    ('America/Chicago', 'Central Time (CT) - Chicago'),
    ('America/Denver', 'Mountain Time (MT) - Denver'),
    ('America/Los_Angeles', 'Pacific Time (PT) - Los Angeles'),
    ('America/Anchorage', 'Alaska Time (AKT)'),
    ('Pacific/Honolulu', 'Hawaii Time (HST)'),
    ('America/Phoenix', 'Arizona (MST - no DST)'),
    ('America/Toronto', 'Eastern Time - Toronto'),
    ('America/Vancouver', 'Pacific Time - Vancouver'),
    ('America/Mexico_City', 'Mexico City (CST)'),
    # South America
    ('America/Sao_Paulo', 'São Paulo (BRT)'),
    ('America/Buenos_Aires', 'Buenos Aires (ART)'),
    ('America/Bogota', 'Bogotá (COT)'),
    ('America/Santiago', 'Santiago (CLT)'),
    # Europe
    ('Europe/London', 'London (GMT/BST)'),
    ('Europe/Paris', 'Paris (CET/CEST)'),
    ('Europe/Berlin', 'Berlin (CET/CEST)'),
    ('Europe/Amsterdam', 'Amsterdam (CET/CEST)'),
    ('Europe/Madrid', 'Madrid (CET/CEST)'),
    ('Europe/Rome', 'Rome (CET/CEST)'),
    ('Europe/Zurich', 'Zurich (CET/CEST)'),
    ('Europe/Stockholm', 'Stockholm (CET/CEST)'),
    ('Europe/Warsaw', 'Warsaw (CET/CEST)'),
    ('Europe/Athens', 'Athens (EET/EEST)'),
    ('Europe/Moscow', 'Moscow (MSK)'),
    ('Europe/Istanbul', 'Istanbul (TRT)'),
    # Middle East & Africa
    ('Asia/Dubai', 'Dubai (GST)'),
    ('Asia/Jerusalem', 'Jerusalem (IST)'),
    ('Africa/Cairo', 'Cairo (EET)'),
    ('Africa/Johannesburg', 'Johannesburg (SAST)'),
    ('Africa/Lagos', 'Lagos (WAT)'),
    # Asia
    ('Asia/Kolkata', 'India (IST)'),
    ('Asia/Bangkok', 'Bangkok (ICT)'),
    ('Asia/Singapore', 'Singapore (SGT)'),
    ('Asia/Hong_Kong', 'Hong Kong (HKT)'),
    ('Asia/Shanghai', 'Shanghai (CST)'),
    ('Asia/Tokyo', 'Tokyo (JST)'),
    ('Asia/Seoul', 'Seoul (KST)'),
    ('Asia/Manila', 'Manila (PHT)'),
    ('Asia/Jakarta', 'Jakarta (WIB)'),
    # Oceania
    ('Australia/Sydney', 'Sydney (AEST/AEDT)'),
    ('Australia/Melbourne', 'Melbourne (AEST/AEDT)'),
    ('Australia/Brisbane', 'Brisbane (AEST - no DST)'),
    ('Australia/Perth', 'Perth (AWST)'),
    ('Pacific/Auckland', 'Auckland (NZST/NZDT)'),
    ('Pacific/Fiji', 'Fiji (FJT)'),
    # UTC
    ('UTC', 'UTC'),
]


class NotEnoughCredits(Exception):
    """Raised when a user doesn't have enough credits for an operation."""
    pass


def charge_credits(user, credits_to_charge: int):
    """
    Atomically charge credits for a user, enforcing their monthly quota.

    Args:
        user: The Django user object
        credits_to_charge: Number of credits to charge

    Raises:
        NotEnoughCredits: If user doesn't have enough credits
    """
    from .models import UsageAccount

    if user is None or not user.is_authenticated:
        raise NotEnoughCredits("You must be logged in to use AI features.")

    with transaction.atomic():
        usage = UsageAccount.objects.select_for_update().get(user=user)
        usage.ensure_current_period()

        if usage.used_this_period + credits_to_charge > usage.monthly_quota:
            raise NotEnoughCredits(
                f"Not enough AI credits. "
                f"You have {usage.monthly_quota - usage.used_this_period} credits remaining, "
                f"but this operation requires {credits_to_charge}."
            )

        usage.used_this_period = F("used_this_period") + credits_to_charge
        usage.save(update_fields=['used_this_period', 'period_start'])


async def validate_beehiiv_api_key(beehiiv_token: str) -> tuple[bool, list | str]:
    """
    Validate a Beehiiv API key by calling GET /v2/publications.

    Args:
        beehiiv_token: The API token to validate (should include 'Bearer ' prefix)

    Returns:
        Tuple of (is_valid, publications_list_or_error_message)
        - If valid: (True, [{"id": "pub_xxx", "name": "...", "organization_name": "..."}, ...])
        - If invalid: (False, "Error message")
    """
    url = "https://api.beehiiv.com/v2/publications"
    headers = {"Authorization": beehiiv_token}

    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        try:
            async with session.get(url, headers=headers) as response:
                data = await response.json()

                if response.status == 200:
                    publications = [
                        {
                            "id": pub["id"],
                            "name": pub.get("name", "Unnamed Publication"),
                            "organization_name": pub.get("organization_name", "")
                        }
                        for pub in data.get("data", [])
                    ]
                    return (True, publications)
                elif response.status == 401:
                    errors = data.get("errors", [])
                    error_msg = errors[0].get("message", "Invalid API key") if errors else "Invalid API key"
                    return (False, error_msg)
                else:
                    return (False, f"API returned status {response.status}")
        except aiohttp.ClientError as e:
            logger.exception("validate_beehiiv_api_key failed")
            return (False, f"Network error: {str(e)}")
        except Exception as e:
            logger.exception("validate_beehiiv_api_key failed")
            return (False, f"Unexpected error: {str(e)}")


async def fetch_subscriber_count(beehiiv_token: str, beehiiv_pub_id: str) -> int:
    """
    Fetch the active subscriber count for a Beehiiv publication.

    Uses GET /v2/publications/{id}?expand=stats, which returns
    data.stats.active_subscriptions. Returns 0 if the field is missing
    or the request fails (callers should treat 0 as "unknown — don't
    gate processing on recipient totals").
    """
    url = f"https://api.beehiiv.com/v2/publications/{beehiiv_pub_id}?expand=stats"
    headers = {"Authorization": beehiiv_token}

    timeout = aiohttp.ClientTimeout(total=30)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, headers=headers) as response:
                if response.status != 200:
                    logger.error(f"fetch_subscriber_count status: {response.status}")
                    return 0
                data = await response.json()
                pub = data.get('data') or {}
                stats = pub.get('stats') or {}
                return int(stats.get('active_subscriptions') or 0)
    except Exception:
        logger.exception("fetch_subscriber_count failed")
        return 0


async def fetch_publication_stats(beehiiv_token: str, beehiiv_pub_id: str) -> dict:
    """
    Fetch publication-level stats (subscribers, average open/click rate).

    Uses GET /v2/publications/{id}?expand=stats. Returns a dict with keys:
      - 'active_subscriptions' (int or None)
      - 'average_open_rate'    (float in percentage points, e.g. 51.16 for
                                51.16%, or None)
      - 'average_click_rate'   (float in percentage points, or None)

    Each value is None when the field is missing on the response or the
    request fails — callers should render a "—" placeholder rather than
    gating behavior. (Soft-fallback flagged per the project's policy on
    assumptions: a failed stats fetch should not break the page.)
    """
    url = f"https://api.beehiiv.com/v2/publications/{beehiiv_pub_id}?expand=stats"
    headers = {"Authorization": beehiiv_token}

    out = {
        'active_subscriptions': None,
        'average_open_rate': None,
        'average_click_rate': None,
    }

    timeout = aiohttp.ClientTimeout(total=30)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, headers=headers) as response:
                if response.status != 200:
                    logger.error(f"fetch_publication_stats status: {response.status}")
                    return out
                data = await response.json()
                stats = ((data.get('data') or {}).get('stats')) or {}
                # Beehiiv returns each stat as either a number or `false` when
                # not enabled for the publication; coerce non-numeric values
                # to None so the caller can render a placeholder.
                subs = stats.get('active_subscriptions')
                if isinstance(subs, (int, float)) and not isinstance(subs, bool):
                    out['active_subscriptions'] = int(subs)
                for key in ('average_open_rate', 'average_click_rate'):
                    v = stats.get(key)
                    if isinstance(v, (int, float)) and not isinstance(v, bool):
                        out[key] = float(v)
                return out
    except Exception:
        logger.exception("fetch_publication_stats failed")
        return out


PROCESSABLE_PLATFORMS = ('email', 'both')
PROCESSABLE_PUBLISHED_AGE_SECONDS = 48 * 3600
INITIAL_LEARNING_RECIPIENT_MULTIPLIER = 15


def _processable_posts_queryset(user, publication):
    """
    Posts eligible for processing in either Learning/Update flow:
      status = Published, publish_date >= 48h ago, platform in {email, both}
      OR platform is NULL (legacy rows fetched before the platform field
      existed — treat as eligible; they'll be backfilled on next fetch).
    """
    from django.db.models import Q
    from django.utils import timezone as dj_timezone
    from .models import Post

    cutoff = dj_timezone.now() - pd.Timedelta(seconds=PROCESSABLE_PUBLISHED_AGE_SECONDS)
    return (
        Post.objects
        .filter(
            Q(platform__in=PROCESSABLE_PLATFORMS) | Q(platform__isnull=True),
            user=user,
            publication=publication,
            status='Published',
            publish_date__isnull=False,
            publish_date__lte=cutoff,
        )
    )


def select_posts_for_initial_learning(user, publication, subscriber_count):
    """
    Pick the most recent eligible published posts whose cumulative recipients
    reach subscriber_count * INITIAL_LEARNING_RECIPIENT_MULTIPLIER.

    Walks newest-first by publish_date. Returns list[post_id] (Beehiiv IDs).
    If subscriber_count is 0 or no posts accumulate enough recipients, returns
    all eligible posts.
    """
    target = max(0, int(subscriber_count) * INITIAL_LEARNING_RECIPIENT_MULTIPLIER)

    posts = list(
        _processable_posts_queryset(user, publication)
        .order_by('-publish_date')
        .values('post_id', 'recipients')
    )

    if not posts:
        return []

    if target <= 0:
        return [p['post_id'] for p in posts]

    selected = []
    total = 0
    for p in posts:
        selected.append(p['post_id'])
        total += int(p.get('recipients') or 0)
        if total >= target:
            break
    return selected


def select_posts_for_update(user, publication):
    """
    Posts to process during the Updating Your Posts flow: eligible posts that
    haven't been processed yet AND were published more recently than the oldest
    already-processed post (so we don't retroactively backfill older history).
    """
    from .models import ProcessedPost

    eligible = _processable_posts_queryset(user, publication)

    processed_post_pks = ProcessedPost.objects.filter(
        user=user, post__publication=publication,
    ).values_list('post__pk', flat=True)

    eligible_new = eligible.exclude(pk__in=list(processed_post_pks))

    oldest_processed_publish = (
        ProcessedPost.objects
        .filter(user=user, post__publication=publication, post__publish_date__isnull=False)
        .order_by('post__publish_date')
        .values_list('post__publish_date', flat=True)
        .first()
    )

    if oldest_processed_publish is not None:
        eligible_new = eligible_new.filter(publish_date__gt=oldest_processed_publish)

    return list(eligible_new.order_by('-publish_date').values_list('post_id', flat=True))


def wipe_user_publication_data(user, pub_id):
    """
    Atomic cleanup for interrupted "Learning Your Audience" flows: delete every
    Post / ProcessedPost / Section / LinkData row for (user, publication), and
    remove pub_id from UsageAccount.initial_fetched_pub_ids so the user sees the
    onboarding coach again on their next page load.
    """
    from .models import (
        LinkData, Post, Publication, ProcessedPost, Section, UsageAccount,
    )

    with transaction.atomic():
        try:
            publication = Publication.objects.get(pub_id=pub_id)
        except Publication.DoesNotExist:
            publication = None

        base_filter = {'user': user}
        if publication is not None:
            base_filter['publication'] = publication

        LinkData.objects.filter(**base_filter).delete()
        Section.objects.filter(**base_filter).delete()
        if publication is not None:
            ProcessedPost.objects.filter(user=user, post__publication=publication).delete()
            Post.objects.filter(user=user, publication=publication).delete()
        else:
            ProcessedPost.objects.filter(user=user).delete()
            Post.objects.filter(user=user).delete()

        try:
            usage = UsageAccount.objects.get(user=user)
        except UsageAccount.DoesNotExist:
            usage = None
        if usage and pub_id and pub_id in (usage.initial_fetched_pub_ids or []):
            usage.initial_fetched_pub_ids = [
                p for p in (usage.initial_fetched_pub_ids or []) if p != pub_id
            ]
            usage.save(update_fields=['initial_fetched_pub_ids'])


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
    from django.utils import timezone as dj_timezone
    from analytics.llm_tracker import record_call, record_error

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


async def fetch_post_html(session, post_id, semaphore, beehiiv_token, beehiiv_pub_id):
    """
    Fetch HTML content for a single post from Beehiiv API.

    Args:
        session: aiohttp ClientSession
        post_id: The Beehiiv post ID
        semaphore: asyncio.Semaphore to limit concurrent requests
        beehiiv_token: Beehiiv API token
        beehiiv_pub_id: Beehiiv publication ID

    Returns:
        Tuple of (post_id, html_content) or (post_id, None) on error
    """
    if not beehiiv_token or not beehiiv_pub_id:
        logger.error("Missing Beehiiv API credentials")
        return (post_id, None)

    url = f"https://api.beehiiv.com/v2/publications/{beehiiv_pub_id}/posts/{post_id}?expand=free_email_content"
    headers = {"Authorization": beehiiv_token}

    async with semaphore:
        try:
            async with session.get(url, headers=headers) as response:
                if response.status == 200:
                    data = await response.json()
                    html_content = data.get('data', {}).get('content', {}).get('free', {}).get('email', '')
                    logger.debug(f"Successfully fetched HTML for post {post_id}")
                    return (post_id, html_content)
                else:
                    logger.error(f"Error fetching post {post_id}: status {response.status}")
                    return (post_id, None)
        except Exception:
            logger.exception("fetch_post_html failed")
            return (post_id, None)


async def fetch_post_clicks(session, post_id, semaphore, beehiiv_token, beehiiv_pub_id):
    """
    Fetch clicks stats for a single post from Beehiiv API.

    Args:
        session: aiohttp ClientSession
        post_id: The Beehiiv post ID
        semaphore: asyncio.Semaphore to limit concurrent requests
        beehiiv_token: Beehiiv API token
        beehiiv_pub_id: Beehiiv publication ID

    Returns:
        Tuple of (post_id, clicks_dict) or (post_id, None) on error
        clicks_dict maps URLs to their unique click counts
    """
    if not beehiiv_token or not beehiiv_pub_id:
        logger.error("Missing Beehiiv API credentials")
        return (post_id, None)

    url = f"https://api.beehiiv.com/v2/publications/{beehiiv_pub_id}/posts/{post_id}?expand=stats"
    headers = {"Authorization": beehiiv_token}

    async with semaphore:
        try:
            async with session.get(url, headers=headers) as response:
                if response.status == 200:
                    data = await response.json()
                    clicks_data = data.get('data', {}).get('stats', {}).get('clicks', [])

                    # Build clicks dictionary mapping URL to max unique clicks
                    clicks_dict = {}
                    for link_data in clicks_data:
                        url_link = link_data['url']
                        if link_data['email']['unique_clicks'] > 0 and url_link != "https://www.beehiiv.com/":
                            clicks_dict[url_link] = max(
                                link_data['email']['unique_clicks'],
                                clicks_dict.get(url_link, 0)
                            )

                    logger.debug(f"Successfully fetched clicks for post {post_id}")
                    return (post_id, clicks_dict)
                else:
                    logger.error(f"Error fetching clicks for post {post_id}: status {response.status}")
                    return (post_id, None)
        except Exception:
            logger.exception("fetch_post_clicks failed")
            return (post_id, None)


def match_links_with_clicks(html_links_raw, clicks_dict):
    """
    Match click report URLs to HTML links using normalized Levenshtein distance.

    Each click URL is matched to the HTML URL(s) with the smallest normalized
    Levenshtein distance. If multiple HTML URLs share the same minimum distance,
    the click URL's clicks are added to each of them. Each HTML URL's final click
    count is the sum of clicks from all click URLs that include it among their
    closest matches.

    When a processed URL (stripped of &_bhlid= tracking params) appears multiple
    times in the HTML, the click count is averaged across all occurrences, since
    the Beehiiv API cannot distinguish which occurrence was clicked.

    Args:
        html_links_raw: List of raw HTML link hrefs (as-is from the HTML)
        clicks_dict: Dictionary mapping click report URLs to click counts

    Returns:
        Tuple of (link_to_clicks, duplicate_raw_urls) where:
        - link_to_clicks: Dictionary mapping raw HTML links to their click counts
          (averaged for URLs whose processed form appears multiple times)
        - duplicate_raw_urls: Set of raw URLs whose processed form appears more
          than once in the HTML
    """
    if not clicks_dict or not html_links_raw:
        return {}, set()

    # Process HTML links by stripping &_bhlid= and everything after (for matching only)
    html_links_processed = [link.split("&_bhlid=")[0] for link in html_links_raw]

    # Count occurrences of each processed URL
    processed_counts = Counter(html_links_processed)

    # Identify raw URLs whose processed form appears more than once
    duplicate_raw_urls = {
        html_links_raw[i]
        for i in range(len(html_links_raw))
        if processed_counts[html_links_processed[i]] > 1
    }

    # Result: raw HTML link -> click count
    link_to_clicks = {}

    for click_url, click_count in clicks_dict.items():
        # Compute normalized Levenshtein distance to each processed HTML URL
        distances = []
        for processed_url in html_links_processed:
            dist = levenshtein_distance(click_url, processed_url)
            max_len = max(len(click_url), len(processed_url))
            distances.append(dist / max_len if max_len > 0 else 0)

        min_dist = min(distances)

        # Add clicks to every HTML URL tied at the minimum distance
        for i, dist in enumerate(distances):
            if dist == min_dist:
                raw_url = html_links_raw[i]
                link_to_clicks[raw_url] = link_to_clicks.get(raw_url, 0) + click_count

    # Average clicks for URLs whose processed form appears multiple times
    for raw_url in duplicate_raw_urls:
        if raw_url in link_to_clicks:
            processed = raw_url.split("&_bhlid=")[0]
            link_to_clicks[raw_url] /= processed_counts[processed]

    return link_to_clicks, duplicate_raw_urls


def allocate_links_to_sections(section_link_counts, top_n):
    """Divide top_n link slots fairly across sections.

    Each section receives either an equal share or all its links, whichever is
    lesser.  Surplus from capped sections is redistributed iteratively.

    Args:
        section_link_counts: dict mapping section_name -> number of available links.
        top_n: Total link budget to distribute.

    Returns:
        dict mapping section_name -> allocated count.
    """
    if not section_link_counts or top_n <= 0:
        return {name: 0 for name in section_link_counts}

    total_available = sum(section_link_counts.values())
    if top_n >= total_available:
        return dict(section_link_counts)

    allocation = {name: 0 for name in section_link_counts}
    remaining_budget = top_n
    uncapped = {name: count for name, count in section_link_counts.items() if count > 0}

    while remaining_budget > 0 and uncapped:
        equal_share = remaining_budget // len(uncapped)
        if equal_share == 0:
            # Distribute remainder one-by-one to sections with the most links
            for name, _ in sorted(uncapped.items(), key=lambda x: -x[1]):
                if remaining_budget <= 0:
                    break
                allocation[name] += 1
                remaining_budget -= 1
            break

        newly_capped = []
        for name, count in uncapped.items():
            give = min(equal_share, count - allocation[name])
            allocation[name] += give
            remaining_budget -= give
            if allocation[name] >= count:
                newly_capped.append(name)

        for name in newly_capped:
            del uncapped[name]

    return allocation


def select_top_bottom(link_stats, n):
    """Select the top and bottom links by CTR from a sorted list.

    Args:
        link_stats: List of link dicts, sorted by CTR descending.
        n: Number of links to select.

    Returns:
        List of selected link dicts (top ceil(n/2) + bottom floor(n/2)).
    """
    if n >= len(link_stats):
        return list(link_stats)
    if n <= 0:
        return []
    if n == 1:
        return [link_stats[0]]
    top_half = math.ceil(n / 2)
    bottom_half = n - top_half
    return link_stats[:top_half] + link_stats[-bottom_half:]


class LinkDescription(BaseModel):
    tag_id: int
    description: str

class AllLinkDescriptions(BaseModel):
    links: List[LinkDescription]


async def process_post_links(session, post_id, user, beehiiv_token, beehiiv_pub_id,
                             sections, pretty_html):
    """
    Extract, describe, and score clicked links from a single post, grouped by section.

    Click matching is performed at the post level. Links are then assigned to
    sections based on their line position in the prettified HTML. Each section
    receives an allocation from LINK_PROCESS_TOP_N; if a section has more links
    than its allocation, the top and bottom links by CTR are selected.

    Args:
        session: aiohttp.ClientSession
        post_id: Beehiiv post ID
        user: Django user object
        beehiiv_token: Beehiiv API bearer token
        beehiiv_pub_id: Beehiiv publication ID
        sections: List of section dicts from auto_section (name, start_line, end_line, ...)
        pretty_html: The prettified HTML string used by auto_section

    Returns:
        List of dicts with keys: post_id, raw_url, description, section_name,
        rank_in_section, mean_ctr, mean_clicks
    """
    from analytics.models import Post

    post = await asyncio.to_thread(
        lambda: Post.objects.get(post_id=post_id, user=user)
    )
    unique_opens = post.unique_email_opens or 0

    if not sections:
        return []

    # --- Fetch clicks from Beehiiv API ---
    semaphore = asyncio.Semaphore(5)
    _, clicks_dict = await fetch_post_clicks(session, post_id, semaphore, beehiiv_token, beehiiv_pub_id)
    if clicks_dict is None:
        clicks_dict = {}

    # --- Extract links from prettified HTML using BeautifulSoup ---
    soup = BeautifulSoup(pretty_html, 'html.parser')
    html_links = soup.find_all('a', href=True)

    line_links = []  # list of (1-based line number, raw_url)
    for link in html_links:
        line_links.append((link.sourceline, link['href']))

    if not line_links:
        return []

    # --- Match clicks at post level ---
    all_raw_urls = [url for _, url in line_links]
    link_to_clicks, _ = match_links_with_clicks(all_raw_urls, clicks_dict)

    # --- Build section range lookup ---
    section_ranges = []
    for sec in sections:
        section_ranges.append((sec['start_line'], sec['end_line'], sec['name']))

    def find_section(line_num):
        for start, end, name in section_ranges:
            if start <= line_num <= end:
                return name
        return None

    # --- Assign links to sections and deduplicate per section ---
    # Key: (section_name, processed_url) -> {ctrs, clicks}
    section_groups = {}
    for line_num, raw_url in line_links:
        sec_name = find_section(line_num)
        if sec_name is None:
            continue
        processed = raw_url.split("&_bhlid=")[0]
        key = (sec_name, processed)
        clicks = link_to_clicks.get(raw_url, 0)
        ctr = (clicks / unique_opens * 100) if unique_opens > 0 else 0
        if key not in section_groups:
            section_groups[key] = {'ctrs': [], 'clicks': []}
        section_groups[key]['ctrs'].append(ctr)
        section_groups[key]['clicks'].append(clicks)

    # --- Build per-section link stats ---
    # section_name -> sorted list of link stat dicts (CTR desc)
    links_by_section = {}
    for (sec_name, url), data in section_groups.items():
        mean_ctr = sum(data['ctrs']) / len(data['ctrs'])
        mean_clicks = sum(data['clicks']) / len(data['clicks'])
        links_by_section.setdefault(sec_name, []).append({
            'url': url,
            'mean_ctr': mean_ctr,
            'mean_clicks': mean_clicks,
        })

    # Sort each section by CTR descending and assign rank_in_section (all links)
    # Filter to links with clicks > 0
    for sec_name in links_by_section:
        links_by_section[sec_name].sort(key=lambda x: x['mean_ctr'], reverse=True)
        for rank, ls in enumerate(links_by_section[sec_name], start=1):
            ls['rank_in_section'] = rank
        links_by_section[sec_name] = [
            ls for ls in links_by_section[sec_name] if ls['mean_ctr'] > 0
        ]

    # --- Allocate and select ---
    section_link_counts = {
        sec_name: len(stats) for sec_name, stats in links_by_section.items()
    }
    top_n = settings.LINK_PROCESS_TOP_N
    allocation = allocate_links_to_sections(section_link_counts, top_n)

    selected_links = []  # list of (section_name, link_stat_dict)
    for sec_name, stats in links_by_section.items():
        n = allocation.get(sec_name, 0)
        chosen = select_top_bottom(stats, n)
        for ls in chosen:
            selected_links.append((sec_name, ls))

    if not selected_links:
        return []

    # Sort all selected links by CTR descending so the LLM_TAG_N indices line
    # up with global CTR rank when we tag links in the prettified HTML below.
    selected_links.sort(key=lambda x: x[1]['mean_ctr'], reverse=True)

    url_to_tag = {}
    for i, (sec_name, ls) in enumerate(selected_links, start=1):
        url_to_tag[ls['url']] = i

    # Tag links in prettified HTML
    soup_tagged = BeautifulSoup(pretty_html, 'html.parser')
    for link in soup_tagged.find_all('a', href=True):
        processed = link['href'].split("&_bhlid=")[0]
        if processed in url_to_tag:
            link['data-tag'] = f"LINK_TAG_{url_to_tag[processed]}"

    tagged_html = str(soup_tagged)
    n_links = len(selected_links)

    tag_summary = "\n".join(
        f"  LINK_TAG_{i}: URL={ls['url'][:120]}"
        for i, (_, ls) in enumerate(selected_links, start=1)
    )

    messages = [
        {"role": "user", "content": f"""Below is the HTML of a newsletter post. Certain links have been tagged with a data-tag attribute, e.g. <a data-tag="LINK_TAG_1" href="...">.

For each tagged link, give a brief, specific description of what likely appears at that URL based on the surrounding context in the newsletter. Be specific — for example, "The GitHub repo for LangChain, an open-source framework for building AI agents" rather than "A GitHub link".

Some URLs may appear multiple times; these will be given the same tag. In this case, you may use all of their contexts to infer what the URL is.

There are exactly {n_links} tagged links. Return exactly {n_links} descriptions, one per tag ID.

Tagged links:
{tag_summary}

Newsletter HTML:
{tagged_html}"""}
    ]

    max_retries = settings.LINK_PROCESS_MAX_RETRIES
    for attempt in range(1, max_retries + 2):
        response = await llm_call("process_post_links", messages, "gpt-5.4-mini", "low",
                                   response_format=AllLinkDescriptions, user=user)
        parsed = response.output[-1].content[0].parsed
        if len(parsed.links) == n_links:
            break
        if attempt <= max_retries:
            logger.warning(
                f"[{post.title}] Expected {n_links} descriptions, got {len(parsed.links)} — "
                f"retrying (attempt {attempt}/{max_retries})"
            )
        else:
            raise ValueError(
                f"[{post.title}] Expected {n_links} descriptions, got {len(parsed.links)} "
                f"after {max_retries} retries"
            )

    desc_by_tag = {ld.tag_id: ld.description for ld in parsed.links}

    rows = []
    for i, (sec_name, ls) in enumerate(selected_links, start=1):
        rows.append({
            'post_id': post_id,
            'raw_url': ls['url'],
            'description': desc_by_tag.get(i, ''),
            'section_name': sec_name,
            'rank_in_section': ls['rank_in_section'],
            'mean_ctr': round(ls['mean_ctr'], 2),
            'mean_clicks': round(ls['mean_clicks'], 1),
        })
    return rows


# =============================================================================
# Section-based post processing
# =============================================================================

def html_to_text_with_links(html, max_url_len=50):
    """Convert HTML to plain text, placing truncated link URLs inline after link text."""
    soup = BeautifulSoup(html, 'html.parser')
    for a in soup.find_all('a', href=True):
        link_text = a.get_text(strip=True)
        href = a['href']
        if len(href) > max_url_len:
            href = href[:max_url_len - 3] + "..."
        if link_text:
            a.replace_with(f"{link_text} ({href})")
        else:
            a.replace_with(href)
    return soup.get_text(separator='\n', strip=True)


# =============================================================================
# Content Finder
# =============================================================================

def truncate_url(url, max_len):
    """Shorten a URL to max_len characters, adding '...' if truncated."""
    if len(url) <= max_len:
        return url
    return url[:max_len - 3] + "..."


def format_link_history(section, max_links=60, max_url_len=50):
    """
    Build a formatted string of historical link performance for a section_name.
    Queries ALL LinkData matching the section_name for this user/publication,
    ordered by CTR descending. If count exceeds max_links, shows top half and
    bottom half with a truncation note. CTR is shown relative to the section average.

    Returns (formatted_string, total_link_count).
    """
    from analytics.models import LinkData

    links = LinkData.objects.filter(
        user=section.user,
        publication=section.publication,
        section_name=section.section_name,
    ).order_by('-mean_ctr')

    total = links.count()
    if total == 0:
        return "", 0

    all_links = list(links)
    avg_ctr = sum(l.mean_ctr for l in all_links) / total

    if total > max_links:
        half = max_links // 2
        selected = all_links[:half] + all_links[-half:]
        truncation_note = (
            f"Note: Only the top {half} and bottom {half} links by CTR are shown "
            f"({total - max_links} middle links omitted).\n\n"
        )
    else:
        selected = all_links
        truncation_note = ""

    lines = [truncation_note] if truncation_note else []
    for i, link in enumerate(selected, 1):
        if avg_ctr > 0:
            relative = link.mean_ctr / avg_ctr
            rel_str = f"{relative:.1f}x avg"
        else:
            rel_str = "N/A"
        lines.append(
            f"{i}. [{rel_str}] {link.description}\n"
            f"   URL: {truncate_url(link.raw_url, max_url_len)}"
        )

    return "\n".join(lines), total


def perplexity_search(queries, max_results=10, domains=None, max_days_ago=None, historical_urls=None):
    """Call the Perplexity search API with one or more queries and return results as a formatted string.

    If historical_urls is provided, any result whose URL is a substring of
    any historical URL is silently excluded from the output.
    """
    from perplexity import Perplexity
    from datetime import date, timedelta

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


def _serialize_response_output(response):
    """Serialize response.output items to JSON-safe dicts so they can be appended
    to a growing input message list on subsequent LLM calls. Each successive agent
    (plan → dispatch → search) carries forward the full conversation verbatim.

    responses.parse() returns ParsedResponseOutputText content items that carry a
    SDK-local `parsed` field alongside `text`. The API input schema doesn't accept
    it and serializing it against the ResponseOutputText|Refusal union triggers
    noisy Pydantic warnings, so we suppress those warnings during the dump and
    strip `parsed` from each content item before returning.
    """
    import warnings as _warnings

    serialized = []
    for item in response.output:
        try:
            with _warnings.catch_warnings():
                _warnings.filterwarnings(
                    'ignore',
                    message=r'Pydantic serializer warnings:',
                    category=UserWarning,
                )
                data = item.model_dump(mode='json', exclude_none=True)
        except AttributeError:
            data = dict(item)

        for content_piece in (data.get('content') or []):
            if isinstance(content_piece, dict):
                content_piece.pop('parsed', None)

        serialized.append(data)
    return serialized


def _extract_plan_text(response):
    text = ""
    try:
        text = response.output_text or ""
    except AttributeError:
        pass
    if text:
        return text
    for item in response.output:
        if getattr(item, "type", None) == "message":
            for part in getattr(item, "content", []) or []:
                t = getattr(part, "text", None)
                if t:
                    text += t
    return text


async def run_plan_stage(task, sections):
    """Stage 1: single LLM call that sees all sections and drafts a search plan.

    Returns (plan_text, plan_messages) where plan_messages is the full list
    [system(PLAN_PROMPT), user(user_prompt), <plan response output items>].
    """
    from asgiref.sync import sync_to_async

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

    plan_text = _extract_plan_text(response)
    plan_messages = messages + _serialize_response_output(response)
    return plan_text, plan_messages


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

    dispatch_sections = []
    try:
        parsed = response.output[-1].content[0].parsed
        dispatch_sections = list(parsed.sections)
    except (AttributeError, IndexError):
        dispatch_sections = []

    dispatch_sections = dispatch_sections[:settings.CONTENT_FINDER_DISPATCH_MAX_SECTIONS]
    dispatch_messages = messages + _serialize_response_output(response)
    return dispatch_sections, dispatch_messages


async def run_search_agent(section_name, dispatch_messages, historical_urls, task_id, max_rounds=3):
    """
    Per-section search agent. Starts from the full dispatch_messages list,
    appends user(SEARCH_PROMPT), runs `max_rounds` of tool-call turns (each
    appending the assistant output + tool outputs), then appends user(OUTPUT_PROMPT)
    and emits the final structured output.

    Returns (section_name, parsed_links_or_empty_list).
    """
    from analytics.llm_tracker import set_additional_info
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

        # Carry the assistant output (including any reasoning items) forward.
        messages = messages + _serialize_response_output(response)

        tool_calls = [item for item in response.output if item.type == "function_call"]
        if not tool_calls:
            break

        for call in tool_calls:
            args = json.loads(call.arguments)
            queries = args["queries"]
            domains = args.get("domains") or None
            max_days_ago = args.get("max_days_ago") or None

            result = perplexity_search(
                queries, domains=domains, max_days_ago=max_days_ago,
                historical_urls=historical_urls,
            )
            messages.append({
                "type": "function_call_output",
                "call_id": call.call_id,
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

    try:
        parsed = response.output[-1].content[0].parsed
        links = [link.model_dump() for link in parsed.links]
    except (AttributeError, IndexError):
        links = []

    return (section_name, links)


async def run_all_searches(task):
    """Stage 3: fan out parallel search agents, one per entry in task.dispatch_sections."""
    from asgiref.sync import sync_to_async
    from analytics.models import LinkData, ContentSearchFeedback

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


def run_content_finder_background(task_id):
    """
    Background thread entry point for running content finder.
    Branches on task.status: 'planning' runs Stage 1 (exits awaiting user feedback);
    'dispatching' runs Stage 2 + Stage 3.
    """
    from asgiref.sync import async_to_sync
    from django.db import connection, close_old_connections
    from analytics.models import PendingContentSearch, Section
    from analytics.llm_tracker import start_tracking, seed_tracking, finish_tracking, set_llm_context

    task = None
    try:
        task = PendingContentSearch.objects.get(task_id=task_id)
        initial_status = task.status

        set_llm_context(
            user_id=task.user_id,
            publication_id=task.publication_id,
            task_id=task.task_id,
            task_kind='content_finder',
        )

        if initial_status == 'planning':
            if settings.ENVIRONMENT == 'local':
                start_tracking()

            sections = list(
                Section.objects.filter(post=task.post, user=task.user).order_by('start_line')
            )
            if not sections:
                task.status = 'complete'
                task.result_data = []
                task.save(update_fields=['status', 'result_data'])
                return

            plan_text, plan_messages = async_to_sync(run_plan_stage)(task, sections)
            close_old_connections()

            task.plan_text = plan_text
            task.plan_messages = plan_messages
            task.status = 'awaiting_feedback'
            if settings.ENVIRONMENT == 'local':
                task.dev_panel_data = finish_tracking() or {}
                task.save(update_fields=['plan_text', 'plan_messages', 'status', 'dev_panel_data'])
            else:
                task.save(update_fields=['plan_text', 'plan_messages', 'status'])
            return

        if initial_status == 'dispatching':
            # Resume dev-panel accumulation from what Stage 1 recorded so the
            # final panel shows plan + dispatch + all parallel searches together.
            if settings.ENVIRONMENT == 'local':
                seed_tracking(task.dev_panel_data)

            dispatch_sections, dispatch_messages = async_to_sync(run_dispatch_stage)(task)
            close_old_connections()

            task.dispatch_sections = dispatch_sections
            task.dispatch_messages = dispatch_messages
            task.status = 'searching'
            task.save(update_fields=['dispatch_sections', 'dispatch_messages', 'status'])

            if not dispatch_sections:
                task.status = 'complete'
                task.result_data = []
                if settings.ENVIRONMENT == 'local':
                    task.dev_panel_data = finish_tracking() or task.dev_panel_data
                    task.save(update_fields=['status', 'result_data', 'dev_panel_data'])
                else:
                    task.save(update_fields=['status', 'result_data'])
                return

            raw_results = async_to_sync(run_all_searches)(task)
            close_old_connections()

            # Preserve dispatch output order. JSONB doesn't guarantee dict key
            # order, so we store an explicit list keyed by `section` instead of
            # a {section_name: links} dict.
            links_by_section = {name: links for name, links in raw_results if links is not None}
            result_data = [
                {'section': name, 'links': links_by_section[name]}
                for name in dispatch_sections
                if name in links_by_section
            ]

            task.status = 'complete'
            task.result_data = result_data
            if settings.ENVIRONMENT == 'local':
                task.dev_panel_data = finish_tracking() or task.dev_panel_data
                task.save(update_fields=['status', 'result_data', 'dev_panel_data'])
            else:
                task.save(update_fields=['status', 'result_data'])

            from analytics.models import Feedback
            Feedback.objects.get_or_create(
                user=task.user, feature='used_content_finder',
                defaults={'response': 'completed'}
            )
            return

        # Any other status shouldn't reach the thread; no-op defensively.
        return

    except Exception as e:
        logger.exception("Content finder background task failed")
        try:
            close_old_connections()
            t = PendingContentSearch.objects.get(task_id=task_id)
            t.status = 'error'
            t.error_message = str(e)
            t.save(update_fields=['status', 'error_message'])
        except Exception:
            logger.exception("Failed to save error status for content finder task")
    finally:
        connection.close()


def build_sections_desc(user, publication, post, n_examples=5):
    """Build a human-readable sections description string for the LLM prompt.

    Uses the N posts whose publish_date is closest to the target post as
    "typical" examples for each known section name.

    Args:
        user: Django User object.
        publication: Publication model instance.
        post: Target Post model instance.
        n_examples: Number of closest posts to use as examples.

    Returns:
        A formatted string describing each section with typical descriptions,
        line positions, and first/last HTML lines from nearby posts.
    """
    from analytics.models import Section as SectionModel

    target_date = post.publish_date
    if not target_date:
        return ""

    # All sections for this user/publication, excluding the target post
    sections_qs = SectionModel.objects.filter(
        user=user, publication=publication
    ).exclude(post=post).select_related('post')

    # Group by section_name
    by_name = {}
    for sec in sections_qs:
        if not sec.post.publish_date:
            continue
        by_name.setdefault(sec.section_name, []).append(sec)

    if not by_name:
        return ""

    # Find the 10 closest posts (by publish_date proximity) to determine frequency threshold
    nearby_post_ids = set()
    all_sections_by_proximity = sorted(
        [sec for secs in by_name.values() for sec in secs],
        key=lambda s: abs((s.post.publish_date - target_date).total_seconds()),
    )
    for sec in all_sections_by_proximity:
        nearby_post_ids.add(sec.post_id)
        if len(nearby_post_ids) >= 10:
            break

    n_nearby = len(nearby_post_ids)
    min_appearances = max(1, math.ceil(n_nearby * 0.15))

    # Only include sections that appear in at least 15% of recent posts
    filtered_by_name = {}
    for section_name, rows in by_name.items():
        posts_with_section = {r.post_id for r in rows} & nearby_post_ids
        if len(posts_with_section) >= min_appearances:
            filtered_by_name[section_name] = rows

    if not filtered_by_name:
        return ""

    output_parts = []

    for section_name, rows in sorted(filtered_by_name.items()):
        # Sort by temporal proximity to target post
        rows_sorted = sorted(
            rows,
            key=lambda r: abs((r.post.publish_date - target_date).total_seconds()),
        )
        examples = rows_sorted[:n_examples]

        MAX_CHARS = 500
        example_parts = []
        for i, ex in enumerate(examples):
            total = ex.post_html_length
            start_pct = round(ex.start_line / total * 100) if total else 0
            end_pct = round(ex.end_line / total * 100) if total else 0

            html_lines = ex.section_html.splitlines() if ex.section_html else []
            first_line = html_lines[0].strip() if html_lines else ''
            last_line = html_lines[-1].strip() if html_lines else ''

            full_text = html_to_text_with_links(ex.section_html)
            if len(full_text) > MAX_CHARS:
                half = MAX_CHARS // 2
                full_text = full_text[:half] + " [...] " + full_text[-half:]

            example_parts.append(
                f'<lines={ex.start_line}-{ex.end_line} '
                f'(relative position in HTML={start_pct}%-{end_pct}%)>\n'
                f'<first_html_line>{first_line}</first_html_line>\n'
                f'<last_html_line>{last_line}</last_html_line>\n'
                f'<text_content>{full_text}</text_content>\n'
                f'</example>'
            )

        part = (
            f'<section name="{section_name}">\n'
            + "\n".join(example_parts) + "\n"
            f'</section>'
        )
        output_parts.append(part)

    return "\n".join(output_parts)


class SectionItem(BaseModel):
    name: str
    title: Optional[str]
    start_line: int
    end_line: int


class AllSections(BaseModel):
    sections: List[SectionItem]


async def auto_section(html, user, publication, post, n_examples=5, pretty_html=None):
    """Identify sections in newsletter HTML via a single structured-output LLM call.

    Args:
        html: The newsletter HTML string (raw, not line-numbered).
        user: Django User object.
        publication: Publication model instance.
        post: Target Post model instance.
        n_examples: Number of nearby-post examples per section.
        pretty_html: Optional pre-prettified HTML. If provided, skips internal
            prettify to avoid double-prettifying when the caller already has it.

    Returns:
        List of section dicts with keys: name, title, description,
        start_line, end_line, section_html, post_html_length.
    """
    from asgiref.sync import sync_to_async
    from analytics.prompts import AUTO_SECTION_PROMPT

    if pretty_html is None:
        pretty_html = BeautifulSoup(html, 'html.parser').prettify()
    html_lines = pretty_html.split('\n')
    post_html_length = len(html_lines)
    numbered_html = "\n".join(f"{i+1}: {line}" for i, line in enumerate(html_lines))

    sections_prompt = await sync_to_async(build_sections_desc)(
        user, publication, post, n_examples
    )

    system_content = AUTO_SECTION_PROMPT
    if sections_prompt:
        system_content += f"\nSECTIONS FROM NEARBY POSTS\n{sections_prompt}"
    else:
        system_content += "\nNo other issues processed yet."

    input_messages = [
        {
            "role": "system",
            "content": system_content,
        },
        {
            "role": "user",
            "content": numbered_html,
        }
    ]

    response = await llm_call(
        "auto_section", input_messages, "gpt-5.4", "low",
        response_format=AllSections, user=user
    )

    parsed = response.output_parsed

    # Enrich each section with section_html and post_html_length
    results = []
    for sec in parsed.sections:
        start = max(1, sec.start_line)
        end = min(post_html_length, sec.end_line)
        section_html = "\n".join(html_lines[start - 1:end])
        results.append({
            "name": sec.name,
            "title": sec.title,
            "start_line": start,
            "end_line": end,
            "section_html": section_html,
            "post_html_length": post_html_length,
        })

    return results


async def process_post_full(session, post_id, user, beehiiv_token, beehiiv_pub_id, publication):
    """Fetch HTML, extract sections and links for a single post.

    Runs auto_section then process_post_links using the same prettified HTML.
    If either step fails, the exception propagates and nothing is saved.

    Args:
        session: aiohttp ClientSession.
        post_id: Beehiiv post ID.
        user: Django User object.
        beehiiv_token: Beehiiv API bearer token.
        beehiiv_pub_id: Beehiiv publication ID.
        publication: Publication model instance.

    Returns:
        Tuple of (sections, link_rows).
    """
    from asgiref.sync import sync_to_async
    from analytics.models import Post

    semaphore = asyncio.Semaphore(5)
    _, html = await fetch_post_html(session, post_id, semaphore, beehiiv_token, beehiiv_pub_id)

    if not html:
        raise RuntimeError(f"No HTML fetched for post {post_id}")

    pretty_html = BeautifulSoup(html, 'html.parser').prettify()
    post = await sync_to_async(Post.objects.get)(post_id=post_id, user=user)

    sections = await auto_section(html, user, publication, post, pretty_html=pretty_html)
    link_rows = await process_post_links(
        session, post_id, user, beehiiv_token, beehiiv_pub_id, sections, pretty_html
    )

    return sections, link_rows


async def _save_post_full(post_id, sections, link_rows, user, publication):
    """Save section and link results to DB atomically and mark post as processed."""
    from asgiref.sync import sync_to_async
    from django.db import transaction
    from analytics.models import Post, Section as SectionModel, LinkData, ProcessedPost

    post = await sync_to_async(Post.objects.get)(post_id=post_id, user=user)

    def _save_in_transaction():
        with transaction.atomic():
            SectionModel.objects.filter(post=post, user=user).delete()
            LinkData.objects.filter(post=post, user=user).delete()

            if sections:
                seen_names = set()
                deduped = []
                for s in sections:
                    if s['name'] not in seen_names:
                        seen_names.add(s['name'])
                        deduped.append(s)

                SectionModel.objects.bulk_create([
                    SectionModel(
                        post=post,
                        user=user,
                        publication=publication,
                        section_name=s['name'],
                        section_title=s.get('title'),
                        start_line=s['start_line'],
                        end_line=s['end_line'],
                        post_html_length=s['post_html_length'],
                        section_html=s['section_html'],
                    )
                    for s in deduped
                ])

            if link_rows:
                LinkData.objects.bulk_create([
                    LinkData(
                        post=post,
                        user=user,
                        publication=publication,
                        raw_url=row['raw_url'],
                        description=row['description'],
                        section_name=row['section_name'],
                        rank_in_section=row['rank_in_section'],
                        mean_ctr=row['mean_ctr'],
                        mean_clicks=row['mean_clicks'],
                    )
                    for row in link_rows
                ])

            ProcessedPost.objects.update_or_create(
                post=post,
                user=user,
                defaults={'publication': publication}
            )

    await sync_to_async(_save_in_transaction)()


async def process_posts_sections_sequential(post_ids, user, beehiiv_token, beehiiv_pub_id, publication):
    """Process posts for section and link extraction with hybrid sequential/parallel strategy.

    To seed build_sections_desc with context, the first posts are processed
    sequentially until at least 2 total posts have been processed for this
    user/publication. Remaining posts run in parallel (semaphore=3).

    Both auto_section and process_post_links must succeed for a post to be
    saved; failure in either causes the entire post to be skipped.

    Args:
        post_ids: List of Beehiiv post IDs.
        user: Django User object.
        beehiiv_token: Beehiiv API bearer token.
        beehiiv_pub_id: Beehiiv publication ID.
        publication: Publication model instance.

    Returns:
        Dict mapping post_id to list of section dicts.
    """
    from asgiref.sync import sync_to_async
    from analytics.models import Post, Section as SectionModel

    results_by_post = {}

    # Count posts with sections that WON'T be touched by this run.
    # _save_post_full deletes existing sections for each post in the batch,
    # so only sections from OTHER posts provide reliable context.
    batch_post_pks = await sync_to_async(
        lambda: list(Post.objects.filter(
            post_id__in=post_ids, user=user
        ).values_list('pk', flat=True))
    )()

    stable_context_count = await sync_to_async(
        lambda: SectionModel.objects.filter(
            user=user, publication=publication
        ).exclude(
            post_id__in=batch_post_pks
        ).values('post').distinct().count()
    )()

    # We need at least 2 posts with stable section context before parallelizing.
    sequential_needed = max(0, 2 - stable_context_count)
    sequential_needed = min(sequential_needed, len(post_ids))
    sequential_ids = post_ids[:sequential_needed]
    parallel_ids = post_ids[sequential_needed:]

    async with aiohttp.ClientSession() as session:
        # --- Sequential phase ---
        for post_id in sequential_ids:
            try:
                sections, link_rows = await process_post_full(
                    session, post_id, user, beehiiv_token, beehiiv_pub_id, publication
                )
                await _save_post_full(post_id, sections, link_rows, user, publication)
                results_by_post[post_id] = sections
            except Exception as e:
                logger.error(f"Error processing post {post_id}: {e}", exc_info=True)

        # --- Parallel phase ---
        if parallel_ids:
            semaphore = asyncio.Semaphore(3)

            async def _process_one(pid):
                async with semaphore:
                    sections, link_rows = await process_post_full(
                        session, pid, user, beehiiv_token, beehiiv_pub_id, publication
                    )
                    await _save_post_full(pid, sections, link_rows, user, publication)
                    return pid, sections

            tasks = [asyncio.create_task(_process_one(pid)) for pid in parallel_ids]
            for coro in asyncio.as_completed(tasks):
                try:
                    pid, sections = await coro
                    results_by_post[pid] = sections
                except Exception as e:
                    logger.error(f"Error processing post in parallel: {e}", exc_info=True)

    return results_by_post


async def fetch_posts_page(session, page, pagination_size, semaphore, beehiiv_token, beehiiv_pub_id):
    """
    Fetch a single page of posts from Beehiiv API.

    Args:
        session: aiohttp ClientSession
        page: Page number to fetch
        pagination_size: Number of posts per page
        semaphore: asyncio.Semaphore to limit concurrent requests
        beehiiv_token: Beehiiv API token
        beehiiv_pub_id: Beehiiv publication ID

    Returns:
        List of post data dictionaries
    """
    url = f"https://api.beehiiv.com/v2/publications/{beehiiv_pub_id}/posts?expand=stats&status=all&limit={pagination_size}&page={page}"
    headers = {"Authorization": beehiiv_token}

    async with semaphore:
        try:
            async with session.get(url, headers=headers) as response:
                logger.debug(f"Page {page} status code: {response.status}")
                data = await response.json()
                return data.get('data', [])
        except Exception:
            logger.exception("fetch_posts_page failed")
            raise


async def fetch_all_posts(beehiiv_token, beehiiv_pub_id):
    """
    Fetch all posts from Beehiiv API in parallel with pagination.

    Args:
        beehiiv_token: Beehiiv API token
        beehiiv_pub_id: Beehiiv publication ID

    Returns:
        List of all post data dictionaries
    """
    pagination_size = 10
    posts_list = []
    semaphore = asyncio.Semaphore(10)

    try:
        timeout = aiohttp.ClientTimeout(total=60)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            page = 1
            while True:
                tasks = [fetch_posts_page(session, p, pagination_size, semaphore, beehiiv_token, beehiiv_pub_id) for p in range(page, page + 5)]
                results = await asyncio.gather(*tasks)
                all_empty = True
                for posts_data in results:
                    if posts_data:
                        all_empty = False
                        posts_list.extend(posts_data)
                    if len(posts_data) < pagination_size:
                        return posts_list
                if all_empty:
                    break
                page += 5
    except Exception:
        logger.exception("fetch_all_posts failed")
        raise

    return posts_list


def process_posts_data(posts_list):
    """
    Process raw posts data from API into DataFrame.

    Args:
        posts_list: List of post dictionaries from API

    Returns:
        pandas DataFrame of post data
    """
    posts = {
        'id': [],
        'title': [],
        'status': [],
        'created': [],
        'publish_date': [],
        'web_url': [],
        'platform': [],
        'recipients': [],
        'unique_email_opens': [],
    }

    email_keys = {
        'recipients': 'recipients',
        'unique_email_opens': 'unique_opens',
    }

    clicks = {}

    for post in posts_list:
        for key in posts.keys():
            if key not in email_keys:
                posts[key].append(post.get(key))
            else:
                posts[key].append(post.get('stats', {}).get('email', {}).get(email_keys[key], 0))

        clicks[post['title']] = post.get('stats', {}).get('clicks', [])

    posts_df = pd.DataFrame(posts)

    # Convert creation date (always present) - keep in UTC
    posts_df['creation_date'] = pd.to_datetime(posts_df['created'], unit='s', utc=True)

    # Convert publish_date to datetime for status check
    posts_df['publish_date_dt'] = pd.to_datetime(posts_df['publish_date'], unit='s', utc=True, errors='coerce')

    # Convert API status to simplified status: "Draft", "Scheduled", or "Published"
    # Beehiiv uses "confirmed" for both published and scheduled posts
    import datetime
    now = datetime.datetime.now(datetime.timezone.utc)

    def get_display_status(row):
        if row['status'] == 'draft':
            return "Draft"
        elif pd.notna(row['publish_date_dt']) and row['publish_date_dt'] > now:
            return "Scheduled"
        else:
            return "Published"

    posts_df['status'] = posts_df.apply(get_display_status, axis=1)
    posts_df = posts_df.drop(columns=['publish_date_dt'])

    # Rename raw publish_date to avoid conflict, then create datetime field
    posts_df = posts_df.rename(columns={'publish_date': 'publish_date_raw'})

    # Convert publish_date - keep in UTC (handle drafts which have no publish_date)
    posts_df['publish_date'] = pd.to_datetime(posts_df['publish_date_raw'], unit='s', utc=True, errors='coerce')

    posts_df = posts_df.drop(columns=['created', 'publish_date_raw'])

    return posts_df


def save_posts_to_db(posts_df, user, publication):
    """
    Upsert a posts DataFrame (output of process_posts_data) into the Post table
    for (user, publication). Returns (created_count, updated_count).
    """
    from .models import Post

    created_count = 0
    updated_count = 0

    for _, row in posts_df.iterrows():
        publish_date = row.get('publish_date')
        if pd.isna(publish_date):
            publish_date = None

        _, created = Post.objects.update_or_create(
            post_id=row['id'],
            user=user,
            defaults={
                'publication': publication,
                'title': row['title'],
                'status': row.get('status', 'Published'),
                'platform': row.get('platform') or None,
                'creation_date': row.get('creation_date'),
                'publish_date': publish_date,
                'recipients': row.get('recipients', 0),
                'unique_email_opens': row.get('unique_email_opens', 0),
            },
        )

        if created:
            created_count += 1
        else:
            updated_count += 1

    return created_count, updated_count


async def refresh_posts_data(beehiiv_token, beehiiv_pub_id):
    """
    Fetch all posts from Beehiiv API.

    Args:
        beehiiv_token: Beehiiv API token
        beehiiv_pub_id: Beehiiv publication ID

    Returns:
        Tuple of (posts_df, success_message) or (None, error_message)
    """
    try:
        # Fetch posts
        posts_list = await fetch_all_posts(beehiiv_token, beehiiv_pub_id)

        if not posts_list:
            return None, "No posts were fetched from the API."

        # Process the data
        posts_df = process_posts_data(posts_list)

        return posts_df, f"Successfully fetched and processed {len(posts_df)} posts."

    except Exception as e:
        logger.exception("refresh_posts_data failed")
        return None, f"Error refreshing posts: {str(e)}"


INCREMENTAL_FETCH_PAGE_SIZE = 5
INCREMENTAL_FETCH_PUBLISH_AGE_SECONDS = 72 * 3600
INCREMENTAL_FETCH_PREFETCH_WINDOW = 1


async def _fetch_incremental_track(
    session,
    beehiiv_token,
    beehiiv_pub_id,
    existing_post_ids,
    order_by,
    apply_publish_age_check,
    prefetch_window=INCREMENTAL_FETCH_PREFETCH_WINDOW,
):
    """
    Paginate posts from Beehiiv newest -> oldest for a single sort order.

    Up to `prefetch_window` pages are fetched in parallel. Pages are still
    evaluated in order so stop semantics match the serial version — when a
    page triggers the stop condition, any speculatively in-flight later
    pages are cancelled.

    Stops when the current batch contains a post we already have locally.
    If apply_publish_age_check is True (Track A), additionally requires the
    oldest post in the batch to be at least 72h old before stopping.
    """
    from datetime import timezone as dt_timezone

    fetched = []
    headers = {"Authorization": beehiiv_token}

    def build_url(page):
        return (
            f"https://api.beehiiv.com/v2/publications/{beehiiv_pub_id}/posts"
            f"?expand=stats&status=all&order_by={order_by}&direction=desc"
            f"&limit={INCREMENTAL_FETCH_PAGE_SIZE}&page={page}"
        )

    async def fetch_page(page):
        try:
            async with session.get(build_url(page), headers=headers) as response:
                if response.status != 200:
                    logger.error(
                        f"_fetch_incremental_track ({order_by}) page {page} status: {response.status}"
                    )
                    return None
                data = await response.json()
                return data.get('data', [])
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                f"_fetch_incremental_track ({order_by}) page {page} exception"
            )
            return None

    async def cancel_pending(tasks):
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    in_flight = {}
    next_page_to_launch = 1

    for _ in range(prefetch_window):
        in_flight[next_page_to_launch] = asyncio.create_task(fetch_page(next_page_to_launch))
        next_page_to_launch += 1

    try:
        while in_flight:
            current_page = min(in_flight.keys())
            task = in_flight.pop(current_page)
            page_posts = await task

            if page_posts is None:
                await cancel_pending(list(in_flight.values()))
                in_flight.clear()
                break

            if not page_posts:
                await cancel_pending(list(in_flight.values()))
                in_flight.clear()
                break

            fetched.extend(page_posts)

            should_stop = False
            any_known = any(p.get('id') in existing_post_ids for p in page_posts)
            if any_known:
                if not apply_publish_age_check:
                    should_stop = True
                else:
                    publish_timestamps = [
                        p.get('publish_date') for p in page_posts if p.get('publish_date')
                    ]
                    if publish_timestamps:
                        oldest_ts = min(publish_timestamps)
                        oldest_dt = datetime.fromtimestamp(oldest_ts, tz=dt_timezone.utc)
                        age_seconds = (datetime.now(tz=dt_timezone.utc) - oldest_dt).total_seconds()
                        if age_seconds >= INCREMENTAL_FETCH_PUBLISH_AGE_SECONDS:
                            should_stop = True
                    else:
                        # No publish_date on any post in batch (all drafts) — treat as old enough
                        should_stop = True

            if len(page_posts) < INCREMENTAL_FETCH_PAGE_SIZE:
                should_stop = True

            if should_stop:
                await cancel_pending(list(in_flight.values()))
                in_flight.clear()
                break

            in_flight[next_page_to_launch] = asyncio.create_task(fetch_page(next_page_to_launch))
            next_page_to_launch += 1
    finally:
        if in_flight:
            await cancel_pending(list(in_flight.values()))

    return fetched


async def incremental_fetch_posts(beehiiv_token, beehiiv_pub_id, existing_post_ids):
    """
    Run two parallel fetch tracks against Beehiiv to pick up recent changes:
      - Track A: ordered by publish_date desc, stops once any batch item is
                 already known AND its oldest post is >= 72h old.
      - Track B: ordered by created (creation_date) desc, stops once any
                 batch item is already known.

    Both tracks share a single aiohttp session so requests are pooled. Results
    are deduplicated by post id before being returned.
    """
    existing_post_ids = set(existing_post_ids or [])

    timeout = aiohttp.ClientTimeout(total=120)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        track_a, track_b = await asyncio.gather(
            _fetch_incremental_track(
                session, beehiiv_token, beehiiv_pub_id, existing_post_ids,
                order_by='publish_date', apply_publish_age_check=True,
            ),
            _fetch_incremental_track(
                session, beehiiv_token, beehiiv_pub_id, existing_post_ids,
                order_by='created', apply_publish_age_check=False,
            ),
        )

    by_id = {}
    for post in track_a + track_b:
        pid = post.get('id')
        if pid:
            by_id[pid] = post
    return list(by_id.values())


async def incremental_refresh_posts_data(beehiiv_token, beehiiv_pub_id, existing_post_ids):
    """
    Run the incremental two-track fetch and process results into a DataFrame.

    Returns (posts_df, message). posts_df may be empty if no posts were
    fetched. Returns (None, error_message) on failure.
    """
    try:
        posts_list = await incremental_fetch_posts(
            beehiiv_token, beehiiv_pub_id, existing_post_ids
        )

        if not posts_list:
            return pd.DataFrame(), "No new posts found."

        posts_df = process_posts_data(posts_list)
        return posts_df, f"Fetched {len(posts_df)} post(s) for incremental update."

    except Exception as e:
        logger.exception("incremental_refresh_posts_data failed")
        return None, f"Error refreshing posts: {str(e)}"


# =============================================================================
# Improvement Tips
# =============================================================================


class ProofreadingTip(BaseModel):
    start_line: int
    end_line: int
    suggestion: str

class ContentTip(BaseModel):
    start_line: int
    end_line: int
    suggestion: str
    old_text: str
    new_text: str
    why: str

class AllImprovementTips(BaseModel):
    proofreading_tips: List[ProofreadingTip]
    content_tips: List[ContentTip]


_INLINE_TAGS_FOR_ANCHOR = frozenset({
    'a', 'span', 'strong', 'em', 'b', 'i', 'u', 'code', 'kbd', 'mark',
    'small', 'sub', 'sup', 'abbr', 'cite', 'q', 'time', 'img', 'br',
    'wbr', 's', 'del', 'ins', 'font', 'label',
})


def _insert_tip_anchor(soup, line, anchor_tag):
    """Insert an inline anchor tag at ~the given prettified sourceline.

    For block elements, the anchor is placed at the start of the block's text
    flow so the block renders as a single paragraph. For inline elements, the
    anchor is placed immediately before them.
    """
    best = None
    best_line = -1
    for el in soup.find_all(True):
        sl = el.sourceline
        if sl is None or sl > line:
            continue
        if sl > best_line:
            best = el
            best_line = sl
    if best is None:
        (soup.body or soup).append(anchor_tag)
        return
    if best.name in _INLINE_TAGS_FOR_ANCHOR:
        best.insert_before(anchor_tag)
        return
    first_text = next(
        (c for c in best.descendants
         if isinstance(c, NavigableString) and c.strip()),
        None,
    )
    if first_text is not None:
        first_text.insert_before(anchor_tag)
    else:
        best.insert(0, anchor_tag)


def _render_new_text_with_diff(old_text: str, new_text: str) -> str:
    """HTML-escape new_text and wrap word-level insertions/replacements vs old_text in <mark class="diff-new">."""
    new_tokens = re.split(r'(\s+)', new_text)
    if not old_text:
        return html_module.escape(new_text)
    old_tokens = re.split(r'(\s+)', old_text)
    matcher = difflib.SequenceMatcher(a=old_tokens, b=new_tokens, autojunk=False)
    parts = []
    for tag, _i1, _i2, j1, j2 in matcher.get_opcodes():
        segment = ''.join(new_tokens[j1:j2])
        if not segment:
            continue
        escaped = html_module.escape(segment)
        if tag == 'equal':
            parts.append(escaped)
        elif tag in ('replace', 'insert'):
            parts.append(f'<mark class="diff-new">{escaped}</mark>')
    return ''.join(parts)


async def generate_improvement_tips_html(post, user, publication, beehiiv_token, beehiiv_pub_id):
    """
    Generate annotated two-column HTML with improvement tips for a post.

    Fetches post HTML from Beehiiv, builds numbered text with HTML line mapping,
    gathers link history context, calls LLM for tips, and renders a two-column
    layout with tip cards connected to their target content via SVG connectors.

    Args:
        post: Post model instance
        user: Django User instance
        publication: Publication model instance
        beehiiv_token: Beehiiv API token
        beehiiv_pub_id: Beehiiv publication ID

    Returns:
        Complete annotated HTML string
    """
    from analytics.models import Section

    # --- 1) Fetch post HTML from Beehiiv API ---
    sem = asyncio.Semaphore(5)
    async with aiohttp.ClientSession() as session:
        _, html = await fetch_post_html(session, post.post_id, sem, beehiiv_token, beehiiv_pub_id)
    if not html:
        raise RuntimeError(f"Failed to fetch HTML for post {post.post_id}")

    # --- 2) Build numbered HTML for the LLM ---
    pretty_html = BeautifulSoup(html, 'html.parser').prettify()
    html_lines = pretty_html.split('\n')
    post_html_length = len(html_lines)
    numbered_html = "\n".join(f"{i+1}: {line}" for i, line in enumerate(html_lines))

    # --- 3) Link history with sample titles for every section ---
    def _build_link_history():
        all_section_names = list(
            Section.objects.filter(user=user, publication=publication)
            .order_by('section_name')
            .values_list('section_name', flat=True)
            .distinct()
        )

        ref_date = post.publish_date or post.creation_date

        parts = []
        for sname in sorted(all_section_names):
            representative = Section.objects.filter(
                user=user, publication=publication, section_name=sname
            ).first()
            formatted, count = format_link_history(representative)
            if count > 0:
                nearby_sections = list(
                    Section.objects.filter(
                        user=user,
                        publication=publication,
                        section_name=sname,
                        section_title__isnull=False,
                    )
                    .exclude(section_title='')
                    .exclude(post=post)
                    .select_related('post')
                )
                if ref_date:
                    nearby_sections = sorted(
                        nearby_sections,
                        key=lambda s: abs((s.post.publish_date or s.post.creation_date or ref_date) - ref_date)
                    )
                sample_titles = []
                seen = set()
                for s in nearby_sections:
                    if s.section_title not in seen:
                        seen.add(s.section_title)
                        sample_titles.append(s.section_title)
                    if len(sample_titles) >= 5:
                        break

                titles_str = ""
                if sample_titles:
                    titles_list = "\n".join(f"  - {t}" for t in sample_titles)
                    titles_str = f"\nSample titles from recent issues:\n{titles_list}\n"

                parts.append(
                    f"<section name=\"{sname}\">{titles_str}\n{formatted}\n</section>"
                )

        return "\n\n".join(parts)

    from asgiref.sync import sync_to_async
    link_history_str = await sync_to_async(_build_link_history)()

    # --- 4) Call LLM for tips ---
    messages = [
        {"role": "user", "content": f"<link_history>\n{link_history_str}\n</link_history>"},
        {"role": "user", "content": f"<post title=\"{post.title}\">\n{numbered_html}\n</post>"},
        {"role": "system", "content": IMPROVEMENT_TIP_PROMPT},
    ]

    response = await llm_call(
        "generate_improvement_tips",
        messages,
        settings.IMPROVEMENT_TIPS_MODEL,
        settings.IMPROVEMENT_TIPS_REASONING,
        response_format=AllImprovementTips,
        user=user,
    )
    tips = response.output[-1].content[0].parsed

    # --- 5) Build two-column annotated HTML ---
    tip_type_to_header = {
        "content": "\U0001f4f0 Content Tip",
        "proofreading": "\u270d\ufe0f Proofreading",
    }
    tip_type_to_header_color = {
        "content": "#E65100",
        "proofreading": "#0D47A1",
    }

    # Tag each tip with its kind so rendering can dispatch on it.
    typed_tips = (
        [('proofreading', t) for t in tips.proofreading_tips]
        + [('content', t) for t in tips.content_tips]
    )

    # Tips' start_line/end_line now refer directly to HTML lines. Take the midpoint
    # so the anchor lands in the body of a multi-line span, not at its start.
    tips_with_anchors = []
    for tip_kind, t in typed_tips:
        lo, hi = sorted((t.start_line, t.end_line))
        lo = max(1, lo)
        hi = min(post_html_length, hi)
        if hi < lo:
            continue
        mid = (lo + hi) // 2
        tips_with_anchors.append((tip_kind, t, mid))

    tips_with_anchors.sort(key=lambda tup: tup[2])

    # Insert anchor spans via DOM manipulation so logically-continuous paragraphs
    # that are physically split across many prettified lines (by inline elements
    # like <a>) stay grouped in the rendered output.
    soup_render = BeautifulSoup(pretty_html, 'html.parser')
    for i, (tip_kind, tip, mid) in enumerate(tips_with_anchors):
        marker_id = f"tip-target-{i}"
        anchor = soup_render.new_tag("span", attrs={
            "id": marker_id,
            "data-tip-anchor": "true",
            "data-tip-type": tip_kind,
        })
        _insert_tip_anchor(soup_render, mid, anchor)

    newsletter_html = str(soup_render)

    copy_icon_svg = (
        '<svg class="copy-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" '
        'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
        '<rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>'
        '<path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>'
        '</svg>'
        '<svg class="check-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" '
        'stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" '
        'style="display:none;">'
        '<polyline points="20 6 9 17 4 12"></polyline>'
        '</svg>'
    )

    # Build tip card divs
    tip_cards = []
    for i, (tip_kind, tip, _mid) in enumerate(tips_with_anchors):
        header = tip_type_to_header[tip_kind]
        header_color = tip_type_to_header_color[tip_kind]
        safe_suggestion = html_module.escape(tip.suggestion)

        change_block = ""
        why_block = ""
        if tip_kind == 'content':
            has_old = bool(tip.old_text and tip.old_text.strip())
            has_new = bool(tip.new_text and tip.new_text.strip())
            if has_old or has_new:
                old_block = (
                    f'<div class="old-box"><div class="old-text">{html_module.escape(tip.old_text)}</div></div>'
                    if has_old else ''
                )
                copy_btn = (
                    f'<button type="button" class="copy-btn" title="Copy to clipboard" '
                    f'aria-label="Copy suggested text">{copy_icon_svg}</button>'
                ) if has_new else ''
                new_rendered = _render_new_text_with_diff(tip.old_text or '', tip.new_text)
                new_block = (
                    f'<div class="new-box">'
                    f'<div class="new-text copy-source">{new_rendered}</div>'
                    f'{copy_btn}'
                    f'</div>'
                ) if has_new else ''
                change_block = f"""
        <div style="font-weight: bold; margin-bottom: 4px; margin-top: 10px; color: {header_color};">Suggested change</div>
        <div class="suggested-change">{old_block}{new_block}</div>"""
            if tip.why and tip.why.strip() and tip.why.lower() != 'none':
                safe_why = html_module.escape(tip.why)
                why_block = f"""
        <div style="font-weight: bold; margin-bottom: 4px; margin-top: 10px; color: {header_color};">Why?</div>
        <div>{safe_why}</div>"""

        tip_cards.append(f"""
    <div class="tip-card {tip_kind}" id="tip-card-{i}" data-target="tip-target-{i}" data-tip-type="{tip_kind}">
        <div style="font-weight: bold; margin-bottom: 4px; color: {header_color};">{header}</div>
        <div style="margin-bottom: 10px;">{safe_suggestion}</div>{change_block}{why_block}
    </div>""")

    tip_cards_html = '\n'.join(tip_cards)

    result_html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
    body {{
        margin: 0;
        padding: 0;
        background: #f5f5f5;
    }}
    .annotated-wrapper {{
        display: flex;
        max-width: 1400px;
        margin: 0 auto;
        position: relative;
    }}
    .tips-column {{
        flex: 0 0 30%;
        max-width: 30%;
        position: relative;
        min-height: 100%;
    }}
    .newsletter-column {{
        flex: 0 0 70%;
        max-width: 70%;
        background: white;
        box-shadow: 0 1px 4px rgba(0,0,0,0.1);
        position: relative;
    }}
    .tip-card {{
        position: absolute;
        width: calc(100% - 32px);
        right: 0;
        background-color: #FFFDE7;
        border-right: 4px solid #F9A825;
        padding: 12px 14px;
        font-family: Arial, sans-serif;
        font-size: 13px;
        line-height: 1.5;
        color: #333;
        border-radius: 4px;
        box-shadow: 0 1px 3px rgba(0,0,0,0.1);
        transition: box-shadow 0.2s;
    }}
    .tip-card.proofreading {{
        background-color: #E3F2FD;
        border-right-color: #1976D2;
    }}
    .tip-card:hover {{
        box-shadow: 0 2px 8px rgba(0,0,0,0.2);
    }}
    svg.connectors {{
        position: absolute;
        top: 0;
        left: 0;
        width: 100%;
        height: 100%;
        pointer-events: none;
        z-index: 10;
    }}
    [data-tip-anchor] {{
        background-color: #FFF9C4;
        outline: 2px solid #F9A825;
        outline-offset: 2px;
        border-radius: 2px;
    }}
    [data-tip-anchor][data-tip-type="proofreading"] {{
        background-color: #BBDEFB;
        outline-color: #1976D2;
    }}
    .top-banner {{
        position: absolute;
        top: 16px;
        right: 0;
        width: calc(100% - 32px);
        background-color: #FFFDE7;
        border-right: 4px solid #F9A825;
        padding: 12px 14px;
        font-family: Arial, sans-serif;
        font-size: 13px;
        line-height: 1.5;
        color: #333;
        border-radius: 4px;
        box-shadow: 0 1px 3px rgba(0,0,0,0.1);
    }}
    .suggested-change {{
        display: flex;
        flex-direction: column;
        gap: 6px;
    }}
    .old-box {{
        background-color: rgba(0,0,0,0.04);
        border-radius: 4px;
        padding: 8px 10px;
    }}
    .new-box {{
        display: flex;
        align-items: flex-start;
        gap: 8px;
        background-color: rgba(46,125,50,0.08);
        border-left: 3px solid #2e7d32;
        border-radius: 4px;
        padding: 8px 10px;
    }}
    .old-text {{
        text-decoration: line-through;
        color: #888;
        white-space: pre-wrap;
        word-break: break-word;
    }}
    .new-text {{
        flex: 1 1 auto;
        min-width: 0;
        white-space: pre-wrap;
        word-break: break-word;
    }}
    .new-text mark.diff-new {{
        background-color: #FFF59D;
        color: inherit;
        padding: 0 2px;
        border-radius: 2px;
    }}
    .copy-btn {{
        flex: 0 0 auto;
        background: transparent;
        border: 1px solid rgba(0,0,0,0.15);
        border-radius: 3px;
        padding: 3px 6px;
        cursor: pointer;
        color: #555;
        line-height: 0;
        transition: background 0.15s, color 0.15s, border-color 0.15s;
    }}
    .copy-btn:hover {{
        background: rgba(0,0,0,0.06);
        color: #000;
    }}
    .copy-btn.copied {{
        color: #2e7d32;
        border-color: #2e7d32;
    }}
</style>
</head>
<body>
<div class="annotated-wrapper" id="annotated-wrapper">
    <div class="tips-column" id="tips-column">
        <div class="top-banner">Suggested content changes appear below.</div>
        {tip_cards_html}
    </div>
    <div class="newsletter-column" id="newsletter-column">
        {newsletter_html}
    </div>
    <svg class="connectors" id="connectors-svg"></svg>
</div>
<script>
    function positionCardsAndDrawConnectors() {{
        const wrapper = document.getElementById('annotated-wrapper');
        const svg = document.getElementById('connectors-svg');
        const tipsCol = document.getElementById('tips-column');
        const wrapperRect = wrapper.getBoundingClientRect();

        svg.setAttribute('width', wrapperRect.width);
        svg.setAttribute('height', wrapperRect.height);
        svg.style.width = wrapperRect.width + 'px';
        svg.style.height = wrapperRect.height + 'px';
        svg.innerHTML = '';

        const cards = Array.from(document.querySelectorAll('.tip-card'));
        let minNextTop = 80;

        cards.forEach(function(card) {{
            const targetId = card.getAttribute('data-target');
            const target = document.getElementById(targetId);
            if (!target) return;

            const targetRect = target.getBoundingClientRect();
            const desiredTop = targetRect.top + targetRect.height / 2 - wrapperRect.top - 20;
            const actualTop = Math.max(desiredTop, minNextTop);
            card.style.top = actualTop + 'px';

            const cardHeight = card.getBoundingClientRect().height;
            minNextTop = actualTop + cardHeight + 12;

            const cardRect = card.getBoundingClientRect();
            const x1 = cardRect.right - wrapperRect.left;
            const y1 = cardRect.top + 20 - wrapperRect.top;
            const x2 = targetRect.left - wrapperRect.left;
            const y2 = targetRect.top + targetRect.height / 2 - wrapperRect.top;

            const midX = (x1 + x2) / 2;
            const connectorColor = card.classList.contains('proofreading') ? '#1976D2' : '#F9A825';
            const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
            path.setAttribute('d', 'M ' + x1 + ' ' + y1 + ' C ' + midX + ' ' + y1 + ' ' + midX + ' ' + y2 + ' ' + x2 + ' ' + y2);
            path.setAttribute('stroke', connectorColor);
            path.setAttribute('stroke-width', '2');
            path.setAttribute('fill', 'none');
            path.setAttribute('stroke-dasharray', '6,3');

            const circle = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
            circle.setAttribute('cx', x2);
            circle.setAttribute('cy', y2);
            circle.setAttribute('r', '4');
            circle.setAttribute('fill', connectorColor);

            svg.appendChild(path);
            svg.appendChild(circle);
        }});

        const lastCard = cards[cards.length - 1];
        if (lastCard) {{
            const lastBottom = parseFloat(lastCard.style.top) + lastCard.getBoundingClientRect().height + 20;
            tipsCol.style.minHeight = lastBottom + 'px';
        }}
    }}

    window.addEventListener('load', positionCardsAndDrawConnectors);
    window.addEventListener('resize', positionCardsAndDrawConnectors);

    function fallbackCopy(text) {{
        try {{
            const ta = document.createElement('textarea');
            ta.value = text;
            ta.style.position = 'fixed';
            ta.style.top = '0';
            ta.style.left = '0';
            ta.style.opacity = '0';
            document.body.appendChild(ta);
            ta.focus();
            ta.select();
            const ok = document.execCommand('copy');
            document.body.removeChild(ta);
            return ok;
        }} catch (e) {{
            return false;
        }}
    }}

    function flashCopied(btn, ok) {{
        btn.classList.toggle('copied', ok);
        btn.setAttribute('title', ok ? 'Copied!' : 'Copy failed');
        const copyIcon = btn.querySelector('.copy-icon');
        const checkIcon = btn.querySelector('.check-icon');
        if (ok && copyIcon && checkIcon) {{
            copyIcon.style.display = 'none';
            checkIcon.style.display = '';
        }}
        setTimeout(function() {{
            btn.classList.remove('copied');
            btn.setAttribute('title', 'Copy to clipboard');
            if (copyIcon && checkIcon) {{
                copyIcon.style.display = '';
                checkIcon.style.display = 'none';
            }}
        }}, 1500);
    }}

    document.addEventListener('click', function(e) {{
        const btn = e.target.closest('.copy-btn');
        if (!btn) return;
        const card = btn.closest('.tip-card');
        if (!card) return;
        const src = card.querySelector('.copy-source');
        if (!src) return;
        const text = src.innerText;
        if (navigator.clipboard && navigator.clipboard.writeText) {{
            navigator.clipboard.writeText(text).then(
                function() {{ flashCopied(btn, true); }},
                function() {{ flashCopied(btn, fallbackCopy(text)); }}
            );
        }} else {{
            flashCopied(btn, fallbackCopy(text));
        }}
    }});
</script>
</body>
</html>"""

    return result_html


def run_improvement_tips_background(task_id):
    """
    Background thread entry point for generating improvement tips.
    Loads the PendingImprovementTips task, generates annotated HTML, saves result.
    """
    from asgiref.sync import async_to_sync
    from django.db import connection, close_old_connections
    from analytics.models import PendingImprovementTips, UsageAccount

    try:
        task = PendingImprovementTips.objects.get(task_id=task_id)
        task.status = 'running'
        task.save(update_fields=['status'])

        from analytics.llm_tracker import start_tracking, finish_tracking, set_llm_context
        set_llm_context(
            user_id=task.user_id,
            publication_id=task.publication_id,
            task_id=task.task_id,
            task_kind='improvement_tips',
        )
        if settings.ENVIRONMENT == 'local':
            start_tracking()

        post = task.post
        user = task.user
        publication = task.publication

        try:
            usage = UsageAccount.objects.get(user=user)
            beehiiv_token = usage.beehiiv_token
            beehiiv_pub_id = usage.beehiiv_pub_id
        except UsageAccount.DoesNotExist:
            raise RuntimeError("No API credentials configured")

        result_html = async_to_sync(generate_improvement_tips_html)(
            post, user, publication, beehiiv_token, beehiiv_pub_id
        )

        # Long-running LLM calls can leave the DB connection stale (RDS-side
        # idle timeout). Drop any unhealthy connection so the next ORM call
        # opens a fresh one instead of raising SSL SYSCALL EOF.
        close_old_connections()

        task.status = 'complete'
        task.result_html = result_html
        if settings.ENVIRONMENT == 'local':
            task.dev_panel_data = finish_tracking() or {}
            task.save(update_fields=['status', 'result_html', 'dev_panel_data'])
        else:
            task.save(update_fields=['status', 'result_html'])

        # Mark that the user has used post improvement
        from analytics.models import Feedback
        Feedback.objects.get_or_create(
            user=task.user, feature='used_post_improvement',
            defaults={'response': 'completed'}
        )

    except Exception as e:
        logger.exception("Improvement tips background task failed")
        try:
            close_old_connections()
            task = PendingImprovementTips.objects.get(task_id=task_id)
            task.status = 'error'
            task.error_message = str(e)
            task.save(update_fields=['status', 'error_message'])
        except Exception:
            logger.exception("Failed to save error status for improvement tips task")
    finally:
        connection.close()


# =============================================================================
# Learning / Updating background task runners
# =============================================================================

def _heartbeat_task(task):
    """Refresh last_heartbeat on the task row (one-shot)."""
    from django.utils import timezone as dj_timezone
    task.last_heartbeat = dj_timezone.now()
    task.save(update_fields=['last_heartbeat'])


def _is_abandoned(task):
    """Re-read abandoned flag from DB. Returns True if task was marked abandoned."""
    from analytics.models import PendingLearningTask
    try:
        fresh = PendingLearningTask.objects.only('abandoned').get(pk=task.pk)
        return bool(fresh.abandoned)
    except PendingLearningTask.DoesNotExist:
        return True


def _heartbeat_loop(task_id, stop_event, interval=5):
    """
    Background loop that refreshes `last_heartbeat` on the task row while the
    runner thread is alive. Lets the stale-sweep detect a dead runner (crash,
    gunicorn restart) independently of client polling.
    """
    from django.db import connection
    from django.utils import timezone as dj_timezone
    from analytics.models import PendingLearningTask
    try:
        while not stop_event.wait(interval):
            try:
                PendingLearningTask.objects.filter(task_id=task_id).update(
                    last_heartbeat=dj_timezone.now(),
                )
            except Exception:
                logger.exception("Heartbeat loop update failed")
    finally:
        connection.close()


def _run_learning_task_impl(task_id, kind):
    """
    Shared implementation for both kind='initial' and kind='update' runners.
    - Fetch phase: full refresh (initial) OR incremental fetch (update).
    - Select post ids to process, charge credits, run processing.
    - Update task phase/counts throughout. A daemon heartbeat thread keeps
      `last_heartbeat` fresh so the stale-sweep only fires if the runner
      actually dies.
    - On abandoned flag (kind='initial' only): wipe user/publication data
      and exit. `initial_fetched_pub_ids` is only set on a clean complete
      with >0 posts processed, so a zero-result or abandoned run lets the
      user hit the Learning coach again on their next visit.
    """
    import threading
    from asgiref.sync import async_to_sync
    from django.db import connection
    from django.utils import timezone as dj_timezone
    from analytics.models import (
        PendingLearningTask, Publication, UsageAccount, Post, ProcessedPost,
    )

    task = None
    stop_heartbeat = threading.Event()
    hb_thread = threading.Thread(
        target=_heartbeat_loop,
        args=(task_id, stop_heartbeat),
        daemon=True,
    )
    hb_thread.start()

    try:
        task = PendingLearningTask.objects.select_related('publication', 'user').get(task_id=task_id)
        task.status = 'running'
        task.phase = 'fetch'
        _heartbeat_task(task)

        from analytics.llm_tracker import set_llm_context
        set_llm_context(
            user_id=task.user_id,
            publication_id=task.publication_id,
            task_id=task.task_id,
            task_kind=f'learning_{kind}',
        )

        usage = UsageAccount.objects.get(user=task.user)
        beehiiv_token = usage.beehiiv_token
        publication = task.publication
        beehiiv_pub_id = publication.pub_id if publication else None

        if not beehiiv_token or not beehiiv_pub_id:
            task.status = 'error'
            task.error_message = "Missing Beehiiv credentials or publication."
            task.save(update_fields=['status', 'error_message'])
            return

        # --- Fetch phase ---
        if kind == 'initial':
            posts_df, fetch_msg = async_to_sync(refresh_posts_data)(
                beehiiv_token, beehiiv_pub_id,
            )
        else:
            existing_post_ids = list(
                Post.objects.filter(user=task.user, publication=publication)
                .values_list('post_id', flat=True)
            )
            posts_df, fetch_msg = async_to_sync(incremental_refresh_posts_data)(
                beehiiv_token, beehiiv_pub_id, existing_post_ids,
            )

        if posts_df is None:
            task.status = 'error'
            task.error_message = fetch_msg or "Fetch failed."
            task.save(update_fields=['status', 'error_message'])
            return

        if _is_abandoned(task):
            if kind == 'initial':
                wipe_user_publication_data(task.user, beehiiv_pub_id)
            task.status = 'abandoned'
            task.save(update_fields=['status'])
            return

        if not posts_df.empty:
            save_posts_to_db(posts_df, task.user, publication)

        if kind == 'initial' and _is_abandoned(task):
            wipe_user_publication_data(task.user, beehiiv_pub_id)
            task.status = 'abandoned'
            task.save(update_fields=['status'])
            return

        # --- Select posts to process ---
        if kind == 'initial':
            subs = async_to_sync(fetch_subscriber_count)(beehiiv_token, beehiiv_pub_id)
            post_ids_to_process = select_posts_for_initial_learning(
                task.user, publication, subs,
            )
        else:
            post_ids_to_process = select_posts_for_update(task.user, publication)

        # Silently cap at MAX_POSTS_PROCESSED_PER_PERIOD across the user's
        # current billing period. Over-cap posts are dropped without error;
        # they become eligible again next period via select_posts_for_update.
        usage.ensure_current_period()
        already_processed_this_period = ProcessedPost.objects.filter(
            user=task.user,
            created_at__gte=usage.period_start,
        ).count()
        remaining_quota = max(
            0, settings.MAX_POSTS_PROCESSED_PER_PERIOD - already_processed_this_period,
        )
        post_ids_to_process = post_ids_to_process[:remaining_quota]

        task.target_process_count = len(post_ids_to_process)
        task.phase = 'process'
        task.save(update_fields=['target_process_count', 'phase'])

        if not post_ids_to_process:
            # Empty run — don't flip initial_fetched_pub_ids so the user can
            # retry via the Learning coach on their next visit.
            task.status = 'complete'
            task.save(update_fields=['status'])
            return

        if kind == 'initial' and _is_abandoned(task):
            wipe_user_publication_data(task.user, beehiiv_pub_id)
            task.status = 'abandoned'
            task.save(update_fields=['status'])
            return

        # --- Process phase ---
        # process_posts_sections_sequential handles the first-2-sequential +
        # rest-parallel semantics internally, so we can't interrupt between
        # individual posts. Worst-case race window is one post's worth of
        # writes; those are cleaned up by the post-process abandon check
        # below (which wipes for kind='initial').
        async_to_sync(process_posts_sections_sequential)(
            post_ids_to_process, task.user, beehiiv_token, beehiiv_pub_id, publication,
        )

        processed_count = ProcessedPost.objects.filter(
            user=task.user,
            post__post_id__in=post_ids_to_process,
        ).count()
        task.posts_processed_count = processed_count

        if kind == 'initial' and _is_abandoned(task):
            wipe_user_publication_data(task.user, beehiiv_pub_id)
            task.status = 'abandoned'
            task.save(update_fields=['status', 'posts_processed_count'])
            return

        # Only flip initial_fetched_pub_ids after a clean, non-empty run so a
        # zero-result initial scan doesn't strand the user on the "No Data"
        # fallback.
        if kind == 'initial' and processed_count > 0:
            if beehiiv_pub_id not in (usage.initial_fetched_pub_ids or []):
                usage.initial_fetched_pub_ids = list(
                    usage.initial_fetched_pub_ids or []
                ) + [beehiiv_pub_id]
                usage.save(update_fields=['initial_fetched_pub_ids'])

        task.status = 'complete'
        task.save(update_fields=['status', 'posts_processed_count'])

    except Exception as e:
        logger.exception(f"Learning task ({kind}) failed")
        try:
            if task is not None:
                task = PendingLearningTask.objects.get(task_id=task_id)
                task.status = 'error'
                task.error_message = str(e)
                task.save(update_fields=['status', 'error_message'])
        except Exception:
            logger.exception("Failed to save error status for learning task")
    finally:
        stop_heartbeat.set()
        connection.close()


def run_initial_learning_task(task_id):
    """Background thread entrypoint for the initial Learning Your Audience flow."""
    _run_learning_task_impl(task_id, 'initial')


def run_update_task(task_id):
    """Background thread entrypoint for the per-page-load Updating Your Posts flow."""
    _run_learning_task_impl(task_id, 'update')


# =============================================================================
# Niche Analysis (Monetize tab first-visit setup)
# =============================================================================

class NicheAnalysisResult(BaseModel):
    niche: str
    content_types: List[str]


def _build_niche_analysis_prompt(user, publication):
    """
    Gather the inputs for the niche analysis LLM call:
      - Plain text of the most recent N processed posts (sections concatenated
        in `start_line` order, with link URLs inlined after their anchor text).
      - Best-performing links per section across the most recent M issues, by
        section_name, with each link's CTR shown relative to the section's
        average over the same window.

    Returns the user-message string (or None if there's not enough data — the
    caller should treat that as "skip the analysis" rather than erroring).
    """
    from analytics.models import Post, ProcessedPost, Section, LinkData

    recent_n = settings.NICHE_ANALYSIS_RECENT_POSTS
    history_m = settings.NICHE_ANALYSIS_LINK_HISTORY_ISSUES
    top_links = settings.NICHE_ANALYSIS_TOP_LINKS_PER_SECTION

    # --- Recent processed posts: get text via section_html ---
    recent_processed_post_ids = list(
        ProcessedPost.objects.filter(user=user, publication=publication)
        .select_related('post')
        .filter(post__publish_date__isnull=False)
        .order_by('-post__publish_date')
        .values_list('post_id', flat=True)[:recent_n]
    )

    if not recent_processed_post_ids:
        return None

    recent_posts = list(
        Post.objects.filter(pk__in=recent_processed_post_ids)
        .order_by('-publish_date')
    )

    post_blocks = []
    for post in recent_posts:
        sections = list(
            Section.objects.filter(post=post, user=user).order_by('start_line')
        )
        if not sections:
            continue
        section_blocks = []
        for sec in sections:
            text = html_to_text_with_links(sec.section_html or "", max_url_len=75)
            if not text.strip():
                continue
            label = sec.section_title or sec.section_name
            section_blocks.append(f"### {label}\n{text}")
        if not section_blocks:
            continue
        date_str = post.publish_date.strftime('%Y-%m-%d') if post.publish_date else 'unknown date'
        post_blocks.append(
            f"<post title=\"{post.title}\" date=\"{date_str}\">\n" +
            "\n\n".join(section_blocks) +
            "\n</post>"
        )

    if not post_blocks:
        return None

    posts_section_str = "\n\n".join(post_blocks)

    # --- Best links per section over the last M issues ---
    history_post_ids = list(
        Post.objects.filter(
            user=user, publication=publication,
            publish_date__isnull=False,
        )
        .order_by('-publish_date')
        .values_list('pk', flat=True)[:history_m]
    )

    section_link_blocks = []
    if history_post_ids:
        history_links = list(
            LinkData.objects.filter(
                user=user, publication=publication,
                post_id__in=history_post_ids,
            ).select_related('post')
        )

        # Group by section_name
        by_section = {}
        for link in history_links:
            by_section.setdefault(link.section_name, []).append(link)

        for section_name in sorted(by_section.keys()):
            links = by_section[section_name]
            if not links:
                continue
            section_avg = sum(l.mean_ctr for l in links) / len(links)
            links_sorted = sorted(links, key=lambda l: l.mean_ctr, reverse=True)[:top_links]
            entries = []
            for i, link in enumerate(links_sorted, start=1):
                if section_avg > 0:
                    rel = f"{link.mean_ctr / section_avg:.1f}x avg"
                else:
                    rel = "N/A"
                desc = (link.description or "").strip() or truncate_url(link.raw_url, 75)
                entries.append(f"  {i}. [{rel}] {desc}")
            section_link_blocks.append(
                f"<section name=\"{section_name}\">\n" + "\n".join(entries) + "\n</section>"
            )

    link_history_str = "\n\n".join(section_link_blocks) if section_link_blocks else "(no link history available)"

    return (
        "<recent_posts>\n"
        f"{posts_section_str}\n"
        "</recent_posts>\n\n"
        "<best_links_by_section>\n"
        f"{link_history_str}\n"
        "</best_links_by_section>"
    )


async def _run_niche_analysis_llm(user_prompt):
    """Single LLM call for niche analysis. Returns a parsed NicheAnalysisResult."""
    messages = [
        {"role": "system", "content": NICHE_ANALYSIS_PROMPT},
        {"role": "user", "content": user_prompt},
    ]
    response = await llm_call(
        "niche_analysis",
        messages,
        settings.NICHE_ANALYSIS_MODEL,
        settings.NICHE_ANALYSIS_REASONING,
        response_format=NicheAnalysisResult,
        timeout=90.0,
    )
    parsed = response.output[-1].content[0].parsed
    return parsed


def run_niche_analysis_background(task_id):
    """
    Background thread entry point for the Monetize-tab niche analysis. Loads
    the PendingNicheAnalysis row, gathers post text + per-section link history,
    runs the LLM, and writes the result back to the row.
    """
    from asgiref.sync import async_to_sync
    from django.db import connection, close_old_connections
    from analytics.models import PendingNicheAnalysis
    from analytics.llm_tracker import start_tracking, finish_tracking, set_llm_context

    try:
        task = PendingNicheAnalysis.objects.select_related('user', 'publication').get(task_id=task_id)
        task.status = 'running'
        task.save(update_fields=['status'])

        set_llm_context(
            user_id=task.user_id,
            publication_id=task.publication_id,
            task_id=task.task_id,
            task_kind='niche_analysis',
        )
        if settings.ENVIRONMENT == 'local':
            start_tracking()

        user_prompt = _build_niche_analysis_prompt(task.user, task.publication)
        if user_prompt is None:
            # No processed posts yet — caller should not have spawned us, but be
            # defensive. Flagged as a soft fallback per global instructions: we
            # complete the task with empty results rather than erroring, which
            # would surface a generic error toast to the user.
            task.status = 'complete'
            task.niche = ''
            task.content_types = []
            if settings.ENVIRONMENT == 'local':
                task.dev_panel_data = finish_tracking() or {}
                task.save(update_fields=['status', 'niche', 'content_types', 'dev_panel_data'])
            else:
                task.save(update_fields=['status', 'niche', 'content_types'])
            return

        parsed = async_to_sync(_run_niche_analysis_llm)(user_prompt)
        close_old_connections()

        # Cap content_types at 5 entries. Falling short is acceptable — we
        # render whatever the model returned. Going over is silently truncated
        # (flagged as soft fallback per global instructions).
        types = [t.strip() for t in (parsed.content_types or []) if t and t.strip()][:5]

        task.status = 'complete'
        task.niche = (parsed.niche or '').strip()[:255]
        task.content_types = types
        if settings.ENVIRONMENT == 'local':
            task.dev_panel_data = finish_tracking() or {}
            task.save(update_fields=['status', 'niche', 'content_types', 'dev_panel_data'])
        else:
            task.save(update_fields=['status', 'niche', 'content_types'])

    except Exception as e:
        logger.exception("Niche analysis background task failed")
        try:
            close_old_connections()
            task = PendingNicheAnalysis.objects.get(task_id=task_id)
            task.status = 'error'
            task.error_message = str(e)
            task.save(update_fields=['status', 'error_message'])
        except Exception:
            logger.exception("Failed to save error status for niche analysis task")
    finally:
        connection.close()