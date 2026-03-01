import logging
import os
import threading
import time

from django.apps import AppConfig

logger = logging.getLogger(__name__)

_click_viz_thread_started = False
_click_viz_lock = threading.Lock()


def _click_viz_email_loop():
    """Background loop that runs send_click_viz_emails every 10 minutes."""
    # Wait for the app to fully start before first run
    time.sleep(60)

    from django.core.management import call_command

    while True:
        try:
            call_command('send_click_viz_emails', triggered_by='auto')
        except Exception:
            logger.exception("click_viz_email_loop: error running send_click_viz_emails")
        time.sleep(600)  # 10 minutes


class AnalyticsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'analytics'

    def ready(self):
        # Import signals to register them
        from . import signals  # noqa: F401

        # Start the log sink worker thread
        # Safe to call multiple times - will only start once
        from .logsink import log_sink
        log_sink.start()

        # Start background click viz email loop (production/dev only, not in
        # runserver reloader child, manage.py commands, or migrations)
        global _click_viz_thread_started
        with _click_viz_lock:
            if _click_viz_thread_started:
                return

            # Only start in gunicorn (SERVER_SOFTWARE) or runserver main process (RUN_MAIN=true)
            is_gunicorn = 'gunicorn' in os.environ.get('SERVER_SOFTWARE', '')
            is_runserver_main = os.environ.get('RUN_MAIN') == 'true'

            if is_gunicorn or is_runserver_main:
                t = threading.Thread(
                    target=_click_viz_email_loop,
                    name="ClickVizEmailLoop",
                    daemon=True,
                )
                t.start()
                _click_viz_thread_started = True
                logger.info("Click viz email background loop started")
