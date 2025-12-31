#!/bin/bash

python3 -m pip install -r requirements.txt

python3 manage.py collectstatic --noinput

exec python3 -m gunicorn beehiiv_analytics.wsgi:application \
  --bind 0.0.0.0:${PORT:-8000} \
  --workers 1 \
  --threads 4 \
  --timeout 120 \
  --graceful-timeout 30