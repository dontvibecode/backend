from django.test import TestCase

from chatbot.models import User
from chatbot.services.user import InsufficientTokensError, UserService


class TokenAccountingTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(
            username="token-user",
            email="tokens@example.com",
            method="google",
            token_limit=100,
            token_used=0,
        )

    def test_charge_increments_usage_and_returns_remaining_balance(self):
        remaining = UserService.consume_tokens(self.user.id, 35)

        self.user.refresh_from_db()
        self.assertEqual(self.user.token_used, 35)
        self.assertEqual(remaining, 65)

    def test_charge_can_use_exact_remaining_balance(self):
        remaining = UserService.consume_tokens(self.user.id, 100)

        self.user.refresh_from_db()
        self.assertEqual(self.user.token_used, 100)
        self.assertEqual(remaining, 0)

    def test_charge_that_exceeds_limit_is_rejected_without_mutation(self):
        with self.assertRaises(InsufficientTokensError):
            UserService.consume_tokens(self.user.id, 101)

        self.user.refresh_from_db()
        self.assertEqual(self.user.token_used, 0)

    def test_two_charges_cannot_spend_the_same_remaining_allowance(self):
        UserService.consume_tokens(self.user.id, 70)

        with self.assertRaises(InsufficientTokensError):
            UserService.consume_tokens(self.user.id, 70)

        self.user.refresh_from_db()
        self.assertEqual(self.user.token_used, 70)
