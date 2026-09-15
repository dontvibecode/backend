import base64
from datetime import timedelta
from io import StringIO
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.core.cache import cache
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from chatbot.models import Conversation, Message, Preferences, SpeechClip, SpeechPlay, User
from chatbot.serializers import PreferencesSerializer
from chatbot.services import speech_text
from chatbot.services.speech import (
    InvalidVoice,
    NothingToSpeak,
    SpeechBudgetExhausted,
    SpeechDailyLimitReached,
    SpeechService,
    SpeechTooLong,
    SpeechUnavailable,
)
from chatbot.services.user import FREE_SPEECH_DAILY_CHARACTERS
from chatbot.views.speech import SpeechClipAPIView, SpeechVoicesAPIView

GEORGE = "JBFqnCBsd6RMkjVDRZzb"
MODEL = "eleven_flash_v2_5"

LESSON_JSON = {
    "lesson_title": "Binary Search",
    "breakdown": "Halve the range each step.",
    "explanation": (
        "### How it works\n"
        "Compare with the **middle**.\n"
        "```python\n"
        "mid = (lo + hi) // 2\n"
        "```\n"
        "Then repeat."
    ),
}

SPEECH_SETTINGS = {
    "ELEVENLABS_API_KEY": "test-key",
    "ELEVENLABS_MODEL_ID": MODEL,
    "ELEVENLABS_OUTPUT_FORMAT": "mp3_44100_64",
    "ELEVENLABS_DEFAULT_VOICE_ID": GEORGE,
    "ELEVENLABS_VOICE_IDS": [],
    "ELEVENLABS_MONTHLY_CHARACTER_BUDGET": 10_000,
    "ELEVENLABS_MAX_CLIP_CHARACTERS": 6_000,
}


class FakeApiError(Exception):
    """Shaped like elevenlabs.core.api_error.ApiError."""

    def __init__(self, status_code, body):
        super().__init__(f"status {status_code}")
        self.status_code = status_code
        self.body = body


def make_client():
    client = MagicMock()

    def convert_with_timestamps(voice_id, *, text, model_id, output_format):
        characters = list(text)
        return SimpleNamespace(
            audio_base_64=base64.b64encode(b"mp3-bytes").decode(),
            alignment=SimpleNamespace(
                characters=characters,
                character_start_times_seconds=[i * 0.05 for i in range(len(characters))],
                character_end_times_seconds=[(i + 1) * 0.05 for i in range(len(characters))],
            ),
        )

    client.text_to_speech.convert_with_timestamps.side_effect = convert_with_timestamps
    client.voices.get_all.return_value = SimpleNamespace(
        voices=[
            SimpleNamespace(voice_id=GEORGE, name="George", category="premade",
                            description="Warm", preview_url="https://previews.example/george.mp3"),
            SimpleNamespace(voice_id="alice-id", name="Alice", category="premade",
                            description="", preview_url=None),
            SimpleNamespace(voice_id="clone-id", name="My clone", category="cloned",
                            description="", preview_url=None),
        ]
    )
    return client


