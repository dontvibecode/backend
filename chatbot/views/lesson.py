from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from chatbot.models import Message
from chatbot.services.lesson import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    MAX_QUERY_LENGTH,
    InvalidCursor,
    decode_cursor,
    search_shared_lessons,
    update_search_vector,
)


class SharedLessonListAPIView(APIView):
    def get(self, request):
        """
        One page of lessons shared by any user, optionally filtered by `q`.
        Pass back `next_cursor` as `cursor` to get the following page.
        """
        query = request.query_params.get("q", "").strip()[:MAX_QUERY_LENGTH]

        try:
            limit = int(request.query_params.get("limit", DEFAULT_PAGE_SIZE))
        except ValueError:
            limit = DEFAULT_PAGE_SIZE
        limit = max(1, min(limit, MAX_PAGE_SIZE))

        raw_cursor = request.query_params.get("cursor")
        try:
            cursor = decode_cursor(raw_cursor) if raw_cursor else None
        except InvalidCursor:
            return Response(
                {"error": "Invalid cursor"}, status=status.HTTP_400_BAD_REQUEST
            )

        lessons, next_cursor = search_shared_lessons(query, cursor, limit)
        return Response(
            {"results": lessons, "next_cursor": next_cursor},
            status=status.HTTP_200_OK,
        )


class LessonShareAPIView(APIView):
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
        update_search_vector(lesson.id, lesson.shared)
        return Response(
            status=status.HTTP_200_OK,
            data={"lesson_id": lesson.id, "shared": lesson.shared},
        )
