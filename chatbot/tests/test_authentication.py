import os
from types import SimpleNamespace
from django.test import SimpleTestCase
from unittest.mock import ANY, patch

from rest_framework.exceptions import AuthenticationFailed

from chatbot.authentication import GoogleIDTokenAuthentication
from rest_framework.test import APIRequestFactory


class GoogleAuthenticationTests(SimpleTestCase):
    def setUp(self):
        self.authenticator = GoogleIDTokenAuthentication()
        self.factory = APIRequestFactory()

    def test_request_without_bearer_token_is_not_authenticated(self):
        request = self.factory.get("/")
        result = self.authenticator.authenticate(request)
        self.assertIsNone(result)

    def test_valid_google_token_returns_local_user(self):
        request = self.factory.get("/", HTTP_AUTHORIZATION="Bearer valid-token")

        with (
            patch(
                "chatbot.authentication.id_token.verify_oauth2_token",
                return_value={"email": "test@example.com", "name": "Test User"},
            ) as verify_mock,
            patch("chatbot.authentication.user_service") as service_mock,
            patch.dict(os.environ, {"GOOGLE_CLIENT_ID": "test-client-id"}),
        ):
            fake_user = SimpleNamespace(email="test@example.com", name="Test User")
            service_mock.get_or_create_google_user.return_value = fake_user
            result = self.authenticator.authenticate(request)
            self.assertIsNotNone(result)
            verify_mock.assert_called_once_with(
                "valid-token",
                ANY,
                "test-client-id",
            )
            service_mock.get_or_create_google_user.assert_called_once_with(
                email="test@example.com", name="Test User"
            )
            service_mock.reconcile_membership.assert_called_once_with(fake_user)

            self.assertEqual(result[0].email, "test@example.com")
            self.assertEqual(result[0].name, "Test User")
            self.assertTrue(result[0].is_authenticated)
            self.assertIsNone(result[1])

    def test_invalid_google_token_does_not_create_user(self):
        request = self.factory.get("/", HTTP_AUTHORIZATION="Bearer invalid-token")

        with (
            patch(
                "chatbot.authentication.id_token.verify_oauth2_token",
                side_effect=ValueError("Invalid token"),
            ),
            patch.dict(os.environ, {"GOOGLE_CLIENT_ID": "test-client-id"}),
            patch("chatbot.authentication.user_service") as service_mock,
        ):
            with self.assertRaises(AuthenticationFailed):
                self.authenticator.authenticate(request)
            service_mock.get_or_create_google_user.assert_not_called()
            service_mock.reconcile_membership.assert_not_called()
