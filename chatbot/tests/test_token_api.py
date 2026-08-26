from django.test import TestCase

from chatbot.views.token import TokenUsageAPIView
from chatbot.models import User
from rest_framework.test import APIRequestFactory, force_authenticate


class TokenApiTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.email = "test@example.com"
        self.user = User.objects.create(email=self.email, username="testuser")
        self.user.is_authenticated = True

    def test_existing_user_with_no_usage_returns_empty_list(self):
        request = self.factory.get("/token/usage/")
        force_authenticate(request, user=self.user)
        view = TokenUsageAPIView.as_view()
        response = view(request)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, [])

    def test_usage_is_scoped_to_authenticated_user(self):
        another_email = "another@example.com"
        another_user = User.objects.create(email=another_email, username="anotheruser")
        own_usage = self.user.tokenusage_set.create(token_used=100, tokens_remaining=60000, action="router")
        another_user.tokenusage_set.create(token_used=100, tokens_remaining=90000, action="router")
        request = self.factory.get("/token/usage/")
        force_authenticate(request, user=self.user)
        view = TokenUsageAPIView.as_view()
        response = view(request)
        self.assertEqual(response.status_code, 200)
        returned_ids = [usage["id"] for usage in response.data]
        self.assertEqual(returned_ids, [own_usage.id])
