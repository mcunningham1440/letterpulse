"""
Tests for the credit system: charge_credits() and UsageAccount.ensure_current_period().

These cover the billing-period rollover math and the atomic charge enforcement
that gate every paid AI feature in the app.
"""

from datetime import date, datetime, timezone as dt_timezone
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase

from analytics.utils import NotEnoughCredits, charge_credits


User = get_user_model()


# Welcome-email suppression now lives in analytics/tests/conftest.py as a
# session-autouse fixture.


def _make_user(email="alice@example.com", date_joined=None, monthly_quota=75,
               used_this_period=0, period_start=None):
    """
    Create a User + UsageAccount with the given billing parameters.

    The post_save signal auto-creates a UsageAccount, so we fetch and update
    it rather than creating a second one.
    """
    user = User.objects.create_user(username=email.split("@")[0], email=email,
                                    password="x")
    if date_joined is not None:
        user.date_joined = date_joined
        user.save(update_fields=["date_joined"])

    usage = user.usage_account
    usage.monthly_quota = monthly_quota
    usage.used_this_period = used_this_period
    if period_start is not None:
        usage.period_start = period_start
    usage.save()
    return user, usage


def _patch_today(d: date):
    """Patch timezone.now() inside analytics.models to return midnight on `d`."""
    fake_now = datetime(d.year, d.month, d.day, 12, 0, tzinfo=dt_timezone.utc)
    return mock.patch("analytics.models.timezone.now", return_value=fake_now)


class EnsureCurrentPeriodTests(TestCase):
    """Cover the billing-period rollover logic."""

    def test_no_rollover_when_inside_current_period(self):
        # Joined Mar 15 2026; today is Mar 20 2026 -> still in the Mar 15 period.
        _, usage = _make_user(
            date_joined=datetime(2026, 3, 15, tzinfo=dt_timezone.utc),
            used_this_period=10,
            period_start=date(2026, 3, 15),
        )
        with _patch_today(date(2026, 3, 20)):
            usage.ensure_current_period()
        self.assertEqual(usage.period_start, date(2026, 3, 15))
        self.assertEqual(usage.used_this_period, 10)  # not reset

    def test_rollover_resets_used_credits(self):
        # Joined Mar 15; today is Apr 15 -> new period starts Apr 15, reset.
        _, usage = _make_user(
            date_joined=datetime(2026, 3, 15, tzinfo=dt_timezone.utc),
            used_this_period=50,
            period_start=date(2026, 3, 15),
        )
        with _patch_today(date(2026, 4, 15)):
            usage.ensure_current_period()
        self.assertEqual(usage.period_start, date(2026, 4, 15))
        self.assertEqual(usage.used_this_period, 0)

    def test_day_before_renewal_stays_in_old_period(self):
        # Today is Apr 14, billing day is 15 -> still in the Mar 15 period.
        _, usage = _make_user(
            date_joined=datetime(2026, 3, 15, tzinfo=dt_timezone.utc),
            used_this_period=50,
            period_start=date(2026, 3, 15),
        )
        with _patch_today(date(2026, 4, 14)):
            usage.ensure_current_period()
        self.assertEqual(usage.period_start, date(2026, 3, 15))
        self.assertEqual(usage.used_this_period, 50)

    def test_signup_on_31st_clamps_to_short_month(self):
        # Joined Jan 31 2026. In Feb 2026 (28 days), billing day clamps to Feb 28.
        _, usage = _make_user(
            date_joined=datetime(2026, 1, 31, tzinfo=dt_timezone.utc),
            period_start=date(2026, 1, 31),
            used_this_period=20,
        )
        with _patch_today(date(2026, 2, 28)):
            usage.ensure_current_period()
        self.assertEqual(usage.period_start, date(2026, 2, 28))
        self.assertEqual(usage.used_this_period, 0)

    def test_signup_on_31st_then_31_day_month_uses_31st(self):
        # Joined Jan 31. By Mar 31 the period should land on Mar 31 (not Feb 28).
        _, usage = _make_user(
            date_joined=datetime(2026, 1, 31, tzinfo=dt_timezone.utc),
            period_start=date(2026, 2, 28),
            used_this_period=10,
        )
        with _patch_today(date(2026, 3, 31)):
            usage.ensure_current_period()
        self.assertEqual(usage.period_start, date(2026, 3, 31))
        self.assertEqual(usage.used_this_period, 0)

    def test_year_rollover(self):
        # Joined Dec 10 2025. Today Jan 5 2026 -> still in Dec 10 2025 period.
        _, usage = _make_user(
            date_joined=datetime(2025, 12, 10, tzinfo=dt_timezone.utc),
            period_start=date(2025, 12, 10),
            used_this_period=5,
        )
        with _patch_today(date(2026, 1, 5)):
            usage.ensure_current_period()
        self.assertEqual(usage.period_start, date(2025, 12, 10))
        self.assertEqual(usage.used_this_period, 5)

    def test_year_rollover_into_new_period(self):
        # Joined Dec 10 2025. Today Jan 10 2026 -> new period starts Jan 10.
        _, usage = _make_user(
            date_joined=datetime(2025, 12, 10, tzinfo=dt_timezone.utc),
            period_start=date(2025, 12, 10),
            used_this_period=42,
        )
        with _patch_today(date(2026, 1, 10)):
            usage.ensure_current_period()
        self.assertEqual(usage.period_start, date(2026, 1, 10))
        self.assertEqual(usage.used_this_period, 0)

    def test_idempotent_within_same_period(self):
        # Calling twice in the same period must not double-reset or change state.
        _, usage = _make_user(
            date_joined=datetime(2026, 3, 15, tzinfo=dt_timezone.utc),
            period_start=date(2026, 3, 15),
            used_this_period=7,
        )
        with _patch_today(date(2026, 3, 25)):
            usage.ensure_current_period()
            usage.ensure_current_period()
        self.assertEqual(usage.period_start, date(2026, 3, 15))
        self.assertEqual(usage.used_this_period, 7)


