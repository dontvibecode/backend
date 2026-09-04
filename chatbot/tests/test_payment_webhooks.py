from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import stripe
from django.test import SimpleTestCase, TestCase
from rest_framework.test import APIRequestFactory

from chatbot.models import ProcessedStripeEvent, User
from chatbot.services.payment import PaymentService
from chatbot.services.user import PRO_TIER
from chatbot.views.payment import StripeWebhookView


def stripe_event(event_id, event_type, obj):
    """
    Builds an event the way the SDK does.

    Tests used to hand the view plain dicts, which hid the fact that a real
    StripeObject has no `.get()`: every handler passed here and then raised
    AttributeError against live Stripe traffic.
    """
    return stripe.Event.construct_from(
        {"id": event_id, "type": event_type, "data": {"object": obj}},
        "test-api-key",
    )


class CreateSubscriptionTests(SimpleTestCase):
    def setUp(self):
        self.service = PaymentService()
        self.service.get_or_create_stripe_customer = MagicMock(
            return_value="cus_123"
        )

    @patch("chatbot.services.payment.stripe.Subscription.create")
    @patch("chatbot.services.payment.stripe.Subscription.list")
    def test_refuses_to_subscribe_an_active_subscriber_again(
        self, sub_list, sub_create
    ):
        sub_list.return_value = SimpleNamespace(
            data=[SimpleNamespace(id="sub_existing")]
        )

        with self.assertRaisesRegex(ValueError, "already active"):
            self.service.create_subscription(SimpleNamespace(id=1))

        sub_create.assert_not_called()

    @patch("chatbot.services.payment.stripe.Subscription.create")
    @patch("chatbot.services.payment.stripe.Subscription.list")
    def test_subscribes_when_none_is_active(self, sub_list, sub_create):
        sub_list.return_value = SimpleNamespace(data=[])
        sub_create.return_value = stripe.Subscription.construct_from(
            {
                "id": "sub_new",
                "status": "incomplete",
                "latest_invoice": {
                    "confirmation_secret": {"client_secret": "cs_test"}
                },
            },
            "test-api-key",
        )

        result = self.service.create_subscription(SimpleNamespace(id=1))

        sub_create.assert_called_once()
        self.assertEqual(result["client_secret"], "cs_test")


class SubscriptionDeletedTests(SimpleTestCase):
    def setUp(self):
        self.service = PaymentService()
        self.service._user_for_customer = MagicMock(
            return_value=SimpleNamespace(id=1)
        )
        self.service.users = MagicMock()

    @patch("chatbot.services.payment.stripe.Subscription.list")
    def test_keeps_pro_while_another_subscription_is_active(self, sub_list):
        sub_list.return_value = SimpleNamespace(
            data=[SimpleNamespace(id="sub_other")]
        )

        self.service.handle_subscription_deleted(
            {"customer": "cus_123", "metadata": {}}
        )

        self.service.users.downgrade_to_free.assert_not_called()

    @patch("chatbot.services.payment.stripe.Subscription.list")
    def test_downgrades_when_nothing_is_left(self, sub_list):
        sub_list.return_value = SimpleNamespace(data=[])

        self.service.handle_subscription_deleted(
            {"customer": "cus_123", "metadata": {}}
        )

        self.service.users.downgrade_to_free.assert_called_once()


class PaymentIntentHandlerTests(SimpleTestCase):
    def setUp(self):
        self.service = PaymentService()
        self.user = SimpleNamespace(id=123)
        self.service._user_for_customer = MagicMock(return_value=self.user)
        self.service.users = MagicMock()

    def test_handle_payment_intent_succeeded(self):
        payment_intent = {
            "customer": "customer_123",
            "metadata": {
                "type": "token_purchase",
                "token_amount": "200000",
            },
        }
        self.service.handle_payment_intent_succeeded(payment_intent)
        self.service._user_for_customer.assert_called_once_with("customer_123")
        self.service.users.add_purchased_tokens.assert_called_once_with(
            self.user.id, 200000
        )

    def test_handle_payment_intent_succeeded_with_unknown_customer(self):
        """
        A duplicate Stripe customer must not lose a paid purchase: the
        `user_id` we wrote at creation identifies the buyer on its own.
        """
        self.service._user_for_customer = MagicMock(return_value=None)
        payment_intent = {
            "customer": "customer_we_never_stored",
            "metadata": {
                "type": "token_purchase",
                "token_amount": "200000",
                "user_id": "123",
            },
        }

        with patch("chatbot.services.payment.User") as user_model:
            user_model.objects.filter.return_value.first.return_value = self.user
            self.service.handle_payment_intent_succeeded(payment_intent)

        self.service._user_for_customer.assert_not_called()
        self.service.users.add_purchased_tokens.assert_called_once_with(
            self.user.id, 200000
        )

    def test_handle_payment_intent_succeeded_with_unrelated_payment_intent(self):
        payment_intent = {
            "customer": "customer_123",
            "metadata": {
                "type": "subscription",
            },
        }
        self.service.handle_payment_intent_succeeded(payment_intent)
        self.service._user_for_customer.assert_not_called()
        self.service.users.add_purchased_tokens.assert_not_called()


