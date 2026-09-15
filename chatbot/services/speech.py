"""
Reads chat messages aloud with ElevenLabs.

Each narration is generated at most once and kept in R2; after that, playing
it costs nothing. Anything that stops premium audio from being served raises a
SpeechError carrying the prepared text, so the browser's built-in voice can
read the same words instead of the feature simply failing.
"""

import base64
import hashlib
import logging
import time
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone

from ..models import Message, Preferences, SpeechClip, SpeechPlay
from . import speech_text
from .storage import ObjectStorageService
from .user import FREE_TIER, TIER_SPEECH_DAILY_CHARACTERS

logger = logging.getLogger(__name__)

AUDIO_CONTENT_TYPE = "audio/mpeg"
DOWNLOAD_LINK_LIFETIME = timedelta(hours=6)
DAILY_WINDOW = timedelta(days=1)
BUDGET_WINDOW = timedelta(days=30)
# A PENDING row this old belongs to a request that died mid-generation.
ABANDONED_AFTER = timedelta(minutes=2)
# While another request generates the same clip, check back this often, up to
# this many times (45 seconds in all), before giving up.
POLL_INTERVAL_SECONDS = 0.5
POLL_ATTEMPTS = 90
VOICES_CACHE_SECONDS = 6 * 60 * 60
GENERATION_TIMEOUT_SECONDS = 60


class SpeechError(Exception):
    """
    Premium audio can't be served right now. `units` is the prepared text, so
    the client can read it with the browser's voice instead.
    """

    code = "speech_unavailable"

    def __init__(self, units=None, detail=""):
        super().__init__(detail or self.code)
        self.units = units or []
        self.detail = detail


class SpeechUnavailable(SpeechError):
    """No API key, or ElevenLabs or storage failed."""


class SpeechDailyLimitReached(SpeechError):
    code = "speech_daily_limit"


class SpeechBudgetExhausted(SpeechError):
    code = "speech_budget_exhausted"


class SpeechTooLong(SpeechError):
    code = "speech_too_long"


class SpeechBusy(SpeechError):
    code = "speech_busy"


class InvalidVoice(ValueError):
    """The client asked for a voice the picker doesn't offer."""


class NothingToSpeak(ValueError):
    """The message has no readable text."""


