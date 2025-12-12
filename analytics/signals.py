"""
Signal handlers for the analytics app.
"""

from django.db.models.signals import post_save
from django.dispatch import receiver
from django.contrib.auth import get_user_model
from django.utils import timezone

from .models import UsageAccount

User = get_user_model()


@receiver(post_save, sender=User)
def create_usage_account(sender, instance, created, **kwargs):
    """
    Automatically create a UsageAccount when a new user is created.
    The billing period starts on their signup date.
    """
    if created:
        UsageAccount.objects.create(
            user=instance,
            period_start=timezone.now().date()
        )
