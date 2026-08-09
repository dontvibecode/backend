import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, TestCase
from pydantic import ValidationError
from rest_framework import serializers
from rest_framework.test import APIRequestFactory, force_authenticate

from chatbot.services.user import InsufficientTokensError
from chatbot.models import (
    Conversation,
    Exercise,
    ExerciseFile,
    Message,
    TokenUsage,
    User,
)
from chatbot.serializers import ExerciseSubmissionSerializer
from chatbot.services.llm import LLMService
from chatbot.views.exercise import ExerciseSubmissionAPIView


class ExerciseSubmissionSerializerTests(SimpleTestCase):
    def test_rejects_mismatched_file_and_submission_counts(self):
        serializer = ExerciseSubmissionSerializer(
            data={
                "ability_level": "beginner",
                "message_id": 1,
                "exercise_id": 1,
                "exercise_file_ids": [1, 2],
                "user_submissions": ["first"],
            }
        )

        with self.assertRaises(serializers.ValidationError):
            serializer.is_valid(raise_exception=True)

    def test_rejects_duplicate_file_ids(self):
        serializer = ExerciseSubmissionSerializer(
            data={
                "ability_level": "beginner",
                "message_id": 1,
                "exercise_id": 1,
                "exercise_file_ids": [1, 1],
                "user_submissions": ["first", "second"],
            }
        )

        with self.assertRaises(serializers.ValidationError):
            serializer.is_valid(raise_exception=True)


class ExerciseServiceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(
            username="exercise-user",
            email="exercise@example.com",
            method="google",
            membership="pro",
        )
        self.conversation = Conversation.objects.create(
            user=self.user,
            title="Exercise conversation",
        )
        self.message = Message.objects.create(
            conversation=self.conversation,
            from_user=False,
            json={"lesson": "Test lesson"},
        )
        self.exercise = Exercise.objects.create(
            message=self.message,
            title="Test exercise",
        )
        self.first_file = ExerciseFile.objects.create(
            exercise=self.exercise,
            filename="first.py",
            text="First file",
            code="pass",
        )
        self.second_file = ExerciseFile.objects.create(
            exercise=self.exercise,
            filename="second.py",
            text="Second file",
            code="pass",
        )

    def make_service(self):
        client = MagicMock()
        response_data = {
            "correctness": 2,
            "heading": "Correct",
            "summary": "Well done",
            "corrections": [],
        }
        client.models.generate_content.return_value = SimpleNamespace(
            text=json.dumps(response_data),
            usage_metadata=SimpleNamespace(total_token_count=25),
        )

        with patch("chatbot.services.llm.genai.Client", return_value=client):
            service = LLMService(None, self.user.id)

        return service, response_data

    def test_evaluation_returns_object_and_saves_all_related_state(self):
        service, response_data = self.make_service()

        with (
            patch.object(service, "has_enough_tokens", return_value=True),
            patch(
                "chatbot.services.llm.UserService.consume_tokens",
                return_value=99_975,
            ),
        ):
            result = service.mark_exercise(
                ability_level="beginner",
                message_id=self.message.id,
                exercise_id=self.exercise.id,
                exercise_file_ids=[self.first_file.id, self.second_file.id],
                user_submissions=["print('first')", "print('second')"],
            )

        self.exercise.refresh_from_db()
        self.first_file.refresh_from_db()
        self.second_file.refresh_from_db()

        self.assertEqual(result, response_data)
        self.assertEqual(self.exercise.feedback, response_data)
        self.assertEqual(self.exercise.correctness, 2)
        self.assertEqual(self.first_file.user_submission, "print('first')")
        self.assertEqual(self.second_file.user_submission, "print('second')")
        self.assertTrue(
            TokenUsage.objects.filter(
                user=self.user,
                token_used=25,
                tokens_remaining=99_975,
                action="exercise_evaluator",
            ).exists()
        )

    def test_evaluation_requires_exercise_to_belong_to_message(self):
        other_message = Message.objects.create(
            conversation=self.conversation,
            from_user=False,
            json={"lesson": "Other lesson"},
        )
        service, _ = self.make_service()

        with self.assertRaises(Exercise.DoesNotExist):
            service.mark_exercise(
                ability_level="beginner",
                message_id=other_message.id,
                exercise_id=self.exercise.id,
                exercise_file_ids=[self.first_file.id],
                user_submissions=["submission"],
            )

    def test_evaluation_rejects_file_from_another_exercise(self):
        other_exercise = Exercise.objects.create(
            message=self.message,
            title="Other exercise",
        )
        other_file = ExerciseFile.objects.create(
            exercise=other_exercise,
            filename="other.py",
            text="Other file",
            code="pass",
        )
        service, _ = self.make_service()

        with self.assertRaises(ExerciseFile.DoesNotExist):
            service.mark_exercise(
                ability_level="beginner",
                message_id=self.message.id,
                exercise_id=self.exercise.id,
                exercise_file_ids=[other_file.id],
                user_submissions=["submission"],
            )

    def test_bulk_save_rejects_unowned_files_without_partial_updates(self):
        other_user = User.objects.create(
            username="other-user",
            email="other@example.com",
            method="google",
        )
        other_conversation = Conversation.objects.create(
            user=other_user,
            title="Other conversation",
        )
        other_message = Message.objects.create(
            conversation=other_conversation,
            from_user=False,
            json={"lesson": "Other lesson"},
        )
        other_exercise = Exercise.objects.create(
            message=other_message,
            title="Other exercise",
        )
        other_file = ExerciseFile.objects.create(
            exercise=other_exercise,
            filename="other.py",
            text="Other file",
            code="pass",
        )
        service, _ = self.make_service()

        with self.assertRaises(ExerciseFile.DoesNotExist):
            service.save_user_submissions(
                exercise_file_ids=[self.first_file.id, other_file.id],
                user_submissions=["owned update", "unowned update"],
            )

        self.first_file.refresh_from_db()
        self.assertIsNone(self.first_file.user_submission)

    @patch("chatbot.views.exercise.LLMService", autospec=True)
    def test_submission_view_calls_evaluation_with_validated_relationship_ids(
        self, service_class
    ):
        response_data = {"correctness": 2, "summary": "Well done"}
        service_class.return_value.mark_exercise.return_value = response_data
        request = APIRequestFactory().post(
            "/exercise/submit/",
            {
                "ability_level": "beginner",
                "message_id": self.message.id,
                "exercise_id": self.exercise.id,
                "exercise_file_ids": [self.first_file.id, self.second_file.id],
                "user_submissions": ["first", "second"],
            },
            format="json",
        )
        self.user.is_authenticated = True
        force_authenticate(request, user=self.user)

        response = ExerciseSubmissionAPIView.as_view()(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, response_data)
        service_class.return_value.mark_exercise.assert_called_once_with(
            ability_level="beginner",
            message_id=self.message.id,
            exercise_id=self.exercise.id,
            exercise_file_ids=[self.first_file.id, self.second_file.id],
            user_submissions=["first", "second"],
        )

    def test_no_token_loss_when_exercise_json_is_invalid(self):
        service, _ = self.make_service()
        incomplete_json_response = SimpleNamespace(
            text="Invalid JSON",
            usage_metadata=SimpleNamespace(total_token_count=100),
        )

        service.client.models.generate_content.return_value = incomplete_json_response

        previous_token_used = self.user.token_used
        usage_count_before = TokenUsage.objects.count()
        exercise_count_before = Exercise.objects.count()

        with patch.object(service, "has_enough_tokens", return_value=True):
            with self.assertRaises(ValidationError):
                service.generate_exercise(
                    ability_level="beginner",
                    message=self.message,
                    exercise_files_text="[]",
                )

        self.user.refresh_from_db()
        self.assertEqual(self.user.token_used, previous_token_used)
        self.assertEqual(TokenUsage.objects.count(), usage_count_before)
        self.assertEqual(Exercise.objects.count(), exercise_count_before)

    def test_no_token_loss_when_exercise_json_has_invalid_shape(self):
        service, _ = self.make_service()
        incomplete_json_response = SimpleNamespace(
            text="""
                {
                    "exercise_title": "Incomplete exercise",
                    "exercise_tags": []
                }
            """,
            usage_metadata=SimpleNamespace(total_token_count=100),
        )
        service.client.models.generate_content.return_value = incomplete_json_response

        previous_token_used = self.user.token_used
        usage_count_before = TokenUsage.objects.count()
        exercise_count_before = Exercise.objects.count()

        with patch.object(service, "has_enough_tokens", return_value=True):
            with self.assertRaises(ValidationError):
                service.generate_exercise(
                    ability_level="beginner",
                    message=self.message,
                    exercise_files_text="[]",
                )

        self.user.refresh_from_db()
        self.assertEqual(self.user.token_used, previous_token_used)
        self.assertEqual(TokenUsage.objects.count(), usage_count_before)
        self.assertEqual(Exercise.objects.count(), exercise_count_before)

    def test_evaluation_rolls_back_if_token_charge_fails(self):
        service, response_data = self.make_service()
        previous_token_used = self.user.token_used
        usage_count_before = TokenUsage.objects.count()

        with (
            patch.object(service, "has_enough_tokens", return_value=True),
            patch(
                "chatbot.services.llm.UserService.consume_tokens",
                side_effect=InsufficientTokensError,
            ),
        ):
            with self.assertRaises(InsufficientTokensError):
                service.mark_exercise(
                    ability_level="beginner",
                    message_id=self.message.id,
                    exercise_id=self.exercise.id,
                    exercise_file_ids=[self.first_file.id, self.second_file.id],
                    user_submissions=["print('first')", "print('second')"],
                )

        self.user.refresh_from_db()
        self.exercise.refresh_from_db()
        self.first_file.refresh_from_db()
        self.second_file.refresh_from_db()
        
        self.assertEqual(self.user.token_used, previous_token_used)
        self.assertEqual(TokenUsage.objects.count(), usage_count_before)
        self.assertIsNone(self.exercise.feedback)
        self.assertIsNone(self.exercise.correctness)
        self.assertIsNone(self.first_file.user_submission)
        self.assertIsNone(self.second_file.user_submission)