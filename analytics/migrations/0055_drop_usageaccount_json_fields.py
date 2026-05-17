"""
Step 2 of 2: drop UsageAccount.available_publications and
UsageAccount.initial_fetched_pub_ids now that 0054 has backfilled
UserPublication rows. All read/write sites have been updated to use the
join model.
"""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("analytics", "0054_user_publication"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="usageaccount",
            name="available_publications",
        ),
        migrations.RemoveField(
            model_name="usageaccount",
            name="initial_fetched_pub_ids",
        ),
    ]
