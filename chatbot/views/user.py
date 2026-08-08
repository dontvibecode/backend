from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from ..models import User
from ..services.user import UserService

from ..serializers import UserProfileUpdateSerializer, UserResponseSerializer, UserSerializer


class UserWithPreferencesAPIView(APIView):
    """
    API endpoint for retrieving and updating a user along with preferences.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.service = UserService()

    def get(self, request, email):
        """
        Fetch the authenticated user and their preferences.

        `email` is part of the URL for the frontend's benefit; the identity
        used is always the authenticated one.
        """
        user = self.service.get_user_by_email_with_preferences(email=request.user.email)
        if not user:
            return Response({"error": "User not found"}, status=status.HTTP_404_NOT_FOUND)

        output_serializer = UserResponseSerializer(user)
        return Response(output_serializer.data, status=status.HTTP_200_OK)

    def post(self, request):
        input_serializer = UserSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)

        validated_data = input_serializer.validated_data
        try:
            user = self.service.create_user(
                username=validated_data["username"],
                email=validated_data["email"],
                method=validated_data.get("method"),
            )
        except ValueError as ve:
            return Response({"error": str(ve)}, status=status.HTTP_400_BAD_REQUEST)

        user_with_preferences = User.objects.select_related("preferences").get(id=user.id)
        output_serializer = UserResponseSerializer(user_with_preferences)
        return Response(output_serializer.data, status=status.HTTP_201_CREATED)

    def put(self, request, email):
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
