from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from chatbot.models import Conversation

from ..serializers import ConversationSerializer


class ConversationAPIView(APIView):
    """
    API endpoint for managing conversations.
    """

    def get(self, request, email):
        conversations = Conversation.objects.filter(user__email=email).order_by(
            "-created_at"
        )
        output_serializer = ConversationSerializer(conversations, many=True)
        return Response(output_serializer.data, status=status.HTTP_200_OK)
