import json

from django.http import StreamingHttpResponse
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from ..models import Conversation
from ..services.conversation import ConversationService
from ..services.llm import LLMService
from ..serializers import MessageSerializer


class ChatAPIView(APIView):
    """
    Read access to the messages of a conversation.
    """

    def get(self, request, pk):
        try:
            messages = ConversationService.get_messages_from_conversation(
                conversation_id=pk, user_id=request.user.id
            )
        except Conversation.DoesNotExist:
            return Response(
                {"error": "Conversation not found"}, status=status.HTTP_404_NOT_FOUND
            )

        output_serializer = MessageSerializer(messages, many=True)
        return Response(output_serializer.data, status=status.HTTP_200_OK)


class ChatStreamAPIView(APIView):
    """
    Streaming endpoint that sends thought summaries and progress in real-time.
    """

    def post(self, request):
        """
        Handle POST requests for streaming chat responses.
        """
        # Validate input
        input_serializer = MessageSerializer(
            data=request.data, context={"request": request}
        )
        if not input_serializer.is_valid():
            return Response(input_serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        validated_data = input_serializer.validated_data
        conversation = validated_data["conversation"]
        if conversation and ConversationService.conversation_message_count_limit(
            user_id=request.user.id, conversation_id=conversation.id
        ):
            return Response(
                {"warning": "Conversation message count limit reached"},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        llm_service = LLMService(
            int(conversation.id) if conversation else None, user_id=request.user.id
        )

        # Pre-flight affordability check. The router sees the history and the
        # instructor sees a short prepared_context, so the history plus a small
        # allowance approximates what this turn will cost.
        estimated_dynamic_content = (
            str(llm_service.get_conversation_history())
            + validated_data["text"]
            + " " * 500
        )
        if not llm_service.has_enough_tokens(
            user_id=request.user.id, prompt=estimated_dynamic_content
        ):
            return Response(
                {"warning": "Insufficient tokens"},
                status=status.HTTP_402_PAYMENT_REQUIRED,
            )

        def event_stream():
            """
            Generator that yields SSE-formatted events.
            """
            try:
                for event in llm_service.respond_streaming(
                    user_id=request.user.id,
                    user_input=validated_data["text"],
                    experience_level=validated_data["experience_level"],
                ):
                    # SSE format: "data: {json}\n\n"
                    yield f"data: {json.dumps(event)}\n\n"
            except Exception as e:
                yield f"data: {json.dumps({'stage': 'error', 'data': str(e)})}\n\n"

        response = StreamingHttpResponse(
            event_stream(), content_type="text/event-stream"
        )
        # NOTE: kept because CORS_ALLOWED_ORIGINS only lists localhost, so this
        # header is what makes the deployed frontend work. Remove it once the
        # real frontend origin is configured in settings.
        response["Access-Control-Allow-Origin"] = "*"
        response["Cache-Control"] = "no-cache"
        response["X-Accel-Buffering"] = "no"  # Disable nginx buffering
        return response
