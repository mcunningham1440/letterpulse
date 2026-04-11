"""
Logging utilities: middleware for execution logging.

This module provides:
- ExecutionLoggingMiddleware: Logs all HTTP requests
"""

import logging
import time
import traceback as tb_module
from typing import Callable

from django.http import HttpRequest, HttpResponse
from django.utils import timezone

from .logsink import (
    generate_request_id,
    log_sink,
    request_context,
)

logger = logging.getLogger(__name__)


class ExecutionLoggingMiddleware:
    """
    Django middleware that logs all HTTP requests.

    Sets up request context (request_id, user_id) for the duration
    of the request, and logs timing and success/failure.
    """

    def __init__(self, get_response: Callable):
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        # Generate request ID
        request_id = generate_request_id()

        # Store request_id on request object for access in views
        request.execution_log_request_id = request_id

        # Get user ID if authenticated
        user_id = None
        if hasattr(request, 'user') and request.user.is_authenticated:
            user_id = request.user.id

        # Record start time
        ts_start = timezone.now()
        start_time = time.perf_counter()

        # Track success/error
        success = True
        error_type = ''
        error_message = ''
        traceback = ''

        try:
            # Execute the request within request context
            with request_context(request_id, user_id):
                response = self.get_response(request)

            # Check for error status codes
            if response.status_code >= 400:
                success = False
                error_type = f'HTTP_{response.status_code}'
                error_message = getattr(response, 'reason_phrase', str(response.status_code))

            return response

        except Exception as e:
            # Capture exception details
            success = False
            error_type = type(e).__name__
            error_message = str(e)[:2000]  # Truncate long messages
            traceback = tb_module.format_exc()[:20000]  # Truncate long tracebacks
            raise

        finally:
            # Calculate duration
            ts_end = timezone.now()
            duration_ms = int((time.perf_counter() - start_time) * 1000)

            # Re-check user_id after auth middleware may have run
            if user_id is None and hasattr(request, 'user') and request.user.is_authenticated:
                user_id = request.user.id

            # Determine view name
            view_name = self._get_view_name(request)

            # Queue the log entry
            log_entry = {
                'ts_start': ts_start,
                'ts_end': ts_end,
                'duration_ms': duration_ms,
                'kind': 'request',
                'name': view_name,
                'success': success,
                'error_type': error_type,
                'error_message': error_message,
                'traceback': traceback,
                'user_id': user_id,
                'request_id': request_id,
                'parent_id': None,
                'inputs': {},   # Placeholder
                'outputs': {},  # Placeholder
                'meta': {},     # Placeholder
            }

            log_sink.put(log_entry)

    def _get_view_name(self, request: HttpRequest) -> str:
        """
        Extract the view name from the request.

        Returns the URL name if available, otherwise the path.
        """
        # Try to get the resolved view name
        if hasattr(request, 'resolver_match') and request.resolver_match:
            if request.resolver_match.view_name:
                return request.resolver_match.view_name
            if request.resolver_match.func:
                func = request.resolver_match.func
                if hasattr(func, '__name__'):
                    return func.__name__

        # Fall back to method + path
        return f"{request.method} {request.path}"
