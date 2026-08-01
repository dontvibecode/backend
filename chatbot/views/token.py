from chatbot.serializers import TokenUsageSerializer
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
        data =self.user_service.get_token_by_user_email(email=request.user.email)
        if not data:
            return Response({"error": "User not found"}, status=status.HTTP_404_NOT_FOUND)
        return Response(data, status=status.HTTP_200_OK)


class TokenUsageAPIView(APIView):
    """
    API endpoint for managing token usage.
    """
    def __init__(self):
        self.user_service = UserService()

    def get(self, request, email):
        data = self.user_service.get_token_usage_by_user_email(email=request.user.email)
        if not data:
            return Response({"error": "User not found"}, status=status.HTTP_404_NOT_FOUND)
        output_serializer = TokenUsageSerializer(data, many=True)
        return Response(output_serializer.data, status=status.HTTP_200_OK)