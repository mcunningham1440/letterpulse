from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('analytics', '0042_pending_learning_task'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='report',
            name='content_set',
        ),
        migrations.RemoveField(
            model_name='report',
            name='publication',
        ),
        migrations.RemoveField(
            model_name='report',
            name='user',
        ),
        migrations.RemoveField(
            model_name='contentset',
            name='publication',
        ),
        migrations.RemoveField(
            model_name='contentset',
            name='user',
        ),
        migrations.DeleteModel(
            name='Report',
        ),
        migrations.DeleteModel(
            name='ContentSet',
        ),
    ]
