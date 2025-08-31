import json
from django.shortcuts import render
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from chatbot.models import Conversation, Message

from .llm_service import LLMService
from .serializers import MessageSerializer, serializers


class ChatAPIView(APIView):
    """
    API endpoint for the chat interface.
    """

    def get(self, request, *args, **kwargs):
        conversation_id = kwargs.get("conversation_id")
        try:
            conversation = Conversation.objects.get(id=conversation_id)
            messages = conversation.message_set.all().order_by("created_at")
            output_serializer = MessageSerializer(messages, many=True)
            return Response(output_serializer.data, status=status.HTTP_200_OK)
        except:
            return Response(
                {"error": "Conversation not found"}, status=status.HTTP_404_NOT_FOUND
            )

    def post(self, request, *args, **kwargs):
        # Handle incoming user messages
        input_serializer = MessageSerializer(data=request.data)
        if not input_serializer.is_valid():
            return Response(input_serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        validated_data = input_serializer.validated_data
        llm_service = LLMService(int(validated_data["conversation"].id))

        try:
            message = llm_service.respond(
                user_input=validated_data["text"],
                experience_level="senior",  # TODO: remove hardcoding
            )
            output_serializer = MessageSerializer(message)
            response = Response(
                data=output_serializer.data, status=status.HTTP_201_CREATED
            )
            response["Access-Control-Allow-Origin"] = "*"
            return response
        except Exception as e:
            return Response(
                {"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
