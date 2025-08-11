from django.shortcuts import render
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from .llm_service import LLMService
from .serializers import MessageSerializer


class ChatAPIView(APIView):
    """
    API endpoint for the chat interface.
    """

    def post(self, request, *args, **kwargs):
        # Handle incoming user messages
        llm_service = LLMService()
        messsage_serialiser = MessageSerializer(data=request.data)
        if not messsage_serialiser.is_valid():
            return Response(
                messsage_serialiser.errors, status=status.HTTP_400_BAD_REQUEST
            )

        validated_data = messsage_serialiser.validated_data

        try:
            response_text = llm_service.respond(
                user_input=validated_data["prompt"],
                experience_level=validated_data["experience_level"],
            )
            return Response({"response": response_text}, status=status.HTTP_200_OK)
        except Exception as e:
            return Response(
                {"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
