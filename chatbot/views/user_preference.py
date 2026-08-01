from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from ..services.user import UserService

from ..serializers import PreferencesSerializer


class PreferencesAPIView(APIView):
    """
    API endpoint for reading the authenticated user's preferences.
    """

    def get(self, request, pk=None):
        """
        `pk` is accepted because it is part of the URL, but the preferences
        returned are always the authenticated user's own.
        """
        preferences = UserService.get_preferences_by_user_id(user_id=request.user.id)
        if not preferences:
            return Response(
                {"error": "User preferences not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        output_serializer = PreferencesSerializer(preferences)
        return Response(output_serializer.data, status=status.HTTP_200_OK)
