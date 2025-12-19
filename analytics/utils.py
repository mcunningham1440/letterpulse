"""
Utility functions for the Django analytics app.
Adapted from the original utils.py for Streamlit.
"""

import json
from openai import AsyncOpenAI, OpenAI
from pydantic import BaseModel
from typing import List, Literal
import pandas as pd
import asyncio
import aiohttp
import os
import logging
from datetime import datetime
import time
from bs4 import BeautifulSoup
from django.conf import settings
from django.db import transaction
from django.db.models import F
import numpy as np
from Levenshtein import distance as levenshtein_distance
from dotenv import load_dotenv

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
                f"Not enough credits. "
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
                        if link_data['email']['unique_clicks'] > 0:
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


def match_links_with_clicks(html_links_raw, clicks_dict, relative_distance_cutoff=0.4):
    """
    Match HTML links with click report links using exact matching first,
    then Levenshtein distance for unmatched links.
    
    Args:
        html_links_raw: List of raw HTML links (may contain jwt_token)
        clicks_dict: Dictionary mapping click report URLs to click counts
        relative_distance_cutoff: Maximum relative Levenshtein distance for fuzzy matching (default 0.4)
    
    Returns:
        Dictionary mapping cleaned HTML links to their click counts
    """
    # Step 1: Clean HTML links by removing jwt_token
    html_links_cleaned = [link.replace("&jwt_token={{jwt_token}}", "") for link in html_links_raw]
    
    # Track which HTML links are still available for matching
    available_html_links = set(html_links_cleaned)
    
    # Result dictionary: HTML link -> click count
    link_to_clicks = {}
    
    # Step 2: Exact matching pass
    for click_link, click_count in clicks_dict.items():
        if click_link in available_html_links:
            link_to_clicks[click_link] = click_count
            available_html_links.remove(click_link)
    
    # Step 3: Fuzzy matching with Levenshtein distance for unmatched click report links
    unmatched_click_links = [link for link in clicks_dict.keys() if link not in link_to_clicks]
    
    if unmatched_click_links and available_html_links:
        available_html_links_list = list(available_html_links)
        
        for click_link in unmatched_click_links:
            # Calculate distances to all remaining HTML links
            distances = [levenshtein_distance(click_link, html_link) for html_link in available_html_links_list]
            
            # Find the closest match
            min_idx = np.argmin(distances)
            closest_html_link = available_html_links_list[min_idx]
            min_distance = distances[min_idx]
            
            # Calculate relative distance
            max_length = max(len(click_link), len(closest_html_link))
            relative_distance = min_distance / max_length if max_length > 0 else 0
            
            # Only match if relative distance is below cutoff
            if relative_distance <= relative_distance_cutoff:
                link_to_clicks[closest_html_link] = clicks_dict[click_link]
                # Remove from available list to prevent duplicate matching
                available_html_links_list.pop(min_idx)
    
    return link_to_clicks


