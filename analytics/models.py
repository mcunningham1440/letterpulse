from django.db import models
from django.conf import settings
from django.contrib.postgres.fields import ArrayField
from django.core.serializers.json import DjangoJSONEncoder
from django.utils import timezone
import json


def get_default_monthly_credits():
    """Get default monthly credits from settings"""
    return getattr(settings, 'DEFAULT_MONTHLY_CREDITS', 100)


class UsageAccount(models.Model):
    """Track AI usage credits for each user"""

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
        Lazy reset when a new month starts.
        Call this before checking or charging credits.
        """
        today = timezone.now().date()
        current_period_start = today.replace(day=1)
        if self.period_start != current_period_start:
            self.period_start = current_period_start
            self.used_this_period = 0


class Post(models.Model):
    """Model representing a Beehiiv newsletter post"""

    post_id = models.CharField(max_length=255, unique=True, help_text="Beehiiv post ID")
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
    
    name = models.CharField(max_length=255, unique=True)
    description = models.TextField(blank=True, help_text="Optional description of this content set")
    items_data = models.JSONField(help_text="JSON data containing the extracted items")
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-created_at']
    
    def __str__(self):
        return self.name
    
    def get_items_count(self):
        """Return the number of items in this content set"""
        if isinstance(self.items_data, list):
            return len(self.items_data)
        return 0
    
    def to_dataframe(self):
        """Convert items_data to a pandas DataFrame"""
        import pandas as pd
        if isinstance(self.items_data, list) and len(self.items_data) > 0:
            df = pd.DataFrame(self.items_data)
            # Convert post_date to datetime.date if it's a string
            if 'post_date' in df.columns:
                df['post_date'] = pd.to_datetime(df['post_date']).dt.date
            return df
        return pd.DataFrame()
    
    @classmethod
    def from_dataframe(cls, name, df, description=""):
        """Create a ContentSet from a pandas DataFrame"""
        # Convert DataFrame to records
        df_copy = df.copy()
        if 'post_date' in df_copy.columns:
            df_copy['post_date'] = df_copy['post_date'].astype(str)
        
        items_data = df_copy.to_dict(orient='records')
        
        return cls(
            name=name,
            description=description,
            items_data=items_data
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
