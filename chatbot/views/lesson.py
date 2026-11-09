from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from chatbot.models import Message
from chatbot.serializers import MessageSerializer


class LessonAPIView(APIView):
    def get(self, request):
        """
        Fetch all shared lessons across all users.
        """
        lessons = Message.objects.filter(shared=True).order_by("-created_at")
        serializer = MessageSerializer(lessons, many=True)
        return Response(status=status.HTTP_200_OK, data={"lessons": serializer.data})

    def post(self, request, lesson_id):
        try:
            lesson = Message.objects.select_related("conversation").get(id=lesson_id)
        except Message.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)

        if not lesson.conversation or lesson.conversation.user != request.user:
            return Response(
                status=status.HTTP_403_FORBIDDEN,
                data={"error": "You are not the owner of this lesson"},
            )

        if lesson.shared:
            lesson.shared = False
            lesson.shared_by = None
            lesson.shared_at = None
        else:
            lesson.shared = True
            lesson.shared_by = request.user
            lesson.shared_at = timezone.now()

        lesson.save(update_fields=["shared", "shared_by", "shared_at"])
        return Response(
            status=status.HTTP_200_OK,
            data={"lesson_id": lesson.id, "shared": lesson.shared},
        )

