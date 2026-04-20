"""
Utility functions for the Django analytics app.
Adapted from the original utils.py for Streamlit.
"""

import json
import math
from collections import Counter
from openai import AsyncOpenAI, OpenAI, BadRequestError
from pydantic import BaseModel
from typing import List, Literal, Optional
import pandas as pd
import asyncio
import aiohttp
import os
import logging
from datetime import datetime
from zoneinfo import ZoneInfo
import time
import html as html_module
from bs4 import BeautifulSoup, NavigableString
from django.conf import settings
from django.db import transaction
from django.db.models import F
from Levenshtein import distance as levenshtein_distance
from dotenv import load_dotenv
from analytics.prompts import (
    CONTENT_FINDER_SYSTEM_PROMPT,
    CONTENT_FINDER_FILTER_SECTIONS_INSTRUCTION,
    CONTENT_FINDER_SECTION_INCLUSION_CRITERIA,
    IMPROVEMENT_TIP_PROMPT,
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


def convert_to_user_timezone(dt, user_timezone_str):
    """
    Convert a UTC datetime to the user's preferred timezone.

    Args:
        dt: A timezone-aware datetime object (expected to be in UTC)
        user_timezone_str: IANA timezone string (e.g., 'America/New_York')

    Returns:
        datetime object in the user's timezone, or None if dt is None/NaT
    """
    if dt is None:
        return None

    # Handle pandas NaT (Not a Time) values
    if pd.isna(dt):
        return None

    user_tz = ZoneInfo(user_timezone_str)
    return dt.astimezone(user_tz)


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


async def llm_call(function_name, messages, model, reasoning_level, response_format=None, tools=None, tool_choice=None, user=None, store=False, previous_response_id=None):
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

    Returns:
        OpenAI API response object
    """
    start_datetime = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
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

    try:
        if asyncio.get_event_loop().is_running():
            client = AsyncOpenAI(api_key=OPENAI_API_KEY, timeout=90.0, max_retries=2)
            try:
                if response_format is not None:
                    kwargs["text_format"] = response_format

                    response = await client.responses.parse(**kwargs)
                else:
                    response = await client.responses.create(**kwargs)
            finally:
                await client.close()
        else:
            client = OpenAI(api_key=OPENAI_API_KEY, timeout=90.0, max_retries=2)
            try:
                if response_format is not None:
                    kwargs["text_format"] = response_format

                    response = client.responses.parse(**kwargs)
                else:
                    response = client.responses.create(**kwargs)
            finally:
                client.close()
    except Exception:
        logger.exception("llm_call failed")
        raise
    duration = time.time() - start_time

    # Record to dev panel tracker (no-op outside local mode)
    from analytics.llm_tracker import is_tracking, record_call
    if is_tracking():
        record_call(function_name, model, messages, response, duration)

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
        

async def fetch_posts_html_and_clicks_parallel(post_ids, beehiiv_token, beehiiv_pub_id):
    """
    Fetch HTML content and clicks stats for multiple posts in parallel.
    Since we can't use expand=free_email_content and expand=stats together,
    we make 2 requests per post.

    Args:
        post_ids: List of Beehiiv post IDs
        beehiiv_token: Beehiiv API token
        beehiiv_pub_id: Beehiiv publication ID

    Returns:
        Tuple of (htmls, clicks_by_id) where:
        - htmls: Dictionary mapping post IDs to HTML content
        - clicks_by_id: Dictionary mapping post IDs to clicks dictionaries
    """
    semaphore = asyncio.Semaphore(5)  # Limit to 5 concurrent requests
    htmls = {}
    clicks_by_id = {}

    timeout = aiohttp.ClientTimeout(total=60)  # Increased timeout for more requests
    async with aiohttp.ClientSession(timeout=timeout) as session:
        # Fetch HTML content
        html_tasks = [fetch_post_html(session, post_id, semaphore, beehiiv_token, beehiiv_pub_id) for post_id in post_ids]
        html_results = await asyncio.gather(*html_tasks)

        for post_id, html_content in html_results:
            if html_content is not None:
                htmls[f"{post_id}.html"] = html_content

        # Fetch clicks data
        clicks_tasks = [fetch_post_clicks(session, post_id, semaphore, beehiiv_token, beehiiv_pub_id) for post_id in post_ids]
        clicks_results = await asyncio.gather(*clicks_tasks)
        
        for post_id, clicks_dict in clicks_results:
            if clicks_dict is not None:
                clicks_by_id[post_id] = clicks_dict
    
    # Give the event loop a moment to complete cleanup
    await asyncio.sleep(0)
    
    return htmls, clicks_by_id


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
        rank_in_post, rank_in_section, mean_ctr, mean_clicks
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

    # --- Compute global rank_in_post and tag for LLM ---
    # Sort all selected links by CTR descending for global ranking
    selected_links.sort(key=lambda x: x[1]['mean_ctr'], reverse=True)

    url_to_tag = {}
    for i, (sec_name, ls) in enumerate(selected_links, start=1):
        ls['rank_in_post'] = i
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
            'rank_in_post': ls['rank_in_post'],
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

CONTENT_FINDER_DISMISS_TOOL = {
    "type": "function",
    "name": "dismiss_section",
    "description": "Call this to skip a section that does not require new external content (e.g. intro sections, sponsored content, reader polls, self-promotion, recurring sections that don't change week-to-week).",
    "parameters": {
        "type": "object",
        "properties": {
            "reason": {
                "type": "string",
                "description": "Brief explanation of why this section does not need new content."
            }
        },
        "required": ["reason"],
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


async def run_content_finder_agent(messages, allow_exclusion, model, reasoning, max_rounds=3, historical_urls=None):
    """
    Per-section agentic loop: call llm_call, execute tool calls (web search),
    feed results back, repeat until max_rounds is hit.
    Uses store=True + previous_response_id to preserve reasoning traces across rounds.
    Returns (final_response_or_None, all_responses, all_search_results).
    If the model dismisses the section, returns (None, ...).
    """
    input_messages = list(messages)
    all_responses, all_results = [], []
    prev_response_id = None

    for round_num in range(max_rounds):
        tools = (
            [CONTENT_FINDER_SEARCH_TOOL, CONTENT_FINDER_DISMISS_TOOL]
            if round_num == 0 and allow_exclusion
            else [CONTENT_FINDER_SEARCH_TOOL]
        )

        response = await llm_call(
            "content_finder_search",
            input_messages,
            model,
            reasoning,
            tools=tools,
            tool_choice="required",
            store=True,
            previous_response_id=prev_response_id,
        )
        all_responses.append(response)
        prev_response_id = response.id

        tool_calls = [item for item in response.output if item.type == "function_call"]

        if not tool_calls:
            return response, all_responses, all_results

        # Check for dismiss
        for call in tool_calls:
            if call.name == "dismiss_section":
                return None, all_responses, all_results

        # With previous_response_id, only send new tool outputs
        input_messages = []

        for call in tool_calls:
            args = json.loads(call.arguments)
            queries = args["queries"]
            domains = args.get("domains") or None
            max_days_ago = args.get("max_days_ago") or None

            result = perplexity_search(queries, domains=domains, max_days_ago=max_days_ago, historical_urls=historical_urls)
            all_results.append(result)

            input_messages.append({
                "type": "function_call_output",
                "call_id": call.call_id,
                "output": result,
            })

    # Max rounds reached — force structured output
    response = await llm_call(
        "content_finder_final_output",
        input_messages,
        model,
        reasoning,
        response_format=ContentFinderAllLinks,
        store=True,
        previous_response_id=prev_response_id,
    )
    all_responses.append(response)
    return response, all_responses, all_results


async def process_content_finder_section(section, allow_exclusion, max_links=60, max_url_len=75, model="gpt-5.4-mini", reasoning="medium", max_rounds=3, historical_urls=None):
    """
    Orchestrate content finding for a single section.
    Returns (section_name, parsed_links_or_None).
    """
    from asgiref.sync import sync_to_async
    link_history, link_count = await sync_to_async(format_link_history)(section, max_links=max_links, max_url_len=max_url_len)

    if link_count == 0:
        return (section.section_name, None)

    system_prompt = CONTENT_FINDER_SYSTEM_PROMPT.format(
        CONTENT_FINDER_FILTER_SECTIONS_INSTRUCTION if allow_exclusion else "",
        CONTENT_FINDER_SECTION_INCLUSION_CRITERIA if allow_exclusion else "",
    )
    user_prompt = build_content_finder_user_prompt(section, link_history, link_count, max_url_len=max_url_len)

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    response, all_responses, all_results = await run_content_finder_agent(
        messages, allow_exclusion, model, reasoning, max_rounds=max_rounds,
        historical_urls=historical_urls,
    )

    if response is None:
        return (section.section_name, None)

    # Parse structured output from the final response
    try:
        parsed = response.output[-1].content[0].parsed
        links = [link.model_dump() for link in parsed.links]
    except (AttributeError, IndexError):
        links = []

    return (section.section_name, links)


def run_content_finder_background(task_id):
    """
    Background thread entry point for running content finder.
    Loads the PendingContentSearch, processes sections, saves results.
    """
    import threading
    from asgiref.sync import async_to_sync
    from django.db import connection, close_old_connections
    from analytics.models import PendingContentSearch, Section, LinkData

    try:
        task = PendingContentSearch.objects.get(task_id=task_id)
        task.status = 'running'
        task.save(update_fields=['status'])

        from analytics.llm_tracker import start_tracking, finish_tracking
        if settings.ENVIRONMENT == 'local':
            start_tracking()

        # Load sections for the post
        sections = Section.objects.filter(
            post=task.post,
            user=task.user,
        ).order_by('start_line')

        if task.mode == 'manual' and task.selected_sections:
            sections = sections.filter(section_name__in=task.selected_sections)

        sections = list(sections)

        if not sections:
            task.status = 'complete'
            task.result_data = {}
            task.save(update_fields=['status', 'result_data'])
            return

        # #<TEMPORARY>
        # # Dev shortcut: return dummy results to speed up onboarding testing
        # if settings.ENVIRONMENT == 'local':
        #     import time
        #     time.sleep(2)  # simulate brief delay
        #     result_data = {}
        #     for section in sections:
        #         result_data[section.section_name] = [
        #             {
        #                 'title': f'Sample Article for {section.section_name}',
        #                 'url': 'https://example.com/sample-article',
        #                 'source': 'Example News',
        #                 'date': '2026-04-15',
        #                 'description': 'A sample article that matches your audience preferences.',
        #                 'relevance': 'This content aligns with topics your readers frequently click on.',
        #             },
        #             {
        #                 'title': f'Another Story for {section.section_name}',
        #                 'url': 'https://example.com/another-story',
        #                 'source': 'Demo Source',
        #                 'date': '2026-04-14',
        #                 'description': 'Another sample link for testing the onboarding flow.',
        #                 'relevance': 'High engagement potential based on historical click patterns.',
        #             },
        #         ]
        #     task.status = 'complete'
        #     task.result_data = result_data
        #     task.save(update_fields=['status', 'result_data'])
        #     from analytics.models import Feedback
        #     Feedback.objects.get_or_create(
        #         user=task.user, feature='used_content_finder',
        #         defaults={'response': 'completed'}
        #     )
        #     return
        # #</TEMPORARY>

        allow_exclusion = (task.mode == 'auto')
        model = settings.CONTENT_FINDER_MODEL
        reasoning = settings.CONTENT_FINDER_REASONING
        max_rounds = settings.CONTENT_FINDER_MAX_ROUNDS
        max_links = settings.CONTENT_FINDER_MAX_LINKS
        max_url_len = settings.CONTENT_FINDER_MAX_URL_LEN

        # Load all historical URLs for this user/publication once (strip trailing /)
        historical_urls = set(
            url.rstrip('/') for url in
            LinkData.objects.filter(
                user=task.user,
                publication=task.publication,
            ).values_list('raw_url', flat=True)
        )

        # Also exclude URLs the user has already reviewed via content search feedback
        from analytics.models import ContentSearchFeedback
        feedback_urls = set(
            url.rstrip('/') for url in
            ContentSearchFeedback.objects.filter(
                user=task.user,
                publication=task.publication,
            ).values_list('url', flat=True)
        )
        historical_urls |= feedback_urls

        async def run_all():
            tasks = [
                process_content_finder_section(
                    section, allow_exclusion,
                    max_links=max_links, max_url_len=max_url_len,
                    model=model, reasoning=reasoning, max_rounds=max_rounds,
                    historical_urls=historical_urls,
                )
                for section in sections
            ]
            return await asyncio.gather(*tasks)

        raw_results = async_to_sync(run_all)()

        # Long-running LLM calls can leave the DB connection stale (RDS-side
        # idle timeout). Drop any unhealthy connection so the next ORM call
        # opens a fresh one instead of raising SSL SYSCALL EOF.
        close_old_connections()

        result_data = {}
        for section_name, links in raw_results:
            if links is not None:
                result_data[section_name] = links

        task.status = 'complete'
        task.result_data = result_data
        if settings.ENVIRONMENT == 'local':
            task.dev_panel_data = finish_tracking() or {}
            task.save(update_fields=['status', 'result_data', 'dev_panel_data'])
        else:
            task.save(update_fields=['status', 'result_data'])

        # Mark that the user has used content finder
        from analytics.models import Feedback
        Feedback.objects.get_or_create(
            user=task.user, feature='used_content_finder',
            defaults={'response': 'completed'}
        )

    except Exception as e:
        logger.exception("Content finder background task failed")
        try:
            close_old_connections()
            task = PendingContentSearch.objects.get(task_id=task_id)
            task.status = 'error'
            task.error_message = str(e)
            task.save(update_fields=['status', 'error_message'])
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
                        rank_in_post=row['rank_in_post'],
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


def load_posts_from_db(publication_id=None, user=None):
    """Load posts from database into a DataFrame, filtered by publication and user.

    Args:
        publication_id: Optional Beehiiv publication ID (e.g., 'pub_xxx') to filter posts.
                        If None, returns all posts for the user.
        user: The user who owns the posts. Required for proper scoping.
    """
    from .models import Post

    queryset = Post.objects.all()
    if user:
        queryset = queryset.filter(user=user)
    if publication_id:
        queryset = queryset.filter(publication__pub_id=publication_id)

    posts = queryset.values(
        'post_id', 'title',
        # 'subtitle',  # Commented out - re-enable to show subtitle column
        'status', 'creation_date', 'publish_date',
        'recipients', 'delivered', 'email_opens', 'unique_email_opens',
        'email_clicks', 'unique_email_clicks', 'unsubscribes', 'spam_reports'
    )

    if posts:
        df = pd.DataFrame(list(posts))
        # Rename post_id to id to match the expected format
        df = df.rename(columns={'post_id': 'id'})
        return df
    return pd.DataFrame()


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
        # 'subtitle': [],  # Commented out - re-enable to show subtitle column
        'status': [],
        'created': [],
        'publish_date': [],
        'web_url': [],
        'platform': [],
        'recipients': [],
        'delivered': [],
        'email_opens': [],
        'unique_email_opens': [],
        'email_clicks': [],
        'unique_email_clicks': [],
        'unsubscribes': [],
        'spam_reports': [],
    }

    email_keys = {
        'recipients': 'recipients',
        'delivered': 'delivered',
        'email_opens': 'opens',
        'unique_email_opens': 'unique_opens',
        'email_clicks': 'clicks',
        'unique_email_clicks': 'unique_clicks',
        'unsubscribes': 'unsubscribes',
        'spam_reports': 'spam_reports'
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

    posts_df['publish_dow'] = posts_df['publish_date'].dt.strftime('%A')
    posts_df['email_open_rate'] = posts_df['unique_email_opens'] / posts_df['delivered'].replace(0, pd.NA)
    posts_df['email_click_rate'] = posts_df['unique_email_clicks'] / posts_df['unique_email_opens'].replace(0, pd.NA)

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
                'subtitle': row.get('subtitle', ''),
                'status': row.get('status', 'Published'),
                'platform': row.get('platform') or None,
                'creation_date': row.get('creation_date'),
                'publish_date': publish_date,
                'recipients': row.get('recipients', 0),
                'delivered': row.get('delivered', 0),
                'email_opens': row.get('email_opens', 0),
                'unique_email_opens': row.get('unique_email_opens', 0),
                'email_clicks': row.get('email_clicks', 0),
                'unique_email_clicks': row.get('unique_email_clicks', 0),
                'unsubscribes': row.get('unsubscribes', 0),
                'spam_reports': row.get('spam_reports', 0),
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


def generate_click_visualization_html(post_html, clicks_dict, unique_email_opens):
    """
    Generate HTML with click counts and CTR displayed next to each link.
    Uses improved link matching with Levenshtein distance.
    Links to URLs that appear multiple times are shown in purple with averaged
    click counts; unique links are shown in yellow/orange.

    Args:
        post_html: HTML content of the post
        clicks_dict: Dictionary mapping URLs to click counts
        unique_email_opens: Number of unique email opens for CTR calculation

    Returns:
        Modified HTML string with click visualizations
    """
    soup = BeautifulSoup(post_html, 'html.parser')

    # Find all links
    html_links = soup.find_all('a', href=True)
    html_links_raw = [link['href'] for link in html_links]

    # Use improved matching to get clicks for each link (with averaging for duplicates)
    link_to_clicks, duplicate_raw_urls = match_links_with_clicks(html_links_raw, clicks_dict)

    # Add banner at the top if there are any duplicate links with clicks
    if duplicate_raw_urls & set(link_to_clicks.keys()):
        body = soup.find('body')
        banner = soup.new_tag('div', style='background-color: #7B2D8E; color: white; padding: 12px 20px; margin: 20px auto; max-width: 720px; font-family: Arial, sans-serif; font-size: 14px; line-height: 1.5; border-radius: 0.5rem; text-align: center;')
        asterisk = soup.new_tag('sup')
        asterisk.string = '*'
        banner.append(asterisk)
        banner.append(" If multiple links in a post go to the same URL, those links are shown in purple. Click counts and CTRs for those links are averaged among all links to that URL.")
        if body:
            body.insert(0, banner)
        else:
            soup.insert(0, banner)

    for link in html_links:
        click_count = link_to_clicks.get(link['href'], 0)
        is_duplicate = link['href'] in duplicate_raw_urls

        # Calculate CTR
        ctr = (click_count / unique_email_opens * 100) if unique_email_opens > 0 else 0

        # Create a highlighted span with click info
        if click_count > 0:
            if is_duplicate:
                style = 'background-color: #E8D5F0; color: #4A0E6B; padding: 10px; margin: 10px 0; border-left: 4px solid #7B2D8E; font-weight: bold; font-family: Arial, sans-serif; font-size: 14px; white-space: nowrap; display: inline-block;'
            else:
                style = 'background-color: yellow; color: black; padding: 10px; margin: 10px 0; border-left: 4px solid orange; font-weight: bold; font-family: Arial, sans-serif; font-size: 14px; white-space: nowrap; display: inline-block;'
            click_info = soup.new_tag('span', style=style)
            # Display as integer if whole number, otherwise 1 decimal place
            click_display = int(click_count) if click_count == int(click_count) else f"{click_count:.1f}"
            has_s = "s" if click_count != 1 else ""
            if is_duplicate:
                click_info.append(f"{click_display} click{has_s} ({ctr:.1f}% CTR) average")
                asterisk = soup.new_tag('sup')
                asterisk.string = '*'
                click_info.append(asterisk)
            else:
                click_info.string = f"{click_display} click{has_s} ({ctr:.1f}% CTR)"

            # Insert the click info after the link
            link.insert_after(click_info)

    return str(soup)


async def fetch_recent_published_posts(beehiiv_token, beehiiv_pub_id, max_pages=3):
    """
    Fetch recently published posts from Beehiiv API, ordered by publish_date desc.
    Only fetches confirmed (published) posts. Stops early if oldest post on page is >24h old.

    Args:
        beehiiv_token: Beehiiv API token
        beehiiv_pub_id: Beehiiv publication ID
        max_pages: Maximum pages to fetch (10 posts per page)

    Returns:
        List of raw API post dicts
    """
    from datetime import timezone as dt_timezone
    posts_list = []
    pagination_size = 10

    try:
        timeout = aiohttp.ClientTimeout(total=60)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            for page in range(1, max_pages + 1):
                url = (
                    f"https://api.beehiiv.com/v2/publications/{beehiiv_pub_id}/posts"
                    f"?expand=stats&status=confirmed&order_by=publish_date&direction=desc"
                    f"&limit={pagination_size}&page={page}"
                )
                headers = {"Authorization": beehiiv_token}

                async with session.get(url, headers=headers) as response:
                    if response.status != 200:
                        logger.error(f"fetch_recent_published_posts page {page} status: {response.status}")
                        break
                    data = await response.json()
                    page_posts = data.get('data', [])

                if not page_posts:
                    break

                posts_list.extend(page_posts)

                # Stop early if the oldest post on this page is >24h old
                oldest_publish_ts = page_posts[-1].get('publish_date')
                if oldest_publish_ts:
                    oldest_dt = datetime.fromtimestamp(oldest_publish_ts, tz=dt_timezone.utc)
                    now = datetime.now(tz=dt_timezone.utc)
                    if (now - oldest_dt).total_seconds() > 86400:
                        break

                if len(page_posts) < pagination_size:
                    break

    except Exception:
        logger.exception("fetch_recent_published_posts failed")
        raise

    return posts_list


def build_click_viz_email_html(viz_html, post_title, site_url):
    """
    Wrap click visualization HTML with a branded header banner and footer for email delivery.

    Args:
        viz_html: The click visualization HTML string
        post_title: Title of the newsletter post
        site_url: Base URL of the LetterPulse site (e.g. https://letterpulse.com)

    Returns:
        Complete HTML string ready for email
    """
    account_url = f"{site_url.rstrip('/')}/account/"

    posts_url = f"{site_url.rstrip('/')}/posts/"

    banner = (
        # Outer wrapper — dark background matching the app sidebar
        '<div style="background-color: #212529; margin: 0 auto 24px auto; max-width: 720px; '
        'font-family: Arial, Helvetica, sans-serif; border-radius: 8px; overflow: hidden;">'

        # Top bar — brand name on dark background with green accent underline
        '<div style="padding: 20px 24px 16px 24px; text-align: center; '
        'border-bottom: 3px solid #28a745;">'
        '<span style="color: #ffffff; font-size: 22px; font-weight: 700; letter-spacing: 0.5px;">'
        'LetterPulse'
        '</span>'
        '</div>'

        # Content area — slightly lighter dark panel
        '<div style="background-color: #2c3034; padding: 20px 24px; color: #dee2e6; '
        'font-size: 14px; line-height: 1.6;">'

        # Post title
        '<div style="text-align: center; margin-bottom: 14px; font-size: 16px; color: #ffffff;">'
        f'Click visualization for <strong>{post_title}</strong>'
        '</div>'

        # Divider
        '<div style="border-top: 1px solid #495057; margin: 0 auto 14px auto; max-width: 400px;"></div>'

        # Instructions
        '<div style="text-align: center; color: #adb5bd; font-size: 13px;">'
        f'Process your post in the <a href="{posts_url}" style="color: #28a745; text-decoration: none; font-weight: 600;">Posts</a> tab '
        'to see each section\'s performance and compare with previous issues.'
        '</div>'
        '<div style="text-align: center; margin-top: 6px; color: #adb5bd; font-size: 13px;">'
        f'Toggle these emails in <a href="{account_url}" style="color: #28a745; text-decoration: none; font-weight: 600;">Account</a> settings.'
        '</div>'

        '</div>'  # end content area
        '</div>'  # end outer wrapper
    )

    footer = (
        '<div style="text-align: center; padding: 20px; margin: 20px auto; max-width: 720px; '
        'font-family: Arial, Helvetica, sans-serif; font-size: 12px; color: #6c757d;">'
        f'<a href="{account_url}" style="color: #28a745; text-decoration: none;">Manage email settings</a>'
        ' &nbsp;&middot;&nbsp; '
        f'<a href="{site_url}" style="color: #28a745; text-decoration: none;">LetterPulse</a>'
        '</div>'
    )

    # Insert banner after <body> tag (or at start if no body tag)
    soup = BeautifulSoup(viz_html, 'html.parser')
    body = soup.find('body')
    if body:
        # Insert banner as first child of body
        banner_soup = BeautifulSoup(banner, 'html.parser')
        body.insert(0, banner_soup)
        # Append footer at end of body
        footer_soup = BeautifulSoup(footer, 'html.parser')
        body.append(footer_soup)
    else:
        return banner + viz_html + footer

    return str(soup)


# =============================================================================
# Improvement Tips
# =============================================================================


class ImprovementTip(BaseModel):
    tip_type: Literal["content", "proofreading"]
    line_number: int
    tip_text: str
    why: str


class AllImprovementTips(BaseModel):
    tips: List[ImprovementTip]


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

    # --- 2) Build numbered text lines with HTML line mapping ---
    pretty_html = BeautifulSoup(html, 'html.parser').prettify()
    html_lines = pretty_html.split('\n')

    # Re-parse prettified HTML so element.sourceline == prettified line numbers
    soup2 = BeautifulSoup(pretty_html, 'html.parser')

    # Record sourceline for each <a> before replacement
    a_lines = {id(a): a.sourceline for a in soup2.find_all('a', href=True)}

    # Do <a> replacement while tracking source lines
    replacement_lines = {}
    for a in list(soup2.find_all('a', href=True)):
        src = a_lines.get(id(a))
        link_text = a.get_text(strip=True)
        href = a['href']
        if len(href) > 50:
            href = href[:47] + "..."
        replacement = f"{link_text} ({href})" if link_text else href
        new_node = NavigableString(replacement)
        a.replace_with(new_node)
        replacement_lines[id(new_node)] = src

    # Walk all NavigableString nodes, collecting (text, sourceline)
    text_source_pairs = []
    for node in soup2.descendants:
        if isinstance(node, NavigableString):
            text = node.strip()
            if not text:
                continue
            src = replacement_lines.get(id(node))
            if src is None and node.parent:
                src = node.parent.sourceline
            text_source_pairs.append((text, src))

    # Build text output
    post_text = soup2.get_text(separator='\n', strip=True)
    text_lines = [line for line in post_text.split('\n') if line.strip()]

    # Map each text line -> prettified HTML line via source pairs
    text_to_html_line = {}
    pair_cursor = 0
    for text_num, text_line in enumerate(text_lines, 1):
        for p in range(pair_cursor, len(text_source_pairs)):
            piece_text, piece_src = text_source_pairs[p]
            if piece_text in text_line and piece_src:
                text_to_html_line[text_num] = piece_src
                pair_cursor = p + 1
                break

    numbered_text = "\n".join(f"{i+1}\t{line}" for i, line in enumerate(text_lines))

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
        {"role": "user", "content": f"<post title=\"{post.title}\">\n{numbered_text}\n</post>"},
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

    valid_tips = [t for t in tips.tips if text_to_html_line.get(t.line_number)]
    sorted_tips_asc = sorted(valid_tips, key=lambda t: text_to_html_line[t.line_number])

    # Insert anchor spans (reverse order to preserve indices)
    annotated_lines = list(html_lines)
    for i, tip in enumerate(reversed(sorted_tips_asc)):
        marker_id = f"tip-target-{len(sorted_tips_asc) - 1 - i}"
        html_line_num = text_to_html_line[tip.line_number]
        anchor = f'<span id="{marker_id}" data-tip-anchor="true" data-tip-type="{tip.tip_type}"></span>'
        annotated_lines.insert(html_line_num - 1, anchor)

    newsletter_html = '\n'.join(annotated_lines)

    # Build tip card divs
    tip_cards = []
    for i, tip in enumerate(sorted_tips_asc):
        header = tip_type_to_header[tip.tip_type]
        header_color = tip_type_to_header_color[tip.tip_type]
        safe_text = html_module.escape(tip.tip_text)
        has_why = tip.why and tip.why.lower() not in ('none', '')
        why_block = ""
        if has_why:
            safe_why = html_module.escape(tip.why)
            why_block = f"""
        <div style="font-weight: bold; margin-bottom: 4px; margin-top: 10px; color: {header_color};">Why?</div>
        <div>{safe_why}</div>"""
        tip_cards.append(f"""
    <div class="tip-card {tip.tip_type}" id="tip-card-{i}" data-target="tip-target-{i}" data-tip-type="{tip.tip_type}">
        <div style="font-weight: bold; margin-bottom: 4px; color: {header_color};">{header}</div>
        <div style="margin-bottom: 10px;">{safe_text}</div>{why_block}
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

        from analytics.llm_tracker import start_tracking, finish_tracking
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