"""
LogSink: Queue-based async logging system for execution logs.

This module provides a non-blocking logging system that:
- Queues log entries in a thread-safe queue
- Batch inserts to the database via a background worker thread
- Gracefully handles queue overflow and database errors
- Never breaks the application if logging fails

Usage:
    from analytics.logsink import log_sink
    log_sink.put(log_entry_dict)
"""

import logging
import threading
import time
import uuid
from contextlib import contextmanager
from queue import Empty, Full, Queue
from typing import Any, Dict, List, Optional

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)


class LogSink:
    """
    Thread-safe queue-based log sink with background worker.

    Each Gunicorn worker process will have its own LogSink instance
    with its own queue and worker thread.
    """

    def __init__(
        self,
        maxsize: Optional[int] = None,
        batch_size: Optional[int] = None,
        flush_interval: Optional[float] = None,
        on_full: Optional[str] = None,
    ):
        """
        Initialize the log sink.

        Args:
            maxsize: Maximum queue size before overflow handling
            batch_size: Number of entries to batch before writing
            flush_interval: Max seconds to wait before flushing partial batch
            on_full: 'drop' to silently drop logs when full, 'sync' to write synchronously
        """
        # Read from settings with fallback defaults
        self._maxsize = maxsize or getattr(settings, 'EXECUTION_LOG_QUEUE_MAXSIZE', 2000)
        self._batch_size = batch_size or getattr(settings, 'EXECUTION_LOG_BATCH_SIZE', 50)
        self._flush_interval = flush_interval or getattr(settings, 'EXECUTION_LOG_FLUSH_INTERVAL', 1.0)
        self._on_full = on_full or getattr(settings, 'EXECUTION_LOG_ON_FULL', 'drop')

        self._queue: Queue = Queue(maxsize=self._maxsize)
        self._worker_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._started = False
        self._lock = threading.Lock()

    def start(self):
        """
        Start the background worker thread.

        Safe to call multiple times - will only start once.
        Called from apps.py ready() method.
        """
        with self._lock:
            if self._started:
                return

            self._stop_event.clear()
            self._worker_thread = threading.Thread(
                target=self._worker_loop,
                name="LogSinkWorker",
                daemon=True  # Daemon so it doesn't block app shutdown
            )
            self._worker_thread.start()
            self._started = True
            logger.info("LogSink worker thread started")

    def stop(self, timeout: float = 5.0):
        """
        Stop the background worker thread gracefully.

        Flushes remaining entries and waits for thread to finish.
        """
        with self._lock:
            if not self._started:
                return

            self._stop_event.set()
            if self._worker_thread and self._worker_thread.is_alive():
                self._worker_thread.join(timeout=timeout)
            self._started = False
            logger.info("LogSink worker thread stopped")

    def put(self, log_entry: Dict[str, Any]):
        """
        Add a log entry to the queue.

        This method is designed to NEVER raise an exception.
        If the queue is full, behavior depends on on_full setting.

        Args:
            log_entry: Dict with log entry data (will be converted to ExecutionLog)
        """
        try:
            self._queue.put_nowait(log_entry)
        except Full:
            if self._on_full == 'sync':
                self._sync_write([log_entry])
            else:
                # 'drop' - silently drop the log entry
                logger.debug("LogSink queue full, dropping log entry")

    def _worker_loop(self):
        """
        Background worker loop that batches and writes log entries.

        Runs until stop_event is set, then flushes remaining entries.
        """
        batch: List[Dict[str, Any]] = []
        last_flush = time.time()

        while not self._stop_event.is_set():
            try:
                # Try to get an entry with timeout
                entry = self._queue.get(timeout=0.1)
                batch.append(entry)

                # Check if we should flush
                should_flush = (
                    len(batch) >= self._batch_size or
                    (time.time() - last_flush) >= self._flush_interval
                )

                if should_flush and batch:
                    self._flush_batch(batch)
                    batch = []
                    last_flush = time.time()

            except Empty:
                # No entry available, check if we should flush on interval
                if batch and (time.time() - last_flush) >= self._flush_interval:
                    self._flush_batch(batch)
                    batch = []
                    last_flush = time.time()

        # Drain remaining entries on shutdown
        while True:
            try:
                entry = self._queue.get_nowait()
                batch.append(entry)
            except Empty:
                break

        if batch:
            self._flush_batch(batch)

    def _flush_batch(self, batch: List[Dict[str, Any]]):
        """
        Write a batch of log entries to the database.

        Uses bulk_create for efficiency. Never raises exceptions.
        """
        if not batch:
            return

        try:
            # Import here to avoid circular imports
            from .models import ExecutionLog

            log_objects = []
            for entry in batch:
                # Convert dict to ExecutionLog instance
                log_obj = ExecutionLog(
                    ts_start=entry.get('ts_start'),
                    ts_end=entry.get('ts_end'),
                    duration_ms=entry.get('duration_ms', 0),
                    kind=entry.get('kind', 'function'),
                    name=entry.get('name', 'unknown'),
                    success=entry.get('success', True),
                    error_type=entry.get('error_type', ''),
                    error_message=entry.get('error_message', ''),
                    traceback=entry.get('traceback', ''),
                    user_id=entry.get('user_id'),
                    request_id=entry.get('request_id', ''),
                    parent_id=entry.get('parent_id'),
                    inputs=entry.get('inputs', {}),
                    outputs=entry.get('outputs', {}),
                    meta=entry.get('meta', {}),
                )
                log_objects.append(log_obj)

            ExecutionLog.objects.bulk_create(log_objects, ignore_conflicts=True)
            logger.debug(f"LogSink flushed {len(log_objects)} entries")

        except Exception as e:
            # Log the error but NEVER propagate - logging must not break the app
            logger.error(f"LogSink failed to flush batch: {e}", exc_info=True)

    def _sync_write(self, entries: List[Dict[str, Any]]):
        """
        Synchronously write entries (fallback when queue is full).

        Used when on_full='sync'. Never raises exceptions.
        """
        try:
            self._flush_batch(entries)
        except Exception as e:
            logger.error(f"LogSink sync write failed: {e}", exc_info=True)


# Global singleton instance
# Each Gunicorn worker process will have its own instance
log_sink = LogSink()


def generate_request_id() -> str:
    """Generate a unique request ID for correlation."""
    return str(uuid.uuid4())


# Thread-local storage for request context
_request_context = threading.local()


def get_current_request_id() -> Optional[str]:
    """Get the request ID for the current thread, if any."""
    return getattr(_request_context, 'request_id', None)


def set_current_request_id(request_id: Optional[str]):
    """Set the request ID for the current thread."""
    _request_context.request_id = request_id


def get_current_user_id() -> Optional[int]:
    """Get the user ID for the current thread, if any."""
    return getattr(_request_context, 'user_id', None)


def set_current_user_id(user_id: Optional[int]):
    """Set the user ID for the current thread."""
    _request_context.user_id = user_id


@contextmanager
def request_context(request_id: str, user_id: Optional[int] = None):
    """
    Context manager for setting request context.

    Used by middleware to set context for the duration of a request.
    """
    old_request_id = get_current_request_id()
    old_user_id = get_current_user_id()

    set_current_request_id(request_id)
    set_current_user_id(user_id)

    try:
        yield
    finally:
        set_current_request_id(old_request_id)
        set_current_user_id(old_user_id)