async def extract_items(post_html, content_desc, clicks_dict, title, post_date, unique_email_opens, user=None):
    """
    Extract items from a single post HTML using AI.

    Args:
        post_html: HTML content of the post
        content_desc: Description of the content to extract
        clicks_dict: Dictionary mapping URLs to click counts for this post
        title: Post title
        post_date: Date the post was published
        unique_email_opens: Number of unique email opens
        user: Django user object for credit charging (optional)

    Returns:
        DataFrame containing extracted items
    """
    # Step 1: Get line numbers for sections matching the content description
    parsing_prompt = """You are given an HTML document with line numbers.
Your task is to identify items within the HTML that correspond to the ContentDescription at bottom.
Provide the start and end line numbers (inclusive) for each item that matches the description.
Make each discrete item a separate pair of start and end line numbers.
Do not include any items that do not match the description.

Sometimes, the user may be referring to a section which contains multiple similar items, as in Example1.

<Example1>
**New product releases**
-Tesla unveils the Roadster X-Plus, a lightweight carbon-ceramic edition with a 0–60 time of 1.7 seconds and a 700-mile solid-state battery pack.
-BMW releases the i5 Touring ActiveHybrid, featuring adaptive solar-roof charging and an AI-driven energy-routing system for long-distance commuters.
-Toyota launches the Land Cruiser Micro-Hybrid, a compact off-road SUV aimed at urban explorers, with a detachable roof rack drone for scouting terrain.
-Rivian debuts the R1T TrailForge Edition, adding magnetically adjustable suspension plates and an onboard terrain-mapping assistant trained on 40M trail miles.
</Example1>

In this case, if the user asked for “new product releases”, you would make each one of the four news items into a separate item, unless specifically instructed otherwise.
Make sure to include all of them, for instance, in this case, you would return four pairs of start and end line numbers.

In other cases, the user may be referring to a single item, as in Example2.

<Example2>
**New product release**
Audi introduces the A7 NeoSport, a sleek fastback hybrid that pairs a 2.0L turbo engine with a next-gen ultracapacitor boost system, delivering instantaneous torque without relying on traditional lithium-ion packs. Early testers report near-zero lag during acceleration and a smoother handoff between electric assist and combustion power than any previous Audi hybrid.

The NeoSport also debuts Audi’s new “HoloHUD” panoramic projection system, which layers 3D navigation cues, lane boundaries, and contextual alerts directly onto the windshield. The display dynamically adapts to sunlight, fog, and glare, giving drivers a floating augmented-reality interface that feels more like a fighter jet cockpit than a dashboard.
</Example2>

In this case, if the user asked for “new product release”, you would make it a single item, returning a single pair of start and end line numbers.

Use your judgement and the content description to determine whether to extract multiple items or a single item.
"""
    
    content_description = f"""<ContentDescription>
{content_desc}
</ContentDescription>"""
    
    soup = BeautifulSoup(post_html, 'html.parser')
    all_lines = soup.prettify().split('\n')
    numbered_lines = [f"{i+1}\t{line}" for i, line in enumerate(all_lines)]
    
    class Item(BaseModel):
        StartLine: int
        EndLine: int

    class AllItems(BaseModel):
        Items: List[Item]

    messages = [
        {"role": "system", "content": parsing_prompt},
        {"role": "user", "content": '\n'.join(numbered_lines)},
        {"role": "user", "content": content_description}
    ]

    # 5.1: 'none', 'low', 'medium', and 'high'
    # 5-mini: 'minimal', 'low', 'medium', and 'high'
    response = await llm_call("extract_items", messages, "gpt-5.1", "low", response_format=AllItems, user=user)
    output = response.output[-1].content[0].parsed

    # Step 2: Extract text and links from each section
    news_items = []

    html_links_raw = [link['href'] for link in soup.find_all('a') if link.has_attr('href')]
    link_to_clicks = match_links_with_clicks(html_links_raw, clicks_dict)

    for item in output.Items:
        reconstructed_html = "\n".join(all_lines[item.StartLine - 1:item.EndLine])
        soup = BeautifulSoup(reconstructed_html, 'html.parser')
        
        # Extract text
        text = soup.get_text(" ", strip=True)
                        
        # Clean HTML links for output
        selected_links = [link['href'].replace("&jwt_token={{jwt_token}}", "") for link in soup.find_all('a') if link.has_attr('href')]
        
        # Get clicks for each link using the matched results
        clicks = [link_to_clicks.get(link, 0) for link in selected_links]

        news_items.append({
            "post_title": title,
            "post_date": post_date,
            "text": text,
            "links": selected_links,
            "clicks": clicks,
            "click_rate": [clicks / unique_email_opens if unique_email_opens > 0 else 0 for clicks in clicks]
        })

    items = pd.DataFrame(news_items)

    return items


