from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('analytics', '0016_add_survey_response'),
    ]

    operations = [
        migrations.CreateModel(
            name='ProcessedPost',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('sections_data', models.JSONField(help_text='JSON array of sections, each with section_name and items list')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('post', models.ForeignKey(help_text='The post this extraction belongs to', on_delete=django.db.models.deletion.CASCADE, related_name='processed_posts', to='analytics.post')),
                ('publication', models.ForeignKey(blank=True, help_text='The publication this processed post belongs to', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='processed_posts', to='analytics.publication')),
                ('user', models.ForeignKey(help_text='The user who owns this processed data', on_delete=django.db.models.deletion.CASCADE, related_name='processed_posts', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Processed Post',
                'verbose_name_plural': 'Processed Posts',
                'ordering': ['-created_at'],
                'unique_together': {('post', 'user')},
            },
        ),
    ]
