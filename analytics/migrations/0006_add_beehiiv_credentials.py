# Generated manually

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("analytics", "0005_usageaccount"),
    ]

    operations = [
        migrations.AddField(
            model_name="usageaccount",
            name="beehiiv_token",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Beehiiv API token",
                max_length=255,
            ),
        ),
        migrations.AddField(
            model_name="usageaccount",
            name="beehiiv_pub_id",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Beehiiv publication ID",
                max_length=255,
            ),
        ),
    ]
