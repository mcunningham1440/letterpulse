"""
Add user/publication fields to Report (decouple from ContentSet),
add PendingReport model, and migrate existing data.
"""

import uuid
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def populate_report_user_publication(apps, schema_editor):
    """Copy user and publication from content_set to the new direct fields."""
    Report = apps.get_model('analytics', 'Report')
    for report in Report.objects.select_related('content_set').all():
        if report.content_set:
            report.user_id = report.content_set.user_id
            report.publication_id = report.content_set.publication_id
            report.save(update_fields=['user_id', 'publication_id'])


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('analytics', '0018_processingtemplate'),
    ]

    operations = [
        # Step 1: Make content_set nullable
        migrations.AlterField(
            model_name='report',
            name='content_set',
            field=models.ForeignKey(
                blank=True,
                help_text='The content set this report is based on (legacy, nullable)',
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='reports',
                to='analytics.contentset',
            ),
        ),

        # Step 2: Add user (nullable initially) and publication fields
        migrations.AddField(
            model_name='report',
            name='user',
            field=models.ForeignKey(
                help_text='The user who owns this report',
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='reports',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name='report',
            name='publication',
            field=models.ForeignKey(
                blank=True,
                help_text='The publication this report belongs to',
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='reports',
                to='analytics.publication',
            ),
        ),

        # Step 3: Populate user/publication from content_set
        migrations.RunPython(
            populate_report_user_publication,
            migrations.RunPython.noop,
        ),

        # Step 4: Make user non-nullable
        migrations.AlterField(
            model_name='report',
            name='user',
            field=models.ForeignKey(
                help_text='The user who owns this report',
                on_delete=django.db.models.deletion.CASCADE,
                related_name='reports',
                to=settings.AUTH_USER_MODEL,
            ),
        ),

        # Step 5: Update unique_together
        migrations.AlterUniqueTogether(
            name='report',
            unique_together={('name', 'user', 'publication')},
        ),

        # Step 6: Create PendingReport model
        migrations.CreateModel(
            name='PendingReport',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('task_id', models.UUIDField(default=uuid.uuid4, unique=True)),
                ('status', models.CharField(default='pending', help_text='pending, complete, or error', max_length=20)),
                ('result_text', models.TextField(blank=True)),
                ('error_message', models.TextField(blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('publication', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='pending_reports',
                    to='analytics.publication',
                )),
                ('user', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='pending_reports',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
    ]
