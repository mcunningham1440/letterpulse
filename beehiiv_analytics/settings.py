"""
Django settings for beehiiv_analytics project.
"""

import json
import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent


# Quick-start development settings - unsuitable for production

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = os.environ["SECRET_KEY"]

# Key used by EncryptedCharField (analytics/fields.py) to derive the Fernet
# key for per-user Beehiiv tokens. Falls back to SECRET_KEY when unset so
# existing deployments keep decrypting tokens that were originally encrypted
# under SECRET_KEY. To rotate SECRET_KEY without invalidating stored tokens,
# set this env var to the *current* SECRET_KEY value before rotating.
BEEHIIV_TOKEN_ENCRYPTION_KEY = os.environ.get('BEEHIIV_TOKEN_ENCRYPTION_KEY', SECRET_KEY)

ENVIRONMENT = os.environ.get('ENVIRONMENT', 'local')
DEBUG = ENVIRONMENT == 'local'


def _csv_env(key):
    """Parse a comma-separated env var into a list of stripped, non-empty entries."""
    return [s.strip() for s in os.environ.get(key, '').split(',') if s.strip()]


# Loopback hosts are always permitted (health checks, local dev). Public
# hostnames per deployment target are supplied via the ALLOWED_HOSTS env var
# as a comma-separated list (e.g. "letterpulse.com,xyz.awsapprunner.com").
ALLOWED_HOSTS = _csv_env('ALLOWED_HOSTS') + ['127.0.0.1', 'localhost']

# CSRF_TRUSTED_ORIGINS env var is comma-separated full origins (with scheme),
# e.g. "https://letterpulse.com,https://xyz.awsapprunner.com".
CSRF_TRUSTED_ORIGINS = _csv_env('CSRF_TRUSTED_ORIGINS')


# Application definition

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.sites',  # Required by allauth
    'django.contrib.humanize',  # For number formatting (intcomma)
    'analytics',  # Must come before allauth to override its templates
    'allauth',
    'allauth.account',
]

SITE_ID = 1

# Authentication backends
AUTHENTICATION_BACKENDS = [
    'django.contrib.auth.backends.ModelBackend',
    'allauth.account.auth_backends.AuthenticationBackend',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'analytics.logutils.ExecutionLoggingMiddleware',  # Execution logging (after auth)
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'allauth.account.middleware.AccountMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
]

ROOT_URLCONF = 'beehiiv_analytics.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'analytics.context_processors.usage_context',
                'analytics.context_processors.environment_context',
                'analytics.context_processors.limited_data_context',
            ],
        },
    },
]

WSGI_APPLICATION = 'beehiiv_analytics.wsgi.application'


# Database

db_secret = json.loads(os.environ["DATABASE_SECRET"])
db_host = os.environ["DB_HOST"]
_db_options = {}
if 'rds.amazonaws.com' in (db_host or ''):
    _db_options['sslmode'] = 'require'
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.environ.get('DB_NAME', 'letterpulse'),
        'USER': db_secret['username'],
        'PASSWORD': db_secret['password'],
        'HOST': db_host,
        'PORT': os.environ.get('DB_PORT', '5432'),
        'OPTIONS': _db_options,
    }
}


# Password validation

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]


# Internationalization

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'America/Chicago'
USE_I18N = True
USE_TZ = True


# Static files (CSS, JavaScript, Images)

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}


# Logging

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {
        "django.request": {"handlers": ["console"], "level": "ERROR", "propagate": False},
    },
}


if ENVIRONMENT != 'local':
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_SSL_REDIRECT = True
    SECURE_HSTS_SECONDS = 60 * 60 * 24 * 365
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = False
    SECURE_REFERRER_POLICY = 'same-origin'
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SESSION_COOKIE_HTTPONLY = True


# Default primary key field type

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# django-allauth settings
LOGIN_REDIRECT_URL = 'analytics:insights'
LOGOUT_REDIRECT_URL = 'account_login'
ACCOUNT_LOGIN_METHODS = {'email'}
ACCOUNT_SIGNUP_FIELDS = ['email*', 'password1*', 'password2*']
ACCOUNT_EMAIL_VERIFICATION = 'none'
ACCOUNT_SESSION_REMEMBER = True
ACCOUNT_UNIQUE_EMAIL = True
ACCOUNT_ADAPTER = 'analytics.adapters.CustomAccountAdapter'
ACCOUNT_FORMS = {'signup': 'analytics.forms.CustomSignupForm'}
ACCOUNT_RATE_LIMITS = {
    'login_failed': '5/5m',
    'signup': '5/h',
    'reset_password': '5/h',
    'reset_password_email': '5/h',
    'change_password': '5/h',
    'manage_email': '10/h',
    'confirm_email': '10/h',
}

# Email settings
EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST = 'smtp.gmail.com'
EMAIL_PORT = 587
EMAIL_USE_TLS = True
EMAIL_HOST_USER = os.environ.get('EMAIL_HOST_USER', '')
EMAIL_HOST_PASSWORD = os.environ.get('EMAIL_HOST_PASSWORD', '')
DEFAULT_FROM_EMAIL = os.environ.get('EMAIL_HOST_USER', 'noreply@letterpulse.app')

