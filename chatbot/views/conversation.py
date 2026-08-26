from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.request import Request
from rest_framework import status
from django.db.models import Count, Q

from chatbot.models import Conversation

from ..serializers import ConversationSerializer


class ConversationAPIView(APIView):
    """
    API endpoint for managing conversations.
    """

    def get(self, request: Request):
        conversations = Conversation.objects.filter(user_id=request.user.id).annotate(
            exercises_count=Count('message__exercises'),
            exercises_almost_count=Count('message__exercises', filter=Q(message__exercises__correctness=1)),
            exercises_correct_count=Count('message__exercises', filter=Q(message__exercises__correctness=2)),
        ).order_by("-created_at")
        output_serializer = ConversationSerializer(conversations, many=True)
        return Response(output_serializer.data, status=status.HTTP_200_OK)
    
    def post(self, request, pk):
        try:
            conversation = Conversation.objects.get(id=pk, user_id=request.user.id)
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
            conversation = Conversation.objects.get(id=pk, user_id=request.user.id)
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
