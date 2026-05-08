import asyncio
import logging
from datetime import datetime

import aiohttp

logger = logging.getLogger(__name__)


INCREMENTAL_FETCH_PAGE_SIZE = 5
INCREMENTAL_FETCH_PUBLISH_AGE_SECONDS = 72 * 3600
INCREMENTAL_FETCH_PREFETCH_WINDOW = 1


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
