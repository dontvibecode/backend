from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from ..models import Message
from ..serializers import SpeechRequestSerializer
from ..services.speech import (
    InvalidVoice,
    NothingToSpeak,
    SpeechDailyLimitReached,
    SpeechError,
    SpeechService,
    SpeechTooLong,
)

# Every other SpeechError means "try again later" and answers 503. All of them
# carry the prepared text, so the client can read it with the browser voice.
ERROR_STATUS = {
    SpeechDailyLimitReached: status.HTTP_429_TOO_MANY_REQUESTS,
    SpeechTooLong: status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
}


class SpeechVoicesAPIView(APIView):
    """The voices the picker may offer, and whether premium voices work at all."""

    def get(self, request):
        service = SpeechService()
        try:
            voices = service.get_voices()
        except SpeechError:
            voices = []

        offered_ids = [voice["id"] for voice in voices]
        return Response(
            {
                "available": bool(voices),
                "voices": voices,
                "default_voice_id": (
                    service.default_voice_id(offered_ids) if voices else None
                ),
            },
            status=status.HTTP_200_OK,
        )


class SpeechClipAPIView(APIView):
    """
    POST {"scope": "summary" | "full", "voice_id"?: str} returns a short-lived
    link to the message's narration, generating the audio first if needed.
    """

    throttle_scope = "speech"

    def post(self, request, message_id):
        serializer = SpeechRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            clip = SpeechService().get_clip(
                user=request.user,
                message_id=message_id,
                scope=serializer.validated_data["scope"],
                requested_voice_id=serializer.validated_data.get("voice_id") or None,
            )
        except Message.DoesNotExist:
            return Response(
                {"error": "Message not found."}, status=status.HTTP_404_NOT_FOUND
            )
        except (InvalidVoice, NothingToSpeak) as error:
            return Response({"error": str(error)}, status=status.HTTP_400_BAD_REQUEST)
        except SpeechError as error:
            return Response(
                {"error": error.code, "detail": error.detail, "units": error.units},
                status=ERROR_STATUS.get(
                    type(error), status.HTTP_503_SERVICE_UNAVAILABLE
                ),
            )

        return Response(clip, status=status.HTTP_200_OK)