class ChargeCreditsTests(TestCase):
    """Cover the atomic charge path."""

    def test_charge_within_quota_increments_used(self):
        user, usage = _make_user(monthly_quota=75, used_this_period=10,
                                 period_start=date(2026, 3, 15),
                                 date_joined=datetime(2026, 3, 15, tzinfo=dt_timezone.utc))
        with _patch_today(date(2026, 3, 20)):
            charge_credits(user, 5)
        usage.refresh_from_db()
        self.assertEqual(usage.used_this_period, 15)

    def test_charge_exactly_remaining_succeeds(self):
        user, usage = _make_user(monthly_quota=10, used_this_period=7,
                                 period_start=date(2026, 3, 15),
                                 date_joined=datetime(2026, 3, 15, tzinfo=dt_timezone.utc))
        with _patch_today(date(2026, 3, 20)):
            charge_credits(user, 3)
        usage.refresh_from_db()
        self.assertEqual(usage.used_this_period, 10)

    def test_charge_over_quota_raises_and_does_not_mutate(self):
        user, usage = _make_user(monthly_quota=10, used_this_period=8,
                                 period_start=date(2026, 3, 15),
                                 date_joined=datetime(2026, 3, 15, tzinfo=dt_timezone.utc))
        with _patch_today(date(2026, 3, 20)):
            with self.assertRaises(NotEnoughCredits):
                charge_credits(user, 5)
        usage.refresh_from_db()
        self.assertEqual(usage.used_this_period, 8)

    def test_charge_resets_period_before_quota_check(self):
        # User maxed out last period. After rollover, the charge should succeed
        # because used_this_period gets zeroed first.
        user, usage = _make_user(monthly_quota=10, used_this_period=10,
                                 period_start=date(2026, 3, 15),
                                 date_joined=datetime(2026, 3, 15, tzinfo=dt_timezone.utc))
        with _patch_today(date(2026, 4, 15)):
            charge_credits(user, 4)
        usage.refresh_from_db()
        self.assertEqual(usage.period_start, date(2026, 4, 15))
        self.assertEqual(usage.used_this_period, 4)

    def test_anonymous_user_raises(self):
        from django.contrib.auth.models import AnonymousUser
        with self.assertRaises(NotEnoughCredits):
            charge_credits(AnonymousUser(), 1)

    def test_none_user_raises(self):
        with self.assertRaises(NotEnoughCredits):
            charge_credits(None, 1)

    def test_zero_charge_succeeds_without_mutation(self):
        user, usage = _make_user(monthly_quota=10, used_this_period=5,
                                 period_start=date(2026, 3, 15),
                                 date_joined=datetime(2026, 3, 15, tzinfo=dt_timezone.utc))
        with _patch_today(date(2026, 3, 20)):
            charge_credits(user, 0)
        usage.refresh_from_db()
        self.assertEqual(usage.used_this_period, 5)


class RemainingPropertyTests(TestCase):
    """Quick sanity checks on the derived properties charge_credits leans on."""

    def test_remaining_is_quota_minus_used(self):
        _, usage = _make_user(monthly_quota=75, used_this_period=20)
        self.assertEqual(usage.remaining, 55)

    def test_remaining_floors_at_zero(self):
        # Defensive: even if used somehow exceeds quota, remaining shouldn't go negative.
        _, usage = _make_user(monthly_quota=10, used_this_period=10)
        usage.used_this_period = 15  # bypass the PositiveIntegerField at the Python level
        self.assertEqual(usage.remaining, 0)
