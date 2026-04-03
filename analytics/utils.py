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
from bs4 import BeautifulSoup
from django.conf import settings
from django.db import transaction
from django.db.models import F
from Levenshtein import distance as levenshtein_distance
from dotenv import load_dotenv
from analytics.prompts import INSIGHTS_PROMPT, TIP_PROMPT

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


async def llm_call(function_name, messages, model, reasoning_level, response_format=None, tools=None, user=None):
    """
    Make an async call to OpenAI API and log the request.

    Args:
        function_name: Name of the function making the call (for logging)
        messages: List of message dicts for the API
        model: Model name (e.g., "gpt-5.1")
        reasoning_level: Reasoning effort level ("low", "medium", "high")
        response_format: Optional Pydantic model for structured output
        tools: Optional list of tools
        user: Django user object for logging (optional)

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

    try:
        if asyncio.get_event_loop().is_running():
            client = AsyncOpenAI(api_key=OPENAI_API_KEY)
            try:
                if response_format is not None:
                    kwargs["text_format"] = response_format

                    response = await client.responses.parse(**kwargs)
                else:
                    response = await client.responses.create(**kwargs)
            finally:
                await client.close()
        else:
            client = OpenAI(api_key=OPENAI_API_KEY)
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

    # Log the call
    # log_file = settings.DATA_DIR / "llm_call_logs.csv"
    # file_exists = os.path.exists(log_file)

    # user_email = user.email if user and user.is_authenticated else "anonymous"

    # with open(log_file, 'a', newline='', encoding='utf-8') as f:
    #     writer = csv.writer(f)
    #     if not file_exists:
    #         writer.writerow(['function_name', 'run_datetime', 'run_time_s', 'model', 'input_tokens', 'cached_tokens', 'output_tokens', 'reasoning_tokens', 'user'])
    #     writer.writerow([
    #         function_name,
    #         start_datetime,
    #         f"{duration:.4f}",
    #         model,
    #         response.usage.input_tokens,
    #         response.usage.input_tokens_details.cached_tokens,
    #         response.usage.output_tokens,
    #         response.usage.output_tokens_details.reasoning_tokens,
    #         user_email
    #     ])

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

    #<TEMPORARY>
    import json as _json
    from django.conf import settings as _settings
    _dump_dir = _settings.DATA_DIR
    _dump_dir.mkdir(parents=True, exist_ok=True)
    _link_stats = {}
    for sec_name, stats in links_by_section.items():
        n_selected = len(select_top_bottom(stats, allocation.get(sec_name, 0)))
        _link_stats[sec_name] = {
            "total_with_clicks": len(stats),
            "selected": n_selected,
            "allocation": allocation.get(sec_name, 0),
        }
    _dump_path = _dump_dir / f"link_selection_stats_{post_id}.json"
    _dump_path.write_text(_json.dumps(_link_stats, indent=2))
    #</TEMPORARY>

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

    #<TEMPORARY>
    import time as _time
    _t0 = _time.time()
    _total_input_tokens = 0
    _total_output_tokens = 0
    #</TEMPORARY>

    max_retries = settings.LINK_PROCESS_MAX_RETRIES
    for attempt in range(1, max_retries + 2):
        response = await llm_call("process_post_links", messages, "gpt-5.4-mini", "low",
                                   response_format=AllLinkDescriptions, user=user)
        #<TEMPORARY>
        _total_input_tokens += response.usage.input_tokens
        _total_output_tokens += response.usage.output_tokens
        #</TEMPORARY>
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

    #<TEMPORARY>
    _elapsed = _time.time() - _t0
    _stats_path = _dump_dir / f"link_selection_stats_{post_id}.json"
    _existing = _json.loads(_stats_path.read_text())
    _existing["_llm"] = {
        "elapsed_seconds": round(_elapsed, 2),
        "input_tokens": _total_input_tokens,
        "output_tokens": _total_output_tokens,
        "attempts": attempt,
    }
    _stats_path.write_text(_json.dumps(_existing, indent=2))
    #</TEMPORARY>

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

        desc_lines = [f"        - {ex.section_description}" for ex in examples]

        pos_lines = []
        for i, ex in enumerate(examples):
            total = ex.post_html_length
            start_pct = round(ex.start_line / total * 100) if total else 0
            end_pct = round(ex.end_line / total * 100) if total else 0
            pos_lines.append(
                f"        - Lines {ex.start_line}-{ex.end_line} "
                f"({start_pct}%-{end_pct}% of way through HTML)" if i == 0 else f"({start_pct}%-{end_pct}%)"
            )

        first_lines = []
        last_lines = []
        for ex in examples:
            lines = ex.section_html.splitlines() if ex.section_html else []
            first_lines.append(f"        - {lines[0].strip() if lines else ''}")
            last_lines.append(f"        - {lines[-1].strip() if lines else ''}")

        part = (
            f"Name: {section_name}\n"
            f"    Typical section descriptions:\n"
            + "\n".join(desc_lines) + "\n"
            f"    Typical start and end positions:\n"
            + "\n".join(pos_lines) + "\n"
            f"    Typical first HTML lines:\n"
            + "\n".join(first_lines) + "\n"
            f"    Typical last HTML lines:\n"
            + "\n".join(last_lines)
        )
        output_parts.append(part)

    return "\n".join(output_parts)


class SectionItem(BaseModel):
    name: str
    title: Optional[str]
    description: str
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

    #<TEMPORARY>
    import json as _json
    from django.conf import settings as _settings
    _dump_dir = _settings.DATA_DIR
    _dump_dir.mkdir(parents=True, exist_ok=True)
    _dump_path = _dump_dir / f"auto_section_input_{post.post_id}.json"
    _dump_path.write_text(_json.dumps(input_messages, indent=2, ensure_ascii=True))
    #</TEMPORARY>

    #<TEMPORARY>
    import time as _time
    _t0 = _time.time()
    #</TEMPORARY>

    response = await llm_call(
        "auto_section", input_messages, "gpt-5.4", "low",
        response_format=AllSections, user=user
    )

    #<TEMPORARY>
    _elapsed = _time.time() - _t0
    _usage = response.usage
    _log_path = _dump_dir / f"auto_section_stats_{post.post_id}.json"
    _log_path.write_text(_json.dumps({
        "post_id": post.post_id,
        "elapsed_seconds": round(_elapsed, 2),
        "input_tokens": _usage.input_tokens,
        "output_tokens": _usage.output_tokens,
    }, indent=2))
    #</TEMPORARY>

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
            "description": sec.description,
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
                        section_description=s['description'],
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


async def generate_content_insights(items_display, user=None):
    """
    Generate AI insights from content items using OpenAI API.

    Operates on a single section's items. When the item count exceeds
    MAX_REPORT_ITEMS, the top and bottom half by CTR are included and
    the middle is omitted (with a note to the LLM).

    Args:
        items_display: DataFrame with columns including 'text' and 'click_rate'
        user: Django user object for credit charging (optional)

    Returns:
        OpenAI response object containing the generated report
    """
    from django.conf import settings as django_settings

    max_items = django_settings.MAX_REPORT_ITEMS

    items_display = items_display.copy()
    items_display = items_display[items_display["click_rate"].apply(lambda x: len(x) > 0)]
    items_display["max_click_rate"] = items_display["click_rate"].apply(max)
    items_display["max_click_rate_percentile"] = items_display["max_click_rate"].rank(pct=True)

    # Sort descending by CTR
    items_display = (
        items_display
        .sort_values("max_click_rate", ascending=False)
        .reset_index(drop=True)
    )

    count = len(items_display)

    if max_items < count:
        half = max_items // 2
        n_omitted = count - max_items
        items_display = pd.concat([items_display.head(half), items_display.tail(half)])
        truncation_note = (
            f"Note: {count} items exist but only the top {half} and bottom {half} "
            f"by CTR are shown ({n_omitted} middle items omitted). "
            f"The distribution is not bimodal — the omitted items fall between these two groups.\n\n"
        )
    else:
        truncation_note = ""

    newsletter_items = ""
    newsletter_items += truncation_note
    for i, row in items_display.iterrows():
        newsletter_items += f"<item {i+1}>\n" + row["text"] + "\n"
        newsletter_items += f"CTR: {round(row['max_click_rate'] * 100, 2)}% "
        newsletter_items += f"(percentile {row['max_click_rate_percentile']:.0%})\n"
        newsletter_items += f"</item {i+1}>\n\n"

    messages = [
        {"role": "user", "content": INSIGHTS_PROMPT},
        {"role": "user", "content": newsletter_items}
    ]

    try:
        response = await llm_call("generate_content_insights", messages, "gpt-5.4", "medium", user=user)
    except BadRequestError as e:
        if e.code != "context_length_exceeded":
            raise

        # Truncate each item's text to 2500 chars and retry
        logger.warning("Context length exceeded — truncating item texts to 2500 chars and retrying")
        newsletter_items = ""
        newsletter_items += truncation_note
        for i, row in items_display.iterrows():
            newsletter_items += f"<item {i+1}>\n" + row["text"][:2500] + "\n"
            newsletter_items += f"CTR: {round(row['max_click_rate'] * 100, 2)}% "
            newsletter_items += f"(percentile {row['max_click_rate_percentile']:.0%})\n"
            newsletter_items += f"</item {i+1}>\n\n"

        messages = [
            {"role": "user", "content": INSIGHTS_PROMPT},
            {"role": "user", "content": newsletter_items}
        ]

        response = await llm_call("generate_content_insights", messages, "gpt-5.4", "medium", user=user)

    return response


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

    posts_df = posts_df.drop(columns=['created', 'publish_date_raw', 'platform'])

    return posts_df


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


async def annotate_post_html(post_id, content_perf_evals, beehiiv_token, beehiiv_pub_id, user=None):
    """
    Fetch post HTML, get LLM tips based on performance evaluations,
    and insert them with yellow highlighting.

    Args:
        post_id: The Beehiiv post ID
        content_perf_evals: List of performance evaluation texts to inform tips
        beehiiv_token: Beehiiv API token
        beehiiv_pub_id: Beehiiv publication ID
        user: Django user object for credit charging (optional)

    Returns:
        Modified HTML string with tips inserted
    """
    # Step 1: Fetch the HTML
    semaphore = asyncio.Semaphore(1)
    timeout = aiohttp.ClientTimeout(total=60)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        post_id_result, post_html = await fetch_post_html(session, post_id, semaphore, beehiiv_token, beehiiv_pub_id)
        
    # Step 2: Split into numbered lines
    soup = BeautifulSoup(post_html, 'html.parser')
    all_lines = soup.prettify().split('\n')
    numbered_lines = [f"{i+1}\t{line}" for i, line in enumerate(all_lines)]
    
    # Step 3: Define tip schema
    class Tip(BaseModel):
        tip_type: Literal["content", "wording"]
        line_number: int
        tip_text: str
        why: str
    
    class AllTips(BaseModel):
        tips: List[Tip]
    
    # Step 4: Prompt LLM for tips
    # Combine all performance evaluations with XML tags
    combined_perf_eval = "\n\n".join([
        f"<performance_evaluation_{i+1}>\n{eval_text}\n</performance_evaluation_{i+1}>"
        for i, eval_text in enumerate(content_perf_evals)
    ])
    
    messages = [
        {"role": "user", "content": f"{combined_perf_eval}"},
        {"role": "user", "content": "<html_document>\n" + '\n'.join(numbered_lines) + "\n</html_document>"},
        {"role": "system", "content": TIP_PROMPT},
    ]

    response = await llm_call("annotate_post_html", messages, "gpt-5.1", "medium", response_format=AllTips, user=user)
    tips = response.output[-1].content[0].parsed
    
    # Step 5: Insert tips with yellow highlighting
    # Sort tips by line number in descending order to avoid offset issues
    sorted_tips = sorted(tips.tips, key=lambda x: x.line_number, reverse=True)
    tip_type_to_header = {
        "content": "📰 Content Tip",
        "wording": "✍️ Wording Tip"
    }
    
    for tip in sorted_tips:
        if 0 < tip.line_number <= len(all_lines):
            # Create tip HTML with yellow highlighting
            tip_html = f"""
