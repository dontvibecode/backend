from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, TestCase
from rest_framework.test import APIRequestFactory

from chatbot.models import ProcessedStripeEvent
from chatbot.services.payment import PaymentService
from chatbot.views.payment import StripeWebhookView


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
        service.verify_webhook.return_value = {
            "id": "evt_test_123",
            "type": "payment_intent.succeeded",
            "data": {
                "object": {
                    "customer": "cus_123",
                    "metadata": {
                        "type": "token_purchase",
                        "token_amount": "200000",
                    },
                }
            },
        }

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
        service.verify_webhook.return_value = {
            "id": "evt_failure",
            "type": "payment_intent.succeeded",
            "data": {
                "object": {
                    "customer": "cus_123",
                    "metadata": {
                        "type": "token_purchase",
                        "token_amount": "200000",
                    },
                }
            },
        }

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