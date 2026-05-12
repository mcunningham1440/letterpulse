"""
Drop the deferred schema items called out in 0049_drop_dead_fields and
0050_drop_click_viz: their docstrings noted that the SurveyResponse model,
the CronRunLog model, and UsageAccount.survey_completed were all removed
from models.py without corresponding migrations, and explicitly punted the
schema cleanup to a separate decision.

This is that decision. None of the three are referenced anywhere in the
live code (templates, JS, views, utils, admin) outside their original
migrations.

The breaking symptom this fixes: UsageAccount.objects.create(...) in the
post_save signal fails on a freshly migrated DB because
survey_completed is NOT NULL with no DB-level default and the model no
longer carries the field, so the INSERT omits it.
"""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("analytics", "0050_drop_click_viz"),
    ]

    operations = [
        migrations.RemoveField(model_name="surveyresponse", name="user"),
        migrations.DeleteModel(name="SurveyResponse"),

        migrations.DeleteModel(name="CronRunLog"),

        migrations.RemoveField(model_name="usageaccount", name="survey_completed"),
    ]