class SpeechTextTests(SimpleTestCase):
    def units(self, text=None, json=None, scope="full"):
        return [(u.field, u.line, u.text) for u in speech_text.build_units(text, json, scope)]

    def test_a_code_block_is_one_cue_on_its_opening_fence_line(self):
        markdown = "Intro line\n\n```python\nx = 1\n\ny = 2\n```\nAfter code"

        self.assertEqual(
            self.units(markdown),
            [
                ("text", 1, "Intro line."),
                ("text", 3, speech_text.CODE_BLOCK_CUE),
                ("text", 8, "After code."),
            ],
        )

    def test_display_math_is_one_cue_on_its_opening_line(self):
        markdown = (
            "Attention is:\n"
            "$$\\text{softmax}(QK^T)V$$\n"
            "$$\n"
            "M_{ij} = -\\infty\n"
            "$$\n"
            "\\[ a = b \\]\n"
            "After the equations"
        )

        self.assertEqual(
            self.units(markdown),
            [
                ("text", 1, "Attention is:"),
                ("text", 2, speech_text.EQUATION_CUE),
                ("text", 3, speech_text.EQUATION_CUE),
                ("text", 6, speech_text.EQUATION_CUE),
                ("text", 7, "After the equations."),
            ],
        )

    def test_an_equation_followed_by_text_is_not_display_math(self):
        self.assertEqual(self.units("$$a$$ then more\nNext")[1], ("text", 2, "Next."))

    def test_markdown_syntax_links_and_emoji_are_not_read_aloud(self):
        markdown = (
            "### Common Mistakes\n"
            "❌ **Wrong:** `left = mid` (loops)\n"
            "- [MDN docs](https://developer.mozilla.org) explain `useEffect`\n"
            "---\n"
            "1. First step"
        )

        self.assertEqual(
            self.units(markdown),
            [
                ("text", 1, "Common Mistakes."),
                ("text", 2, "Wrong: left = mid (loops)."),
                ("text", 3, "MDN docs explain useEffect."),
                ("text", 5, "First step."),
            ],
        )

    def test_inline_code_keeps_characters_that_look_like_markup(self):
        self.assertEqual(self.units("Use `a * b` here"), [("text", 1, "Use a * b here.")])

    def test_summary_is_title_and_breakdown_and_full_adds_the_explanation(self):
        summary = self.units(json=LESSON_JSON, scope="summary")
        full = self.units(json=LESSON_JSON, scope="full")

        self.assertEqual([u[:2] for u in summary], [("title", 1), ("breakdown", 1)])
        self.assertEqual(
            [u[:2] for u in full],
            [
                ("title", 1),
                ("breakdown", 1),
                ("explanation", 1),
                ("explanation", 2),
                ("explanation", 3),
                ("explanation", 6),
            ],
        )

    def test_a_plain_reply_has_a_single_scope(self):
        self.assertEqual(speech_text.normalise_scope(None, "summary"), "full")
        self.assertEqual(speech_text.normalise_scope(LESSON_JSON, "summary"), "summary")

    def test_marks_come_from_the_alignment_when_it_matches_the_text(self):
        units = speech_text.build_units(None, LESSON_JSON, "summary")
        text, offsets = speech_text.join_units(units)
        times = [index * 0.1 for index in range(len(text))]

        marks = speech_text.build_marks(units, offsets, list(text), times)

        self.assertEqual(
            marks,
            [
                {"field": "title", "line": 1, "start": 0.0},
                {"field": "breakdown", "line": 1, "start": round(offsets[1] * 0.1, 3)},
            ],
        )

    def test_marks_fall_back_to_position_and_never_run_backwards(self):
        units = speech_text.build_units(None, LESSON_JSON, "full")
        text, offsets = speech_text.join_units(units)
        # As if normalisation rewrote every character, so nothing matches.
        characters = ["x"] * len(text)
        times = [index * 0.1 for index in range(len(text))]

        starts = [m["start"] for m in speech_text.build_marks(units, offsets, characters, times)]

        self.assertEqual(len(starts), len(units))
        self.assertEqual(starts, sorted(starts))
        self.assertGreater(starts[-1], 0)