<div style="
    background-color: yellow;
    color: black !important;
    padding: 10px;
    margin: 10px 0;
    border-left: 4px solid orange;
    font-family: Arial, sans-serif;
    font-size: 14px;
    line-height: 1.6;
    text-align: left;
    font-style: normal !important;
    font-weight: normal !important;
    text-decoration: none !important;

    display: flex;
    flex-direction: column;
    gap: 4px;
">
    <div style="
        width: 0;
        height: 0;
        border-left: 8px solid transparent;
        border-right: 8px solid transparent;
        border-bottom: 12px solid #4A90A4;
        margin: 0 auto 8px;
    "></div>
    <div style="
        font-weight: bold !important;
        font-style: normal !important;
        text-decoration: none !important;
    ">
        {tip_type_to_header[tip.tip_type]}
    </div>
    <div style="
        font-weight: normal !important;
        font-style: normal !important;
        text-decoration: none !important;
    ">
        {tip.tip_text}
    </div>
    <div style="
        font-weight: bold !important;
        margin-top: 10px;
        font-style: normal !important;
        text-decoration: none !important;
    ">
        Why?
    </div>
    <div style="
        font-weight: normal !important;
        font-style: normal !important;
        text-decoration: none !important;
    ">
        {tip.why}
    </div>
</div>
"""        
            # Insert at the specified line (converting to 0-indexed)
            all_lines.insert(tip.line_number - 1, tip_html)
    
    # Step 6: Return the modified HTML
    return '\n'.join(all_lines)


async def annotate_posts_parallel(post_ids, content_perf_evals, beehiiv_token, beehiiv_pub_id, user=None):
    """
    Annotate multiple posts in parallel using asyncio.

    Args:
        post_ids: List of Beehiiv post IDs to annotate
        content_perf_evals: List of performance evaluation texts to inform tips
        beehiiv_token: Beehiiv API token
        beehiiv_pub_id: Beehiiv publication ID
        user: Django user object for credit charging (optional)

    Returns:
        Dictionary mapping post_ids to their annotated HTML content
    """
    tasks = [annotate_post_html(post_id, content_perf_evals, beehiiv_token, beehiiv_pub_id, user=user) for post_id in post_ids]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    # Build result dictionary, filtering out exceptions
    annotated_htmls = {}
    for post_id, result in zip(post_ids, results):
        if isinstance(result, Exception):
            logger.error(f"Error annotating post {post_id}: {result}")
        else:
            annotated_htmls[post_id] = result
    
    return annotated_htmls


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