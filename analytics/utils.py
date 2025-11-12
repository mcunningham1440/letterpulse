"""
Utility functions for the Django analytics app.
Adapted from the original utils.py for Streamlit.
"""

from openai import AsyncOpenAI, OpenAI
from pydantic import BaseModel
from typing import List
import pandas as pd
import asyncio
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
    else:
        client = OpenAI(api_key=os.environ.get('OPENAI_API_KEY'))
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
    duration = time.time() - start_time

    log_file = settings.DATA_DIR / "llm_call_logs.csv"
    file_exists = os.path.exists(log_file)
    
    with open(log_file, 'a', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(['function_name', 'run_datetime', 'run_time_s', 'model', 'prompt_tokens', 'completion_tokens'])
        writer.writerow([function_name, start_datetime, f"{duration:.4f}", model, completion.usage.prompt_tokens, completion.usage.completion_tokens])
    
    return completion


async def extract_items(post_html, content_desc, clicks_by_title, title, post_date, unique_email_opens):
    """
    Extract items from a single post HTML using AI.
    
    Args:
        post_html: HTML content of the post
        content_desc: Description of the content to extract
        clicks_by_title: Dictionary mapping post titles to click data
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
        
        # Get clicks for each link
        clicks = [clicks_by_title[title].get(link, 0) for link in html_links]

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


async def extract_items_parallel(posts_data, content_desc, htmls, clicks_by_title):
    """
    Extract items from multiple posts in parallel using asyncio.
    
    Args:
        posts_data: List of tuples containing (title, html_filename, post_date, unique_email_opens)
        content_desc: Description of content to extract
        htmls: Dictionary of HTML content keyed by filename
        clicks_by_title: Dictionary of click data
    
    Returns:
        List of DataFrames containing extracted items
    """
    tasks = []
    for title, html_filename, post_date, unique_email_opens in posts_data:
        if html_filename in htmls:
            current_html = htmls[html_filename]
            task = extract_items(current_html, content_desc, clicks_by_title, title, post_date, unique_email_opens)
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


def load_htmls():
    """Load all HTML files from the archived_htmls directory"""
    htmls = {}
    html_dir = settings.ARCHIVED_HTMLS_DIR
    if os.path.exists(html_dir):
        for item in os.listdir(html_dir):
            if item.endswith('.html'):
                with open(html_dir / item, "r", encoding="utf-8") as f:
                    htmls[item] = f.read()
    return htmls


def load_clicks_by_title():
    """Load the clicks by title JSON data"""
    import json
    clicks_file = settings.CLICKS_BY_TITLE_JSON
    if os.path.exists(clicks_file):
        with open(clicks_file, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def load_posts_csv():
    """Load posts from CSV file into a DataFrame"""
    posts_file = settings.POSTS_CSV
    if os.path.exists(posts_file):
        df = pd.read_csv(posts_file)
        # Convert publish_date_cst to datetime with UTC awareness and extract just the date
        df['publish_date_cst'] = pd.to_datetime(df['publish_date_cst'], utc=True).dt.date
        return df
    return pd.DataFrame()
