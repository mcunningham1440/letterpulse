"""
Utility functions for the Django analytics app.
Adapted from the original utils.py for Streamlit.
"""

import json
from collections import Counter
from openai import AsyncOpenAI, OpenAI, BadRequestError
from pydantic import BaseModel
from typing import List, Literal
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


class LinkDescription(BaseModel):
    tag_id: int
    description: str

class AllLinkDescriptions(BaseModel):
    links: List[LinkDescription]


async def process_post_links(session, post_id, user, beehiiv_token, beehiiv_pub_id):
    """
    Extract, describe, and score all clicked links from a single post.

    Fetches HTML and click data from the Beehiiv API, extracts links,
    matches them with click data, deduplicates by stripping tracking params,
    tags top links in the HTML, and uses GPT to describe each tagged link.

    Args:
        session: aiohttp.ClientSession
        post_id: Beehiiv post ID
        user: Django user object
        beehiiv_token: Beehiiv API bearer token
        beehiiv_pub_id: Beehiiv publication ID

    Returns:
        List of dicts: [{post_id, raw_url, description, rank_in_post, mean_ctr, mean_clicks}, ...]
    """
    from analytics.models import Post

    post = await asyncio.to_thread(
        lambda: Post.objects.get(post_id=post_id, user=user)
    )
    unique_opens = post.unique_email_opens or 0

    api_headers = {"Authorization": beehiiv_token}

    async def get_html():
        async with session.get(
            f"https://api.beehiiv.com/v2/publications/{beehiiv_pub_id}/posts/{post_id}?expand=free_email_content",
            headers=api_headers,
        ) as resp:
            return await resp.json()

    async def get_clicks():
        async with session.get(
            f"https://api.beehiiv.com/v2/publications/{beehiiv_pub_id}/posts/{post_id}?expand=stats",
            headers=api_headers,
        ) as resp:
            return await resp.json()

    html_data, clicks_data_json = await asyncio.gather(get_html(), get_clicks())

    post_html = html_data.get('data', {}).get('content', {}).get('free', {}).get('email', '')
    clicks_data = clicks_data_json.get('data', {}).get('stats', {}).get('clicks', [])

    # Build clicks dict (URL -> max unique clicks)
    clicks_dict = {}
    for link_entry in clicks_data:
        url = link_entry['url']
        uc = link_entry['email']['unique_clicks']
        if uc > 0 and url != "https://www.beehiiv.com/":
            clicks_dict[url] = max(uc, clicks_dict.get(url, 0))

    if not post_html:
        logger.warning(f"No HTML fetched for {post_id}")
        return []

    # Extract links and match clicks
    soup = BeautifulSoup(post_html, 'html.parser')
    html_links_raw = [link['href'] for link in soup.find_all('a', href=True)]
    link_to_clicks, _ = match_links_with_clicks(html_links_raw, clicks_dict)

    # Deduplicate by processed URL (stripped of &_bhlid= tracking params), compute mean CTR
    processed_groups = {}
    for raw_url in html_links_raw:
        processed = raw_url.split("&_bhlid=")[0]
        clicks = link_to_clicks.get(raw_url, 0)
        ctr = (clicks / unique_opens * 100) if unique_opens > 0 else 0
        if processed not in processed_groups:
            processed_groups[processed] = {'ctrs': [], 'clicks': []}
        processed_groups[processed]['ctrs'].append(ctr)
        processed_groups[processed]['clicks'].append(clicks)

    link_stats = []
    for url, data in processed_groups.items():
        mean_ctr = sum(data['ctrs']) / len(data['ctrs'])
        mean_clicks = sum(data['clicks']) / len(data['clicks'])
        link_stats.append({
            'url': url,
            'mean_ctr': mean_ctr,
            'mean_clicks': mean_clicks,
        })

    # Filter to links with clicks > 0
    link_stats = [ls for ls in link_stats if ls['mean_ctr'] > 0]

    # Sort by CTR descending
    link_stats.sort(key=lambda x: x['mean_ctr'], reverse=True)

    # Apply TOP_P or TOP_N filtering
    top_p = settings.LINK_PROCESS_TOP_P
    top_n = settings.LINK_PROCESS_TOP_N
    if top_p is not None:
        all_ctrs = [ls['mean_ctr'] for ls in link_stats]
        if all_ctrs:
            sorted_ctrs = sorted(all_ctrs)
            idx = int(top_p * len(sorted_ctrs))
            threshold = sorted_ctrs[min(idx, len(sorted_ctrs) - 1)]
            top_links = [ls for ls in link_stats if ls['mean_ctr'] >= threshold]
        else:
            top_links = []
    elif top_n is not None:
        top_links = link_stats[:top_n]
    else:
        top_links = link_stats

    if not top_links:
        return []

    # Build URL-to-tag mapping
    url_to_tag = {ls['url']: i for i, ls in enumerate(top_links, start=1)}

    # Tag top links in HTML with data-tag attributes
    soup_tagged = BeautifulSoup(post_html, 'html.parser')
    for link in soup_tagged.find_all('a', href=True):
        processed = link['href'].split("&_bhlid=")[0]
        if processed in url_to_tag:
            link['data-tag'] = f"LINK_TAG_{url_to_tag[processed]}"

    tagged_html = str(soup_tagged)
    n_links = len(top_links)

    tag_summary = "\n".join(
        f"  LINK_TAG_{i}: URL={ls['url'][:120]}"
        for i, ls in enumerate(top_links, start=1)
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
    for i, ls in enumerate(top_links, start=1):
        rows.append({
            'post_id': post_id,
            'raw_url': ls['url'],
            'description': desc_by_tag.get(i, ''),
            'rank_in_post': i,
            'mean_ctr': round(ls['mean_ctr'], 2),
            'mean_clicks': round(ls['mean_clicks'], 1),
        })
    return rows


async def process_posts_links_parallel(post_ids, user, beehiiv_token, beehiiv_pub_id):
    """
    Process multiple posts in parallel, extracting and describing links.

    Args:
        post_ids: List of Beehiiv post IDs
        user: Django user object
        beehiiv_token: Beehiiv API bearer token
        beehiiv_pub_id: Beehiiv publication ID

    Returns:
        Dict mapping post_id to list of link row dicts
    """
    semaphore = asyncio.Semaphore(10)

    async def bounded(post_id):
        async with semaphore:
            return post_id, await process_post_links(session, post_id, user, beehiiv_token, beehiiv_pub_id)

    async with aiohttp.ClientSession() as session:
        results = await asyncio.gather(
            *[bounded(pid) for pid in post_ids],
            return_exceptions=True
        )

    results_by_post = {}
    for result in results:
        if isinstance(result, Exception):
            logger.error(f"Error processing post links: {result}")
        else:
            post_id, rows = result
            results_by_post[post_id] = rows

    return results_by_post


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

    output_parts = []

    for section_name, rows in sorted(by_name.items()):
        # Sort by temporal proximity to target post
        rows_sorted = sorted(
            rows,
            key=lambda r: abs((r.post.publish_date - target_date).total_seconds()),
        )
        examples = rows_sorted[:n_examples]

        desc_lines = [f"        - {ex.section_description}" for ex in examples]

        pos_lines = []
        for ex in examples:
            total = ex.post_html_length
            start_pct = round(ex.start_line / total * 100) if total else 0
            end_pct = round(ex.end_line / total * 100) if total else 0
            pos_lines.append(
                f"        - Lines {ex.start_line}-{ex.end_line} "
                f"({start_pct}%-{end_pct}%)"
            )

        first_lines = []
        last_lines = []
        for ex in examples:
            lines = ex.section_html.splitlines() if ex.section_html else []
            first_lines.append(f"        - {lines[0].strip() if lines else ''}")
            last_lines.append(f"        - {lines[-1].strip() if lines else ''}")

        part = (
            f"Section name: {section_name}\n"
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


async def auto_section(html, user, publication, post, n_examples=5):
    """Run an agentic loop to identify sections in newsletter HTML.

    Args:
        html: The newsletter HTML string (raw, not line-numbered).
        user: Django User object.
        publication: Publication model instance.
        post: Target Post model instance.
        n_examples: Number of nearby-post examples per section.

    Returns:
        List of section dicts with keys: name, title, description,
        start_line, end_line, section_html, post_html_length.
    """
    from asgiref.sync import sync_to_async

    html_lines = html.splitlines()
    post_html_length = len(html_lines)
    numbered_html = "\n".join(f"{i+1}: {line}" for i, line in enumerate(html_lines))

    sections_prompt = await sync_to_async(build_sections_desc)(
        user, publication, post, n_examples
    )

    known_sections_block = ""
    if sections_prompt:
        known_sections_block = f"\nKnown sections from nearby posts:\n{sections_prompt}\n\n"

    input_messages = [
        {
            "role": "user",
            "content": (
                f"Here is the newsletter HTML (line-numbered):\n\n{numbered_html}\n\n"
                f"{known_sections_block}"
                "Identify all distinct sections in this HTML. "
                "Use locate_section for each section you find. "
                "When finished, call end_workflow."
            ),
        }
    ]

    tools = [
        {
            "type": "function",
            "name": "locate_section",
            "description": "Identify a section's name, description, and boundaries in the HTML.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Short identifier for the section.",
                    },
                    "title": {
                        "type": "string",
                        "description": "Display title of the section.",
                    },
                    "description": {
                        "type": "string",
                        "description": "Brief description of what this section contains.",
                    },
                    "start_line": {
                        "type": "integer",
                        "description": "1-based starting line number in the HTML.",
                    },
                    "end_line": {
                        "type": "integer",
                        "description": "1-based ending line number in the HTML.",
                    },
                },
                "required": ["name", "title", "description", "start_line", "end_line"],
                "additionalProperties": False,
            },
            "strict": True,
        },
        {
            "type": "function",
            "name": "end_workflow",
            "description": "Call this when all sectioning work is complete.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
            "strict": True,
        },
    ]

    max_iterations = getattr(settings, 'SECTION_MAX_AGENT_ITERATIONS', 10)
    located_sections = []

    for _ in range(max_iterations):
        response = await llm_call(
            "auto_section", input_messages, "gpt-5.4", "low",
            tools=tools, user=user
        )

        # Append all output for context continuity
        input_messages += response.output

        tool_calls = [item for item in response.output if item.type == "function_call"]

        if not tool_calls:
            break

        should_stop = False

        for tool_call in tool_calls:
            args = json.loads(tool_call.arguments)

            if tool_call.name == "locate_section":
                located_sections.append(args)

            input_messages.append({
                "type": "function_call_output",
                "call_id": tool_call.call_id,
                "output": json.dumps(None),
            })

            if tool_call.name == "end_workflow":
                should_stop = True

        if should_stop:
            break

    # Enrich each section with section_html and post_html_length
    results = []
    for sec in located_sections:
        start = max(1, sec["start_line"])
        end = min(post_html_length, sec["end_line"])
        section_html = "\n".join(html_lines[start - 1:end])
        results.append({
            "name": sec["name"],
            "title": sec["title"],
            "description": sec["description"],
            "start_line": start,
            "end_line": end,
            "section_html": section_html,
            "post_html_length": post_html_length,
        })

    return results


