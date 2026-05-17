import logging
import os
import sys

from django.apps import AppConfig
from django.db.utils import OperationalError, ProgrammingError

logger = logging.getLogger(__name__)


# Management commands where booting a stuck-task sweep is either unnecessary
# (no live workers to recover from) or actively harmful (schema may not exist
# yet). Web entrypoints (gunicorn, runserver) don't match anything here.
_BOOT_SWEEP_SKIP_COMMANDS = {
    'migrate', 'makemigrations', 'collectstatic', 'test',
    'shell', 'check', 'dbshell', 'showmigrations', 'sqlmigrate',
    'createsuperuser', 'flush', 'loaddata', 'dumpdata', 'inspectdb',
    'compilemessages', 'makemessages',
}


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

        if self._should_run_boot_sweep():
            self._recover_stuck_tasks_on_boot()

    def _should_run_boot_sweep(self) -> bool:
        if os.environ.get('ANALYTICS_FORCE_BOOT_SWEEP'):
            return True
        cmd = sys.argv[1] if len(sys.argv) > 1 else ''
        if cmd in _BOOT_SWEEP_SKIP_COMMANDS:
            return False
        if cmd == 'runserver' and os.environ.get('RUN_MAIN') != 'true':
            # Suppress the duplicate boot on `runserver`'s auto-reload parent.
            return False
        return True

    def _recover_stuck_tasks_on_boot(self):
        try:
            from .utils.background import recover_stuck_tasks
            counts = recover_stuck_tasks()
            swept = sum(counts.values())
            if swept:
                logger.info("Boot recovery swept %d stuck background tasks: %s",
                            swept, counts)
        except (OperationalError, ProgrammingError):
            # Schema not ready yet (e.g. first migrate of a fresh DB).
            logger.info("Boot recovery skipped: DB schema not ready")
        except Exception:
            logger.exception("Boot recovery sweep failed")
