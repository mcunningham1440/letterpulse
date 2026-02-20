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
                'show_survey': not usage.survey_completed,
            }
        except UsageAccount.DoesNotExist:
            return {'show_survey': False}
    return {'show_survey': False}


def progress_context(request):
    """
    Add progress bar durations to template context.
    """
    return {
        'progress_durations': settings.PROGRESS_DURATIONS,
        'expected_times': settings.EXPECTED_TIMES,
    }
