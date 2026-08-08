from unittest.mock import patch

from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from chatbot.views.chat import ChatAPIView
from chatbot.models import Preferences, User
from chatbot.serializers import UserProfileUpdateSerializer
from chatbot.services.user import UserService
from chatbot.views.user import UserWithPreferencesAPIView


class UserProfileTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(
            username="profile-user",
            email="profile@example.com",
            method="google",
            membership="free",
        )
        Preferences.objects.create(user=self.user)

    def test_update_serializer_only_accepts_profile_fields(self):
        serializer = UserProfileUpdateSerializer(
            data={
                "username": "updated-name",
                "membership": "pro",
                "subscription_active": True,
                "token_limit": 999_999,
            }
        )

        serializer.is_valid(raise_exception=True)

        self.assertEqual(serializer.validated_data, {"username": "updated-name"})

    def test_profile_endpoint_updates_nested_preferences_not_membership(self):
        request = APIRequestFactory().put(
            "/user/profile@example.com/",
            {
                "username": "updated-name",
                "membership": "pro",
                "preferences": {
                    "theme": "dark",
                    "font_size": "large",
                },
            },
            format="json",
        )
        self.user.is_authenticated = True
        force_authenticate(request, user=self.user)

        response = UserWithPreferencesAPIView.as_view()(
            request, email=self.user.email
        )

        self.user.refresh_from_db()
        self.user.preferences.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.user.username, "updated-name")
        self.assertEqual(self.user.membership, "free")
        self.assertEqual(self.user.preferences.theme, "dark")
        self.assertEqual(self.user.preferences.font_size, "large")
        self.assertEqual(response.data["preferences"]["theme"], "dark")

    def test_profile_update_rolls_back_if_preferences_fail(self):
        service = UserService()

        with (
            patch.object(Preferences, "save", side_effect=RuntimeError("save failed")),
            self.assertRaises(RuntimeError),
        ):
            service.update_profile(
                self.user,
                username="should-roll-back",
                preferences={"theme": "dark"},
            )

        self.user.refresh_from_db()
        self.assertEqual(self.user.username, "profile-user")

    def test_user_cannot_retrieve_another_users_messages(self):
        other_user = User.objects.create(
            username="other-user",
            email="other@example.com",
            method="google",
            membership="free",
        )
        other_conversation = other_user.conversation_set.create(
            user=other_user,
            title="Test Conversation",
        )
        other_conversation.message_set.create(
            text="secret message",
        )

        request = APIRequestFactory().get(
            "/conversations/messages/",
        )
        self.user.is_authenticated = True
        force_authenticate(request, user=self.user)

        response = ChatAPIView.as_view()(request, pk=other_conversation.id)
        self.assertEqual(response.status_code, 404)
        self.assertTrue("secret message", str(response.data))
