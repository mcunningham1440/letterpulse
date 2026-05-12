"""
Shared scaffolding for spawning the app's daemon-thread background tasks.

Every long-running flow (content finder, improvement tips, niche analysis,
learning) goes through `spawn_background`, which:

  1. Re-fetches the task row in the worker thread.
  2. Calls `task.claim(running_status=...)` — atomic transition from 'pending'
     to the running state, charging credits if applicable. On re-entry (the
     content finder's confirm-plan path) claim is a no-op and the work fn is
     allowed to branch on the already-set status.
  3. Calls the work fn. On exception, the task is marked errored (refunding
     any charged credits) and the exception is logged.
  4. Closes the thread-local DB connection in `finally`.

The work fn signature is `fn(task) -> None`. Anything the fn writes terminally
(complete vs. error) should go through `task.mark_complete()` / `task.mark_error()`
so the abstract-base idempotency guards apply.

The free-standing `recover_stuck_tasks()` runs at process boot to sweep every
BackgroundTask subclass — covers rows orphaned by a prior crash, deploy, or
worker recycle.
"""

import logging
import threading
from typing import Callable, Iterable

from django.db import close_old_connections, connection

logger = logging.getLogger(__name__)


def refresh_db_connection() -> None:
    """
    Drop any thread-local DB connections that the server has since closed
    (RDS recycles idle connections after a few minutes). Call before an ORM
    block that follows a long LLM/HTTP wait.
    """
    close_old_connections()


def _run_in_thread(task_class, task_id, work_fn, running_status):
    """Thread target. See module docstring."""
    from analytics.utils.credits import NotEnoughCredits

    task = None
    try:
        try:
            task = task_class.objects.get(task_id=task_id)
        except task_class.DoesNotExist:
            logger.warning(
                "Background task %s/%s vanished before thread start",
                task_class.__name__, task_id,
            )
            return

        try:
            task.claim(running_status=running_status)
        except NotEnoughCredits as e:
            task.mark_error(str(e), refund=False)
            return

        try:
            work_fn(task)
        except Exception as e:
            logger.exception(
                "Background task %s/%s failed",
                task_class.__name__, task_id,
            )
            refresh_db_connection()
            try:
                fresh = task_class.objects.get(task_id=task_id)
                fresh.mark_error(str(e))
            except Exception:
                logger.exception(
                    "Failed to mark background task as errored",
                )
    finally:
        connection.close()


def spawn_background(
    task_class,
    task_id,
    work_fn: Callable,
    *,
    running_status: str = 'running',
) -> None:
    """Spawn a daemon thread that runs `work_fn(task)` under the wrapper above."""
    threading.Thread(
        target=_run_in_thread,
        args=(task_class, task_id, work_fn, running_status),
        daemon=True,
    ).start()


def recover_stuck_tasks(
    task_classes: Iterable[type] | None = None,
) -> dict[str, int]:
    """
    Boot-time recovery. Sweep every BackgroundTask subclass for rows stranded
    in a running state by a crashed previous process. Returns {class: swept}.
    """
    if task_classes is None:
        from analytics.models import (
            PendingContentSearch,
            PendingImprovementTips,
            PendingLearningTask,
            PendingNicheAnalysis,
        )
        task_classes = [
            PendingContentSearch,
            PendingImprovementTips,
            PendingLearningTask,
            PendingNicheAnalysis,
        ]

    from django.db.utils import OperationalError, ProgrammingError

    counts: dict[str, int] = {}
    for cls in task_classes:
        try:
            counts[cls.__name__] = cls.sweep_stale()
        except (OperationalError, ProgrammingError):
            # Schema not ready (e.g. table doesn't exist yet). Let the caller
            # decide what to do — log at info level rather than dumping a
            # traceback.
            raise
        except Exception:
            logger.exception("recover_stuck_tasks failed for %s", cls.__name__)
            counts[cls.__name__] = 0
    return counts
