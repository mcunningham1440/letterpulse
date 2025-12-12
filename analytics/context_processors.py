"""
Context processors for the analytics app.
"""

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
