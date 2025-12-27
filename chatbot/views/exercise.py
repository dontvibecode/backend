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

    def get(self, request, message_id):
        """
        Retrieve all exercises by associated message ID.
        """
        try:
            exercises = list(Exercise.objects.filter(message=message_id))
            data = {exercise.id : list(exercise.files.all().values()) for exercise in exercises}
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
            message=message,
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
        full_message = Message.objects.get(id=validated_data["message_id"]).json
        user_submissions = validated_data["user_submissions"]
        exercise_file_ids = validated_data["exercise_file_ids"]
        response = llm_service.mark_exercise(
            ability_level=validated_data["ability_level"],
            message=full_message,
            exercise_id=validated_data["exercise_id"],
            user_submission=json.dumps(user_submissions),
        )
        if not user_submissions or not exercise_file_ids or len(user_submissions) != len(exercise_file_ids):
            return Response(
                {"error": "Invalid input data."}, status=status.HTTP_400_BAD_REQUEST
            )
        for i in range(len(user_submissions)):
            llm_service.save_user_submission(
                exercise_file_id=exercise_file_ids[i],
                user_submission=user_submissions[i],
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
        user_submissions = request.data.get("user_submissions")
        exercise_file_ids = request.data.get("exercise_file_ids")
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