class SpeechService:
    """
    Owns narration: which voices are offered, and turning a message into a
    playable clip within the character allowances.

    The ElevenLabs client, storage and sleep are injectable so tests never
    touch the network or wait in real time.
    """

    def __init__(self, client_factory=None, storage_factory=None, sleep=time.sleep):
        self._client_factory = client_factory or _elevenlabs_client
        self._storage_factory = storage_factory or ObjectStorageService
        self._sleep = sleep
        self._client = None
        self._storage = None

    @property
    def client(self):
        if self._client is None:
            self._client = self._client_factory()
        return self._client

    @property
    def storage(self):
        # Created on first use, so listing voices doesn't require R2 settings.
        if self._storage is None:
            self._storage = self._storage_factory()
        return self._storage

    # --- Voices ------------------------------------------------------------

    def get_voices(self):
        """
        Voices the picker may offer: the ELEVENLABS_VOICE_IDS shortlist in its
        own order, or otherwise every premade voice (the kind free API keys may
        use; Voice Library voices are not available to them). Cached for hours
        because the list almost never changes. Empty without an API key.
        """
        if not settings.ELEVENLABS_API_KEY:
            return []

        shortlist = settings.ELEVENLABS_VOICE_IDS
        cache_key = _voices_cache_key(shortlist)
        voices = cache.get(cache_key)
        if voices is None:
            try:
                response = self.client.voices.get_all()
            except Exception as error:
                raise SpeechUnavailable(detail=_describe_failure(error)) from error
            voices = _offered_voices(response.voices, shortlist)
            cache.set(cache_key, voices, VOICES_CACHE_SECONDS)
        return voices

    @staticmethod
    def default_voice_id(offered_ids):
        """The configured default if it's offered (or nothing is known), else the first offered voice."""
        configured = settings.ELEVENLABS_DEFAULT_VOICE_ID
        if not offered_ids or configured in offered_ids:
            return configured
        return offered_ids[0]

    def resolve_voice_id(self, requested, preferred):
        """
        A voice the client explicitly asks for must be one the picker offers.
        A saved preference is only a wish: if that voice has since been
        removed, the default is used rather than breaking every narration for
        that user. When the list can't be fetched there is nothing to check
        against, so the choice passes through; a stored clip can still match,
        and generation would fail on its own anyway.
        """
        try:
            offered = [voice["id"] for voice in self.get_voices()]
        except SpeechUnavailable:
            offered = []

        if requested:
            if offered and requested not in offered:
                raise InvalidVoice("That voice isn't available.")
            return requested
        if preferred and (not offered or preferred in offered):
            return preferred
        return self.default_voice_id(offered)

    # --- Clips -------------------------------------------------------------

    def get_clip(self, user, message_id, scope, requested_voice_id=None):
        """
        Returns {url, duration, marks, voice_id, cached} for a message's
        narration, generating the audio first if it doesn't exist yet.

        Raises Message.DoesNotExist unless the message is an AI reply in one of
        the user's own conversations. The text always comes from the database,
        never the client, so nobody can spend the credits on arbitrary text.
        """
        message = Message.objects.get(
            id=message_id, from_user=False, conversation__user_id=user.id
        )
        scope = speech_text.normalise_scope(message.json, scope)
        units = speech_text.build_units(message.text, message.json, scope)
        if not units:
            raise NothingToSpeak("This message has nothing to read aloud.")
        text, offsets = speech_text.join_units(units)
        fallback = speech_text.units_payload(units)

        preferred = (
            Preferences.objects.filter(user_id=user.id)
            .values_list("voice_id", flat=True)
            .first()
        )
        identity = {
            "message": message,
            "scope": scope,
            "voice_id": self.resolve_voice_id(requested_voice_id, preferred),
            "model_id": settings.ELEVENLABS_MODEL_ID,
            "text_hash": speech_text.text_hash(text),
        }

        ready = SpeechClip.objects.filter(status=SpeechClip.READY, **identity).first()
        if ready:
            return self._serve(ready, cache_hit=True)

        if not settings.ELEVENLABS_API_KEY:
            raise SpeechUnavailable(fallback, "not_configured")
        if len(text) > settings.ELEVENLABS_MAX_CLIP_CHARACTERS:
            raise SpeechTooLong(fallback)
        self._check_allowances(user, len(text), fallback)

        clip, claimed = self._claim(user, identity, len(text), fallback)
        if not claimed:
            return self._wait_for(clip, fallback)
        return self._generate(clip, text, units, offsets, fallback)

    def _check_allowances(self, user, characters, fallback):
        now = timezone.now()

        daily_limit = TIER_SPEECH_DAILY_CHARACTERS.get(
            user.membership, TIER_SPEECH_DAILY_CHARACTERS[FREE_TIER]
        )
        # A user under their daily line may start one more clip even if it
        # ends past the line, so a fresh allowance can always narrate a lesson.
        if characters_generated_since(now - DAILY_WINDOW, user=user) >= daily_limit:
            raise SpeechDailyLimitReached(fallback)

        # The shared budget stands in for the real ElevenLabs credits, so this
        # clip is counted before starting rather than after.
        used = characters_generated_since(now - BUDGET_WINDOW)
        if used + characters > settings.ELEVENLABS_MONTHLY_CHARACTER_BUDGET:
            raise SpeechBudgetExhausted(fallback)

    def _claim(self, user, identity, characters, fallback):
        """
        Inserts the PENDING row that makes this request the one generating the
        clip. Returns (clip, True) if it won, or (the other request's clip,
        False) if another request got there first.
        """
        for _ in range(2):
            try:
                # A savepoint, so the expected IntegrityError doesn't poison a
                # surrounding transaction.
                with transaction.atomic():
                    clip = SpeechClip.objects.create(
                        user=user, characters=characters, **identity
                    )
                return clip, True
            except IntegrityError:
                existing = SpeechClip.objects.filter(**identity).first()
                if existing is None:
                    continue  # Its request failed and removed it just now.
                abandoned = (
                    existing.status == SpeechClip.PENDING
                    and existing.created_at < timezone.now() - ABANDONED_AFTER
                )
                if not abandoned:
                    return existing, False
                SpeechClip.objects.filter(
                    pk=existing.pk, status=SpeechClip.PENDING
                ).delete()
        raise SpeechBusy(fallback)

    def _wait_for(self, clip, fallback):
        """Waits for another request that is generating this same clip."""
        for attempt in range(POLL_ATTEMPTS + 1):
            if clip.status == SpeechClip.READY:
                return self._serve(clip, cache_hit=True)
            if attempt == POLL_ATTEMPTS:
                break
            self._sleep(POLL_INTERVAL_SECONDS)
            clip = SpeechClip.objects.filter(pk=clip.pk).first()
            if clip is None:
                raise SpeechUnavailable(fallback, "generation_failed")
        raise SpeechBusy(fallback)

    def _generate(self, clip, text, units, offsets, fallback):
        key = _storage_key(clip)
        try:
            response = self.client.text_to_speech.convert_with_timestamps(
                clip.voice_id,
                text=text,
                model_id=clip.model_id,
                output_format=settings.ELEVENLABS_OUTPUT_FORMAT,
            )
            self.storage.upload_bytes(
                key, base64.b64decode(response.audio_base_64), AUDIO_CONTENT_TYPE
            )
        except Exception as error:
            # Broad on purpose: whatever broke (ElevenLabs, the network,
            # storage), release the claim so a later request can retry, and
            # let the user hear the browser voice now.
            SpeechClip.objects.filter(pk=clip.pk, status=SpeechClip.PENDING).delete()
            reason = _describe_failure(error)
            logger.warning("Narration %s failed: %s", clip.pk, reason, exc_info=True)
            raise SpeechUnavailable(fallback, reason) from error

        alignment = response.alignment
        if alignment:
            clip.marks = speech_text.build_marks(
                units,
                offsets,
                alignment.characters,
                alignment.character_start_times_seconds,
            )
            ends = alignment.character_end_times_seconds
            clip.duration_seconds = float(ends[-1]) if ends else None
        clip.storage_key = key
        clip.status = SpeechClip.READY
        clip.save(update_fields=["marks", "duration_seconds", "storage_key", "status"])
        logger.info(
            "Generated narration %s: %s characters with voice %s",
            clip.pk,
            clip.characters,
            clip.voice_id,
        )
        return self._serve(clip, cache_hit=False)

    def _serve(self, clip, cache_hit):
        SpeechPlay.objects.create(clip=clip, cache_hit=cache_hit)
        return {
            "url": self.storage.generate_download_signed_url(
                clip.storage_key, int(DOWNLOAD_LINK_LIFETIME.total_seconds())
            ),
            "duration": clip.duration_seconds,
            "marks": clip.marks,
            "voice_id": clip.voice_id,
            "cached": cache_hit,
        }


