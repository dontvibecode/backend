from django.http import StreamingHttpResponse
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
import json

from ..services.conversation import ConversationService

from ..services.llm import LLMService
from ..serializers import MessageSerializer



class ChatAPIView(APIView):
    """
    API endpoint for the chat interface.
    """

    def get(self, request, pk):
        try:
            messages = ConversationService.get_messages_from_conversation(
                conversation_id=pk
            )
            output_serializer = MessageSerializer(messages, many=True)
            return Response(output_serializer.data, status=status.HTTP_200_OK)
        except:
            return Response(
                {"error": "Conversation not found"}, status=status.HTTP_404_NOT_FOUND
            )

    def post(self, request):
        # Handle incoming user messages
        input_serializer = MessageSerializer(data=request.data)
        if not input_serializer.is_valid():
            return Response(input_serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        validated_data = input_serializer.validated_data
        if validated_data["conversation"]:
            llm_service = LLMService(int(validated_data["conversation"].id), user_id=request.user.id)
        else:
            llm_service = LLMService(None, user_id=request.user.id)

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

class ChatStreamAPIView(APIView):
    """
    Streaming endpoint that sends thought summaries and progress in real-time.
    """

    def post(self, request):
        """
        Handle POST requests for streaming chat responses.
        """
        # Validate input
        input_serializer = MessageSerializer(data=request.data)
        if not input_serializer.is_valid():
            return Response(input_serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        validated_data = input_serializer.validated_data

        # Create LLMService
        if validated_data["conversation"]:
            llm_service = LLMService(
                int(validated_data["conversation"].id),
                user_id=request.user.id
            )
        else:
            llm_service = LLMService(None, user_id=request.user.id)

        def event_stream():
            """
            Generator that yields SSE-formatted events.
            """
            try:
                for event in llm_service.respond_streaming(
                    user_input=validated_data["text"],
                    experience_level=validated_data["experience_level"],
                ):
                    # SSE format: "data: {json}\n\n"
                    yield f"data: {json.dumps(event)}\n\n"
            except Exception as e:
                yield f"data: {json.dumps({'stage': 'error', 'data': str(e)})}\n\n"

        response = StreamingHttpResponse(
            event_stream(),
            content_type='text/event-stream'
        )
        response['Cache-Control'] = 'no-cache'
        response['X-Accel-Buffering'] = 'no'  # Disable nginx buffering
        return response
