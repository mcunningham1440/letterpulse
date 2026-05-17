"""
Shared helpers used across the views package: filename sanitization, the
Beehiiv-credentials accessor, the @require_valid_api_credentials decorator,
and the (user, beehiiv_pub_id) -> Publication resolver used by views that
need a Publication row keyed off the user's currently-selected pub.
"""

import functools
import re

from django.shortcuts import redirect
from django.urls import reverse

from ..models import UsageAccount, UserPublication


def sanitize_filename(filename: str) -> str:
    """
    Sanitize a filename to prevent HTTP header injection and path traversal.

    Removes or replaces characters that could be used for:
    - HTTP header injection (newlines, carriage returns)
    - Path traversal (slashes, backslashes)
    - Shell injection (quotes, semicolons)
    """
    if not filename:
        return "download"

    # Remove any path components
    filename = filename.replace('/', '_').replace('\\', '_')

    # Remove characters that could cause header injection or other issues
    # Keep only alphanumeric, spaces, hyphens, underscores, and periods
    sanitized = re.sub(r'[^\w\s\-.]', '', filename)

    # Collapse multiple spaces/underscores
    sanitized = re.sub(r'[\s_]+', '_', sanitized)

    # Remove leading/trailing underscores and periods
    sanitized = sanitized.strip('_.')

    # Ensure we have a valid filename
    if not sanitized:
        return "download"

    # Limit length to prevent issues
    return sanitized[:200]


def get_user_api_credentials(user):
    """
    Get the Beehiiv API credentials for a user.
    Returns (token, pub_id, is_valid) tuple.
    """
    try:
        usage = UsageAccount.objects.get(user=user)
        if usage.has_api_credentials:
            return usage.beehiiv_token, usage.beehiiv_pub_id, usage.api_key_valid
    except UsageAccount.DoesNotExist:
        pass
    return None, None, False


def require_valid_api_credentials(view_func):
    """
    Decorator that checks for valid API credentials before allowing access.
    Redirects to account page with error message if credentials are missing or invalid.
    """
    @functools.wraps(view_func)
    def wrapper(request, *args, **kwargs):
        token, pub_id, is_valid = get_user_api_credentials(request.user)

        if not token or not pub_id:
            return redirect(reverse('analytics:account') + '?setup=configure')

        if not is_valid:
            return redirect(reverse('analytics:account') + '?setup=invalid')

        return view_func(request, *args, **kwargs)

    return wrapper


def _resolve_publication(user, beehiiv_pub_id):
    """
    Return the Publication for the user's selected Beehiiv pub_id, or None
    if the user has no UserPublication row for that pub (caller should 400).
    Publication rows are created at credentials-validation time, so any pub
    the user currently has access to is guaranteed to have a Publication row.
    """
    up = (
        UserPublication.objects
        .filter(user=user, publication__pub_id=beehiiv_pub_id)
        .select_related('publication')
        .first()
    )
    return up.publication if up else None