@override_settings(**SPEECH_SETTINGS)
class SpeechServiceTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create(username="listener", email="listener@example.com")
        Preferences.objects.create(user=self.user)
        conversation = Conversation.objects.create(user=self.user, title="Search")
        self.lesson = Message.objects.create(
            conversation=conversation, from_user=False, json=LESSON_JSON
        )
        self.reply = Message.objects.create(
            conversation=conversation, from_user=False, text="Sure, **happy** to help!"
        )
        self.question = Message.objects.create(
            conversation=conversation, from_user=True, text="Teach me search"
        )
        self.client_mock = make_client()
        self.storage = MagicMock()
        self.storage.generate_download_signed_url.return_value = "https://r2.example/signed"
        self.service = self.make_service()

    def make_service(self, sleep=None):
        return SpeechService(
            client_factory=lambda: self.client_mock,
            storage_factory=lambda: self.storage,
            sleep=sleep or (lambda seconds: None),
        )

    def convert_calls(self):
        return self.client_mock.text_to_speech.convert_with_timestamps.call_count

    def identity(self, message, scope):
        text, _ = speech_text.join_units(
            speech_text.build_units(message.text, message.json, scope)
        )
        return {
            "message": message,
            "scope": scope,
            "voice_id": GEORGE,
            "model_id": MODEL,
            "text_hash": speech_text.text_hash(text),
        }

    def test_first_request_generates_uploads_and_stores_timing_marks(self):
        result = self.service.get_clip(self.user, self.lesson.id, "summary")

        self.assertEqual(result["url"], "https://r2.example/signed")
        self.assertFalse(result["cached"])
        self.assertEqual([m["field"] for m in result["marks"]], ["title", "breakdown"])

        convert = self.client_mock.text_to_speech.convert_with_timestamps
        convert.assert_called_once()
        self.assertEqual(convert.call_args.args, (GEORGE,))
        self.assertEqual(convert.call_args.kwargs["model_id"], MODEL)
        self.assertEqual(convert.call_args.kwargs["output_format"], "mp3_44100_64")

        key, audio, content_type = self.storage.upload_bytes.call_args.args
        self.assertTrue(key.startswith(f"speech/{self.user.id}/{self.lesson.id}/summary-{GEORGE}-"))
        self.assertEqual((audio, content_type), (b"mp3-bytes", "audio/mpeg"))

        clip = SpeechClip.objects.get()
        self.assertEqual((clip.status, clip.storage_key), (SpeechClip.READY, key))
        self.assertGreater(clip.duration_seconds, 0)

    def test_replaying_serves_stored_audio_without_calling_elevenlabs(self):
        self.service.get_clip(self.user, self.lesson.id, "summary")
        again = self.service.get_clip(self.user, self.lesson.id, "summary")

        self.assertTrue(again["cached"])
        self.assertEqual(self.convert_calls(), 1)
        self.assertEqual(
            list(SpeechPlay.objects.order_by("id").values_list("cache_hit", flat=True)),
            [False, True],
        )

    def test_only_ai_replies_in_the_users_own_conversations_can_be_read(self):
        stranger = User.objects.create(username="stranger", email="stranger@example.com")

        with self.assertRaises(Message.DoesNotExist):
            self.service.get_clip(stranger, self.lesson.id, "full")
        with self.assertRaises(Message.DoesNotExist):
            self.service.get_clip(self.user, self.question.id, "full")
        self.assertEqual(self.convert_calls(), 0)

    def test_requested_voice_must_be_offered_but_a_stale_preference_falls_back(self):
        with self.assertRaises(InvalidVoice):
            self.service.get_clip(self.user, self.reply.id, "full", requested_voice_id="clone-id")

        Preferences.objects.filter(user=self.user).update(voice_id="retired-voice")
        result = self.service.get_clip(self.user, self.reply.id, "full")

        self.assertEqual(result["voice_id"], GEORGE)

    def test_daily_allowance_stops_new_audio_but_never_replays(self):
        self.service.get_clip(self.user, self.reply.id, "full")
        SpeechClip.objects.update(characters=FREE_SPEECH_DAILY_CHARACTERS)

        with self.assertRaises(SpeechDailyLimitReached) as raised:
            self.service.get_clip(self.user, self.lesson.id, "summary")

        self.assertEqual(raised.exception.units[0]["field"], "title")
        self.assertTrue(self.service.get_clip(self.user, self.reply.id, "full")["cached"])
        self.assertEqual(self.convert_calls(), 1)

    @override_settings(ELEVENLABS_MONTHLY_CHARACTER_BUDGET=60)
    def test_the_monthly_budget_is_shared_by_every_user(self):
        other = User.objects.create(username="other", email="other@example.com")
        conversation = Conversation.objects.create(user=other, title="Other")
        message = Message.objects.create(conversation=conversation, from_user=False, text="Hi")
        SpeechClip.objects.create(
            message=message, user=other, scope="full", voice_id=GEORGE,
            model_id=MODEL, text_hash="x" * 64, characters=50,
        )

        with self.assertRaises(SpeechBudgetExhausted):
            self.service.get_clip(self.user, self.lesson.id, "summary")
        self.assertEqual(self.convert_calls(), 0)

    def test_without_an_api_key_stored_audio_plays_but_nothing_new_is_made(self):
        self.service.get_clip(self.user, self.reply.id, "full")

        with override_settings(ELEVENLABS_API_KEY=None):
            self.assertTrue(self.make_service().get_clip(self.user, self.reply.id, "full")["cached"])
            with self.assertRaises(SpeechUnavailable) as raised:
                self.make_service().get_clip(self.user, self.lesson.id, "full")

        self.assertEqual(raised.exception.detail, "not_configured")
        self.assertTrue(raised.exception.units)
        self.assertEqual(self.convert_calls(), 1)

    def test_a_failed_generation_frees_the_claim_and_hands_the_text_to_the_browser(self):
        self.client_mock.text_to_speech.convert_with_timestamps.side_effect = FakeApiError(
            401, {"detail": {"status": "quota_exceeded"}}
        )

        with self.assertRaises(SpeechUnavailable) as raised:
            self.service.get_clip(self.user, self.lesson.id, "full")

        self.assertEqual(raised.exception.detail, "quota_exceeded")
        self.assertEqual(raised.exception.units[-1]["text"], "Then repeat.")
        self.assertFalse(SpeechClip.objects.exists())

    def test_waits_for_a_concurrent_request_instead_of_paying_twice(self):
        pending = SpeechClip.objects.create(
            user=self.user, characters=42, **self.identity(self.lesson, "summary")
        )

        def other_request_finishes(_seconds):
            SpeechClip.objects.filter(pk=pending.pk).update(
                status=SpeechClip.READY, storage_key="speech/done.mp3"
            )

        result = self.make_service(sleep=other_request_finishes).get_clip(
            self.user, self.lesson.id, "summary"
        )

        self.assertTrue(result["cached"])
        self.assertEqual(self.convert_calls(), 0)

    def test_a_request_that_died_mid_generation_does_not_block_the_clip_forever(self):
        abandoned = SpeechClip.objects.create(
            user=self.user, characters=42, **self.identity(self.lesson, "summary")
        )
        SpeechClip.objects.filter(pk=abandoned.pk).update(
            created_at=timezone.now() - timedelta(minutes=10)
        )

        result = self.service.get_clip(self.user, self.lesson.id, "summary")

        self.assertFalse(result["cached"])
        self.assertEqual(self.convert_calls(), 1)
        self.assertEqual(SpeechClip.objects.get().status, SpeechClip.READY)

    @override_settings(ELEVENLABS_MAX_CLIP_CHARACTERS=10)
    def test_text_too_long_for_one_clip_is_left_to_the_browser_voice(self):
        with self.assertRaises(SpeechTooLong) as raised:
            self.service.get_clip(self.user, self.lesson.id, "full")

        self.assertTrue(raised.exception.units)
        self.assertEqual(self.convert_calls(), 0)

    def test_a_message_with_nothing_readable_is_rejected(self):
        blank = Message.objects.create(
            conversation=self.lesson.conversation, from_user=False, text="   "
        )

        with self.assertRaises(NothingToSpeak):
            self.service.get_clip(self.user, blank.id, "full")

    def test_voice_list_offers_premade_voices_alphabetically_and_is_cached(self):
        first = self.service.get_voices()
        second = self.make_service().get_voices()

        self.assertEqual([voice["id"] for voice in first], ["alice-id", GEORGE])
        self.assertEqual(first, second)
        self.client_mock.voices.get_all.assert_called_once()

    @override_settings(ELEVENLABS_VOICE_IDS=["clone-id", "alice-id", "missing-id"])
    def test_a_shortlist_chooses_and_orders_the_voices(self):
        ids = [voice["id"] for voice in self.service.get_voices()]

        self.assertEqual(ids, ["clone-id", "alice-id"])
        self.assertEqual(SpeechService.default_voice_id(ids), "clone-id")


