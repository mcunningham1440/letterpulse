"""
Unit tests for analytics.utils.post_selection.

Covers:
- select_posts_for_initial_learning: cumulative recipients cap, ordering,
  platform filter, 48h age cutoff, subscriber_count=0 fallback.
- select_posts_for_update: excludes already-processed posts and won't go
  earlier than the oldest already-processed publish_date.
- wipe_user_publication_data: atomic delete of Post/ProcessedPost/Section/
  LinkData; resets UserPublication.initial_fetch_done_at.
"""
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from analytics.models import (
    LinkData,
    Post,
    ProcessedPost,
    Section,
    UserPublication,
)
from analytics.tests.factories import (
    make_link_data,
    make_post,
    make_processed_post,
    make_publication,
    make_section,
    make_user,
    make_user_publication,
)
from analytics.utils.post_selection import (
    INITIAL_LEARNING_RECIPIENT_MULTIPLIER,
    select_posts_for_initial_learning,
    select_posts_for_update,
    wipe_user_publication_data,
)


def _hours_ago(h):
    return timezone.now() - timedelta(hours=h)


class SelectPostsForInitialLearningTests(TestCase):

    def _setup(self):
        user, _ = make_user()
        pub = make_publication()
        return user, pub

    def test_returns_newest_first_until_recipient_target_hit(self):
        # subscribers=100 -> target = 100 * 15 = 1500.
        # 3 posts of 1000 recipients each, newest -> oldest. The first two
        # accumulate to 2000 which hits the target; the third should not be
        # selected.
        user, pub = self._setup()
        p_new = make_post(user, pub, post_id="p_new",
                          publish_date=_hours_ago(72), recipients=1000)
        p_mid = make_post(user, pub, post_id="p_mid",
                          publish_date=_hours_ago(96), recipients=1000)
        make_post(user, pub, post_id="p_old",
                  publish_date=_hours_ago(120), recipients=1000)
        result = select_posts_for_initial_learning(user, pub, subscriber_count=100)
        # First two newest were enough to clear the target.
        self.assertEqual(result, [p_new.post_id, p_mid.post_id])

    def test_returns_all_eligible_when_target_unreachable(self):
        user, pub = self._setup()
        p1 = make_post(user, pub, post_id="p1",
                       publish_date=_hours_ago(72), recipients=10)
        p2 = make_post(user, pub, post_id="p2",
                       publish_date=_hours_ago(96), recipients=10)
        result = select_posts_for_initial_learning(user, pub, subscriber_count=1000)
        # Target = 1000 * 15 = 15000; total recipients = 20. Loop exhausts.
        self.assertCountEqual(result, [p1.post_id, p2.post_id])

    def test_subscriber_count_zero_returns_all_eligible(self):
        # When subscriber count is unknown (0), code path skips the cumulative
        # check and returns every eligible post.
        user, pub = self._setup()
        p1 = make_post(user, pub, post_id="p1", publish_date=_hours_ago(72))
        p2 = make_post(user, pub, post_id="p2", publish_date=_hours_ago(96))
        result = select_posts_for_initial_learning(user, pub, subscriber_count=0)
        self.assertCountEqual(result, [p1.post_id, p2.post_id])

    def test_excludes_posts_younger_than_48h(self):
        user, pub = self._setup()
        # 24h old -> too young; 72h old -> eligible.
        make_post(user, pub, post_id="young", publish_date=_hours_ago(24))
        ok = make_post(user, pub, post_id="ok", publish_date=_hours_ago(72))
        result = select_posts_for_initial_learning(user, pub, subscriber_count=0)
        self.assertEqual(result, [ok.post_id])

    def test_excludes_web_only_posts(self):
        user, pub = self._setup()
        make_post(user, pub, post_id="web_only",
                  platform="web", publish_date=_hours_ago(72))
        ok_email = make_post(user, pub, post_id="ok_email",
                             platform="email", publish_date=_hours_ago(72))
        ok_both = make_post(user, pub, post_id="ok_both",
                            platform="both", publish_date=_hours_ago(96))
        result = select_posts_for_initial_learning(user, pub, subscriber_count=0)
        self.assertCountEqual(result, [ok_email.post_id, ok_both.post_id])

    def test_includes_platform_null_posts(self):
        # Legacy rows without platform info treated as eligible.
        user, pub = self._setup()
        legacy = make_post(user, pub, post_id="legacy",
                           platform=None, publish_date=_hours_ago(72))
        result = select_posts_for_initial_learning(user, pub, subscriber_count=0)
        self.assertEqual(result, [legacy.post_id])

    def test_excludes_non_published_status(self):
        user, pub = self._setup()
        make_post(user, pub, post_id="draft", status="Draft",
                  publish_date=_hours_ago(72))
        published = make_post(user, pub, post_id="pub",
                              publish_date=_hours_ago(72))
        result = select_posts_for_initial_learning(user, pub, subscriber_count=0)
        self.assertEqual(result, [published.post_id])

    def test_recipient_multiplier_constant_in_play(self):
        # Sanity-check the multiplier — if someone tweaks the constant in
        # production this test catches it.
        self.assertEqual(INITIAL_LEARNING_RECIPIENT_MULTIPLIER, 15)

    def test_returns_empty_when_no_posts(self):
        user, pub = self._setup()
        self.assertEqual(
            select_posts_for_initial_learning(user, pub, subscriber_count=100),
            [],
        )


