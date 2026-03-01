"""
Management command to send click visualization emails for recently published posts.

Usage:
    python manage.py send_click_viz_emails [--dry-run] [--user-email=<email>]
"""

import io
import logging
from datetime import timezone as dt_timezone
from datetime import datetime, timedelta

from django.core.management.base import BaseCommand
from django.core.mail import EmailMessage
from django.conf import settings
from asgiref.sync import async_to_sync

from analytics.models import UsageAccount, ClickVizEmailLog, Publication, CronRunLog
from analytics.utils import (
    fetch_recent_published_posts,
    fetch_posts_html_and_clicks_parallel,
    generate_click_visualization_html,
    build_click_viz_email_html,
)

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Send click visualization emails for posts published more than 24 hours ago"

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
        parser.add_argument(
            '--test',
            action='store_true',
            help="Send a test email using the most recent published post, ignoring eligibility filters and without logging the send",
        )
        parser.add_argument(
            '--triggered-by',
            type=str,
            default='manual',
            help="How the run was triggered (e.g. cron, manual)",
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        test_mode = options['test']
        user_email_filter = options.get('user_email')
        triggered_by = options.get('triggered_by', 'manual')

        if test_mode and not user_email_filter:
            self.stderr.write(self.style.ERROR("--test requires --user-email"))
            return

        started_at = datetime.now(tz=dt_timezone.utc)

        # Capture stdout to a buffer so we can store it in CronRunLog
        output_buffer = io.StringIO()

        def log(msg, style=None):
            """Write to both stdout and the capture buffer."""
            if style:
                self.stdout.write(style(msg))
            else:
                self.stdout.write(msg)
            output_buffer.write(msg + '\n')

        if test_mode:
            # Test mode: only need valid API key, skip auto_click_viz_email check
            queryset = UsageAccount.objects.filter(
                api_key_valid=True,
                user__email=user_email_filter,
            ).select_related('user')
        else:
            # Normal mode: find users with the feature enabled and valid API keys
            queryset = UsageAccount.objects.filter(
                auto_click_viz_email=True,
                api_key_valid=True,
                auto_click_viz_enabled_at__isnull=False,
            ).select_related('user')

            if user_email_filter:
                queryset = queryset.filter(user__email=user_email_filter)

        users = list(queryset)
        log(f"Found {len(users)} eligible user(s)")

        now = datetime.now(tz=dt_timezone.utc)

        total_sent = 0
        total_errors = 0
        overall_success = True

        for usage in users:
            user = usage.user
            log(f"\nProcessing user: {user.email}")

            try:
                # Fetch recent published posts from Beehiiv API
                recent_posts = async_to_sync(fetch_recent_published_posts)(
                    usage.beehiiv_token, usage.beehiiv_pub_id, max_pages=3
                )
                log(f"  Fetched {len(recent_posts)} recent posts from API")

                if test_mode:
                    # Test mode: pick the most recent published post
                    published = [
                        p for p in recent_posts if p.get('publish_date')
                    ]
                    if not published:
                        log("  No published posts found", style=self.style.WARNING)
                        continue
                    published.sort(key=lambda p: p['publish_date'], reverse=True)
                    eligible_posts = [published[0]]
                    log(f"  [TEST] Using most recent post: {eligible_posts[0].get('title', 'Untitled')}")
                else:
                    cutoff = now - timedelta(hours=24)

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

                        # Post must be published longer ago than the user's delay
                        if publish_dt > cutoff:
                            continue

                        # No successful email log yet
                        if post_id in existing_logs:
                            continue

                        eligible_posts.append(post_data)

                log(f"  {len(eligible_posts)} eligible post(s)")

                if not eligible_posts:
                    continue

                if dry_run:
                    for p in eligible_posts:
                        title = p.get('title', 'Untitled')
                        publish_ts = p.get('publish_date', 0)
                        publish_dt = datetime.fromtimestamp(publish_ts, tz=dt_timezone.utc)
                        log(f"  [DRY RUN] Would send for: {title} (published {publish_dt})")
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

                        if test_mode:
                            # Test mode: don't log the send
                            total_sent += 1
                            log(f"  [TEST] Sent: {post_title} (not logged)", style=self.style.SUCCESS)
                        else:
                            # Log success
                            ClickVizEmailLog.objects.create(
                                user=user,
                                publication=publication,
                                post_id=post_id,
                                post_title=post_title,
                                success=True,
                            )
                            total_sent += 1
                            log(f"  Sent: {post_title}", style=self.style.SUCCESS)

                    except Exception as e:
                        logger.exception(f"Error sending click viz email for {post_id} to {user.email}")
                        if not test_mode:
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
                        log(f"  Failed: {post_title} - {e}", style=self.style.ERROR)

            except Exception as e:
                logger.exception(f"Error processing user {user.email}")
                log(f"  Error processing user: {e}", style=self.style.ERROR)
                total_errors += 1

        summary = f"\nDone. Sent: {total_sent}, Errors: {total_errors}"
        log(summary)

        if total_errors > 0:
            overall_success = False

        # Save CronRunLog (skip for dry runs and test mode)
        if not dry_run and not test_mode:
            finished_at = datetime.now(tz=dt_timezone.utc)
            duration_ms = int((finished_at - started_at).total_seconds() * 1000)
            try:
                CronRunLog.objects.create(
                    command='send_click_viz_emails',
                    started_at=started_at,
                    finished_at=finished_at,
                    duration_ms=duration_ms,
                    users_processed=len(users),
                    emails_sent=total_sent,
                    errors=total_errors,
                    output=output_buffer.getvalue(),
                    success=overall_success,
                    triggered_by=triggered_by,
                )
            except Exception as e:
                logger.error(f"Failed to save CronRunLog: {e}")
