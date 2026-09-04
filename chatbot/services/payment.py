import logging

import stripe

from api.settings import STRIPE_SECRET_KEY, STRIPE_PRO_MEMBERSHIP_PRICE_ID, STRIPE_WEBHOOK_SECRET

from ..models import User
from .user import UserService

logger = logging.getLogger(__name__)


class PaymentService:
    """
    Houses all business logic related to Stripe payments,
    subscriptions, and token purchases.
    Uses PaymentIntent / Subscription API for embedded Elements flow.

    Membership and allowance changes are delegated to UserService, which owns
    that state machine; this class only decides *when* they happen.
    """

    def __init__(self):
        stripe.api_key = STRIPE_SECRET_KEY
        self.users = UserService()

    def get_or_create_stripe_customer(self, user):
        """
        Ensures user has a Stripe customer ID. Creates one if missing.
        """
        if user.stripe_customer_id:
            return user.stripe_customer_id

        # The idempotency key matters: the frontend can fire the subscribe and
        # token-purchase calls at once, and without it each one creates its own
        # Stripe customer. Only the last write wins in our column, so a payment
        # made against the other customer can no longer be traced back here.
        customer = stripe.Customer.create(
            email=user.email,
            metadata={"user_id": str(user.id)},
            idempotency_key=f"customer-for-user-{user.id}",
        )
        user.stripe_customer_id = customer.id
        user.save(update_fields=["stripe_customer_id"])
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

        logger.info(
            "Created subscription %s (status %s) for user %s",
            subscription["id"],
            subscription["status"],
            user.id,
        )

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

    def _active_subscription(self, user):
        """
        Returns the user's active Stripe subscription, or raises ValueError.
        """
        if not user.stripe_customer_id:
            raise ValueError("No subscription found")

        subscriptions = stripe.Subscription.list(
            customer=user.stripe_customer_id,
            status="active",
        )

        if not subscriptions.data:
            raise ValueError("No active subscription")

        return subscriptions.data[0]

    def cancel_subscription(self, user):
        """
        Cancels the user's active subscription at period end.
        They keep Pro access until the current billing period ends.
        Returns the period end date or raises ValueError if no subscription found.
        """
        sub = self._active_subscription(user)

        # Stripe first: if this call fails we must not leave the account
        # marked cancelled while Stripe keeps billing it.
        stripe.Subscription.modify(sub.id, cancel_at_period_end=True)

        user.subscription_active = False
        user.save(update_fields=["subscription_active"])
        return user.membership_expires_at

    def resume_subscription(self, user):
        """
        Resumes a canceled subscription if still within the current billing period.
        """
        sub = self._active_subscription(user)

        if not sub.cancel_at_period_end:
            raise ValueError("No canceled subscription to resume")

        stripe.Subscription.modify(sub.id, cancel_at_period_end=False)

        user.subscription_active = True
        user.save(update_fields=["subscription_active"])
        return True

    def create_setup_intent(self, user):
        """
        Creates a SetupIntent so the user can save a new payment method.
        After confirmation, updates the subscription's default payment method.
        """
        self.get_or_create_stripe_customer(user)
        sub = self._active_subscription(user)

        setup_intent = stripe.SetupIntent.create(
            customer=user.stripe_customer_id,
            metadata={
                "user_id": str(user.id),
                "subscription_id": sub.id,
            },
        )

        return {
            "client_secret": setup_intent.client_secret,
        }

    def update_subscription_payment_method(self, user, payment_method_id):
        """
        Sets a new default payment method on the user's active subscription.
        """
        sub = self._active_subscription(user)
        stripe.Subscription.modify(
            sub.id,
            default_payment_method=payment_method_id,
        )
        return True

    def verify_webhook(self, payload, sig_header):
        """
        Verifies and constructs a Stripe webhook event from the raw payload.
        Raises ValueError or stripe.error.SignatureVerificationError on failure.
        """
        return stripe.Webhook.construct_event(
            payload, sig_header, STRIPE_WEBHOOK_SECRET
        )

    def _user_for_customer(self, customer_id):
        user = User.objects.filter(stripe_customer_id=customer_id).first()
        if not user:
            logger.warning("No user found for Stripe customer %s", customer_id)
        return user

    def _buyer(self, customer_id, metadata):
        """
        Resolves who paid, preferring the `user_id` this service wrote into the
        object's metadata when it created it.

        The customer id alone is not enough: if the account ever ended up with
        more than one Stripe customer, a real payment can arrive under the one
        we no longer have stored, and matching only on it drops the purchase
        silently after the money has been taken.
        """
        user_id = metadata.get("user_id")
        if user_id:
            user = User.objects.filter(id=user_id).first()
            if user:
                return user
            logger.warning("Stripe metadata names unknown user %s", user_id)

        return self._user_for_customer(customer_id)

    def handle_invoice_paid(self, invoice):
        """
        Handles the invoice.payment_succeeded event.
        Called for both the first subscription payment and renewals.
        """
        subscription_details = invoice.get("parent", {}).get("subscription_details", {})
        if not subscription_details.get("subscription"):
            logger.info("Invoice has no subscription; ignoring")
            return

        user = self._buyer(
            invoice["customer"], subscription_details.get("metadata") or {}
        )
        if not user:
            return

        # Payment is the only thing that may grant or extend Pro.
        self.users.upgrade_to_pro(user)

    def handle_payment_intent_succeeded(self, payment_intent):
        """
        Handles the payment_intent.succeeded event for one-time payments.
        Adds purchased tokens to the user's balance.
        """
        metadata = payment_intent.get("metadata", {})
        if metadata.get("type") != "token_purchase":
            return

        user = self._buyer(payment_intent["customer"], metadata)
        if not user:
            return

        token_amount = int(metadata.get("token_amount", 0))
        self.users.add_purchased_tokens(user.id, token_amount)
        logger.info("Added %s purchased tokens to user %s", token_amount, user.id)

    def handle_subscription_deleted(self, subscription):
        """
        Handles the customer.subscription.deleted event.
        Downgrades user back to free tier.
        """
        user = self._user_for_customer(subscription["customer"])
        if not user:
            return

        self.users.downgrade_to_free(user)
