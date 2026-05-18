"""
Tests for the BackgroundTask abstract base: stale-sweep recovery, atomic
credit charge inside claim(), and refund-on-error.

PendingImprovementTips is the simplest concrete subclass to exercise here (no
multi-stage state machine, one credit-charging hook). The behaviors under test
live on the abstract base, so coverage here implicitly covers the other three
concrete subclasses.
"""

from datetime import date, datetime, timedelta, timezone as dt_timezone
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from analytics.models import (
    PendingImprovementTips,
    PendingLearningTask,
    PendingNicheAnalysis,
    Post,
    Publication,
)
from analytics.utils import recover_stuck_tasks
from analytics.utils.credits import NotEnoughCredits


User = get_user_model()


# Welcome-email suppression now lives in analytics/tests/conftest.py as a
# session-autouse fixture.


def _make_user(email="alice@example.com", monthly_quota=10,
               used_this_period=0):
    user = User.objects.create_user(
        username=email.split("@")[0], email=email, password="x",
        date_joined=datetime(2026, 3, 15, tzinfo=dt_timezone.utc),
    )
    usage = user.usage_account
    usage.monthly_quota = monthly_quota
    usage.used_this_period = used_this_period
    usage.period_start = date(2026, 3, 15)
    usage.save()
    return user, usage


def _make_post(user, publication=None):
    if publication is None:
        publication = Publication.objects.create(
            pub_id="pub-test", name="Test Pub",
        )
    return Post.objects.create(
        post_id="post-1", user=user, publication=publication,
        title="Test post",
    )


def _patch_today(d: date):
    fake_now = datetime(d.year, d.month, d.day, 12, 0, tzinfo=dt_timezone.utc)
    return mock.patch("analytics.models.timezone.now", return_value=fake_now)


def _age_heartbeat(task, seconds):
    type(task).objects.filter(pk=task.pk).update(
        last_heartbeat=timezone.now() - timedelta(seconds=seconds),
    )


@override_settings(CREDITS_PER_IMPROVEMENT_TIPS=3)
class ClaimChargesCreditsAtomicallyTests(TestCase):
    """claim() pairs the status transition with the credit charge in one tx."""

    def test_claim_charges_credits_and_flips_status(self):
        user, usage = _make_user(monthly_quota=10, used_this_period=2)
        with _patch_today(date(2026, 3, 20)):
            post = _make_post(user)
            task = PendingImprovementTips.objects.create(
                user=user, publication=post.publication, post=post,
            )
            self.assertEqual(task.status, 'pending')
            self.assertEqual(task.credits_charged, 0)

            claimed = task.claim()
            self.assertTrue(claimed)

        task.refresh_from_db()
        usage.refresh_from_db()
        self.assertEqual(task.status, 'running')
        self.assertEqual(task.credits_charged, 3)
        self.assertEqual(usage.used_this_period, 5)

    def test_claim_refuses_over_quota_and_leaves_task_pending(self):
        user, usage = _make_user(monthly_quota=10, used_this_period=8)
        with _patch_today(date(2026, 3, 20)):
            post = _make_post(user)
            task = PendingImprovementTips.objects.create(
                user=user, publication=post.publication, post=post,
            )
            with self.assertRaises(NotEnoughCredits):
                task.claim()

        task.refresh_from_db()
        usage.refresh_from_db()
        self.assertEqual(task.status, 'pending')
        self.assertEqual(task.credits_charged, 0)
        self.assertEqual(usage.used_this_period, 8)

    def test_second_claim_is_a_noop_and_does_not_double_charge(self):
        user, usage = _make_user(monthly_quota=10, used_this_period=0)
        with _patch_today(date(2026, 3, 20)):
            post = _make_post(user)
            task = PendingImprovementTips.objects.create(
                user=user, publication=post.publication, post=post,
            )
            task.claim()
            second = task.claim()
            self.assertFalse(second)

        usage.refresh_from_db()
        self.assertEqual(usage.used_this_period, 3)

    def test_free_task_charges_nothing(self):
        # PendingLearningTask is free (no get_credits_cost override).
        user, usage = _make_user(monthly_quota=10, used_this_period=5)
        pub = Publication.objects.create(pub_id="pub-x", name="X")
        with _patch_today(date(2026, 3, 20)):
            task = PendingLearningTask.objects.create(
                user=user, publication=pub, kind='update',
            )
            task.claim()

        task.refresh_from_db()
        usage.refresh_from_db()
        self.assertEqual(task.status, 'running')
        self.assertEqual(task.credits_charged, 0)
        self.assertEqual(usage.used_this_period, 5)


