"""
Settings for running unit tests.

Loaded via `python manage.py test --settings=beehiiv_analytics.test_settings`.
Uses an in-memory SQLite database so tests never touch RDS, and stubs out the
env vars required by production settings so tests can run in any environment.
"""

import os

# Provide harmless defaults for env vars that production settings.py reads
# unconditionally at import time. setdefault() preserves real values if a
# developer chooses to set them, but avoids requiring them.
os.environ.setdefault("SECRET_KEY", "test-secret-key-not-for-production")
os.environ.setdefault("OPENAI_API_KEY", "test-openai-key")
os.environ.setdefault("DATABASE_SECRET", '{"username": "test", "password": "test"}')
os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("ENVIRONMENT", "local")

from .settings import *  # noqa: E402,F401,F403

# Override DB to an in-memory SQLite so tests are hermetic and fast.
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

# Don't try to send real email during tests.
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

# Blank out the signup-notification email so the post_save signal early-returns
# instead of spawning a thread that calls send_mail. The welcome-email thread
# is suppressed via a mock.patch in the test base class.
SIGNUP_NOTIFICATION_EMAIL = ""

# Speed up password hashing in tests (the default hasher is intentionally slow).
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

# Skip running real migrations — Django will create the schema directly from
# models. This sidesteps a broken data migration (0022_update_site_domain
# references the sites app without declaring a dependency on it) and keeps
# unit-test setup fast. Real migrations are still exercised in deployed envs.
class _DisableMigrations:
    def __contains__(self, item):
        return True

    def __getitem__(self, item):
        return None

MIGRATION_MODULES = _DisableMigrations()
