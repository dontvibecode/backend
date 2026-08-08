from django.test import TestCase
from django.utils import timezone
from datetime import timedelta

from ..services.user import BILLING_PERIOD, FREE_TOKEN_LIMIT, UserService

from ..models import User


class MembershipTests(TestCase):
    def test_nothing_changes_before_refill_date(self):
        tokens_before = 50_000
        user = User.objects.create(
            username="membership-user",
            email="membership@example.com",
            method="google",
            membership="free",
            token_used=tokens_before,
            membership_updated_at=timezone.now() - BILLING_PERIOD + timedelta(days=1),
        )
        changed = UserService().reconcile_membership(user)
        user.refresh_from_db()

        self.assertEqual(user.token_used, tokens_before)
        self.assertFalse(changed)

    def test_token_reset_after_one_billing_period(self):
        tokens_before = 50_000
        user = User.objects.create(
            username="membership-user",
            email="membership@example.com",
            method="google",
            membership="free",
            token_used=tokens_before,
            membership_updated_at=timezone.now() - BILLING_PERIOD - timedelta(days=1),
        )
        changed = UserService().reconcile_membership(user)
        user.refresh_from_db()

        self.assertEqual(user.token_used, 0)
        self.assertEqual(user.token_limit, FREE_TOKEN_LIMIT)
        self.assertTrue(changed)

    def test_pro_remains_pro_before_expiry(self):
        tokens_before = 50_000
        user = User.objects.create(
            username="membership-user",
            email="membership@example.com",
            method="google",
            membership="pro",
            token_used=tokens_before,
            membership_expires_at=timezone.now() + timedelta(days=1),
            subscription_active=True,
        )
        changed = UserService().reconcile_membership(user)
        user.refresh_from_db()

        self.assertEqual(user.token_used, tokens_before)
        self.assertEqual(user.membership, "pro")
        self.assertFalse(changed)
        self.assertTrue(user.subscription_active)

    def test_expired_active_subscription_waits_for_webhook(self):
        tokens_before = 50_000
        user = User.objects.create(
            username="membership-user",
            email="membership@example.com",
            method="google",
            membership="pro",
            token_used=tokens_before,
            membership_expires_at=timezone.now() - timedelta(days=1),
            subscription_active=True,
        )
        changed = UserService().reconcile_membership(user)
        user.refresh_from_db()

        self.assertEqual(user.token_used, tokens_before)
        self.assertEqual(user.membership, "pro")
        self.assertFalse(changed)
        self.assertTrue(user.subscription_active)

    def test_expired_pro_downgrades_to_free(self):
        tokens_before = 50_000
        user = User.objects.create(
            username="membership-user",
            email="membership@example.com",
            method="google",
            membership="pro",
            token_used=tokens_before,
            membership_expires_at=timezone.now() - timedelta(days=1),
            subscription_active=False,
        )
        changed = UserService().reconcile_membership(user)
        user.refresh_from_db()

        self.assertEqual(user.token_used, 0)
        self.assertEqual(user.token_limit, FREE_TOKEN_LIMIT)
        self.assertEqual(user.membership, "free")
        self.assertTrue(changed)
        self.assertIsNone(user.membership_expires_at)
        self.assertIsNone(user.subscription_active)