async def extract_items_parallel(posts_data, content_desc, htmls, clicks_by_id, user=None):
    """
    Extract items from multiple posts in parallel using asyncio.

    Args:
        posts_data: List of tuples containing (post_id, title, html_filename, post_date, unique_email_opens)
        content_desc: Description of content to extract
        htmls: Dictionary of HTML content keyed by filename
        clicks_by_id: Dictionary mapping post IDs to their clicks dictionaries
        user: Django user object for credit charging (optional)

    Returns:
        List of DataFrames containing extracted items
    """
    tasks = []
    for post_id, title, html_filename, post_date, unique_email_opens in posts_data:
        if html_filename in htmls and post_id in clicks_by_id:
            current_html = htmls[html_filename]
            clicks_dict = clicks_by_id[post_id]
            task = extract_items(current_html, content_desc, clicks_dict, title, post_date, unique_email_opens, user=user)
            tasks.append(task)
    
    # Run all extractions concurrently
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    # Filter out any exceptions and return successful results
    items_list = []
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            # Log the error but continue with other results
            logger.error(f"Error extracting items from post {i}: {str(result)}")
        else:
            items_list.append(result)
    
    # Reverse the list so newer posts appear first when concatenated
    return items_list[::-1]


async def generate_content_insights(items_display, user=None):
    """
    Generate AI insights from content items using OpenAI API.

    Args:
        items_display: DataFrame containing the content items with clicks
        user: Django user object for credit charging (optional)

    Returns:
        OpenAI response object containing structured insights with 3 tags
    """
    analysis_prompt = """
<instructions>
You are an expert data analyst.
You have been given a list of items featured in a newsletter, each with an associated click-through rate (CTR) and percentile ranking among all items.
Produce a report analyzing the dataset of items and their click-through rates (CTR), following the provided template.
Aim for ~3 top performing item archetypes, each supported by multiple examples from the data, and 1-3 underperforming archetypes for contrast. 
Highlight key insights and patterns. 
Set your "top tier" threshold to include 5-10 of the top items by CTR.
Use markdown formatting with headings, bullet points, and sections as shown in the template.
</instructions>

<template>
Here’s what stands out when you look at the highest‑click-through rate (CTR) links (90th percentile and above) and compare them to the rest.

Top tier (≥90th percentile):

- ID 53 – Chicago Tech Mixer and Social (Tech / AI / Data) – 9.6% (100%)
- ID 7 – From Idea to MVP – 9.3% (97%)
- ID 27 – Chicago Coffee Club | Vertical AI Founders & Funders – 9.3% (97%)
- ID 4 – Machine Learning Reading Group Social Hour – 9.2% (94%)
- ID 48 – The AI Collective – 9.2% (94%)
- ID 50 – Fireside Chat: Unlocking the Power of Context Engineering w/ Pinecone – 9.0% (92%)
- ID 43 – Emerging Tech Inno Week: AI Day – 8.7% (90%)


---

### 1. Community‑first, social AI/tech gatherings perform extremely well

High performers with this flavor:

- ID 53 – Chicago Tech Mixer and Social (Tech / AI / Data) – 9.6% (100%)
- ID 4 – Machine Learning Reading Group Social Hour – 9.2% (94%)
- ID 48 – The AI Collective – 9.2% (94%)
- ID 27 – Chicago Coffee Club | Vertical AI Founders & Funders – 9.3% (97%)
- ID 29 – Chicago Data Happy Hour – 7.2% (80%)
- ID 54 – Chicago Tech Connect Breakfast – 7.3% (83%)
- ID 5 – Chicago – International Generalist Day Meetup – 7.0% (76%)

**Common traits:**

- The framing is explicitly social or communal: “Mixer and Social,” “Happy Hour,” “Breakfast,” “Coffee Club,” “Collective,” “Social Hour.”
- Often broad but targeted topics: “Tech / AI / Data,” “Machine Learning,” “Vertical AI” rather than a hyper‑narrow niche.
- Implied low barrier to entry: you can “drop in,” meet people, and benefit even if you’re not deeply technical or prepared.
- Titles emphasize the *community* more than a specific talk title or speaker.

**Event type that works:**  
Community‑centric meetups and socials for AI / tech / data people, with clear networking and “come hang out” positioning.


---

### 2. Builder‑focused, “ship something” sessions are very strong

High performers in this category:

- ID 7 – From Idea to MVP – 9.3% (97%)
- ID 0 – Vibe Coding Unlocked: Effortless App Building with Databricks – 7.5% (85%)
- ID 31 – Building an MCP in Node.js & Using WebAssembly to Safely Run Unsafe Code – 6.7% (70%)
- ID 30 – AI in Healthcare: Innovation, Infrastructure & Human-Centered Trust – 6.8% (74%)

**Common traits:**

- Clear “you will build / create / launch” promise: “Idea to MVP,” “App Building,” “Building an MCP.”
- Concrete outcomes or skills implied, often around modern stacks (Databricks, Node.js, WebAssembly) that builders care about.
- Strong appeal to early‑stage founders and technical product people.

**Event type that works:**  
Hands‑on or concept‑to‑product sessions that speak directly to founders, makers, and engineers trying to get from idea to something real.


---

### 3. AI‑specific and infra‑deep‑dive content pops, especially with credible brands

Strong examples:

- ID 50 – Fireside Chat: Unlocking the Power of Context Engineering w/ Pinecone – 9.0% (92%)
- ID 43 – Emerging Tech Inno Week: AI Day – 8.7% (90%)
- ID 37 – AI Tinkerers #21 Hosted by Drive Capital – 8.2% (88%)

**Common traits:**

- Explicit AI focus, often on infrastructure or cutting‑edge concepts: “Context Engineering,” “AI Day,” “AI Tinkerers.”
- Involvement of recognizable tech brands (Pinecone, NVIDIA, Supermicro) or known communities (AI Tinkerers).
- Framed as deep dives or insider discussions (e.g., “Fireside Chat,” “Insights,” “Tinkerers”) rather than generic panels.

**Event type that works:**  
AI‑forward, infra‑oriented sessions with a clear advanced topic and credible technical brand or community.


---


### What underperforms by comparison

Lower‑CTR events cluster around a few themes:

1. **Finance/crypto/policy‑heavy without a builder angle**

   - ID 44 – Chicago Stablecoin Social – 2.0% (2%)
   - ID 40 – Blockchain & Digital Assets: US Policy Trends & 2026 Outlook – 2.6% (13%)
   - ID 46 – Money Moves: The Future of Investment Management – 2.5% (10%)
   - ID 32 – Bitwise Crypto Diligence Summit – 3.2% (22%)
   - ID 33 – VC / LP Gallery Series w Private Chef: II – 2.6% (13%)

   These skew investor/finance/policy‑oriented, with little in the title about how founders or builders will benefit directly.

2. **Generic networking / corporate events with vague outcomes**

   - ID 56 – Connect & Grow Chicago – 4.9% (47%)
   - ID 24 – Hispanic Heritage Month Celebration 1871 X LIT – 2.4% (8%)
   - ID 55 – Navigate the Patient Landscape – 4.6% (41%)

   These may be valuable for community or mission reasons, but the title doesn’t promise a sharp, actionable benefit to a founder/AI/tech builder audience.

3. **Recurring programs without a specific topical hook**

   - IDs 6, 17, 57 – 1 Million Cups Chicago – low CTRs across the board.
   - ID 11 – ChiTech Fall: Gravity Outlook – 2.1% (4%)
   - ID 42 – Java Global Insights: Innovation w/ Discover and Brazilian Experts – 2.1% (4%)

   The framing is abstract (“Outlook,” “Insights,” “Innovation”) and not clearly tied to what this specific audience will learn, build, or who they’ll meet.

These types of events may still be important for diversity of programming, ecosystem health, or specific partner commitments, but they are not your primary CTR drivers.


---

### Summary: Top‑performing link archetypes

Based on CTR and percentiles, the consistently high‑engagement link types are:

1. **Community‑centric AI/tech socials**
   - Mixers, happy hours, breakfasts, “collectives,” and “clubs” with a clear AI/tech/data focus and a strong networking/social promise.

2. **Builder‑oriented sessions**
   - Events that promise movement from idea → MVP, app building, or concrete technical outcomes that appeal to founders and engineers.

3. **AI‑infra and advanced topic deep dives with strong brands**
   - AI Day, AI Tinkerers, context engineering, infra for financial services—especially when co‑branded with known vendors (Pinecone, NVIDIA, etc.).

If you’re optimizing for engagement, skew your programming and naming toward these patterns, and treat generic finance/policy events, broad “innovation” talks, and unspecific recurring programs as secondary or as vehicles for other goals (e.g., ecosystem signaling, partner relations) rather than CTR workhorses.
</template>
"""

    # Use the items with clicks formatted as string
    items_display["max_click_rate"] = items_display["click_rate"].apply(max)
    items_display["max_click_rate_percentile"] = items_display["max_click_rate"].rank(pct=True)

    items_display = items_display.sort_values(by="max_click_rate", ascending=False).reset_index(drop=True)
    
    newsletter_items = ""
    for i, row in items_display.iterrows():
        newsletter_items += f"ID {i}. " + row["text"] + "\n"
        newsletter_items += f"CTR: {round(row['max_click_rate'] * 100, 2)}% (percentile {row['max_click_rate_percentile']:.0%})\n\n"
        
    messages = [
        {"role": "user", "content": analysis_prompt},
        {"role": "user", "content": newsletter_items}
    ]

    response = await llm_call("generate_content_insights", messages, "gpt-5.1", "medium", user=user)

    return response