def characters_generated_since(since, user=None):
    """Characters sent to ElevenLabs since `since`, including clips still generating."""
    clips = SpeechClip.objects.filter(created_at__gte=since)
    if user is not None:
        clips = clips.filter(user=user)
    return clips.aggregate(total=Sum("characters"))["total"] or 0


def _storage_key(clip):
    return (
        f"speech/{clip.user_id}/{clip.message_id}/"
        f"{clip.scope}-{clip.voice_id}-{clip.model_id}-{clip.text_hash[:16]}.mp3"
    )


def _offered_voices(voices, shortlist):
    if shortlist:
        by_id = {voice.voice_id: voice for voice in voices}
        chosen = [by_id[voice_id] for voice_id in shortlist if voice_id in by_id]
    else:
        chosen = sorted(
            (voice for voice in voices if voice.category == "premade"),
            key=lambda voice: (voice.name or "").lower(),
        )
    return [
        {
            "id": voice.voice_id,
            "name": voice.name or voice.voice_id,
            "description": voice.description or "",
            "preview_url": voice.preview_url,
        }
        for voice in chosen
    ]


def _voices_cache_key(shortlist):
    digest = hashlib.sha256(",".join(shortlist).encode("utf-8")).hexdigest()[:12]
    return f"speech:voices:{digest}"


def _describe_failure(error):
    """A short machine-readable reason, such as 'quota_exceeded'."""
    body = getattr(error, "body", None)
    if isinstance(body, dict):
        detail = body.get("detail")
        if isinstance(detail, dict) and detail.get("status"):
            return str(detail["status"])
    status_code = getattr(error, "status_code", None)
    return f"http_{status_code}" if status_code else type(error).__name__


def _elevenlabs_client():
    # Imported on first use rather than at module load: the SDK is large, and
    # most requests (and every worker boot) never need it.
    from elevenlabs.client import ElevenLabs

    return ElevenLabs(
        api_key=settings.ELEVENLABS_API_KEY, timeout=GENERATION_TIMEOUT_SECONDS
    )
