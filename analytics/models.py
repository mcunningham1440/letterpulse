from django.db import models
from django.conf import settings
from django.contrib.postgres.fields import ArrayField
from django.core.serializers.json import DjangoJSONEncoder
from django.utils import timezone
from calendar import monthrange
import json
import uuid

from .fields import EncryptedCharField


def get_default_monthly_credits():
    """Get default monthly credits from settings"""
    return getattr(settings, 'DEFAULT_MONTHLY_CREDITS', 100)


class UsageAccount(models.Model):
    """Track AI usage credits and API credentials for each user"""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='usage_account'
    )
    monthly_quota = models.PositiveIntegerField(
        default=get_default_monthly_credits,
        help_text="Credits available per month"
    )
    used_this_period = models.PositiveIntegerField(
        default=0,
        help_text="Credits used in current billing period"
    )
    period_start = models.DateField(help_text="Start of current billing period")

    # Beehiiv API credentials (encrypted at rest)
    beehiiv_token = EncryptedCharField(
        max_length=500,  # Extra space for encryption overhead
        blank=True,
        default='',
        help_text="Beehiiv API token (encrypted)"
    )
    beehiiv_pub_id = models.CharField(
        max_length=255,
        blank=True,
        default='',
        help_text="Beehiiv publication ID"
    )
    api_key_valid = models.BooleanField(
        default=False,
        help_text="Whether the API key has been validated against Beehiiv"
    )
    available_publications = models.JSONField(
        default=list,
        blank=True,
        help_text="Cached list of publications from Beehiiv API"
    )
    timezone = models.CharField(
        max_length=50,
        default='America/Chicago',
        help_text="User's preferred timezone for date display"
    )
    survey_completed = models.BooleanField(
        default=False,
        help_text="Whether the user has completed the signup survey"
    )
    newsletter_name = models.CharField(
        max_length=255,
        blank=True,
        default='',
        help_text="Name of the user's newsletter"
    )
    auto_click_viz_email = models.BooleanField(
        default=False,
        help_text="Whether to auto-email click visualizations after post publication"
    )
    auto_click_viz_enabled_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the user enabled auto click viz emails; prevents old posts from triggering"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Usage Account"
        verbose_name_plural = "Usage Accounts"

    def __str__(self):
        return f"{self.user.email} - {self.used_this_period}/{self.monthly_quota} credits"

    @property
    def remaining(self):
        """Return remaining credits for this period"""
        return max(0, self.monthly_quota - self.used_this_period)

    @property
    def usage_percentage(self):
        """Return usage as a percentage"""
        if self.monthly_quota == 0:
            return 100
        return min(100, round((self.used_this_period / self.monthly_quota) * 100, 1))

    def ensure_current_period(self):
        """
        Lazy reset when billing period rolls over.
        Billing resets on the same day of month as user signup.
        Call this before checking or charging credits.
        """
        today = timezone.now().date()

        # Get the billing day from user's signup date
        billing_day = self.user.date_joined.day

        # Calculate the billing day for the current month
        # Handle months with fewer days (e.g., signup on 31st, current month has 30 days)
        _, days_in_current_month = monthrange(today.year, today.month)
        current_month_billing_day = min(billing_day, days_in_current_month)

        # Determine the current period start date
        if today.day >= current_month_billing_day:
            # We're in a period that started this month
            new_period_start = today.replace(day=current_month_billing_day)
        else:
            # We're in a period that started last month
            if today.month == 1:
                prev_year = today.year - 1
                prev_month = 12
            else:
                prev_year = today.year
                prev_month = today.month - 1
            _, days_in_prev_month = monthrange(prev_year, prev_month)
            prev_month_billing_day = min(billing_day, days_in_prev_month)
            new_period_start = today.replace(year=prev_year, month=prev_month, day=prev_month_billing_day)

        # Reset if we've entered a new period
        if self.period_start != new_period_start:
            self.period_start = new_period_start
            self.used_this_period = 0

    @property
    def next_renewal_date(self):
        """Calculate the next credit renewal date based on user's signup day."""
        today = timezone.now().date()
        billing_day = self.user.date_joined.day

        # Try current month first
        _, days_in_current_month = monthrange(today.year, today.month)
        current_month_billing_day = min(billing_day, days_in_current_month)

        if today.day < current_month_billing_day:
            # Renewal is later this month
            return today.replace(day=current_month_billing_day)
        else:
            # Renewal is next month
            if today.month == 12:
                next_year = today.year + 1
                next_month = 1
            else:
                next_year = today.year
                next_month = today.month + 1
            _, days_in_next_month = monthrange(next_year, next_month)
            next_month_billing_day = min(billing_day, days_in_next_month)
            return today.replace(year=next_year, month=next_month, day=next_month_billing_day)

    @property
    def has_api_credentials(self):
        """Check if Beehiiv API credentials are configured"""
        return bool(self.beehiiv_token and self.beehiiv_pub_id)

    @property
    def masked_token(self):
        """Return masked version of the API token for display"""
        if not self.beehiiv_token:
            return ''
        if len(self.beehiiv_token) <= 8:
            return '****'
        return self.beehiiv_token[:4] + '****' + self.beehiiv_token[-4:]


