from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from ..services.user import UserService

class TokenAPIView(APIView):
    """
    API endpoint for managing tokens.
    """
    def __init__(self):
        self.user_service = UserService()

    def get(self, request, email):
        data =self.user_service.get_token_by_user_email(email=email)
        if not data:
            return Response({"error": "User not found"}, status=status.HTTP_404_NOT_FOUND)
        return Response(data, status=status.HTTP_200_OK)