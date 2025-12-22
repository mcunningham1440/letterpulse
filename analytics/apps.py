from django.apps import AppConfig


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
