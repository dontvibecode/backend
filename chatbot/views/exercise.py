import json
import logging

from ..models import Exercise, ExerciseFile, Message
from ..serializers import (
    ExerciseFileSubmissionsSerializer,
    ExerciseSubmissionSerializer,
)
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from django.forms.models import model_to_dict
from ..services.llm import LLMService
from ..services.user import InsufficientTokensError


logger = logging.getLogger(__name__)


class ExerciseAPIView(APIView):
    """
    API view to handle exercise-related requests.
    """

    def get(self, request, message_id):
        """
        Retrieve all exercises by associated message ID.
        """
        exercises = Exercise.objects.filter(
            message_id=message_id, message__conversation__user_id=request.user.id
        ).prefetch_related("files")

        if not exercises.exists():
            return Response(
                {"error": "Exercise not found."}, status=status.HTTP_404_NOT_FOUND
            )

        # Return a mapping keyed by exercise_id so the frontend can look up exercises easily.
        data = {
            exercise.id: {
                # Exercise fields
                "id": exercise.id,
                "message": exercise.message_id,
                "correctness": exercise.correctness,
                "feedback": exercise.feedback,
                "bookmarked": exercise.bookmarked,
                "title": exercise.title,
                "tags": exercise.tags,
                # Related files (use the prefetched objects; avoid `.values()` which would re-query)
                "files": [
                    {
                        "id": f.id,
                        "filename": f.filename,
                        "text": f.text,
                        "code": f.code,
                        "user_submission": f.user_submission,
                        "exercise": f.exercise_id,
                    }
                    for f in exercise.files.all()
                ],
            }
            for exercise in exercises
        }

        return Response(data, status=status.HTTP_200_OK)

    def post(self, request, message_id):
        """
        Create a new exercise.
        """
        try:
            message = Message.objects.get(id=message_id, conversation__user_id=request.user.id)
        except Message.DoesNotExist:
            return Response(
                {"error": "Message not found."}, status=status.HTTP_404_NOT_FOUND
            )
        llm_service = LLMService(None, request.user.id)

        if llm_service.exercise_count_limit_reached(request.user.id, message_id):
            return Response(
                {"warning": "Exercise count limit reached."},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        response = llm_service.generate_exercise(
            ability_level=request.data.get("ability_level", "beginner"),
            message=message,
            exercise_files_text=json.dumps(
                [
                    list(exercise.files.values("filename", "text", "code"))
                    for exercise in message.exercises.all()
                ]
            ),
        )
        if (
            isinstance(response, dict)
            and response.get("warning") == "Insufficient tokens"
        ):
            return Response(response, status=status.HTTP_402_PAYMENT_REQUIRED)
        return Response(response, status=status.HTTP_201_CREATED)


class ExerciseSubmissionAPIView(APIView):
    """
    API view for AI evaluation of exercise submissions.
    """

    def post(self, request):
        """
        Evaluate a user's exercise submission.
        """
        input_serializer = ExerciseSubmissionSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)
        validated_data = input_serializer.validated_data

        user_submissions = validated_data["user_submissions"]
        exercise_file_ids = validated_data["exercise_file_ids"]

        llm_service = LLMService(None, request.user.id)
        try:
            response = llm_service.mark_exercise(
                ability_level=validated_data["ability_level"],
                message_id=validated_data["message_id"],
                exercise_id=validated_data["exercise_id"],
                exercise_file_ids=exercise_file_ids,
                user_submissions=user_submissions,
            )
            if (
                isinstance(response, dict)
                and response.get("warning") == "Insufficient tokens"
            ):
                return Response(response, status=status.HTTP_402_PAYMENT_REQUIRED)
        except (Exercise.DoesNotExist, ExerciseFile.DoesNotExist):
            return Response(
                {"error": "Exercise or exercise files not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        except InsufficientTokensError:
            return Response(
                {"warning": "Insufficient tokens"},
                status=status.HTTP_402_PAYMENT_REQUIRED,
            )
        except Exception:
            logger.exception(
                "Failed to mark exercise %s for user %s",
                validated_data["exercise_id"],
                request.user.id,
            )
            return Response(
                {"error": "Failed to mark exercise."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(response, status=status.HTTP_200_OK)


class ExerciseSaveAPIView(APIView):
    """
    API view to save user submissions for exercises.
    """

    def post(self, request):
        """
        Save the user's submission for a specific exercise. Expects a list of submissions and corresponding list of exercise file IDs.
        """
        input_serializer = ExerciseFileSubmissionsSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)

        llm_service = LLMService(None, request.user.id)
        try:
            llm_service.save_user_submissions(
                exercise_file_ids=input_serializer.validated_data["exercise_file_ids"],
                user_submissions=input_serializer.validated_data["user_submissions"],
            )
        except ExerciseFile.DoesNotExist:
            return Response(
                {"error": "One or more exercise files were not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        return Response(
            {"status": "User submissions saved."}, status=status.HTTP_200_OK
        )


class ExerciseBookmarkAPIView(APIView):
    """
    API view to bookmark an exercise.
    """

    def get(self, request, user_email):
        """
        Get all bookmarked exercises for a user.
        """
        exercises = Exercise.objects.filter(
            bookmarked=True, message__conversation__user__email=request.user.email
        ).values(
            "id",
            "message_id",
            "message__conversation_id",
            "correctness",
            "bookmarked",
            "title",
            "tags",
        )

        return Response(list(exercises), status=status.HTTP_200_OK)

    def post(self, request, exercise_id):
        """
        Toggle the bookmark status of an exercise.
        """
        try:
            exercise = Exercise.objects.get(
                id=exercise_id, message__conversation__user_id=request.user.id
            )
        except Exercise.DoesNotExist:
            return Response(
                {"error": f"Exercise with id {exercise_id} does not exist."},
                status=status.HTTP_404_NOT_FOUND,
            )
        try:
            exercise.bookmarked = not exercise.bookmarked
            exercise.save(update_fields=["bookmarked"])
            return Response(model_to_dict(exercise), status=status.HTTP_200_OK)
        except Exception as e:
            return Response(
                {"error": f"Failed to update bookmark status: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class ExerciseAggregateAPIView(APIView):
    """
    API view to get aggregated exercises for a conversation.
    """

    def get(self, request, conversation_id):
        """
        Get aggregated exercises for a conversation.
        """
        try:
            exercises = Exercise.objects.filter(
                message__conversation_id=conversation_id,
                message__conversation__user_id=request.user.id,
            )
            exercises_count = exercises.count()
            exercises_almost_count = exercises.filter(correctness=1).count()
            exercises_correct_count = exercises.filter(correctness=2).count()
            return Response(
                {
                    "exercises_count": exercises_count,
                    "exercises_almost_count": exercises_almost_count,
                    "exercises_correct_count": exercises_correct_count,
                },
                status=status.HTTP_200_OK,
            )
        except Exception as e:
            return Response(
                {"error": f"Failed to get aggregated exercises: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
