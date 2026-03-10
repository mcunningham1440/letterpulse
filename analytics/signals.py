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

                "<b>Some quick wins to get value from your first session:</b><br><br>"

                "1. Once you've entered your Beehiiv credentials, head to the <a href=\"https://letterpulse.com/posts/\">Posts tab</a> and "
                "<b>process a few of your recent issues</b> with at least 2-3 recurring sections in your newsletter. "
                "<i>Make sure to use real sections! The app won't work properly if given made-up ones like 'just testing'.</i><br>"
                "2. <b>Check out the section performance chart</b> in the <a href=\"https://letterpulse.com/insights/\">Insights tab</a> to see how each section is performing over time.<br>"
                "3. <b>Run &quot;Get Insights&quot;</b> to see what kind of content in those sections performs best.<br><br>"

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

                "Some quick wins to get value from your first session:\n\n"

                "1. Once you've entered your Beehiiv credentials, head to the Posts tab (https://letterpulse.com/posts/) and "
                "process a few of your recent issues with at least 2-3 recurring sections in your newsletter. "
                "Make sure to use real sections! The app won't work properly if given made-up ones like 'just testing'.\n"
                "2. Check out the section performance chart in the Insights tab (https://letterpulse.com/insights/) to see how each section is performing over time.\n"
                "3. Run \"Get Insights\" to see what kind of content in those sections performs best.\n\n"

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
