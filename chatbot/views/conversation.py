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
    
    def post(self, request, pk):
        try:
            conversation = Conversation.objects.get(id=pk)
            conversation.pinned = not conversation.pinned
            conversation.save()
            output_serializer = ConversationSerializer(conversation)
            return Response(
                output_serializer.data,
                status=status.HTTP_200_OK,
            )
        except Conversation.DoesNotExist:
            return Response(
                {"error": "Conversation not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
    
    def delete(self, request, pk):
        try:
            conversation = Conversation.objects.get(id=pk)
            conversation.delete()
            return Response(
                {"message": "Conversation deleted successfully."},
                status=status.HTTP_200_OK,
            )
        except Conversation.DoesNotExist:
            return Response(
                {"error": "Conversation not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
