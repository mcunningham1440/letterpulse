from django.db import migrations, models


def delete_all_reports(apps, schema_editor):
    """Delete all existing Report rows — old report UI is being removed."""
    Report = apps.get_model('analytics', 'Report')
    Report.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ('analytics', '0027_auto_click_viz_email_default_true'),
    ]

    operations = [
        # 1. Delete all existing reports (old UI is being removed)
        migrations.RunPython(delete_all_reports, migrations.RunPython.noop),

        # 2. Add section_name to Report
        migrations.AddField(
            model_name='report',
            name='section_name',
            field=models.CharField(blank=True, default='', max_length=255),
        ),

        # 3. Change unique_together on Report
        migrations.AlterUniqueTogether(
            name='report',
            unique_together={('section_name', 'user', 'publication')},
        ),

        # 4. Add section_name to PendingReport
        migrations.AddField(
            model_name='pendingreport',
            name='section_name',
            field=models.CharField(blank=True, default='', max_length=255),
        ),
    ]
