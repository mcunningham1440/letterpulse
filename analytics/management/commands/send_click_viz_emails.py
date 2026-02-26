"""
Management command to send click visualization emails for recently published posts.

Usage:
    python manage.py send_click_viz_emails [--dry-run] [--user-email=<email>]
"""

import logging
from datetime import timezone as dt_timezone
from datetime import datetime, timedelta

from django.core.management.base import BaseCommand
from django.core.mail import EmailMessage
from django.conf import settings
from asgiref.sync import async_to_sync

from analytics.models import UsageAccount, ClickVizEmailLog, Publication
from analytics.utils import (
    fetch_recent_published_posts,
    fetch_posts_html_and_clicks_parallel,
    generate_click_visualization_html,
    build_click_viz_email_html,
)

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Send click visualization emails for posts published ~6 hours ago"

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help="List eligible posts without sending emails",
        )
        parser.add_argument(
            '--user-email',
            type=str,
            default=None,
            help="Only process a specific user (by email address)",
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        user_email_filter = options.get('user_email')

        # Find all users with the feature enabled and valid API keys
        queryset = UsageAccount.objects.filter(
            auto_click_viz_email=True,
            api_key_valid=True,
            auto_click_viz_enabled_at__isnull=False,
        ).select_related('user')

        if user_email_filter:
            queryset = queryset.filter(user__email=user_email_filter)

        users = list(queryset)
        self.stdout.write(f"Found {len(users)} eligible user(s)")

        now = datetime.now(tz=dt_timezone.utc)
        six_hours_ago = now - timedelta(hours=6)

        total_sent = 0
        total_errors = 0

        for usage in users:
            user = usage.user
            self.stdout.write(f"\nProcessing user: {user.email}")

            try:
                # Fetch recent published posts from Beehiiv API
                recent_posts = async_to_sync(fetch_recent_published_posts)(
                    usage.beehiiv_token, usage.beehiiv_pub_id, max_pages=3
                )
                self.stdout.write(f"  Fetched {len(recent_posts)} recent posts from API")

                # Get post IDs that already have successful email logs for this user
                existing_logs = set(
                    ClickVizEmailLog.objects.filter(
                        user=user, success=True
                    ).values_list('post_id', flat=True)
                )

                # Filter to eligible posts
                eligible_posts = []
                for post_data in recent_posts:
                    post_id = post_data.get('id', '')
                    publish_ts = post_data.get('publish_date')
                    if not publish_ts:
                        continue

                    publish_dt = datetime.fromtimestamp(publish_ts, tz=dt_timezone.utc)

                    # Post must be published after the feature was enabled
                    if publish_dt <= usage.auto_click_viz_enabled_at:
                        continue

                    # Post must be published >6 hours ago (enough time for click data)
                    if publish_dt > six_hours_ago:
                        continue

                    # No successful email log yet
                    if post_id in existing_logs:
                        continue

                    eligible_posts.append(post_data)

                self.stdout.write(f"  {len(eligible_posts)} eligible post(s)")

                if not eligible_posts:
                    continue

                if dry_run:
                    for p in eligible_posts:
                        title = p.get('title', 'Untitled')
                        publish_ts = p.get('publish_date', 0)
                        publish_dt = datetime.fromtimestamp(publish_ts, tz=dt_timezone.utc)
                        self.stdout.write(f"  [DRY RUN] Would send for: {title} (published {publish_dt})")
                    continue

                # Fetch HTML and clicks for eligible posts
                post_ids = [p['id'] for p in eligible_posts]
                htmls, clicks_by_id = async_to_sync(fetch_posts_html_and_clicks_parallel)(
                    post_ids, usage.beehiiv_token, usage.beehiiv_pub_id
                )

                # Get or create Publication record
                publication = None
                try:
                    publication = Publication.objects.get(pub_id=usage.beehiiv_pub_id)
                except Publication.DoesNotExist:
                    pass

                site_url = getattr(settings, 'SITE_URL', 'https://letterpulse.com')

                for post_data in eligible_posts:
                    post_id = post_data['id']
                    post_title = post_data.get('title', 'Untitled')
                    html_filename = f"{post_id}.html"

                    try:
                        if html_filename not in htmls or post_id not in clicks_by_id:
                            raise ValueError(f"Missing HTML or clicks data for post {post_id}")

                        post_html = htmls[html_filename]
                        clicks_dict = clicks_by_id[post_id]

                        # Get unique_email_opens from stats
                        stats = post_data.get('stats', {}).get('email', {})
                        unique_email_opens = stats.get('unique_opens', 0)

                        # Generate click visualization
                        viz_html = generate_click_visualization_html(
                            post_html, clicks_dict, unique_email_opens
                        )

                        # Wrap with email banner/footer
                        email_html = build_click_viz_email_html(viz_html, post_title, site_url)

                        # Send email
                        bcc = [settings.SIGNUP_NOTIFICATION_EMAIL] if getattr(settings, 'SIGNUP_NOTIFICATION_EMAIL', '') else []
                        email = EmailMessage(
                            subject=f"Click Visualization: {post_title}",
                            body=email_html,
                            from_email=settings.DEFAULT_FROM_EMAIL,
                            to=[user.email],
                            bcc=bcc,
                        )
                        email.content_subtype = 'html'
                        email.send()

                        # Log success
                        ClickVizEmailLog.objects.create(
                            user=user,
                            publication=publication,
                            post_id=post_id,
                            post_title=post_title,
                            success=True,
                        )
                        total_sent += 1
                        self.stdout.write(self.style.SUCCESS(f"  Sent: {post_title}"))

                    except Exception as e:
                        logger.exception(f"Error sending click viz email for {post_id} to {user.email}")
                        # Log failure (use update_or_create to handle unique constraint)
                        ClickVizEmailLog.objects.update_or_create(
                            user=user,
                            post_id=post_id,
                            defaults={
                                'publication': publication,
                                'post_title': post_title,
                                'success': False,
                                'error_message': str(e),
                            },
                        )
                        total_errors += 1
                        self.stdout.write(self.style.ERROR(f"  Failed: {post_title} - {e}"))

            except Exception as e:
                logger.exception(f"Error processing user {user.email}")
                self.stdout.write(self.style.ERROR(f"  Error processing user: {e}"))
                total_errors += 1

        self.stdout.write(f"\nDone. Sent: {total_sent}, Errors: {total_errors}")
