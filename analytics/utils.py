"""
Utility functions for the Django analytics app.
Adapted from the original utils.py for Streamlit.
"""

from openai import AsyncOpenAI, OpenAI
from pydantic import BaseModel
from typing import List, Dict
import pandas as pd
import asyncio
import aiohttp
import csv
import os
from datetime import datetime
import time
from bs4 import BeautifulSoup
from django.conf import settings
import numpy as np
from Levenshtein import distance as levenshtein_distance


async def llm_call(function_name, messages, model, reasoning_level, response_format=None, tools=None):
    """Make an async call to OpenAI API and log the request"""
    start_datetime = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    start_time = time.time()

    kwargs = {
        "model": model,
        "input": messages,
        "reasoning": {"effort": reasoning_level}
    }
    if tools is not None:
        kwargs["tools"] = tools

    if asyncio.get_event_loop().is_running():
        client = AsyncOpenAI(api_key=os.environ.get('OPENAI_API_KEY'))
        try:            
            if response_format is not None:
                kwargs["text_format"] = response_format

                response = await client.responses.parse(**kwargs)
            else:
                response = await client.responses.create(**kwargs)
        finally:
            await client.close()
    else:
        client = OpenAI(api_key=os.environ.get('OPENAI_API_KEY'))
        try:
            if response_format is not None:
                kwargs["text_format"] = response_format

                response = client.responses.parse(**kwargs)
            else:
                response = client.responses.create(**kwargs)
        finally:
            client.close()
    duration = time.time() - start_time

    log_file = settings.DATA_DIR / "llm_call_logs.csv"
    file_exists = os.path.exists(log_file)
    
    with open(log_file, 'a', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(['function_name', 'run_datetime', 'run_time_s', 'model', 'input_tokens', 'cached_tokens', 'output_tokens', 'reasoning_tokens'])
        writer.writerow([function_name, start_datetime, f"{duration:.4f}", model, response.usage.input_tokens, response.usage.input_tokens_details.cached_tokens, response.usage.output_tokens, response.usage.output_tokens_details.reasoning_tokens])
    
    return response


async def fetch_post_html(session, post_id, semaphore):
    """
    Fetch HTML content for a single post from Beehiiv API.
    
    Args:
        session: aiohttp ClientSession
        post_id: The Beehiiv post ID
        semaphore: asyncio.Semaphore to limit concurrent requests
    
    Returns:
        Tuple of (post_id, html_content) or (post_id, None) on error
    """
    beehiiv_token = os.environ.get('BEEHIIV_TOKEN')
    beehiiv_pub_id = os.environ.get('BEEHIIV_PUB_ID')
    
    if not beehiiv_token or not beehiiv_pub_id:
        print(f"Error: Missing BEEHIIV_TOKEN or BEEHIIV_PUB_ID environment variables")
        return (post_id, None)
    
    url = f"https://api.beehiiv.com/v2/publications/{beehiiv_pub_id}/posts/{post_id}?expand=free_email_content"
    headers = {"Authorization": beehiiv_token}
    
    async with semaphore:
        try:
            async with session.get(url, headers=headers) as response:
                if response.status == 200:
                    data = await response.json()
                    html_content = data.get('data', {}).get('content', {}).get('free', {}).get('email', '')
                    print(f"Successfully fetched HTML for post {post_id}")
                    return (post_id, html_content)
                else:
                    print(f"Error fetching post {post_id}: status {response.status}")
                    return (post_id, None)
        except Exception as e:
            print(f"Exception fetching post {post_id}: {str(e)}")
            return (post_id, None)


async def fetch_post_clicks(session, post_id, semaphore):
    """
    Fetch clicks stats for a single post from Beehiiv API.
    
    Args:
        session: aiohttp ClientSession
        post_id: The Beehiiv post ID
        semaphore: asyncio.Semaphore to limit concurrent requests
    
    Returns:
        Tuple of (post_id, clicks_dict) or (post_id, None) on error
        clicks_dict maps URLs to their unique click counts
    """
    beehiiv_token = os.environ.get('BEEHIIV_TOKEN')
    beehiiv_pub_id = os.environ.get('BEEHIIV_PUB_ID')
    
    if not beehiiv_token or not beehiiv_pub_id:
        print(f"Error: Missing BEEHIIV_TOKEN or BEEHIIV_PUB_ID environment variables")
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
                    
                    print(f"Successfully fetched clicks for post {post_id}")
                    return (post_id, clicks_dict)
                else:
                    print(f"Error fetching clicks for post {post_id}: status {response.status}")
                    return (post_id, None)
        except Exception as e:
            print(f"Exception fetching clicks for post {post_id}: {str(e)}")
            return (post_id, None)
        

async def fetch_posts_html_and_clicks_parallel(post_ids):
    """
    Fetch HTML content and clicks stats for multiple posts in parallel.
    Since we can't use expand=free_email_content and expand=stats together,
    we make 2 requests per post.
    
    Args:
        post_ids: List of Beehiiv post IDs
    
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
        html_tasks = [fetch_post_html(session, post_id, semaphore) for post_id in post_ids]
        html_results = await asyncio.gather(*html_tasks)
        
        for post_id, html_content in html_results:
            if html_content is not None:
                htmls[f"{post_id}.html"] = html_content
        
        # Fetch clicks data
        clicks_tasks = [fetch_post_clicks(session, post_id, semaphore) for post_id in post_ids]
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


async def extract_items(post_html, content_desc, clicks_dict, title, post_date, unique_email_opens):
    """
    Extract items from a single post HTML using AI.
    
    Args:
        post_html: HTML content of the post
        content_desc: Description of the content to extract
        clicks_dict: Dictionary mapping URLs to click counts for this post
        title: Post title
        post_date: Date the post was published
        unique_email_opens: Number of unique email opens
    
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
    response = await llm_call("extract_items", messages, "gpt-5.1", "low", response_format=AllItems)
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


async def extract_items_parallel(posts_data, content_desc, htmls, clicks_by_id):
    """
    Extract items from multiple posts in parallel using asyncio.
    
    Args:
        posts_data: List of tuples containing (post_id, title, html_filename, post_date, unique_email_opens)
        content_desc: Description of content to extract
        htmls: Dictionary of HTML content keyed by filename
        clicks_by_id: Dictionary mapping post IDs to their clicks dictionaries
    
    Returns:
        List of DataFrames containing extracted items
    """
    tasks = []
    for post_id, title, html_filename, post_date, unique_email_opens in posts_data:
        if html_filename in htmls and post_id in clicks_by_id:
            current_html = htmls[html_filename]
            clicks_dict = clicks_by_id[post_id]
            task = extract_items(current_html, content_desc, clicks_dict, title, post_date, unique_email_opens)
            tasks.append(task)
    
    # Run all extractions concurrently
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    # Filter out any exceptions and return successful results
    items_list = []
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            # Log the error but continue with other results
            print(f"Error extracting items from post {i}: {str(result)}")
        else:
            items_list.append(result)
    
    # Reverse the list so newer posts appear first when concatenated
    return items_list[::-1]


async def generate_content_insights(items_display):
    """
    Generate AI insights from content items using OpenAI API.
    
    Args:
        items_display: DataFrame containing the content items with clicks
    
    Returns:
        OpenAI response object containing structured insights with 3 tags
    """
    ###
    # return "Test insights response"
    ###

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
    
    newsletter_items = ""
    for i, row in items_display.iterrows():
        newsletter_items += f"ID {i}. " + row["text"] + "\n"
        newsletter_items += f"CTR: {row['max_click_rate'] * 100}% (percentile {row['max_click_rate_percentile']:.0%})\n\n"
        
    messages = [
        {"role": "user", "content": analysis_prompt},
        {"role": "user", "content": newsletter_items}
    ]
    
    response = await llm_call("generate_content_insights", messages, "gpt-5.1", "medium")
    
    return response


def get_items_with_clicks_as_str(items, mode="top_k", max_items=None, use_id=False):
    """
    Format items DataFrame as a string for AI analysis.
    
    Args:
        items: DataFrame of items
        mode: 'top_k' for top items by clicks, 'sampled' for random items sorted by clicks, 
              'shuffled' for random items in random order
        max_items: Maximum number of items to include
        use_id: Whether to use item index as ID in output
    
    Returns:
        Formatted string representation
    """
    if mode == "top_k":
        # Sort by clicks in descending order and take top max_items
        items_transformed = items.iloc[items["clicks"].apply(max).sort_values(ascending=False).index]
        if max_items is not None:
            items_transformed = items_transformed.head(max_items)
    elif mode == "sampled":
        # Take random sample, then sort by clicks in descending order
        if max_items is not None:
            items_transformed = items.sample(n=min(max_items, len(items))).reset_index(drop=True)
        else:
            items_transformed = items.copy()
        items_transformed = items_transformed.iloc[items_transformed["clicks"].apply(max).sort_values(ascending=False).index]
    elif mode == "shuffled":
        # Take random sample in random order
        if max_items is not None:
            items_transformed = items.sample(n=min(max_items, len(items))).reset_index(drop=True)
        else:
            items_transformed = items.sample(frac=1).reset_index(drop=True)
    else:
        raise ValueError("mode must be 'top_k', 'sampled', or 'shuffled'")
    
    item_str = ""
    for i, row in items_transformed.iterrows():
        max_clicks = max(row["clicks"])
        item_str += f"Text: {row['text']}\n" if not use_id else f"ID {i}. {row['text']}\n"
        item_str += f"Max Clicks: {max_clicks}\n\n"
    
    return item_str


def load_posts_from_db():
    """Load posts from database into a DataFrame"""
    from .models import Post
    
    posts = Post.objects.all().values(
        'post_id', 'title', 'subtitle', 'publish_date_cst',
        'recipients', 'delivered', 'email_opens', 'unique_email_opens',
        'email_clicks', 'unique_email_clicks', 'unsubscribes', 'spam_reports'
    )
    
    if posts:
        df = pd.DataFrame(list(posts))
        # Rename post_id to id to match the expected format
        df = df.rename(columns={'post_id': 'id'})
        return df
    return pd.DataFrame()


async def fetch_posts_page(session, page, pagination_size, semaphore):
    """
    Fetch a single page of posts from Beehiiv API.
    
    Args:
        session: aiohttp ClientSession
        page: Page number to fetch
        pagination_size: Number of posts per page
        semaphore: asyncio.Semaphore to limit concurrent requests
    
    Returns:
        List of post data dictionaries
    """
    beehiiv_token = os.environ.get('BEEHIIV_TOKEN')
    beehiiv_pub_id = os.environ.get('BEEHIIV_PUB_ID')
    
    url = f"https://api.beehiiv.com/v2/publications/{beehiiv_pub_id}/posts?expand=stats&limit={pagination_size}&page={page}"
    headers = {"Authorization": beehiiv_token}
    
    async with semaphore:
        async with session.get(url, headers=headers) as response:
            print(f"Page {page} status code: {response.status}")
            data = await response.json()
            return data.get('data', [])


async def fetch_all_posts():
    """
    Fetch all posts from Beehiiv API in parallel with pagination.
    
    Returns:
        List of all post data dictionaries
    """
    pagination_size = 10
    posts_list = []
    semaphore = asyncio.Semaphore(10)
    
    timeout = aiohttp.ClientTimeout(total=60)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        page = 1
        while True:
            tasks = [fetch_posts_page(session, p, pagination_size, semaphore) for p in range(page, page + 5)]
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
                posts[key].append(post[key])
            else:
                posts[key].append(post['stats']['email'][email_keys[key]])
        
        clicks[post['title']] = post['stats']['clicks']
    
    posts_df = pd.DataFrame(posts)
    
    # Add derived columns
    posts_df['publish_date_cst'] = pd.to_datetime(posts_df['publish_date'], unit='s', utc=True).dt.tz_convert('America/Chicago')
    posts_df['publish_dow'] = posts_df['publish_date_cst'].dt.strftime('%A')
    posts_df['email_open_rate'] = posts_df['unique_email_opens'] / posts_df['delivered']
    posts_df['email_click_rate'] = posts_df['unique_email_clicks'] / posts_df['unique_email_opens']
    
    posts_df = posts_df.drop(columns=['publish_date'])
    
    # Filter posts
    posts_df = posts_df[
        (posts_df['recipients'] > 10) & 
        (posts_df['platform'].isin(('web', 'both'))) & 
        (posts_df['title'] != "Replit's software engineering agent stuns users")
    ]
    
    posts_df = posts_df.drop(columns=['platform'])
    
    return posts_df


async def refresh_posts_data():
    """
    Fetch all posts from Beehiiv API.
    
    Returns:
        Tuple of (posts_df, success_message) or (None, error_message)
    """
    try:
        # Fetch posts
        posts_list = await fetch_all_posts()
        
        if not posts_list:
            return None, "No posts were fetched from the API."
        
        # Process the data
        posts_df = process_posts_data(posts_list)
        
        return posts_df, f"Successfully fetched and processed {len(posts_df)} posts."
        
    except Exception as e:
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