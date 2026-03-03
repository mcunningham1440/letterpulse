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
        _send_welcome_email(instance)


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
            db_host = settings.DATABASES['default'].get('HOST', '')
            if 'prod' in db_host.lower():
                env_label = "PROD"
            elif 'dev' in db_host.lower():
                env_label = "DEV"
            else:
                env_label = "UNKNOWN"
            send_mail(
                subject=f"[{env_label}] New LetterPulse signup: {user.email}",
                message=f"Environment: {env_label}\nName: {name}\nEmail: {user.email}\nNewsletter: {newsletter_name}\nJoined: {user.date_joined}",
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[to_email],
                fail_silently=True,
            )
        except Exception:
            logger.exception("Failed to send signup notification email")

    threading.Thread(target=_send, daemon=True).start()


def _send_welcome_email(user):
    """Send a welcome email to the new user in a background thread."""
    def _send():
        try:
            user.refresh_from_db()
            first_name = user.first_name.strip()
            greeting = f"Hi {first_name},\n" if first_name else "Hi,\n"
            body = (
                f"{greeting}\n"
                "Thanks for signing up to try out LetterPulse!\n\n"
                "The app has informational tooltips to walk you through getting started, "
                "but please reach out if anything is unclear--I'm always happy to help.\n\n"
                "I'll reach back out in a few days once you've had a chance to try it out, "
                "but if you have any questions or comments in the meantime, please let me know! "
                "You're one of the first people to test out LetterPulse so your feedback will be very helpful.\n\n"
                "Best,\n\n"
                "Michael Cunningham\n"
                "Founder, LetterPulse.com"
            )
            send_mail(
                subject="Welcome to LetterPulse!",
                message=body,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[user.email],
                fail_silently=True,
            )
        except Exception:
            logger.exception("Failed to send welcome email to %s", user.email)

    threading.Thread(target=_send, daemon=True).start()
