from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("analytics", "0040_initial_fetched_pub_ids"),
    ]

    operations = [
        migrations.AddField(
            model_name="post",
            name="platform",
            field=models.CharField(
                blank=True,
                help_text="Beehiiv platform value: email, web, or both",
                max_length=20,
                null=True,
            ),
        ),
    ]