async def process_post_sections(session, post_id, user, beehiiv_token, beehiiv_pub_id, publication):
    """Fetch post HTML and run auto_section to identify sections.

    Args:
        session: aiohttp ClientSession.
        post_id: Beehiiv post ID.
        user: Django User object.
        beehiiv_token: Beehiiv API bearer token.
        beehiiv_pub_id: Beehiiv publication ID.
        publication: Publication model instance.

    Returns:
        List of section dicts.
    """
    from asgiref.sync import sync_to_async
    from analytics.models import Post

    semaphore = asyncio.Semaphore(5)
    _, html = await fetch_post_html(session, post_id, semaphore, beehiiv_token, beehiiv_pub_id)

    if not html:
        logger.error(f"No HTML fetched for post {post_id}")
        return []

    post = await sync_to_async(Post.objects.get)(post_id=post_id, user=user)

    sections = await auto_section(html, user, publication, post)
    return sections


async def process_posts_sections_sequential(post_ids, user, beehiiv_token, beehiiv_pub_id, publication):
    """Process multiple posts sequentially for section extraction.

    Posts are processed one at a time so each post's sections are saved to the
    database before the next post is processed, allowing build_sections_desc
    to use prior results as context.

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
    from analytics.models import Post, Section as SectionModel, ProcessedPost

    results_by_post = {}

    async with aiohttp.ClientSession() as session:
        for post_id in post_ids:
            try:
                sections = await process_post_sections(
                    session, post_id, user, beehiiv_token, beehiiv_pub_id, publication
                )

                # Save sections to DB immediately so the next post can use them as context
                post = await sync_to_async(Post.objects.get)(post_id=post_id, user=user)

                await sync_to_async(
                    SectionModel.objects.filter(post=post, user=user).delete
                )()

                if sections:
                    section_objects = [
                        SectionModel(
                            post=post,
                            user=user,
                            publication=publication,
                            section_name=s['name'],
                            section_description=s['description'],
                            start_line=s['start_line'],
                            end_line=s['end_line'],
                            post_html_length=s['post_html_length'],
                            section_html=s['section_html'],
                        )
                        for s in sections
                    ]
                    await sync_to_async(SectionModel.objects.bulk_create)(section_objects)

                await sync_to_async(ProcessedPost.objects.update_or_create)(
                    post=post,
                    user=user,
                    defaults={'publication': publication}
                )

                results_by_post[post_id] = sections

            except Exception as e:
                logger.error(f"Error processing sections for post {post_id}: {e}", exc_info=True)

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