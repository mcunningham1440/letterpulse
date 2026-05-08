import logging
import threading

from asgiref.sync import async_to_sync
from django.conf import settings
from django.db import connection
from django.utils import timezone as dj_timezone

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


def _heartbeat_task(task):
    """Refresh last_heartbeat on the task row (one-shot)."""
    task.last_heartbeat = dj_timezone.now()
    task.save(update_fields=['last_heartbeat'])


def _is_abandoned(task):
    """Re-read abandoned flag from DB. Returns True if task was marked abandoned."""
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
