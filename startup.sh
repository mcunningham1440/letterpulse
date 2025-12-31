#!/bin/sh
set -eux

pwd
ls -la

# Create venv fresh at runtime (so we know it exists)
python3 -m venv /tmp/venv
. /tmp/venv/bin/activate

python -V
python -m pip -V

# Install deps and FAIL if it doesn't work
python -m pip install -U pip
python -m pip install -r requirements.txt

# Prove imports work (this will fail loudly if deps aren't there)
python -c "import django, gunicorn; print('django', django.get_version())"

python manage.py collectstatic --noinput

exec python -m gunicorn beehiiv_analytics.wsgi:application \
  --bind 0.0.0.0:${PORT:-8000} \
  --workers 1 \
  --threads 4 \
  --timeout 120 \
  --graceful-timeout 30