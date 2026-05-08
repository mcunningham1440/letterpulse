from django.db import transaction


class NotEnoughCredits(Exception):
    """Raised when a user doesn't have enough credits for an operation."""
    pass


def charge_credits(user, credits_to_charge: int):
    """
    Atomically charge credits for a user, enforcing their monthly quota.

    Args:
        user: The Django user object
        credits_to_charge: Number of credits to charge

    Raises:
        NotEnoughCredits: If user doesn't have enough credits
    """
    from analytics.models import UsageAccount

    if user is None or not user.is_authenticated:
        raise NotEnoughCredits("You must be logged in to use AI features.")

    with transaction.atomic():
        usage = UsageAccount.objects.select_for_update().get(user=user)
        usage.ensure_current_period()

        if usage.used_this_period + credits_to_charge > usage.monthly_quota:
            raise NotEnoughCredits(
                f"Not enough AI credits. "
                f"You have {usage.monthly_quota - usage.used_this_period} credits remaining, "
                f"but this operation requires {credits_to_charge}."
            )

        # Plain-int assignment (not F()) so an in-memory reset from
        # ensure_current_period() actually persists. select_for_update() above
        # already locks the row, so F()'s atomicity benefit is redundant.
        usage.used_this_period = usage.used_this_period + credits_to_charge
        usage.save(update_fields=['used_this_period', 'period_start'])
