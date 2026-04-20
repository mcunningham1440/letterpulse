from django.db import migrations, models


def backfill_initial_fetched_pub_ids(apps, schema_editor):
    """Mark each (user, pub_id) combo with existing Posts as already initially fetched."""
    UsageAccount = apps.get_model("analytics", "UsageAccount")
    Post = apps.get_model("analytics", "Post")

    for usage in UsageAccount.objects.all():
        pub_ids = list(
            Post.objects.filter(user=usage.user, publication__pub_id__isnull=False)
            .values_list("publication__pub_id", flat=True)
            .distinct()
        )
        pub_ids = [pid for pid in pub_ids if pid]
        if pub_ids:
            usage.initial_fetched_pub_ids = pub_ids
            usage.save(update_fields=["initial_fetched_pub_ids"])


def reverse_noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("analytics", "0039_content_search_feedback"),
    ]

    operations = [
        migrations.AddField(
            model_name="usageaccount",
            name="initial_fetched_pub_ids",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text="Beehiiv pub_ids for which the user has completed the initial full post fetch",
            ),
        ),
        migrations.RunPython(backfill_initial_fetched_pub_ids, reverse_noop),
    ]
