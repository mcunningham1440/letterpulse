"""
Drop the click-viz email infrastructure, confirmed dead in this audit pass:

- ClickVizEmailLog: model class already removed from models.py; the
  send_click_viz_emails management command (its only writer) and the
  cron_status view (its only reader) have also been removed.
- UsageAccount.auto_click_viz_email / auto_click_viz_enabled_at: per-user
  toggle and enabled-at timestamp; both fields already removed from
  models.py and from the Account UI.

Hand-authored to keep this scoped to the click-viz drops only — running
makemigrations would also schedule deletion of CronRunLog, SurveyResponse,
and UsageAccount.survey_completed (model classes were removed from
models.py without corresponding migrations), which is a separate decision
and not something this migration touches.
"""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("analytics", "0049_drop_dead_fields"),
    ]

    operations = [
        migrations.AlterUniqueTogether(
            name="clickvizemaillog",
            unique_together=set(),
        ),
        migrations.RemoveField(model_name="clickvizemaillog", name="publication"),
        migrations.RemoveField(model_name="clickvizemaillog", name="user"),
        migrations.DeleteModel(name="ClickVizEmailLog"),

        migrations.RemoveField(model_name="usageaccount", name="auto_click_viz_email"),
        migrations.RemoveField(model_name="usageaccount", name="auto_click_viz_enabled_at"),
    ]
