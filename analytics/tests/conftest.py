"""Pytest configuration for the analytics test suite."""
from unittest import mock

import pytest


@pytest.fixture(scope="session", autouse=True)
def _suppress_welcome_email():
    # analytics/signals.py spawns a daemon thread that calls
    # user.refresh_from_db(); under in-memory SQLite that race can corrupt the
    # test connection. Session scope is required — pytest-django doesn't run
    # function-scoped autouse fixtures around unittest.TestCase tests.
    with mock.patch("analytics.signals._send_welcome_email"):
        yield