@override_settings(CREDITS_PER_IMPROVEMENT_TIPS=3)
class MarkErrorRefundsCreditsTests(TestCase):
    """mark_error() returns the credits charged by claim()."""

    def test_mark_error_refunds_full_charge(self):
        user, usage = _make_user(monthly_quota=10, used_this_period=0)
        with _patch_today(date(2026, 3, 20)):
            post = _make_post(user)
            task = PendingImprovementTips.objects.create(
                user=user, publication=post.publication, post=post,
            )
            task.claim()
            usage.refresh_from_db()
            self.assertEqual(usage.used_this_period, 3)

            task.mark_error("boom")

        task.refresh_from_db()
        usage.refresh_from_db()
        self.assertEqual(task.status, 'error')
        self.assertEqual(task.error_message, "boom")
        self.assertEqual(task.credits_charged, 0)
        self.assertEqual(usage.used_this_period, 0)

    def test_mark_error_idempotent_does_not_double_refund(self):
        user, usage = _make_user(monthly_quota=10, used_this_period=0)
        with _patch_today(date(2026, 3, 20)):
            post = _make_post(user)
            task = PendingImprovementTips.objects.create(
                user=user, publication=post.publication, post=post,
            )
            task.claim()
            task.mark_error("once")
            task.mark_error("twice")

        usage.refresh_from_db()
        self.assertEqual(usage.used_this_period, 0)

    def test_mark_error_with_refund_false_keeps_charge(self):
        user, usage = _make_user(monthly_quota=10, used_this_period=0)
        with _patch_today(date(2026, 3, 20)):
            post = _make_post(user)
            task = PendingImprovementTips.objects.create(
                user=user, publication=post.publication, post=post,
            )
            task.claim()
            task.mark_error("boom", refund=False)

        usage.refresh_from_db()
        self.assertEqual(usage.used_this_period, 3)

    def test_mark_error_after_complete_is_noop(self):
        user, usage = _make_user(monthly_quota=10, used_this_period=0)
        with _patch_today(date(2026, 3, 20)):
            post = _make_post(user)
            task = PendingImprovementTips.objects.create(
                user=user, publication=post.publication, post=post,
            )
            task.claim()
            task.mark_complete(result_html="<html>")
            task.mark_error("late error")

        task.refresh_from_db()
        usage.refresh_from_db()
        self.assertEqual(task.status, 'complete')
        # Credits stay used because the task completed successfully.
        self.assertEqual(usage.used_this_period, 3)


@override_settings(CREDITS_PER_IMPROVEMENT_TIPS=3)
class SweepStaleTests(TestCase):
    """sweep_stale() marks heartbeat-stale rows errored and refunds."""

    def test_stale_running_row_is_swept_and_refunded(self):
        user, usage = _make_user(monthly_quota=10, used_this_period=0)
        post = _make_post(user)
        task = PendingImprovementTips.objects.create(
            user=user, publication=post.publication, post=post,
        )
        task.claim()
        _age_heartbeat(task, PendingImprovementTips.STALE_SECONDS + 30)

        swept = PendingImprovementTips.sweep_stale()
        self.assertEqual(swept, 1)

        task.refresh_from_db()
        usage.refresh_from_db()
        self.assertEqual(task.status, 'error')
        self.assertEqual(usage.used_this_period, 0)

    def test_fresh_heartbeat_is_left_alone(self):
        user, _usage = _make_user(monthly_quota=10, used_this_period=0)
        post = _make_post(user)
        task = PendingImprovementTips.objects.create(
            user=user, publication=post.publication, post=post,
        )
        task.claim()
        # Heartbeat is now — should not be swept.
        swept = PendingImprovementTips.sweep_stale()
        self.assertEqual(swept, 0)

        task.refresh_from_db()
        self.assertEqual(task.status, 'running')

    def test_completed_task_is_not_swept(self):
        user, _usage = _make_user(monthly_quota=10, used_this_period=0)
        post = _make_post(user)
        task = PendingImprovementTips.objects.create(
            user=user, publication=post.publication, post=post,
        )
        task.claim()
        task.mark_complete(result_html="<html>")
        _age_heartbeat(task, PendingImprovementTips.STALE_SECONDS + 30)

        swept = PendingImprovementTips.sweep_stale()
        self.assertEqual(swept, 0)
        task.refresh_from_db()
        self.assertEqual(task.status, 'complete')

    def test_recover_stuck_tasks_sweeps_every_subclass(self):
        user, _usage = _make_user(monthly_quota=10, used_this_period=0)
        pub = Publication.objects.create(pub_id="pub-rec", name="Rec")
        post = _make_post(user, publication=pub)

        t1 = PendingImprovementTips.objects.create(
            user=user, publication=pub, post=post,
        )
        t1.claim()
        _age_heartbeat(t1, PendingImprovementTips.STALE_SECONDS + 60)

        t2 = PendingNicheAnalysis.objects.create(user=user, publication=pub)
        t2.claim()
        _age_heartbeat(t2, PendingNicheAnalysis.STALE_SECONDS + 60)

        counts = recover_stuck_tasks()
        self.assertEqual(counts['PendingImprovementTips'], 1)
        self.assertEqual(counts['PendingNicheAnalysis'], 1)


class AwaitingFeedbackIsNotSweptTests(TestCase):
    """Content finder's awaiting_feedback state is intentionally idle."""

    def test_awaiting_feedback_stale_row_is_not_swept(self):
        from analytics.models import PendingContentSearch

        user, _ = _make_user(monthly_quota=10, used_this_period=0)
        post = _make_post(user)
        task = PendingContentSearch.objects.create(
            user=user, publication=post.publication, post=post,
        )
        # Simulate stage 1 completed and the task parked waiting for the
        # user's Confirm click — heartbeat is intentionally old.
        task.status = 'awaiting_feedback'
        task.last_heartbeat = timezone.now() - timedelta(hours=1)
        task.save()

        swept = PendingContentSearch.sweep_stale()
        self.assertEqual(swept, 0)
        task.refresh_from_db()
        self.assertEqual(task.status, 'awaiting_feedback')
