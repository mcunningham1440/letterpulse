from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('analytics', '0017_processedpost'),
    ]

    operations = [
        migrations.CreateModel(
            name='ProcessingTemplate',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=255)),
                ('sections_data', models.JSONField(help_text='JSON array of {name, description} dicts')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('publication', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='processing_templates', to='analytics.publication')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='processing_templates', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Processing Template',
                'verbose_name_plural': 'Processing Templates',
                'ordering': ['-created_at'],
                'unique_together': {('name', 'publication', 'user')},
            },
        ),
    ]
