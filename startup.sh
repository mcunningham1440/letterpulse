#!/bin/bash

. /app/venv/bin/activate

python3 manage.py collectstatic 

exec gunicorn beehiiv_analytics.wsgi:application \
  --bind 0.0.0.0:${PORT:-8000} \
  --workers 1 \
  --threads 4 \
  --timeout 120 \
  --graceful-timeout 30