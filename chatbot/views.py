import json
from django.shortcuts import render
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from chatbot.conversation_service import ConversationService
from chatbot.models import Conversation

from .llm_service import LLMService
from .serializers import ConversationSerializer, MessageSerializer


class ConversationAPIView(APIView):
    """
    API endpoint for managing conversations.
    """

    def get(self, request, *args, **kwargs):
        conversations = Conversation.objects.all().order_by("-created_at")
        output_serializer = ConversationSerializer(conversations, many=True)
        return Response(output_serializer.data, status=status.HTTP_200_OK)


class ChatAPIView(APIView):
    """
    API endpoint for the chat interface.
    """

    def get(self, request, *args, **kwargs):
        conversation_id = kwargs.get("conversation_id")
        try:
            messages = ConversationService.get_messages_from_conversation(
                conversation_id=conversation_id
            )
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
        if validated_data["conversation"]:
            llm_service = LLMService(int(validated_data["conversation"].id))
        else:
            llm_service = LLMService(None)

        try:
            print("Validated data:", validated_data)
            message = llm_service.respond(
                user_input=validated_data["text"],
                experience_level=validated_data["experience_level"],
            )
            print("Message from LLMService:", message)
            output_serializer = MessageSerializer(message)
            print("Output serializer data:", output_serializer.data)
            response = Response(
                data=output_serializer.data, status=status.HTTP_201_CREATED
            )
            response["Access-Control-Allow-Origin"] = "*"
            print("Response:", response)
            return response
        except Exception as e:
            return Response(
                {"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
