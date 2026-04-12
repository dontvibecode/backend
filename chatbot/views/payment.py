import stripe
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from ..services.payment import PaymentService
from api.settings import STRIPE_TOKEN_PACK_200K_PRICE_ID


class SubscribeView(APIView):
    """
    API endpoint for creating a Pro subscription.
    Returns client_secret for the frontend to use with Stripe Elements.
    """
    def __init__(self):
        self.service = PaymentService()

    def post(self, request):
        """
        POST /payments/subscribe/
        Returns: { subscription_id: string, client_secret: string }
        """
        try:
            result = self.service.create_subscription(request.user)
            return Response(result, status=status.HTTP_200_OK)
        except stripe.error.StripeError as e:
            return Response(
                {"error": str(e)},
                status=status.HTTP_400_BAD_REQUEST,
            )


class BuyTokensView(APIView):
    """
    API endpoint for one-time token purchases.
    Returns client_secret for the frontend to use with Stripe Elements.
    """
    def __init__(self):
        self.service = PaymentService()

    def post(self, request):
        """
        POST /payments/tokens/
        Request: { price_id: string, token_amount: number }
        Returns: { client_secret: string }
        """
        token_amount = request.data.get("token_amount")

        if not token_amount:
            return Response(
                {"error": "token_amount is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if token_amount not in [200000]:  # Only allow predefined token amounts
            return Response(
                {"error": "Invalid token_amount"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if token_amount == 200000:
            price_id = STRIPE_TOKEN_PACK_200K_PRICE_ID

        try:
            result = self.service.create_token_purchase(
                request.user, price_id, token_amount
            )
            return Response(result, status=status.HTTP_200_OK)
        except stripe.error.StripeError as e:
            return Response(
                {"error": str(e)},
                status=status.HTTP_400_BAD_REQUEST,
            )


class CancelSubscriptionView(APIView):
    """
    API endpoint for cancelling a Pro subscription.
    """
    def __init__(self):
        self.service = PaymentService()

    def post(self, request):
        """
        POST /payments/cancel/
        Cancels subscription at period end. User keeps Pro until then.
        Returns: { status, message, active_until }
        """
        try:
            active_until = self.service.cancel_subscription(request.user)
            return Response(
                {
                    "status": "canceled",
                    "message": "Subscription will cancel at period end",
                    "active_until": active_until,
                },
                status=status.HTTP_200_OK,
            )
        except ValueError as e:
            return Response(
                {"error": str(e)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except stripe.error.StripeError as e:
            return Response(
                {"error": str(e)},
                status=status.HTTP_400_BAD_REQUEST,
            )


@method_decorator(csrf_exempt, name="dispatch")
class StripeWebhookView(APIView):
    """
    API endpoint for receiving Stripe webhook events.
    No authentication - Stripe signs the payload instead.
    """
    authentication_classes = []
    permission_classes = []

    def __init__(self):
        self.service = PaymentService()

    def post(self, request):
        """
        POST /webhooks/stripe/
        Receives and processes Stripe webhook events.
        """
        payload = request.body
        sig_header = request.META.get("HTTP_STRIPE_SIGNATURE")

        try:
            event = self.service.verify_webhook(payload, sig_header)
        except ValueError:
            return Response(
                {"error": "Invalid payload"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except stripe.error.SignatureVerificationError:
            return Response(
                {"error": "Invalid signature"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        event_type = event["type"]
        data = event["data"]["object"]

        if event_type == "invoice.payment_succeeded":
            self.service.handle_invoice_paid(data)
        elif event_type == "payment_intent.succeeded":
            self.service.handle_payment_intent_succeeded(data)
        elif event_type == "customer.subscription.deleted":
            self.service.handle_subscription_deleted(data)

        return Response({"status": "success"}, status=status.HTTP_200_OK)
