from django.db import models
from django.conf import settings
from django.contrib.postgres.fields import ArrayField
from django.core.serializers.json import DjangoJSONEncoder
from django.utils import timezone
from calendar import monthrange
import json


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

    # Beehiiv API credentials
    beehiiv_token = models.CharField(
        max_length=255,
        blank=True,
        default='',
        help_text="Beehiiv API token"
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

    post_id = models.CharField(max_length=255, unique=True, help_text="Beehiiv post ID")
    publication = models.ForeignKey(
        Publication,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='posts',
        help_text="The publication this post belongs to"
    )
    title = models.CharField(max_length=500)
    subtitle = models.TextField(blank=True, null=True)
    status = models.CharField(max_length=20, default='Published', help_text="Draft or Published")
    creation_date = models.DateTimeField(blank=True, null=True, help_text="When the post was first created in Beehiiv")
    publish_date_cst = models.DateField(blank=True, null=True)
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
        ordering = ['-publish_date_cst']
        indexes = [
            models.Index(fields=['-publish_date_cst']),
            models.Index(fields=['post_id']),
        ]
    
    def __str__(self):
        return f"{self.title} ({self.publish_date_cst})"
    
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
    description = models.TextField(blank=True, help_text="Optional description of this content set")
    items_data = models.JSONField(help_text="JSON data containing the extracted items")
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-created_at']
        unique_together = [['name', 'publication']]

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


class Report(models.Model):
    """Model representing a saved content insights report"""
    
    name = models.CharField(max_length=255)
    content_set = models.ForeignKey(
        ContentSet, 
        on_delete=models.CASCADE, 
        related_name='reports',
        help_text="The content set this report is based on"
    )
    report_text = models.TextField(help_text="The markdown-formatted report content")
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-created_at']
        unique_together = [['name', 'content_set']]
    
    def __str__(self):
        return f"{self.name} - {self.content_set.name}"
