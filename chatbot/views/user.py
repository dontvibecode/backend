from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from ..services.user import UserService

from ..serializers import UserProfileUpdateSerializer, UserResponseSerializer


class UserWithPreferencesAPIView(APIView):
    """
    API endpoint for retrieving and updating a user along with preferences.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.service = UserService()

    def get(self, request):
        """Fetch the authenticated user and their preferences."""
        user = self.service.get_user_by_email_with_preferences(email=request.user.email)
        if not user:
            return Response({"error": "User not found"}, status=status.HTTP_404_NOT_FOUND)

        output_serializer = UserResponseSerializer(user)
        return Response(output_serializer.data, status=status.HTTP_200_OK)

    def put(self, request):
        user_instance = self.service.get_user_by_email(email=request.user.email)
        if not user_instance:
            return Response({"error": "User not found"}, status=status.HTTP_404_NOT_FOUND)

        input_serializer = UserProfileUpdateSerializer(
            data=request.data,
            partial=True,  # Allow partial updates
        )
        input_serializer.is_valid(raise_exception=True)

        updated_user = self.service.update_profile(
            user=user_instance,
            username=input_serializer.validated_data.get("username"),
            preferences=input_serializer.validated_data.get("preferences"),
        )
        return Response(UserResponseSerializer(updated_user).data, status=status.HTTP_200_OK)
