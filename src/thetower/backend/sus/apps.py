from django.apps import AppConfig


class SusConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "thetower.backend.sus"

    def ready(self):
        from thetower.backend.sus import events

        events.connect_signals()
