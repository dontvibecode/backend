from datetime import timedelta

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from chatbot.models import Conversation, Message, User
from chatbot.services.lesson import encode_cursor, update_search_vector
from chatbot.views.lesson import LessonShareAPIView, SharedLessonListAPIView


class SharedLessonTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.user = User.objects.create(email="owner@example.com", username="owner")
        self.user.is_authenticated = True
        self.conversation = Conversation.objects.create(user=self.user, title="Chat")
        self.now = timezone.now()

    def make_lesson(self, title, minutes_ago=0, shared=True, tags=None):
        lesson = Message.objects.create(
            from_user=False,
            conversation=self.conversation,
            json={
                "lesson_title": title,
                "breakdown": f"A short summary about {title}.",
                "tags": tags or [],
            },
            shared=shared,
            shared_by=self.user if shared else None,
            shared_at=self.now - timedelta(minutes=minutes_ago) if shared else None,
        )
        update_search_vector(lesson.id, shared)
        return lesson

    def list_lessons(self, **params):
        request = self.factory.get("/lessons/", params)
        force_authenticate(request, user=self.user)
        return SharedLessonListAPIView.as_view()(request)

    def test_lists_only_shared_lessons_newest_first(self):
        older = self.make_lesson("Older", minutes_ago=10)
        newer = self.make_lesson("Newer", minutes_ago=1)
        self.make_lesson("Private", shared=False)

        response = self.list_lessons()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [lesson["id"] for lesson in response.data["results"]], [newer.id, older.id]
        )
        self.assertEqual(response.data["results"][0]["title"], "Newer")
        self.assertEqual(response.data["results"][0]["author"], "owner")
        self.assertIsNone(response.data["next_cursor"])

    def test_search_matches_word_prefixes_in_title_and_tags(self):
        recursion = self.make_lesson("Understanding Recursion")
        tagged = self.make_lesson("Closures", tags=["javascript"])
        self.make_lesson("SQL Joins")

        by_title = self.list_lessons(q="recur")
        by_tag = self.list_lessons(q="javasc")

        self.assertEqual([l["id"] for l in by_title.data["results"]], [recursion.id])
        self.assertEqual([l["id"] for l in by_tag.data["results"]], [tagged.id])

    def test_query_with_only_punctuation_returns_everything(self):
        self.make_lesson("Anything")

        response = self.list_lessons(q="&|!:*")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["results"]), 1)

    def test_cursor_pages_through_without_gaps_or_repeats(self):
        lessons = [self.make_lesson(f"Lesson {i}", minutes_ago=i) for i in range(5)]

        first = self.list_lessons(limit=2)
        second = self.list_lessons(limit=2, cursor=first.data["next_cursor"])
        third = self.list_lessons(limit=2, cursor=second.data["next_cursor"])

        seen = [
            lesson["id"]
            for page in (first, second, third)
            for lesson in page.data["results"]
        ]
        self.assertEqual(seen, [lesson.id for lesson in lessons])
        self.assertIsNone(third.data["next_cursor"])

    def test_lessons_shared_at_the_same_moment_are_not_skipped(self):
        same_time = [self.make_lesson(f"Tied {i}") for i in range(3)]

        first = self.list_lessons(limit=2)
        second = self.list_lessons(limit=2, cursor=first.data["next_cursor"])

        seen = [l["id"] for l in first.data["results"] + second.data["results"]]
        self.assertEqual(sorted(seen), sorted(lesson.id for lesson in same_time))

    def test_rejects_a_malformed_cursor(self):
        response = self.list_lessons(cursor="not-a-cursor")

        self.assertEqual(response.status_code, 400)

    def test_cursor_round_trips(self):
        lesson = self.make_lesson("Only")

        response = self.list_lessons(cursor=encode_cursor(lesson.shared_at, lesson.id + 1))

        self.assertEqual([l["id"] for l in response.data["results"]], [lesson.id])

    def test_sharing_makes_a_lesson_searchable_and_unsharing_removes_it(self):
        lesson = self.make_lesson("Binary Search", shared=False)

        def toggle():
            request = self.factory.post(f"/lessons/share/{lesson.id}/")
            force_authenticate(request, user=self.user)
            return LessonShareAPIView.as_view()(request, lesson_id=lesson.id)

        toggle()
        found = self.list_lessons(q="binary")
        toggle()
        gone = self.list_lessons(q="binary")

        self.assertEqual([l["id"] for l in found.data["results"]], [lesson.id])
        self.assertEqual(gone.data["results"], [])
