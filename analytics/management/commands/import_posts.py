"""
Management command to import posts from CSV into the database.
"""

from django.core.management.base import BaseCommand
from django.conf import settings
import pandas as pd
from analytics.models import Post


class Command(BaseCommand):
    help = 'Import posts from CSV file into the database'

    def handle(self, *args, **options):
        csv_path = settings.POSTS_CSV
        
        if not csv_path.exists():
            self.stdout.write(self.style.ERROR(f'CSV file not found: {csv_path}'))
            return
        
        self.stdout.write(f'Reading posts from {csv_path}...')
        
        df = pd.read_csv(csv_path)
        df['publish_date_cst'] = pd.to_datetime(df['publish_date_cst'], utc=True).dt.date
        
        created_count = 0
        updated_count = 0
        
        for _, row in df.iterrows():
            post, created = Post.objects.update_or_create(
                post_id=row['id'],
                defaults={
                    'title': row['title'],
                    'subtitle': row.get('subtitle', ''),
                    'publish_date_cst': row['publish_date_cst'],
                    'recipients': row.get('recipients', 0),
                    'delivered': row.get('delivered', 0),
                    'email_opens': row.get('email_opens', 0),
                    'unique_email_opens': row.get('unique_email_opens', 0),
                    'email_clicks': row.get('email_clicks', 0),
                    'unique_email_clicks': row.get('unique_email_clicks', 0),
                    'unsubscribes': row.get('unsubscribes', 0),
                    'spam_reports': row.get('spam_reports', 0),
                }
            )
            
            if created:
                created_count += 1
            else:
                updated_count += 1
        
        self.stdout.write(self.style.SUCCESS(
            f'Successfully imported {created_count} new posts and updated {updated_count} existing posts'
        ))
