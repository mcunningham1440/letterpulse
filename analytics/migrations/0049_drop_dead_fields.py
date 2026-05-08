"""
Drop fields confirmed unused after a dead-code audit:

- Post: subtitle, delivered, email_opens, email_clicks, unique_email_clicks,
  unsubscribes, spam_reports (subtitle was never populated; the engagement
  counters were written from Beehiiv but only consumed by admin.py and the
  unused load_posts_from_db helper).
- LinkData: rank_in_post (admin-only display column).
- PendingContentSearch: mode (always 'auto'), selected_sections (always []).
- ExecutionLog: parent_id, inputs, outputs, meta ("for future use" placeholders
  that were always written as None / {}).

Hand-authored to keep this scoped to the dead-code drops only — running
makemigrations would also schedule deletion of the SurveyResponse table and
UsageAccount.survey_completed (model classes were removed from models.py
without a corresponding migration), which is a separate decision and not
something this migration touches.
"""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("analytics", "0048_add_pending_niche_analysis"),
    ]

    operations = [
        migrations.RemoveField(model_name="post", name="subtitle"),
        migrations.RemoveField(model_name="post", name="delivered"),
        migrations.RemoveField(model_name="post", name="email_opens"),
        migrations.RemoveField(model_name="post", name="email_clicks"),
        migrations.RemoveField(model_name="post", name="unique_email_clicks"),
        migrations.RemoveField(model_name="post", name="unsubscribes"),
        migrations.RemoveField(model_name="post", name="spam_reports"),

        migrations.RemoveField(model_name="linkdata", name="rank_in_post"),

        migrations.RemoveField(model_name="pendingcontentsearch", name="mode"),
        migrations.RemoveField(model_name="pendingcontentsearch", name="selected_sections"),

        migrations.RemoveField(model_name="executionlog", name="parent_id"),
        migrations.RemoveField(model_name="executionlog", name="inputs"),
        migrations.RemoveField(model_name="executionlog", name="outputs"),
        migrations.RemoveField(model_name="executionlog", name="meta"),
    ]
