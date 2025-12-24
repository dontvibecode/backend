import json
from ..models import Exercise, Message
from ..serializers import ExerciseSubmissionSerializer
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from ..services.llm import LLMService


class ExerciseAPIView(APIView):
    """
    API view to handle exercise-related requests.
    """

    def get(self, request, exercise_id):
        """
        Retrieve an exercise by its ID.
        """
        try:
            exercise = Exercise.objects.get(id=exercise_id)
            data = {
                "id": exercise.id,
                "files": exercise.files.values("filename", "text", "code"),
            }
            return Response(data, status=status.HTTP_200_OK)
        except Exercise.DoesNotExist:
            return Response({"error": "Exercise not found."}, status=status.HTTP_404_NOT_FOUND)
        
    def post(self, request, message_id):
        """
        Create a new exercise.
        """
        message = Message.objects.get(id=message_id)
        ability_level = request.data.get("ability_level", "beginner")
        # Convert the QuerySet to a list of dictionaries immediately
        exercise_files_text = json.dumps([
            list(exercise.files.values('filename', 'text', 'code')) 
            for exercise in message.exercises.all()
        ])
        llm_service = LLMService(None, request.user.id)
        response = llm_service.generate_exercise(
            ability_level=ability_level,
            full_message=json.dumps(message.json),
            exercise_files_text=exercise_files_text,
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
        llm_service = LLMService(None, request.user.id)
        validated_data = input_serializer.validated_data
        print("Validated data for exercise submission:", validated_data)
        response = llm_service.mark_exercise(
            ability_level=validated_data["ability_level"],
            message=validated_data["message"],
            original_exercise=validated_data["original_exercise"],
            user_submission=validated_data["user_submission"],
        )
        print("LLMService response for marking user data:", response)
        return Response(response, status=status.HTTP_200_OK)
