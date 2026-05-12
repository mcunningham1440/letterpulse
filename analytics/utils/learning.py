import logging

from asgiref.sync import async_to_sync
from django.conf import settings

from analytics.llm_tracker import set_llm_context
from analytics.models import (
    PendingLearningTask,
    Post,
    ProcessedPost,
    UsageAccount,
)

from .beehiiv_api import fetch_subscriber_count
from .post_selection import (
    select_posts_for_initial_learning,
    select_posts_for_update,
    wipe_user_publication_data,
)
from .posts import (
    incremental_refresh_posts_data,
    process_posts_sections_sequential,
    refresh_posts_data,
    save_posts_to_db,
)

logger = logging.getLogger(__name__)


def _run_learning_task_impl(task, kind):
    """
    Shared body for kind='initial' and kind='update' runners.

    - Fetch phase: full refresh (initial) or incremental fetch (update).
    - For kind='initial' we wipe any leftover (user, publication) data first
      so a previous abandoned attempt can't poison the new run.
    - Select post ids to process, run processing (free — no credit charge).
    - Update task phase/counts throughout; the abstract base's sweep tracks
      liveness via client-poll heartbeats, so this runner doesn't need its
      own heartbeat side thread.
    """
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
        raise RuntimeError("Missing Beehiiv credentials or publication.")

    if kind == 'initial':
        # Clear any leftover state from a prior abandoned/errored initial run
        # so we don't append to a half-finished dataset.
        wipe_user_publication_data(task.user, beehiiv_pub_id)

    task.phase = 'fetch'
    task.save(update_fields=['phase', 'updated_at'])

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
        raise RuntimeError(fetch_msg or "Fetch failed.")

    if not posts_df.empty:
        save_posts_to_db(posts_df, task.user, publication)

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
    task.save(update_fields=['target_process_count', 'phase', 'updated_at'])

    if not post_ids_to_process:
        # Empty run — for kind='initial' we don't flip initial_fetched_pub_ids
        # so the user can retry via the Learning coach on their next visit.
        task.mark_complete()
        return

    # --- Process phase ---
    async_to_sync(process_posts_sections_sequential)(
        post_ids_to_process, task.user, beehiiv_token, beehiiv_pub_id, publication,
    )

    processed_count = ProcessedPost.objects.filter(
        user=task.user,
        post__post_id__in=post_ids_to_process,
    ).count()

    # Only flip initial_fetched_pub_ids after a clean, non-empty run so a
    # zero-result initial scan doesn't strand the user on the "No Data" fallback.
    if kind == 'initial' and processed_count > 0:
        if beehiiv_pub_id not in (usage.initial_fetched_pub_ids or []):
            usage.initial_fetched_pub_ids = list(
                usage.initial_fetched_pub_ids or []
            ) + [beehiiv_pub_id]
            usage.save(update_fields=['initial_fetched_pub_ids'])

    task.mark_complete(posts_processed_count=processed_count)


def run_initial_learning_task(task):
    """Background work fn for the initial Learning Your Audience flow."""
    _run_learning_task_impl(task, 'initial')


def run_update_task(task):
    """Background work fn for the per-page-load Updating Your Posts flow."""
    _run_learning_task_impl(task, 'update')
