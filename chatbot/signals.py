import logging

from django.db import transaction
from django.db.models.signals import post_delete
from django.dispatch import receiver

from .models import SpeechClip
from .services.storage import ObjectStorageService

logger = logging.getLogger(__name__)


@receiver(post_delete, sender=SpeechClip)
def delete_narration_audio(sender, instance, **kwargs):
    """
    Deleting a conversation cascades to its clips, and their audio goes too.
    Deferred until commit, so a delete that rolls back keeps its audio.
    """
    key = instance.storage_key
    if not key:
        return

    def delete():
        try:
            ObjectStorageService().delete_object(key)
        except Exception:
            # Storage isn't configured or can't be reached. The row is already
            # gone, and the key embeds a hash of the text, so the object can't
            # be found by anyone who doesn't already know what it says.
            logger.exception("Could not delete narration audio %s", key)

    transaction.on_commit(delete)