@override_settings(**SPEECH_SETTINGS)
class SpeechViewTests(TestCase):
    def setUp(self):
        cache.clear()
        self.factory = APIRequestFactory()
        self.user = User.objects.create(username="viewer", email="viewer@example.com")
        self.user.is_authenticated = True
        Preferences.objects.create(user=self.user)
        conversation = Conversation.objects.create(user=self.user, title="Chat")
        self.reply = Message.objects.create(
            conversation=conversation, from_user=False, text="Hello there"
        )
        self.service = SpeechService(client_factory=make_client, storage_factory=MagicMock)

    def post(self, message_id, data):
        request = self.factory.post(f"/speech/{message_id}/", data, format="json")
        force_authenticate(request, user=self.user)
        with patch("chatbot.views.speech.SpeechService", return_value=self.service):
            return SpeechClipAPIView.as_view()(request, message_id=message_id)

    def test_returns_a_link_and_timing_marks(self):
        response = self.post(self.reply.id, {"scope": "full"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.data), {"url", "duration", "marks", "voice_id", "cached"})

    def test_someone_elses_message_is_not_found(self):
        stranger = User.objects.create(username="s", email="s@example.com")
        conversation = Conversation.objects.create(user=stranger, title="Theirs")
        theirs = Message.objects.create(conversation=conversation, from_user=False, text="Private")

        self.assertEqual(self.post(theirs.id, {"scope": "full"}).status_code, 404)

    def test_a_reached_limit_answers_429_with_text_for_the_browser_voice(self):
        units = [{"field": "text", "line": 1, "text": "Hello there."}]
        with patch.object(self.service, "get_clip", side_effect=SpeechDailyLimitReached(units)):
            response = self.post(self.reply.id, {"scope": "full"})

        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.data["error"], "speech_daily_limit")
        self.assertEqual(response.data["units"], units)

    def test_rejects_an_unknown_scope(self):
        self.assertEqual(self.post(self.reply.id, {"scope": "everything"}).status_code, 400)

    @override_settings(ELEVENLABS_API_KEY=None)
    def test_voice_list_reports_premium_voices_unavailable_without_a_key(self):
        request = self.factory.get("/speech/voices/")
        force_authenticate(request, user=self.user)

        response = SpeechVoicesAPIView.as_view()(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {"available": False, "voices": [], "default_voice_id": None})