class Publication(models.Model):
    """Model representing a Beehiiv publication"""

    pub_id = models.CharField(max_length=255, unique=True, help_text="Beehiiv publication ID")
    name = models.CharField(max_length=255)
    organization_name = models.CharField(max_length=255, blank=True, default='')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return f"{self.name} ({self.pub_id[:20]}...)"


class Post(models.Model):
    """Model representing a Beehiiv newsletter post"""

    post_id = models.CharField(max_length=255, help_text="Beehiiv post ID")
    publication = models.ForeignKey(
        Publication,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='posts',
        help_text="The publication this post belongs to"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='posts',
        help_text="The user who owns this post data"
    )
    title = models.CharField(max_length=500)
    subtitle = models.TextField(blank=True, null=True)
    status = models.CharField(max_length=20, default='Published', help_text="Draft, Scheduled, or Published")
    creation_date = models.DateTimeField(blank=True, null=True, help_text="When the post was first created in Beehiiv")
    publish_date = models.DateTimeField(blank=True, null=True, help_text="When the post was published (stored in UTC)")
    recipients = models.IntegerField(default=0)
    delivered = models.IntegerField(default=0)
    email_opens = models.IntegerField(default=0)
    unique_email_opens = models.IntegerField(default=0)
    email_clicks = models.IntegerField(default=0)
    unique_email_clicks = models.IntegerField(default=0)
    unsubscribes = models.IntegerField(default=0)
    spam_reports = models.IntegerField(default=0)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-publish_date']
        indexes = [
            models.Index(fields=['-publish_date']),
            models.Index(fields=['post_id']),
        ]
        unique_together = [['post_id', 'user']]

    def __str__(self):
        return f"{self.title} ({self.publish_date})"
    
    @property
    def html_filename(self):
        """Return the expected HTML filename for this post"""
        return f"{self.post_id}.html"


class ContentSet(models.Model):
    """Model representing a saved set of extracted content items"""

    name = models.CharField(max_length=255)
    publication = models.ForeignKey(
        Publication,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='content_sets',
        help_text="The publication this content set belongs to"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='content_sets',
        help_text="The user who owns this content set"
    )
    description = models.TextField(blank=True, help_text="Optional description of this content set")
    items_data = models.JSONField(help_text="JSON data containing the extracted items")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        unique_together = [['name', 'publication', 'user']]

    def __str__(self):
        return self.name

    def get_items_count(self):
        """Return the number of items in this content set"""
        if isinstance(self.items_data, list):
            return len(self.items_data)
        return 0

    def get_items_data(self):
        """
        Get items_data as a list of dicts.
        Converts post_date strings to datetime.date objects if present.
        """
        from datetime import datetime

        if not isinstance(self.items_data, list) or len(self.items_data) == 0:
            return []

        items = []
        for item in self.items_data:
            item_copy = dict(item)
            # Convert post_date string to date object if present
            if 'post_date' in item_copy and item_copy['post_date']:
                try:
                    if isinstance(item_copy['post_date'], str):
                        item_copy['post_date'] = datetime.strptime(
                            item_copy['post_date'], '%Y-%m-%d'
                        ).date()
                except (ValueError, TypeError):
                    pass  # Keep as-is if parsing fails
            items.append(item_copy)
        return items

    @classmethod
    def from_items_data(cls, name, items_data, description=""):
        """
        Create a ContentSet from a list of dicts.
        Converts datetime.date objects to strings for JSON storage.
        """
        processed_items = []
        for item in items_data:
            item_copy = dict(item)
            if 'post_date' in item_copy and item_copy['post_date'] is not None:
                item_copy['post_date'] = str(item_copy['post_date'])
            processed_items.append(item_copy)

        return cls(
            name=name,
            description=description,
            items_data=processed_items
        )

    def to_dataframe(self):
        """
        Convert items_data to a pandas DataFrame.
        """
        import pandas as pd
        items = self.get_items_data()
        if items:
            return pd.DataFrame(items)
        return pd.DataFrame()

    @classmethod
    def from_dataframe(cls, name, df, description=""):
        """
        Create a ContentSet from a pandas DataFrame.
        Converts the DataFrame to a list of dicts for JSON storage.
        """
        # Convert DataFrame to list of dicts
        items_data = df.to_dict('records')
        return cls.from_items_data(name, items_data, description)


