"""
Context processors for the analytics app.
"""

from django.conf import settings
from .models import UsageAccount


def usage_context(request):
    """
    Add usage account information to template context for authenticated users.
    """
    if request.user.is_authenticated:
        try:
            usage = UsageAccount.objects.get(user=request.user)
            usage.ensure_current_period()
            # Save if period was reset
            if usage.used_this_period == 0 and usage.pk:
                usage.save(update_fields=['period_start', 'used_this_period'])
            return {
                'usage': usage,  # Full object for API status checks
                'usage_used': usage.used_this_period,
                'usage_quota': usage.monthly_quota,
                'usage_remaining': usage.remaining,
                'usage_percentage': usage.usage_percentage,
            }
        except UsageAccount.DoesNotExist:
            return {}
    return {}


def environment_context(request):
    """
    Expose whether the app is running in local mode to templates.
    """
    return {'is_local': settings.ENVIRONMENT == 'local'}


def limited_data_context(request):
    """
    Flag the "Limited Data Available" coach when an authenticated user with
    valid Beehiiv credentials has fewer than 5 eligible (Published, email /
    both platform) posts for their currently-selected publication. Shown on
    every page load when active.
    """
    from .models import Post, Publication

    if not request.user.is_authenticated:
        return {'show_limited_data_coach': False}

    try:
        usage = UsageAccount.objects.get(user=request.user)
    except UsageAccount.DoesNotExist:
        return {'show_limited_data_coach': False}

    if not usage.api_key_valid or not usage.beehiiv_pub_id:
        return {'show_limited_data_coach': False}

    # Only show after the initial scan + processing has completed for the
    # current publication. Before that, the user either sees the API-validated
    # coach (Account) or the Learning coach/modal (Write).
    if usage.beehiiv_pub_id not in (usage.initial_fetched_pub_ids or []):
        return {'show_limited_data_coach': False}

    try:
        publication = Publication.objects.get(pub_id=usage.beehiiv_pub_id)
    except Publication.DoesNotExist:
        return {'show_limited_data_coach': False}

    from django.db.models import Q

    eligible_count = Post.objects.filter(
        Q(platform__in=('email', 'both')) | Q(platform__isnull=True),
        user=request.user,
        publication=publication,
        status='Published',
    ).count()

    return {'show_limited_data_coach': eligible_count < 5}
