"""
Signal handlers for the analytics app.
"""

import logging
import threading

from django.conf import settings
from django.core.mail import send_mail
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.contrib.auth import get_user_model
from django.utils import timezone

from .models import UsageAccount

User = get_user_model()
logger = logging.getLogger(__name__)


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
        _send_signup_notification(instance)


def _send_signup_notification(user):
    """Send an email notification about a new signup in a background thread."""
    to_email = getattr(settings, 'SIGNUP_NOTIFICATION_EMAIL', '')
    if not to_email:
        return

    def _send():
        try:
            user.refresh_from_db()
            name = f"{user.first_name} {user.last_name}".strip() or "N/A"
            newsletter = getattr(user, 'usage_account', None)
            newsletter_name = newsletter.newsletter_name if newsletter else "N/A"
            send_mail(
                subject=f"New LetterPulse signup: {user.email}",
                message=f"Name: {name}\nEmail: {user.email}\nNewsletter: {newsletter_name}\nJoined: {user.date_joined}",
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[to_email],
                fail_silently=True,
            )
        except Exception:
            logger.exception("Failed to send signup notification email")

    threading.Thread(target=_send, daemon=True).start()
