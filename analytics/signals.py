"""
Signal handlers for the analytics app.
"""

import logging
import threading

from django.conf import settings
from django.core.mail import send_mail, EmailMultiAlternatives
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.contrib.auth import get_user_model
from django.utils import timezone

from .models import UsageAccount

User = get_user_model()
logger = logging.getLogger(__name__)

# Set to True to suppress the auto welcome email (e.g. when provisioning users manually)
SUPPRESS_WELCOME_EMAIL = False


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
        if not SUPPRESS_WELCOME_EMAIL:
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
            greeting = f"Hi {first_name}," if first_name else "Hi,"
            html_body = (
                f"{greeting}<br><br>"
                "Thanks for signing up to try out LetterPulse!<br><br>"

                "<b>Getting started:</b><br><br>"

                "1. <b>Add your Beehiiv API credentials</b> on the <a href=\"https://letterpulse.com/account/\">Account tab</a> "
                "so LetterPulse can pull in your past issues.<br>"
                "2. Head to the <a href=\"https://letterpulse.com/insights/\">Write tab</a> and click <b>Scan</b> when prompted. "
                "LetterPulse will read your recent issues in the background to learn what your sections are and how each one performs.<br>"
                "3. Once the scan finishes, try <b>Content Finder</b> to get suggested links for an upcoming issue, or "
                "<b>Improvement Tips</b> to get inline editing suggestions on any specific post.<br><br>"

                "The app has informational tooltips to walk you through getting started, "
                "but please reach out if anything is unclear\u2014I'm always happy to help.<br><br>"

                "I'll reach back out in a few days once you've had a chance to try it out, "
                "but if you have any questions or comments in the meantime, please let me know! "
                "<b>You're one of the first people to test out LetterPulse so your feedback will be very helpful.</b><br><br>"
                "<div>Best,<br><br>"
                "Michael Cunningham<br>"
                "Founder, LetterPulse.com</div>"
            )
            plain_body = (
                f"{greeting}\n\n"
                "Thanks for signing up to try out LetterPulse!\n\n"

                "Getting started:\n\n"

                "1. Add your Beehiiv API credentials on the Account tab (https://letterpulse.com/account/) "
                "so LetterPulse can pull in your past issues.\n"
                "2. Head to the Write tab (https://letterpulse.com/insights/) and click Scan when prompted. "
                "LetterPulse will read your recent issues in the background to learn what your sections are and how each one performs.\n"
                "3. Once the scan finishes, try Content Finder to get suggested links for an upcoming issue, or "
                "Improvement Tips to get inline editing suggestions on any specific post.\n\n"

                "The app has informational tooltips to walk you through getting started, "
                "but please reach out if anything is unclear\u2014I'm always happy to help.\n\n"

                "I'll reach back out in a few days once you've had a chance to try it out, "
                "but if you have any questions or comments in the meantime, please let me know! "
                "You're one of the first people to test out LetterPulse so your feedback will be very helpful.\n\n"
                "Best,\n\n"

                "Michael Cunningham\n"
                "Founder, LetterPulse.com"
            )
            msg = EmailMultiAlternatives(
                subject="Welcome to LetterPulse!",
                body=plain_body,
                from_email=settings.DEFAULT_FROM_EMAIL,
                to=[user.email],
            )
            msg.attach_alternative(html_body, "text/html")
            msg.send(fail_silently=True)
        except Exception:
            logger.exception("Failed to send welcome email to %s", user.email)

    threading.Thread(target=_send, daemon=True).start()
