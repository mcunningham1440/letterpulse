# Generated manually for adding user scoping to Post and ContentSet

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def assign_existing_data_to_user(apps, schema_editor):
    """
    Assign all existing Posts and ContentSets to user 'michaelcunningham'.
    """
    User = apps.get_model(settings.AUTH_USER_MODEL.split('.')[0], settings.AUTH_USER_MODEL.split('.')[1])
    Post = apps.get_model('analytics', 'Post')
    ContentSet = apps.get_model('analytics', 'ContentSet')

    # Get or create the michaelcunningham user
    try:
        user = User.objects.get(email='michaelcunningham@me.com')
    except User.DoesNotExist:
        # Try by username as fallback
        try:
            user = User.objects.get(username='michaelcunningham')
        except User.DoesNotExist:
            # If user doesn't exist, get the first user
            user = User.objects.first()
            if user is None:
                # No users exist, nothing to migrate
                return

    # Assign all posts to this user
    Post.objects.filter(user__isnull=True).update(user=user)

    # Assign all content sets to this user
    ContentSet.objects.filter(user__isnull=True).update(user=user)


def reverse_assignment(apps, schema_editor):
    """Reverse: set user to null for all posts and content sets."""
    Post = apps.get_model('analytics', 'Post')
    ContentSet = apps.get_model('analytics', 'ContentSet')

    Post.objects.all().update(user=None)
    ContentSet.objects.all().update(user=None)


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("analytics", "0009_assign_existing_data_to_publication"),
    ]

    operations = [
        # Step 1: Remove the unique constraint on post_id (it will be unique per user now)
        migrations.AlterField(
            model_name='post',
            name='post_id',
            field=models.CharField(help_text='Beehiiv post ID', max_length=255),
        ),
        # Step 2: Add user field to Post (nullable initially)
        migrations.AddField(
            model_name='post',
            name='user',
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='posts',
                to=settings.AUTH_USER_MODEL,
                help_text='The user who owns this post data',
            ),
        ),
        # Step 3: Add user field to ContentSet (nullable initially)
        migrations.AddField(
            model_name='contentset',
            name='user',
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='content_sets',
                to=settings.AUTH_USER_MODEL,
                help_text='The user who owns this content set',
            ),
        ),
        # Step 4: Run data migration to assign existing records to michaelcunningham
        migrations.RunPython(assign_existing_data_to_user, reverse_assignment),
        # Step 5: Make user non-nullable on Post
        migrations.AlterField(
            model_name='post',
            name='user',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name='posts',
                to=settings.AUTH_USER_MODEL,
                help_text='The user who owns this post data',
            ),
        ),
        # Step 6: Make user non-nullable on ContentSet
        migrations.AlterField(
            model_name='contentset',
            name='user',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name='content_sets',
                to=settings.AUTH_USER_MODEL,
                help_text='The user who owns this content set',
            ),
        ),
        # Step 7: Add unique_together constraint for Post (post_id + user)
        migrations.AlterUniqueTogether(
            name='post',
            unique_together={('post_id', 'user')},
        ),
        # Step 8: Update unique_together for ContentSet to include user
        migrations.AlterUniqueTogether(
            name='contentset',
            unique_together={('name', 'publication', 'user')},
        ),
    ]
