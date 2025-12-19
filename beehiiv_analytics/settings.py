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
SECRET_KEY = 'django-insecure-change-this-in-production'

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = True

APP_RUNNER_URL = "https://qp4y3etffq.us-east-1.awsapprunner.com"
CSRF_TRUSTED_ORIGINS = [
    APP_RUNNER_URL
    ]
ALLOWED_HOSTS = [
    "qp4y3etffq.us-east-1.awsapprunner.com",
    "127.0.0.1"
    ]


# Application definition

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.sites',  # Required by allauth
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
                'analytics.context_processors.progress_context',
            ],
        },
    },
]

WSGI_APPLICATION = 'beehiiv_analytics.wsgi.application'


# Database

db = json.loads(os.environ["DATABASE_SECRET"])
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': "letterpulse",
        'USER': db['username'],
        'PASSWORD': db['password'],
        'HOST': "letterpulse-dev.cluster-cwra7ijn7kej.us-east-1.rds.amazonaws.com",
        'PORT': "5432",
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


# Default primary key field type

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Data directories
DATA_DIR = BASE_DIR / 'data'

# django-allauth settings
LOGIN_REDIRECT_URL = 'analytics:extract'
LOGOUT_REDIRECT_URL = 'account_login'
ACCOUNT_LOGIN_METHODS = {'email'}
ACCOUNT_SIGNUP_FIELDS = ['email*', 'password1*', 'password2*']
ACCOUNT_EMAIL_VERIFICATION = 'optional'  # Set to 'mandatory' for production
ACCOUNT_USER_MODEL_USERNAME_FIELD = None
ACCOUNT_SESSION_REMEMBER = True
ACCOUNT_UNIQUE_EMAIL = True

# Email settings (use console backend for development)
EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'
DEFAULT_FROM_EMAIL = 'LetterPulse <noreply@letterpulse.app>'

# =============================================================================
# AI Credit System Configuration
# =============================================================================
# Default monthly credits for new users
DEFAULT_MONTHLY_CREDITS = 100

# Credit costs per operation
CREDITS_PER_EXTRACTION = 1      # Per post extracted from
CREDITS_PER_REPORT = 1          # Flat cost for generating insights
CREDITS_PER_ANNOTATION = 1      # Per post annotated with improvement tips

# =============================================================================
# Progress Bar Expected Durations (seconds)
# =============================================================================
# Base durations for time-based progress bars. Actual duration may vary
# based on number of posts selected.
PROGRESS_DURATIONS = {
    'refresh_posts': 45,        # empirical
    'download_annotated': 60,   # empirical
    'extract_content': 12,      # empirical
    'generate_report': 65,      # empirical
}