class Report(models.Model):
    """Model representing a saved content insights report for a specific section"""

    name = models.CharField(max_length=255)
    section_name = models.CharField(max_length=255, blank=True, default='')
    content_set = models.ForeignKey(
        ContentSet,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='reports',
        help_text="The content set this report is based on (legacy, nullable)"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='reports',
        help_text="The user who owns this report"
    )
    publication = models.ForeignKey(
        'Publication',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='reports',
        help_text="The publication this report belongs to"
    )
    report_text = models.TextField(help_text="The markdown-formatted report content")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        unique_together = [['section_name', 'user', 'publication']]

    def __str__(self):
        return f"{self.section_name} - {self.user.email}"


class ExecutionLog(models.Model):
    """
    Execution logs for HTTP requests and function calls.
    Uses queue-based async logging for minimal overhead.
    """

    # Timing fields
    ts_start = models.DateTimeField(help_text="When execution started")
    ts_end = models.DateTimeField(help_text="When execution ended")
    duration_ms = models.IntegerField(help_text="Duration in milliseconds")

    # Classification
    KIND_CHOICES = [
        ('request', 'HTTP Request'),
        ('function', 'Function Call'),
    ]
    kind = models.CharField(max_length=20, choices=KIND_CHOICES, help_text="Type of log entry")
    name = models.CharField(max_length=255, help_text="View name or module.function")

    # Status
    success = models.BooleanField(default=True, help_text="Whether execution succeeded")
    error_type = models.CharField(max_length=255, blank=True, default='', help_text="Exception class name")
    error_message = models.TextField(blank=True, default='', help_text="Exception message")
    traceback = models.TextField(blank=True, default='', help_text="Full traceback if error")

    # Context
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='execution_logs',
        help_text="User who triggered the execution"
    )
    request_id = models.CharField(
        max_length=64, blank=True, default='', db_index=True,
        help_text="UUID for request correlation"
    )
    parent_id = models.BigIntegerField(
        null=True, blank=True,
        help_text="ID of parent log entry for nesting"
    )

    # Data placeholders (for future use)
    inputs = models.JSONField(default=dict, blank=True, help_text="Input parameters (placeholder)")
    outputs = models.JSONField(default=dict, blank=True, help_text="Output data (placeholder)")
    meta = models.JSONField(default=dict, blank=True, help_text="Additional metadata (placeholder)")

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['-created_at']),
            models.Index(fields=['kind', 'name']),
            models.Index(fields=['request_id']),
            models.Index(fields=['success']),
        ]

    def __str__(self):
        status = "OK" if self.success else "ERROR"
        return f"[{self.kind}] {self.name} - {status} ({self.duration_ms}ms)"