class SpeechPreferencesTests(SimpleTestCase):
    def test_voice_preferences_are_accepted_within_range(self):
        valid = PreferencesSerializer(
            data={"voice_enabled": True, "voice_id": "alice-id", "speech_rate": 1.5}, partial=True
        )
        too_fast = PreferencesSerializer(data={"speech_rate": 3}, partial=True)

        self.assertTrue(valid.is_valid(), valid.errors)
        self.assertFalse(too_fast.is_valid())


@override_settings(**SPEECH_SETTINGS)
class SpeechCleanupAndStatsTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create(username="owner", email="owner@example.com")
        Preferences.objects.create(user=self.user)
        self.conversation = Conversation.objects.create(user=self.user, title="Chat")
        self.reply = Message.objects.create(
            conversation=self.conversation, from_user=False, text="Hello there"
        )
        self.storage = MagicMock()
        self.service = SpeechService(client_factory=make_client, storage_factory=lambda: self.storage)

    def test_deleting_a_conversation_deletes_its_audio_once_committed(self):
        self.service.get_clip(self.user, self.reply.id, "full")
        key = SpeechClip.objects.get().storage_key

        with patch("chatbot.signals.ObjectStorageService") as storage_class:
            with self.captureOnCommitCallbacks(execute=True):
                self.conversation.delete()

        storage_class.return_value.delete_object.assert_called_once_with(key)

    def test_stats_report_generation_and_cache_hit_rate(self):
        self.service.get_clip(self.user, self.reply.id, "full")
        self.service.get_clip(self.user, self.reply.id, "full")
        out = StringIO()

        call_command("speech_stats", stdout=out)

        report = out.getvalue()
        self.assertIn("Clips generated:     1", report)
        self.assertIn("Served from cache:   1 of 2 plays (50%)", report)
