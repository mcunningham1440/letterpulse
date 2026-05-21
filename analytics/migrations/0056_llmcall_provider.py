from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('analytics', '0055_drop_usageaccount_json_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='llmcall',
            name='provider',
            field=models.CharField(
                blank=True,
                db_index=True,
                default='',
                help_text="LLM provider ('openai' or 'anthropic'); empty for pre-multiprovider rows",
                max_length=20,
            ),
        ),
    ]
