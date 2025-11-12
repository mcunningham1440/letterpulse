from django.db import models
from django.contrib.postgres.fields import ArrayField
from django.core.serializers.json import DjangoJSONEncoder
import json


class Post(models.Model):
    """Model representing a Beehiiv newsletter post"""
    
    post_id = models.CharField(max_length=255, unique=True, help_text="Beehiiv post ID")
    title = models.CharField(max_length=500)
    subtitle = models.TextField(blank=True, null=True)
    publish_date_cst = models.DateField()
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