def load_posts_from_db(publication_id=None):
    """Load posts from database into a DataFrame, optionally filtered by publication.

    Args:
        publication_id: Optional Beehiiv publication ID (e.g., 'pub_xxx') to filter posts.
                        If None, returns all posts.
    """
    from .models import Post

    queryset = Post.objects.all()
    if publication_id:
        queryset = queryset.filter(publication__pub_id=publication_id)

    posts = queryset.values(
        'post_id', 'title', 'subtitle', 'status', 'creation_date', 'publish_date_cst',
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
        'subtitle': [],
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

    # Convert API status to simplified status: "Draft" or "Published"
    posts_df['status'] = posts_df['status'].apply(
        lambda s: "Draft" if s == 'draft' else "Published"
    )

    # Convert creation date (always present)
    posts_df['creation_date'] = pd.to_datetime(posts_df['created'], unit='s', utc=True)
    posts_df['creation_date'] = posts_df['creation_date'].dt.tz_convert('America/Chicago')

    # Add derived columns - handle drafts which have no publish_date
    posts_df['publish_date_cst'] = pd.to_datetime(posts_df['publish_date'], unit='s', utc=True, errors='coerce')
    # Convert to Chicago timezone where not null
    mask = posts_df['publish_date_cst'].notna()
    posts_df.loc[mask, 'publish_date_cst'] = posts_df.loc[mask, 'publish_date_cst'].dt.tz_convert('America/Chicago')

    posts_df['publish_dow'] = posts_df['publish_date_cst'].dt.strftime('%A')
    posts_df['email_open_rate'] = posts_df['unique_email_opens'] / posts_df['delivered'].replace(0, pd.NA)
    posts_df['email_click_rate'] = posts_df['unique_email_clicks'] / posts_df['unique_email_opens'].replace(0, pd.NA)

    posts_df = posts_df.drop(columns=['created', 'publish_date', 'platform'])

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
    
    # Use improved matching to get clicks for each link
    link_to_clicks = match_links_with_clicks(html_links_raw, clicks_dict)
    
    for link in html_links:
        # Clean the href to match against link_to_clicks
        href = link['href'].replace("&jwt_token={{jwt_token}}", "")
        
        # Get click count for this link
        click_count = link_to_clicks.get(href, 0)
        
        # Calculate CTR
        ctr = (click_count / unique_email_opens * 100) if unique_email_opens > 0 else 0
        
        # Create a highlighted span with click info
        if click_count > 0:
            click_info = soup.new_tag('span', style='background-color: yellow; color: black; padding: 10px; margin: 10px 0; border-left: 4px solid orange; font-weight: bold; font-family: Arial, sans-serif; font-size: 14px;')
            has_s = "s" if click_count != 1 else ""
            click_info.string = f"{click_count} click{has_s} ({ctr:.1f}% CTR)"
            
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
    tip_prompt = """
You have been given an HTML document with line numbers and performance evaluation(s) of similar content.
Your task is to identify pieces of the content which are most likely to have the LOWEST (worst) click rates based on the evaluations, 
and suggest tips that could be inserted into the HTML to help the writer improve engagement based on the performance insights.

First, identify up to 6 places in the HTML where the content most closely follows the negative patterns described in the performance evaluations or deviates furthest from high-performing patterns.
Ignore content that is obviously an ad; evaluate only the main article content.
Second, for each identified place, determine whether the content can be re-worded for clarity/engagement (Wording Tip) or if the content itself is likely to draw poor engagement (Content Tip).
Finally, for each identified place, suggest a tip to improve it and why the tip is relevant based on the performance evaluations.

There are 2 types of tips you can provide:
1. Wording Tip: Suggested changes to the choice of words or phrasing.
    Wording tip example:
    tip_text: "Make this connection stronger by clearly telling readers the useful information they'll learn — for example, 'how to choose between biological controls and pesticides in real projects.'
    why: "Advice that takes a clear stance on when and how to use biological controls almost always does better than neutral articles."

2. Content Tip: Suggested changes to the information presented.
    Content tip example:
    tip_text: "Consider instead featuring an article that focuses on practical advice for gardeners considering pesticide use."
    why: "Content about broad environmental impacts of pesticide use usually underperforms content about the specific risks of using it on your own garden."

tip_text should be a single brief sentence suggesting an actionable change.
why: should be a single brief sentence explaining the rationale based on performance insights.

Provide the tip type, the line number where each tip should be inserted, the tip text, and the why for each tip.
Don't cite item IDs from the report--the user won't have access to that information.
DO NOT suggest changes to the format of the newsletter, just the type of items written about and how they are worded.
You should NOT start the text of the tip itself with the tip type; this will be added later based on the tip type.

Use language suitable for content creators, avoiding technical jargon and esoteric wording.

Too advanced:
tip_text: "Strengthen this link by foregrounding a clear mental model or framework readers will get (e.g., "how to decide between biological controls and pesticides in real projects")."
why: "Opinionated guidance on when/how to use biological controls consistently outperforms neutral articles."

Good:
tip_text: "Make this connection stronger by clearly telling readers the useful information they'll learn — for example, 'how to choose between biological controls and pesticides in real projects.'"
why: "Advice that takes a clear stance on when and how to use biological controls almost always does better than neutral articles."

Place the tips DIRECTLY BELOW the specific content being referenced.
An up arrow (⬆️) will be added above the tip to indicate its placement--that arrows should not be included in your tip text.
Think carefully about what line number to assign to each tip so that it appears directly below the relevant content.
"""

    # Combine all performance evaluations with XML tags
    combined_perf_eval = "\n\n".join([
        f"<performance_evaluation_{i+1}>\n{eval_text}\n</performance_evaluation_{i+1}>"
        for i, eval_text in enumerate(content_perf_evals)
    ])
    
    messages = [
        {"role": "user", "content": f"{combined_perf_eval}"},
        {"role": "user", "content": "<html_document>\n" + '\n'.join(numbered_lines) + "\n</html_document>"},
        {"role": "system", "content": tip_prompt},
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
        font-weight: normal !important;
        font-style: normal !important;
        text-decoration: none !important;
        text-align: center;
        line-height: 1;
        margin-bottom: 4px;
    ">
        ⬆️
    </div>
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