class StripeWebhookIdempotencyTests(TestCase):
    @patch("chatbot.views.payment.PaymentService", autospec=True)
    def test_duplicate_event_is_processed_once(self, service_class):
        service = service_class.return_value
        service.verify_webhook.return_value = stripe_event(
            "evt_test_123",
            "payment_intent.succeeded",
            {
                "customer": "cus_123",
                "metadata": {
                    "type": "token_purchase",
                    "token_amount": "200000",
                },
            },
        )

        first_request = APIRequestFactory().generic(
            "POST",
            "/webhooks/stripe/",
            data=b"{}",
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="test-signature",
        )

        second_request = APIRequestFactory().generic(
            "POST",
            "/webhooks/stripe/",
            data=b"{}",
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="test-signature",
        )

        first_response = StripeWebhookView.as_view()(first_request)
        self.assertEqual(first_response.data, {"status": "success"})
        self.assertEqual(first_response.status_code, 200)
        self.assertEqual(
            service.handle_payment_intent_succeeded.call_count, 1
        )
        
        second_response = StripeWebhookView.as_view()(second_request)
        self.assertEqual(second_response.data, {"status": "already processed"})
        self.assertEqual(second_response.status_code, 200)
        self.assertEqual(
            service.handle_payment_intent_succeeded.call_count, 1
        )

        self.assertEqual(
            ProcessedStripeEvent.objects.filter(
                event_id="evt_test_123"
            ).count(),
            1,
        )

    @patch("chatbot.views.payment.PaymentService", autospec=True)
    def test_handler_failure_does_not_mark_event_processed(self, service_class):
        service = service_class.return_value
        service.verify_webhook.return_value = stripe_event(
            "evt_failure",
            "payment_intent.succeeded",
            {
                "customer": "cus_123",
                "metadata": {
                    "type": "token_purchase",
                    "token_amount": "200000",
                },
            },
        )

        request = APIRequestFactory().generic(
            "POST",
            "/webhooks/stripe/",
            data=b"{}",
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="test-signature",
        )
        
        service.handle_payment_intent_succeeded.side_effect = RuntimeError(
            "handler failed"
        )
        
        with self.assertRaisesRegex(RuntimeError, "handler failed"):
            StripeWebhookView.as_view()(request)

        self.assertEqual(service.handle_payment_intent_succeeded.call_count, 1)

        self.assertFalse(
            ProcessedStripeEvent.objects.filter(event_id="evt_failure").exists()
        )


class LiveStripeObjectWebhookTests(TestCase):
    """
    Drives the view with an unmocked service, so the payload the handlers see
    is the one the SDK actually builds rather than a hand-written dict.
    """

    def test_paid_subscription_invoice_upgrades_the_buyer(self):
        user = User.objects.create(
            username="buyer", email="buyer@example.com", method="google"
        )
        event = stripe_event(
            "evt_invoice_paid",
            "invoice.payment_succeeded",
            {
                "customer": "cus_we_never_stored",
                "parent": {
                    "subscription_details": {
                        "subscription": "sub_123",
                        "metadata": {
                            "type": "subscription",
                            "user_id": str(user.id),
                        },
                    }
                },
            },
        )

        request = APIRequestFactory().generic(
            "POST",
            "/webhooks/stripe/",
            data=b"{}",
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="test-signature",
        )

        with patch.object(PaymentService, "verify_webhook", return_value=event):
            response = StripeWebhookView.as_view()(request)

        self.assertEqual(response.status_code, 200)
        user.refresh_from_db()
        self.assertEqual(user.membership, PRO_TIER)