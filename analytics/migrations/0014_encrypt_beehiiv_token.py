# Generated migration for encrypting beehiiv_token field

from django.db import migrations
import analytics.fields


class Migration(migrations.Migration):

    dependencies = [
        ('analytics', '0013_convert_publish_date_to_utc'),
    ]

    operations = [
        migrations.AlterField(
            model_name='usageaccount',
            name='beehiiv_token',
            field=analytics.fields.EncryptedCharField(
                blank=True,
                default='',
                help_text='Beehiiv API token (encrypted)',
                max_length=500
            ),
        ),
    ]
