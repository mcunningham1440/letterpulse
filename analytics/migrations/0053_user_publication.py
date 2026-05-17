"""
Replace UsageAccount.available_publications (JSON cache of Beehiiv pubs the
user has access to) and UsageAccount.initial_fetched_pub_ids (JSON set of
pub_ids the user finished the initial Learning fetch for) with a proper
UserPublication join model.

Step 1 of 2: add the UserPublication table and backfill rows from both legacy
JSON fields. The legacy fields themselves are dropped in 0054.
"""

from django.conf import settings
from django.db import migrations, models


def backfill_user_publications(apps, schema_editor):
    UsageAccount = apps.get_model("analytics", "UsageAccount")
    Publication = apps.get_model("analytics", "Publication")
    UserPublication = apps.get_model("analytics", "UserPublication")

    for usage in UsageAccount.objects.select_related("user").all():
        # available_publications -> ensure (Publication, UserPublication) rows
        avail = usage.available_publications or []
        pub_id_to_pub = {}
        for entry in avail:
            pid = (entry or {}).get("id")
            if not pid:
                continue
            pub, _ = Publication.objects.update_or_create(
                pub_id=pid,
                defaults={
                    "name": entry.get("name", "Unknown") or "Unknown",
                    "organization_name": entry.get("organization_name", "") or "",
                },
            )
            pub_id_to_pub[pid] = pub
            UserPublication.objects.get_or_create(
                user=usage.user,
                publication=pub,
            )

        # initial_fetched_pub_ids -> stamp initial_fetch_done_at. The original
        # boolean has no timestamp, so this falls back to usage.updated_at —
        # flagged: pre-migration completions show the row's last-touched time,
        # not the true initial-fetch completion time. Timestamps are diagnostic
        # only (no business logic reads them as a date), so the inaccuracy is
        # acceptable. If a legacy pub_id isn't in available_publications (rare —
        # stale or missing cache), we try the Publication table directly.
        done_pids = usage.initial_fetched_pub_ids or []
        for pid in done_pids:
            if not pid:
                continue
            pub = pub_id_to_pub.get(pid)
            if pub is None:
                pub = Publication.objects.filter(pub_id=pid).first()
                if pub is None:
                    # No Publication row and not in cache — skip; the user
                    # can't access this pub through the app anymore anyway.
                    continue
            up, _ = UserPublication.objects.get_or_create(
                user=usage.user,
                publication=pub,
            )
            if up.initial_fetch_done_at is None:
                up.initial_fetch_done_at = usage.updated_at
                up.save(update_fields=["initial_fetch_done_at"])


def reverse_noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("analytics", "0052_add_user_publication_indexes"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="UserPublication",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("initial_fetch_done_at", models.DateTimeField(
                    blank=True, null=True,
                    help_text="When the user's initial Learning fetch completed for this pub; null if not yet completed.",
                )),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("publication", models.ForeignKey(
                    on_delete=models.deletion.CASCADE,
                    related_name="user_publications",
                    to="analytics.publication",
                )),
                ("user", models.ForeignKey(
                    on_delete=models.deletion.CASCADE,
                    related_name="user_publications",
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                "verbose_name": "User Publication",
                "verbose_name_plural": "User Publications",
                "ordering": ["publication__name"],
                "unique_together": {("user", "publication")},
            },
        ),
        migrations.AddIndex(
            model_name="userpublication",
            index=models.Index(fields=["user", "publication"], name="analytics_u_user_id_idx"),
        ),
        migrations.RunPython(backfill_user_publications, reverse_noop),
    ]
