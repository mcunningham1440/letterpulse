import asyncio
import logging

import aiohttp
import pandas as pd
from bs4 import BeautifulSoup

from .beehiiv_api import (
    fetch_all_posts,
    fetch_post_html,
    incremental_fetch_posts,
)
from .links import process_post_links
from .sections import auto_section

logger = logging.getLogger(__name__)


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
    from analytics.models import Post

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