class ProcessedPost(models.Model):
    """Lightweight marker indicating a post has been processed for link data."""

    post = models.ForeignKey(
        'Post',
        on_delete=models.CASCADE,
        related_name='processed_posts',
        help_text="The post this extraction belongs to"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='processed_posts',
        help_text="The user who owns this processed data"
    )
    publication = models.ForeignKey(
        Publication,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='processed_posts',
        help_text="The publication this processed post belongs to"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        unique_together = [['post', 'user']]
        verbose_name = "Processed Post"
        verbose_name_plural = "Processed Posts"

    def __str__(self):
        return f"{self.post.title} - {self.user.email}"


class LinkData(models.Model):
    """Stores described link data extracted from a processed post."""

    post = models.ForeignKey(
        'Post',
        on_delete=models.CASCADE,
        related_name='link_data',
        help_text="The post this link was extracted from"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='link_data',
        help_text="The user who owns this link data"
    )
    publication = models.ForeignKey(
        Publication,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='link_data',
        help_text="The publication this link data belongs to"
    )
    raw_url = models.URLField(max_length=2048)
    description = models.TextField(blank=True)
    rank_in_post = models.PositiveIntegerField()
    mean_ctr = models.FloatField(help_text="Mean CTR as percentage (e.g. 3.5 = 3.5%)")
    mean_clicks = models.FloatField()

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['post', 'rank_in_post']
        unique_together = [['post', 'user', 'raw_url']]
        verbose_name = "Link Data"
        verbose_name_plural = "Link Data"

    def __str__(self):
        return f"{self.post.title} - rank {self.rank_in_post} - {self.raw_url[:60]}"


class Section(models.Model):
    """Stores section data extracted from a processed post via agentic GPT loop."""

    post = models.ForeignKey(
        'Post',
        on_delete=models.CASCADE,
        related_name='sections',
        help_text="The post this section was extracted from"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='sections',
        help_text="The user who owns this section data"
    )
    publication = models.ForeignKey(
        Publication,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='sections',
        help_text="The publication this section belongs to"
    )
    section_name = models.CharField(max_length=255)
    section_title = models.CharField(max_length=500, blank=True, null=True,
        help_text="Display title as it appears in the newsletter, or None if untitled")
    section_description = models.TextField(blank=True)
    start_line = models.PositiveIntegerField()
    end_line = models.PositiveIntegerField()
    post_html_length = models.PositiveIntegerField(
        help_text="Total line count of the post HTML"
    )
    section_html = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['post', 'start_line']
        unique_together = [['post', 'user', 'section_name']]
        verbose_name = "Section"
        verbose_name_plural = "Sections"

    def __str__(self):
        return f"{self.post.title} - {self.section_name}"


class PendingReport(models.Model):
    """Tracks background report generation tasks"""

    task_id = models.UUIDField(default=uuid.uuid4, unique=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='pending_reports'
    )
    publication = models.ForeignKey(
        Publication,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='pending_reports'
    )
    section_name = models.CharField(max_length=255, blank=True, default='')
    status = models.CharField(
        max_length=20,
        default='pending',
        help_text="pending, complete, or error"
    )
    result_text = models.TextField(blank=True)
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"PendingReport {self.task_id} ({self.status})"


class SurveyResponse(models.Model):
    """Stores user responses to the signup survey"""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='survey_response'
    )
    beehiiv_analytics_inadequate = models.BooleanField(
        null=True,
        help_text="Does the user feel Beehiiv analytics are inadequate?"
    )
    missing_features = models.TextField(
        blank=True,
        default='',
        help_text="What analytics features does the user feel are missing from Beehiiv?"
    )
    other_tools = models.TextField(
        blank=True,
        default='',
        help_text="What other third-party tools does the user use for newsletter analytics?"
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Survey Response"
        verbose_name_plural = "Survey Responses"

    def __str__(self):
        return f"Survey response from {self.user.email}"


class CronRunLog(models.Model):
    """Log of each cron/management command run for monitoring"""

    command = models.CharField(max_length=255, help_text="Management command name")
    started_at = models.DateTimeField(help_text="When the run started")
    finished_at = models.DateTimeField(null=True, blank=True, help_text="When the run finished")
    duration_ms = models.PositiveIntegerField(null=True, blank=True, help_text="Duration in milliseconds")
    users_processed = models.PositiveIntegerField(default=0)
    emails_sent = models.PositiveIntegerField(default=0)
    errors = models.PositiveIntegerField(default=0)
    output = models.TextField(blank=True, default='', help_text="Captured stdout from the command")
    success = models.BooleanField(default=True)
    triggered_by = models.CharField(
        max_length=50, default='cron',
        help_text="How the run was triggered: cron, manual, etc."
    )

    class Meta:
        ordering = ['-started_at']
        verbose_name = "Cron Run Log"
        verbose_name_plural = "Cron Run Logs"

    def __str__(self):
        status = "OK" if self.success else "FAIL"
        return f"{self.command} at {self.started_at:%Y-%m-%d %H:%M} ({status})"


class ClickVizEmailLog(models.Model):
    """Log of click visualization emails sent to users"""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='click_viz_email_logs'
    )
    publication = models.ForeignKey(
        Publication,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='click_viz_email_logs'
    )
    post_id = models.CharField(max_length=255, help_text="Beehiiv post ID")
    post_title = models.CharField(max_length=500, blank=True, default='')
    sent_at = models.DateTimeField(auto_now_add=True)
    success = models.BooleanField(default=True)
    error_message = models.TextField(blank=True, default='')

    class Meta:
        ordering = ['-sent_at']
        unique_together = [['user', 'post_id']]
        verbose_name = "Click Viz Email Log"
        verbose_name_plural = "Click Viz Email Logs"

    def __str__(self):
        status = "OK" if self.success else "FAIL"
        return f"{self.user.email} - {self.post_id} ({status})"
