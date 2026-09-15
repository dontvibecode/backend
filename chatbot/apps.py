from django.apps import AppConfig


class ChatbotConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'chatbot'

    def ready(self):
        # Connects the handler that deletes narration audio along with its clip.
        from . import signals  # noqa: F401
