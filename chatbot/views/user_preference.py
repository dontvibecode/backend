from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from ..services.user import UserService

from ..serializers import PreferencesSerializer


class PreferencesAPIView(APIView):
    """
    API endpoint for managing conversations.
    """

    def get(self, **kwargs):
        user_id = kwargs.get("user_id")
        try:
            preferences = UserService.get_preferences_by_user_id(user_id=user_id)
            if not preferences:
                return Response(
                    {"error": "User preferences not found"}, status=status.HTTP_404_NOT_FOUND
                )
            output_serializer = PreferencesSerializer(preferences)
            return Response(output_serializer.data, status=status.HTTP_200_OK)
        except:
            return Response(
                {"error": "Error when fetching user preferences"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
