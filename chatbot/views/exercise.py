import json
from ..models import Exercise, Message
from ..serializers import ExerciseSubmissionSerializer
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from django.forms.models import model_to_dict
from ..services.llm import LLMService


class ExerciseAPIView(APIView):
    """
    API view to handle exercise-related requests.
    """

    def get(self, request, message_id):
        """
        Retrieve all exercises by associated message ID.
        """
        exercises = (
            Exercise.objects
            .filter(message_id=message_id)
            .prefetch_related("files")
        )

        if not exercises.exists():
            return Response({"error": "Exercise not found."}, status=status.HTTP_404_NOT_FOUND)

        # Return a mapping keyed by exercise_id so the frontend can look up exercises easily.
        data = {
            exercise.id: {
                # Exercise fields
                "id": exercise.id,
                "message": exercise.message_id,
                "correctness": exercise.correctness,
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
            message = Message.objects.get(id=message_id)
        except Message.DoesNotExist:
            return Response({"error": "Message not found."}, status=status.HTTP_404_NOT_FOUND)        
        llm_service = LLMService(None, request.user.id)
        response = llm_service.generate_exercise(
            ability_level=request.data.get("ability_level", "beginner"),
            message=message,
            exercise_files_text=json.dumps([
                list(exercise.files.values('filename', 'text', 'code')) 
                for exercise in message.exercises.all()
            ]),
        )
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
        if not input_serializer.is_valid():
            return Response(input_serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        validated_data = input_serializer.validated_data

        try:
            message_obj = Message.objects.get(id=validated_data["message_id"])
            full_message = message_obj.json
        except Message.DoesNotExist:
            return Response(
                {"error": f"Message with id {validated_data['message_id']} does not exist."},
                status=status.HTTP_404_NOT_FOUND
            )

        user_submissions = validated_data["user_submissions"]
        exercise_file_ids = validated_data["exercise_file_ids"]

        if not user_submissions or not exercise_file_ids or len(user_submissions) != len(exercise_file_ids):
            return Response(
                {"error": "Invalid input data."}, status=status.HTTP_400_BAD_REQUEST
            )

        llm_service = LLMService(None, request.user.id)
        try:
            response = llm_service.mark_exercise(
                ability_level=validated_data["ability_level"],
                message=full_message,
                exercise_id=validated_data["exercise_id"],
                user_submission=json.dumps(user_submissions),
            )
        except Exception as e:
            return Response(
                {"error": f"Failed to mark exercise: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

        for i in range(len(user_submissions)):
            save_result = llm_service.save_user_submission(
                exercise_file_id=exercise_file_ids[i],
                user_submission=user_submissions[i],
            )
            if isinstance(save_result, dict) and save_result.get("status") == "error":
                return Response(
                    {"error": f"Failed to save user submission for file id {exercise_file_ids[i]}: {save_result.get('message', '')}"},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR
                )

        print("LLMService response for marking user data:", response)
        return Response(response, status=status.HTTP_200_OK)


class ExerciseSaveAPIView(APIView):
    """
    API view to save user submissions for exercises.
    """
    def post(self, request):
        """
        Save the user's submission for a specific exercise. Expects a list of submissions and corresponding list of exercise file IDs.
        """
        user_submissions = request.data.get("user_submissions", None)
        exercise_file_ids = request.data.get("exercise_file_ids", None)
        if not user_submissions or not exercise_file_ids or len(user_submissions) != len(exercise_file_ids):
            return Response(
                {"error": "Invalid input data."}, status=status.HTTP_400_BAD_REQUEST
            )
        llm_service = LLMService(None, request.user.id)
        for i in range(len(user_submissions)):
            llm_service.save_user_submission(
                exercise_file_id=exercise_file_ids[i],
                user_submission=user_submissions[i],
            )
        return Response(
            {"status": "User submissions saved."}, status=status.HTTP_200_OK
        )


class ExerciseBookmarkAPIView(APIView):
    """
    API view to bookmark an exercise.
    """
    def get(self, request):
        """
        Get all bookmarked exercises for a user.
        """
        exercises = (
            Exercise.objects
            .filter(bookmarked=True)
            .select_related("message__conversation")
            .values(
                "id",
                "message_id",
                "message__conversation_id",
                "correctness",
                "bookmarked",
                "title",
                "tags",
            )
        )

        return Response(list(exercises), status=status.HTTP_200_OK)


    def post(self, request, exercise_id):
        """
        Toggle the bookmark status of an exercise.
        """
        try:
            exercise = Exercise.objects.get(id=exercise_id)
        except Exercise.DoesNotExist:
            return Response(
                {"error": f"Exercise with id {exercise_id} does not exist."},
                status=status.HTTP_404_NOT_FOUND
            )
        try:
            exercise.bookmarked = not exercise.bookmarked
            exercise.save(update_fields=["bookmarked"])
            return Response(model_to_dict(exercise), status=status.HTTP_200_OK)
        except Exception as e:
            return Response(
                {"error": f"Failed to update bookmark status: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