class SelectPostsForUpdateTests(TestCase):

    def test_excludes_already_processed_posts(self):
        user, _ = make_user()
        pub = make_publication()
        p_done = make_post(user, pub, post_id="done", publish_date=_hours_ago(72))
        make_processed_post(p_done)
        p_new = make_post(user, pub, post_id="new", publish_date=_hours_ago(80))
        # New post is older than the already-processed one — but the
        # oldest_processed_publish gate is based on the *oldest* processed
        # publish date, which here equals p_done's date. p_new is older so
        # it gets filtered out.
        result = select_posts_for_update(user, pub)
        self.assertEqual(result, [])

    def test_only_includes_posts_newer_than_oldest_processed(self):
        user, _ = make_user()
        pub = make_publication()
        # p_done processed at 96h ago. Any new post older than 96h must be
        # skipped (we don't backfill history).
        p_done = make_post(user, pub, post_id="done", publish_date=_hours_ago(96))
        make_processed_post(p_done)
        p_newer = make_post(user, pub, post_id="newer", publish_date=_hours_ago(72))
        p_older = make_post(user, pub, post_id="older", publish_date=_hours_ago(120))
        result = select_posts_for_update(user, pub)
        self.assertEqual(result, [p_newer.post_id])
        self.assertNotIn(p_older.post_id, result)

    def test_no_processed_history_returns_all_eligible_unprocessed(self):
        # When the user has no ProcessedPost rows yet, every eligible post
        # should be returned (this can happen if the user clicks "Refresh"
        # before any initial fetch completed).
        user, _ = make_user()
        pub = make_publication()
        p1 = make_post(user, pub, post_id="p1", publish_date=_hours_ago(72))
        p2 = make_post(user, pub, post_id="p2", publish_date=_hours_ago(96))
        result = select_posts_for_update(user, pub)
        self.assertCountEqual(result, [p1.post_id, p2.post_id])

    def test_does_not_leak_other_users_processed_state(self):
        # User B has processed posts older than User A's targets. User A's
        # results must not be filtered by User B's ProcessedPost history.
        user_a, _ = make_user(email="a@example.com")
        user_b, _ = make_user(email="b@example.com")
        pub = make_publication()
        b_done = make_post(user_b, pub, post_id="b_done", publish_date=_hours_ago(48))
        make_processed_post(b_done)
        a_new = make_post(user_a, pub, post_id="a_new", publish_date=_hours_ago(72))
        result = select_posts_for_update(user_a, pub)
        self.assertEqual(result, [a_new.post_id])


class WipeUserPublicationDataTests(TestCase):

    def test_deletes_post_processed_section_linkdata_for_user_pub(self):
        user, _ = make_user()
        pub = make_publication(pub_id="pub_wipe")
        up = make_user_publication(user, pub, initial_fetch_done_at=timezone.now())
        post = make_post(user, pub, post_id="x", publish_date=_hours_ago(72))
        make_processed_post(post)
        make_section(post, section_name="Main")
        make_link_data(post, raw_url="https://example.com/wipe", section_name="Main")

        wipe_user_publication_data(user, "pub_wipe")

        self.assertFalse(Post.objects.filter(user=user, publication=pub).exists())
        self.assertFalse(ProcessedPost.objects.filter(user=user).exists())
        self.assertFalse(Section.objects.filter(user=user, publication=pub).exists())
        self.assertFalse(LinkData.objects.filter(user=user, publication=pub).exists())

        up.refresh_from_db()
        self.assertIsNone(up.initial_fetch_done_at)

    def test_does_not_touch_other_publications(self):
        user, _ = make_user()
        pub_a = make_publication(pub_id="pub_a")
        pub_b = make_publication(pub_id="pub_b")
        make_user_publication(user, pub_a, initial_fetch_done_at=timezone.now())
        up_b = make_user_publication(user, pub_b, initial_fetch_done_at=timezone.now())
        post_b = make_post(user, pub_b, post_id="b1", publish_date=_hours_ago(72))
        make_processed_post(post_b)

        wipe_user_publication_data(user, "pub_a")

        # Pub B's row + post must still exist; its UP timestamp untouched.
        self.assertTrue(Post.objects.filter(user=user, publication=pub_b).exists())
        up_b.refresh_from_db()
        self.assertIsNotNone(up_b.initial_fetch_done_at)

    def test_unknown_pub_id_deletes_unscoped_user_data(self):
        # If the Publication row is missing (e.g. cleanup raced ahead of the
        # join model), the helper still wipes user-scoped Post/ProcessedPost
        # rows by user only — soft fallback flagged for transparency.
        user, _ = make_user()
        pub = make_publication(pub_id="real_pub")
        post = make_post(user, pub, post_id="x", publish_date=_hours_ago(72))
        make_processed_post(post)

        wipe_user_publication_data(user, "missing_pub_id")

        # Without a matching Publication, post + processed_post are deleted
        # unscoped by publication. This is the intentional fallback in the
        # helper — flagging it here so the assumption is locked in.
        self.assertFalse(Post.objects.filter(user=user).exists())
