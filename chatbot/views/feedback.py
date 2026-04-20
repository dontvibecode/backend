from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from ..services.feedback import FeedbackService


class FeedbackView(APIView):
    authentication_classes = []
    permission_classes = []

    def __init__(self):
        self.service = FeedbackService()

    def post(self, request):
        email = request.data.get("email")
        if not email:
            return Response({"error": "Email is required"}, status=status.HTTP_400_BAD_REQUEST)

        message = request.data.get("message", "")
        if not message:
            return Response({"error": "Message is required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            result = self.service.send_user_feedback(email=email, message=message)
            return Response(result, status=status.HTTP_200_OK)
        except ValueError as e:
            return Response({"error": str(e)}, status=status.HTTP_429_TOO_MANY_REQUESTS)
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)
