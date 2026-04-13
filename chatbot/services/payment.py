import json

import stripe
from django.utils import timezone
from dateutil.relativedelta import relativedelta

from api.settings import STRIPE_SECRET_KEY, STRIPE_PRO_MEMBERSHIP_PRICE_ID, STRIPE_WEBHOOK_SECRET

from ..models import User


class PaymentService:
    """
    Houses all business logic related to Stripe payments,
    subscriptions, and token purchases.
    Uses PaymentIntent / Subscription API for embedded Elements flow.
    """

    def __init__(self):
        stripe.api_key = STRIPE_SECRET_KEY

    def get_or_create_stripe_customer(self, user):
        """we
        Ensures user has a Stripe customer ID. Creates one if missing.
        """
        if user.stripe_customer_id:
            return user.stripe_customer_id

        customer = stripe.Customer.create(
            email=user.email,
            metadata={"user_id": str(user.id)},
        )
        user.stripe_customer_id = customer.id
        user.save()
        return customer.id

    def create_subscription(self, user):
        customer_id = self.get_or_create_stripe_customer(user)

        subscription = stripe.Subscription.create(
            customer=customer_id,
            items=[{"price": STRIPE_PRO_MEMBERSHIP_PRICE_ID}],
            payment_behavior="default_incomplete",
            payment_settings={"save_default_payment_method": "on_subscription"},
            expand=["latest_invoice.confirmation_secret"],
            metadata={
                "user_id": str(user.id),
                "type": "subscription",
            },
        )

        print(f"Subscription created: {subscription['id']}, status: {subscription['status']}")
        print(f"Latest invoice ID: {subscription.latest_invoice['id']}, status: {subscription.latest_invoice['status']}")
        print(f"Confirmation secret: {subscription.latest_invoice.confirmation_secret}")

        return {
            "subscription_id": subscription["id"],
            "client_secret": subscription.latest_invoice.confirmation_secret.client_secret,
        }

    def create_token_purchase(self, user, price_id, token_amount):
        """
        Creates a Stripe PaymentIntent for a one-time token purchase.
        Returns the client_secret for the frontend to confirm via Elements.
        """
        customer_id = self.get_or_create_stripe_customer(user)

        # Look up the price to get the amount
        price = stripe.Price.retrieve(price_id)

        payment_intent = stripe.PaymentIntent.create(
            amount=price.unit_amount,
            currency=price.currency,
            customer=customer_id,
            metadata={
                "user_id": str(user.id),
                "type": "token_purchase",
                "token_amount": str(token_amount),
            },
        )

        return {
            "client_secret": payment_intent.client_secret,
        }

    def cancel_subscription(self, user):
        """
        Cancels the user's active subscription at period end.
        They keep Pro access until the current billing period ends.
        Returns the period end date or raises ValueError if no subscription found.
        """
        if not user.stripe_customer_id:
            raise ValueError("No subscription found")

        subscriptions = stripe.Subscription.list(
            customer=user.stripe_customer_id,
            status="active",
        )

        if not subscriptions.data:
            raise ValueError("No active subscription")

        sub = subscriptions.data[0]
        stripe.Subscription.modify(sub.id, cancel_at_period_end=True)
        return user.membership_expires_at

    def verify_webhook(self, payload, sig_header):
        """
        Verifies and constructs a Stripe webhook event from the raw payload.
        Raises ValueError or stripe.error.SignatureVerificationError on failure.
        """
        return stripe.Webhook.construct_event(
            payload, sig_header, STRIPE_WEBHOOK_SECRET
        )

    def handle_invoice_paid(self, invoice):
        """
        Handles the invoice.payment_succeeded event.
        Called for both the first subscription payment and renewals.
        """
        print("[DEBUG] Handling invoice.payment_succeeded event")
        parent = invoice.get("parent", {})
        subscription_details = parent.get("subscription_details", {})
        if not subscription_details.get("subscription"):
            print("[DEBUG] Invoice does not have a subscription ID, skipping")
            return

        try:
            user = User.objects.get(stripe_customer_id=invoice["customer"])
            print(f"[DEBUG] Found user {user.email} for customer ID {invoice['customer']}")
        except User.DoesNotExist:
            return

        print(f"[DEBUG] Updating user {user.email} membership to Pro")
        user.membership = "pro"
        user.token_limit = max(500000, user.token_limit + 200000)  # Add tokens on renewal, cap at 5M
        user.token_used = 0
        user.membership_updated_at = timezone.now()
        user.membership_expires_at = timezone.now() + relativedelta(months=1)
        user.save()
        print(f"[DEBUG]: Succeeded")

    def handle_payment_intent_succeeded(self, payment_intent):
        """
        Handles the payment_intent.succeeded event for one-time payments.
        Adds purchased tokens to the user's balance.
        """
        print(f"[DEBUG]: handle_payment_intent_succeeded invoked")
        metadata = payment_intent.get("metadata", {})
        print(f"[DEBUG]: metadata: {json.dumps(metadata)}")
        if metadata.get("type") != "token_purchase":
            print(f"[DEBUG]: metadata type is not token_purchase, skipping")
            return

        try:
            user = User.objects.get(stripe_customer_id=payment_intent["customer"])
            print(f"[DEBUG] Found user {user.email} for customer ID {payment_intent['customer']}")
        except User.DoesNotExist:
            return

        print(f"[DEBUG] Adding tokens to user {user.email}")
        token_amount = int(metadata.get("token_amount", 0))
        user.token_limit += token_amount
        user.save()
        print(f"[DEBUG]: Added {token_amount} tokens to user {user.email}")

    def handle_subscription_deleted(self, subscription):
        """
        Handles the customer.subscription.deleted event.
        Downgrades user back to free tier.
        """
        print(f"[DEBUG]: handle_subscription_deleted invoked")
        try:
            user = User.objects.get(stripe_customer_id=subscription["customer"])
            user.membership = "free"
            user.token_limit = 50000
            user.token_used = 0
            user.membership_updated_at = timezone.now()
            user.membership_expires_at = None
            user.save()
            print(f"[DEBUG]: user {user.email} downgraded to free tier")
        except User.DoesNotExist:
            print(f"[DEBUG]: User not found for customer ID {subscription['customer']}")
            pass