# =============================================================================
# Signup Configuration
# =============================================================================
# Email address to receive notifications when a new user signs up.
SIGNUP_NOTIFICATION_EMAIL = os.environ.get('SIGNUP_NOTIFICATION_EMAIL', '')

# Maximum new user signups allowed per rolling 24-hour window. Enforced in
# analytics/adapters.py:CustomAccountAdapter.is_open_for_signup. Set to None
# to disable the cap.
DAILY_SIGNUP_CAP = 5

# =============================================================================
# Messages Framework Configuration
# =============================================================================
# Map Django message levels to Bootstrap alert classes
from django.contrib.messages import constants as messages
MESSAGE_TAGS = {
    messages.DEBUG: 'secondary',
    messages.INFO: 'info',
    messages.SUCCESS: 'success',
    messages.WARNING: 'warning',
    messages.ERROR: 'danger',
}

# =============================================================================
# AI Credit System Configuration
# =============================================================================
# Default monthly credits for new users
DEFAULT_MONTHLY_CREDITS = 75

# Credit costs per operation
CREDITS_PER_IMPROVEMENT_TIPS = 1  # Per post improvement tips generation

# Silent cap on post processing per billing period (no credit charge).
MAX_POSTS_PROCESSED_PER_PERIOD = 45

# Link processing configuration
LINK_PROCESS_TOP_N = 60             # Total links to select across all sections
LINK_PROCESS_MAX_RETRIES = 2        # Max LLM retries for link description count mismatch

# =============================================================================
# Execution Logging Configuration
# =============================================================================
# Queue-based async logging for requests and function calls
EXECUTION_LOG_QUEUE_MAXSIZE = 2000   # Max entries in queue before overflow
EXECUTION_LOG_BATCH_SIZE = 50        # Entries per bulk_create
EXECUTION_LOG_FLUSH_INTERVAL = 1.0   # Seconds between flushes
EXECUTION_LOG_ON_FULL = 'drop'       # 'drop' or 'sync' when queue is full

# =============================================================================
# Content Finder Configuration
# =============================================================================
PERPLEXITY_API_KEY = os.environ.get("PERPLEXITY_API_KEY", "")
CREDITS_PER_CONTENT_SEARCH = 1
CONTENT_FINDER_PLAN_MODEL = "gpt-5.4"
CONTENT_FINDER_PLAN_REASONING = "medium"
CONTENT_FINDER_DISPATCH_MAX_SECTIONS = 6
CONTENT_FINDER_MODEL = "gpt-5.4-mini"
CONTENT_FINDER_REASONING = "medium"
CONTENT_FINDER_MAX_ROUNDS = 3       # Max search round-trips per section before forcing final answer
CONTENT_FINDER_MAX_LINKS = 60       # Max historical links per section for context
CONTENT_FINDER_MAX_URL_LEN = 75     # Truncate displayed URLs to this length

# =============================================================================
# Improvement Tips Configuration
# =============================================================================
IMPROVEMENT_TIPS_MODEL = "gpt-5.4-mini"
IMPROVEMENT_TIPS_REASONING = "medium"

# =============================================================================
# Niche Analysis Configuration (Monetize tab first-visit setup)
# =============================================================================
NICHE_ANALYSIS_MODEL = "gpt-5.4"
NICHE_ANALYSIS_REASONING = "medium"
NICHE_ANALYSIS_RECENT_POSTS = 3       # Number of recent processed posts to feed in as text
NICHE_ANALYSIS_LINK_HISTORY_ISSUES = 10  # Window of issues for the per-section link history
NICHE_ANALYSIS_TOP_LINKS_PER_SECTION = 5  # Top-CTR links per section to include in the prompt

# =============================================================================
# LLM Pricing (per million tokens) — local dev panel only
# =============================================================================
LLM_PRICING = {
    'gpt-5.4': {
        'input_per_million': 2.50,
        'cached_input_per_million': 0.25,
        'output_per_million': 15.00,
    },
    'gpt-5.4-mini': {
        'input_per_million': 0.75,
        'cached_input_per_million': 0.075,
        'output_per_million': 4.50,
    },
    'claude-sonnet-4-6': {
        'input_per_million': 3.00,
        'cached_input_per_million': 0.30,
        'cache_write_per_million': 3.75,
        'output_per_million': 15.00,
    },
    'claude-haiku-4-5': {
        'input_per_million': 1.00,
        'cached_input_per_million': 0.10,
        'cache_write_per_million': 1.25,
        'output_per_million': 5.00,
    },
}

# A model NOT in this dict gets no fallback (raises on the
# first retryable failure). Keep the mapping explicit so a typo in a model
# name doesn't silently disable the safety net.
LLM_FALLBACK_MAP = {
    "gpt-5.4": "claude-sonnet-4-6",
    "claude-sonnet-4-6": "gpt-5.4",
    "gpt-5.4-mini": "claude-haiku-4-5",
    "claude-haiku-4-5": "gpt-5.4-mini",
}
