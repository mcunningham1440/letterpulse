import pandas as pd
from django.db import transaction
from django.db.models import Q
from django.utils import timezone as dj_timezone

from analytics.models import (
    LinkData,
    Post,
    ProcessedPost,
    Publication,
    Section,
    UsageAccount,
)


PROCESSABLE_PLATFORMS = ('email', 'both')
PROCESSABLE_PUBLISHED_AGE_SECONDS = 48 * 3600
INITIAL_LEARNING_RECIPIENT_MULTIPLIER = 15


def _processable_posts_queryset(user, publication):
    """
    Posts eligible for processing in either Learning/Update flow:
      status = Published, publish_date >= 48h ago, platform in {email, both}
      OR platform is NULL (legacy rows fetched before the platform field
      existed — treat as eligible; they'll be backfilled on next fetch).
    """
    cutoff = dj_timezone.now() - pd.Timedelta(seconds=PROCESSABLE_PUBLISHED_AGE_SECONDS)
    return (
        Post.objects
        .filter(
            Q(platform__in=PROCESSABLE_PLATFORMS) | Q(platform__isnull=True),
            user=user,
            publication=publication,
            status='Published',
            publish_date__isnull=False,
            publish_date__lte=cutoff,
        )
    )


def select_posts_for_initial_learning(user, publication, subscriber_count):
    """
    Pick the most recent eligible published posts whose cumulative recipients
    reach subscriber_count * INITIAL_LEARNING_RECIPIENT_MULTIPLIER.

    Walks newest-first by publish_date. Returns list[post_id] (Beehiiv IDs).
    If subscriber_count is 0 or no posts accumulate enough recipients, returns
    all eligible posts.
    """
    target = max(0, int(subscriber_count) * INITIAL_LEARNING_RECIPIENT_MULTIPLIER)

    posts = list(
        _processable_posts_queryset(user, publication)
        .order_by('-publish_date')
        .values('post_id', 'recipients')
    )

    if not posts:
        return []

    if target <= 0:
        return [p['post_id'] for p in posts]

    selected = []
    total = 0
    for p in posts:
        selected.append(p['post_id'])
        total += int(p.get('recipients') or 0)
        if total >= target:
            break
    return selected


def select_posts_for_update(user, publication):
    """
    Posts to process during the Updating Your Posts flow: eligible posts that
    haven't been processed yet AND were published more recently than the oldest
    already-processed post (so we don't retroactively backfill older history).
    """
    eligible = _processable_posts_queryset(user, publication)

    processed_post_pks = ProcessedPost.objects.filter(
        user=user, post__publication=publication,
    ).values_list('post__pk', flat=True)

    eligible_new = eligible.exclude(pk__in=list(processed_post_pks))

    oldest_processed_publish = (
        ProcessedPost.objects
        .filter(user=user, post__publication=publication, post__publish_date__isnull=False)
        .order_by('post__publish_date')
        .values_list('post__publish_date', flat=True)
        .first()
    )

    if oldest_processed_publish is not None:
        eligible_new = eligible_new.filter(publish_date__gt=oldest_processed_publish)

    return list(eligible_new.order_by('-publish_date').values_list('post_id', flat=True))


def wipe_user_publication_data(user, pub_id):
    """
    Atomic cleanup for interrupted "Learning Your Audience" flows: delete every
    Post / ProcessedPost / Section / LinkData row for (user, publication), and
    remove pub_id from UsageAccount.initial_fetched_pub_ids so the user sees the
    onboarding coach again on their next page load.
    """
    with transaction.atomic():
        try:
            publication = Publication.objects.get(pub_id=pub_id)
        except Publication.DoesNotExist:
            publication = None

        base_filter = {'user': user}
        if publication is not None:
            base_filter['publication'] = publication

        LinkData.objects.filter(**base_filter).delete()
        Section.objects.filter(**base_filter).delete()
        if publication is not None:
            ProcessedPost.objects.filter(user=user, post__publication=publication).delete()
            Post.objects.filter(user=user, publication=publication).delete()
        else:
            ProcessedPost.objects.filter(user=user).delete()
            Post.objects.filter(user=user).delete()

        try:
            usage = UsageAccount.objects.get(user=user)
        except UsageAccount.DoesNotExist:
            usage = None
        if usage and pub_id and pub_id in (usage.initial_fetched_pub_ids or []):
            usage.initial_fetched_pub_ids = [
                p for p in (usage.initial_fetched_pub_ids or []) if p != pub_id
            ]
            usage.save(update_fields=['initial_fetched_pub_ids'])
