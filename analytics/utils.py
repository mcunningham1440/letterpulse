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


async def llm_call(function_name, messages, model, response_format=None):
    """Make an async call to OpenAI API and log the request"""
    start_datetime = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    start_time = time.time()

    if asyncio.get_event_loop().is_running():
        client = AsyncOpenAI(api_key=os.environ.get('OPENAI_API_KEY'))
        try:
            if response_format is not None:
                completion = await client.beta.chat.completions.parse(
                    model=model,
                    messages=messages,
                    response_format=response_format
                )
            else:
                completion = await client.chat.completions.create(
                    model=model,
                    messages=messages
                )
        finally:
            await client.close()
    else:
        client = OpenAI(api_key=os.environ.get('OPENAI_API_KEY'))
        try:
            if response_format is not None:
                completion = client.beta.chat.completions.parse(
                    model=model,
                    messages=messages,
                    response_format=response_format
                )
            else:
                completion = client.chat.completions.create(
                    model=model,
                    messages=messages
                )
        finally:
            client.close()
    duration = time.time() - start_time

    log_file = settings.DATA_DIR / "llm_call_logs.csv"
    file_exists = os.path.exists(log_file)
    
    with open(log_file, 'a', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(['function_name', 'run_datetime', 'run_time_s', 'model', 'prompt_tokens', 'completion_tokens'])
        writer.writerow([function_name, start_datetime, f"{duration:.4f}", model, completion.usage.prompt_tokens, completion.usage.completion_tokens])
    
    return completion


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


async def fetch_posts_html_parallel(post_ids):
    """
    Fetch HTML content for multiple posts in parallel with rate limiting.
    
    Args:
        post_ids: List of Beehiiv post IDs
    
    Returns:
        Dictionary mapping post IDs to HTML content
    """
    semaphore = asyncio.Semaphore(5)  # Limit to 5 concurrent requests
    htmls = {}
    
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        tasks = [fetch_post_html(session, post_id, semaphore) for post_id in post_ids]
        results = await asyncio.gather(*tasks)
        
        for post_id, html_content in results:
            if html_content is not None:
                htmls[f"{post_id}.html"] = html_content
    
    # Give the event loop a moment to complete cleanup
    await asyncio.sleep(0)
    
    return htmls


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
    parsing_prompt = f"""You are given an HTML document with line numbers.
Your task is to identify sections of the HTML that correspond to the content described below.
Provide the start and end line numbers (inclusive) for each section that matches the description.
Make each discrete item a separate section.
Do not include any sections that do not match the description.

Content Description:
{content_desc}."""
    
    soup = BeautifulSoup(post_html, 'html.parser')
    all_lines = soup.prettify().split('\n')
    numbered_lines = [f"{i+1}\t{line}" for i, line in enumerate(all_lines)]
    
    class Section(BaseModel):
        StartLine: int
        EndLine: int

    class AllSections(BaseModel):
        Sections: List[Section]

    messages = [
        {"role": "system", "content": parsing_prompt},
        {"role": "user", "content": '\n'.join(numbered_lines)}
    ]

    completion = await llm_call("extract_items", messages, "gpt-5-mini", response_format=AllSections)
    output = completion.choices[0].message.parsed

    # Step 2: Extract text and links from each section
    news_items = []

    for section in output.Sections:
        reconstructed_html = "\n".join(all_lines[section.StartLine - 1:section.EndLine])
        soup = BeautifulSoup(reconstructed_html, 'html.parser')
        
        # Extract text
        text = soup.get_text(" ", strip=True)
        
        # Extract all links
        html_links = [link['href'].replace("&jwt_token={{jwt_token}}", "") for link in soup.find_all('a') if link.has_attr('href')]
        
        # Get clicks for each link from clicks_dict
        clicks = [clicks_dict.get(link, 0) for link in html_links]

        news_items.append({
            "post_title": title,
            "post_date": post_date,
            "text": text,
            "links": html_links,
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
        OpenAI response object containing the insights
    """
    analysis_prompt = """You will be provided with a list of items from a newsletter and their click counts.
Your task is to identify common characteristics among the most clicked items.
Limit your response to 3 concise takeaways at most."""

    # Use the items with clicks formatted as string
    items_str = get_items_with_clicks_as_str(items_display, mode="by_clicks")
    
    messages = [
        {"role": "user", "content": analysis_prompt},
        {"role": "user", "content": items_str}
    ]
    
    response = await llm_call("generate_content_insights", messages, "gpt-5")
    
    return response


def get_items_with_clicks_as_str(items, mode="by_clicks", max_items=None):
    """
    Format items DataFrame as a string for AI analysis.
    
    Args:
        items: DataFrame of items
        mode: 'by_clicks' to sort by clicks, 'shuffled' for random order
        max_items: Maximum number of items to include
    
    Returns:
        Formatted string representation
    """
    if mode == "by_clicks":
        items_transformed = items.iloc[items["clicks"].apply(max).sort_values(ascending=False).index]
    elif mode == "shuffled":
        items_transformed = items.sample(frac=1).reset_index(drop=True)
    else:
        raise ValueError("mode must be 'by_clicks' or 'shuffled'")
    
    if max_items is not None:
        items_transformed = items_transformed.head(max_items)
    
    item_str = ""
    for _, row in items_transformed.iterrows():
        max_clicks = max(row["clicks"])
        item_str += f"Text: {row['text']}\n"
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
