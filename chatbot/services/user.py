import logging

from dateutil.relativedelta import relativedelta
from django.db import IntegrityError, transaction
from django.db.models import F, Value
from django.db.models.functions import Greatest
from django.utils import timezone

from ..models import Preferences, TokenUsage, User

logger = logging.getLogger(__name__)


# The single source of truth for the token economy. No other module may
# hardcode a tier name or an allowance; import from here instead.
FREE_TIER = "free"
PRO_TIER = "pro"

FREE_TOKEN_LIMIT = 100_000
PRO_TOKEN_LIMIT = 500_000

# One billing period: how long Pro access lasts, and how often the free
# allowance refills.
BILLING_PERIOD = relativedelta(months=1)

TIER_TOKEN_LIMITS = {FREE_TIER: FREE_TOKEN_LIMIT, PRO_TIER: PRO_TOKEN_LIMIT}


class InsufficientTokensError(Exception):
    """Raised when a token charge would exceed the user's allowance."""


class UserService:
    """
    Houses all business logic related to User and Preferences models,
    and owns every transition of the membership / token state machine.
    """

    # --- Lookups ----------------------------------------------------------

    def get_user_by_email_with_preferences(self, email):
        """
        Fetches a single user and their related preferences
        using an efficient SQL JOIN.
        """
        return User.objects.select_related("preferences").filter(email=email).first()

    def get_user_by_email(self, email):
        """
        Fetches a user by their email, or None if there is no such user.
        """
        return User.objects.filter(email=email).first()

    @staticmethod
    def get_preferences_by_user_id(user_id):
        """
        Fetches preferences for a given user ID, or None if absent.
        """
        return Preferences.objects.filter(user_id=user_id).first()

    # --- Creation ---------------------------------------------------------

    def create_user(self, **kwargs):
        """
        Creates a new user and their default preferences.
        """
        username = kwargs.get("username")
        email = kwargs.get("email")
        method = kwargs.get("method")

        if not username or not email:
            raise ValueError("Username and email are required to create a user.")

        with transaction.atomic():
            user = User.objects.create(username=username, email=email, method=method)
            Preferences.objects.create(user=user)
        return user

    def get_or_create_google_user(self, email, name=None):
        """
        Resolves the User row for a verified Google identity, creating it
        (plus default preferences) on first login.

        `username` is unique in the schema but Google display names are not,
        so a suffix is appended until the name is free. Two people signing in
        at the same moment can still collide on either column, which is what
        the IntegrityError retry handles.
        """
        user = User.objects.filter(email=email).first()
        if user:
            return user

        base_username = (name or "").strip() or email.split("@")[0]

        for _ in range(5):
            try:
                return self.create_user(
                    username=self._unique_username(base_username),
                    email=email,
                    method="google",
                )
            except IntegrityError:
                existing = User.objects.filter(email=email).first()
                if existing:
                    # A concurrent first login for this email won the race.
                    return existing
                # Otherwise the username collided; loop and pick a new suffix.

        raise IntegrityError(f"Could not allocate a unique username for {email}")

    @staticmethod
    def _unique_username(base):
        username = base
        n = 1
        while User.objects.filter(username=username).exists():
            n += 1
            username = f"{base} ({n})"
        return username

    # --- Update user profile ----------------------------------------------

    def update_profile(self, user, *, username=None, preferences=None):
        """
        Updates a user's profile.
        """
        with transaction.atomic():
            if username is not None:
                user.username = username
                user.save(update_fields=["username"])
            if preferences:
                for field, value in preferences.items():
                    setattr(user.preferences, field, value)
                user.preferences.save(update_fields=list(preferences.keys()))
        return User.objects.select_related("preferences").get(id=user.id)

    # --- Membership state machine ----------------------------------------

    def reconcile_membership(self, user):
        """
        Applies any time-based membership transition that has come due.

        This is the ONLY place elapsed time changes a user's tier or refills
        their allowance. It runs on every authenticated request, so the common
        case (nothing due) must stay free: it is two comparisons against fields
        already loaded on `user`, with no extra query.

        Returns True if the user's row was changed.
        """
        now = timezone.now()

        if user.membership == PRO_TIER:
            if not user.membership_expires_at or now < user.membership_expires_at:
                return False
            if user.subscription_active:
                # The paid period lapsed but Stripe still considers the
                # subscription live, so the renewal invoice is in flight.
                # `handle_invoice_paid` extends the period and refills the
                # allowance; granting anything here would hand out a free month
                # whenever a renewal payment fails.
                logger.info(
                    "Pro period lapsed for user %s while subscription is active; "
                    "waiting for the renewal webhook",
                    user.id,
                )
                return False
            # Cancelled at period end, and the period has now ended.
            return self.downgrade_to_free(user)

        if not user.membership_updated_at:
            return False

        if now >= user.membership_updated_at + BILLING_PERIOD:
            return self._refill_free_allowance(user, now)

        return False

    def _refill_free_allowance(self, user, now):
        """
        Refills the free monthly allowance exactly once per period.

        The condition lives in the UPDATE's WHERE clause rather than in Python
        so that concurrent requests (a page load fires several at once) cannot
        both refill: whoever writes first moves `membership_updated_at`, and
        every other request matches zero rows.
        """
        updated = User.objects.filter(
            id=user.id,
            membership=FREE_TIER,
            membership_updated_at__lte=now - BILLING_PERIOD,
        ).update(
            token_used=0,
            # Greatest, not assignment: purchased token packs raise token_limit
            # above the tier baseline and must survive the refill.
            token_limit=Greatest(F("token_limit"), Value(FREE_TOKEN_LIMIT)),
            membership_updated_at=now,
        )

        if not updated:
            return False

        user.refresh_from_db(
            fields=["token_used", "token_limit", "membership_updated_at"]
        )
        logger.info("Refilled free allowance for user %s", user.id)
        return True

    def upgrade_to_pro(self, user):
        """
        Grants (or renews) Pro for one billing period. Called by the Stripe
        `invoice.payment_succeeded` webhook — payment is the only thing that
        may create Pro access.
        """
        now = timezone.now()
        User.objects.filter(id=user.id).update(
            membership=PRO_TIER,
            subscription_active=True,
            token_used=0,
            token_limit=Greatest(F("token_limit"), Value(PRO_TOKEN_LIMIT)),
            membership_updated_at=now,
            membership_expires_at=now + BILLING_PERIOD,
        )
        user.refresh_from_db()
        logger.info(
            "User %s upgraded/renewed to pro until %s",
            user.id,
            user.membership_expires_at,
        )
        return True

    def downgrade_to_free(self, user):
        """
        Returns a user to the free tier and its allowance. Called when a Pro
        period ends without renewal, and by the `customer.subscription.deleted`
        webhook.
        """
        now = timezone.now()
        User.objects.filter(id=user.id).update(
            membership=FREE_TIER,
            subscription_active=None,
            token_used=0,
            token_limit=Greatest(FREE_TOKEN_LIMIT, F("token_limit") - F("token_used")),
            membership_updated_at=now,
            membership_expires_at=None,
        )
        user.refresh_from_db()
        logger.info("User %s downgraded to free", user.id)
        return True

    # --- Token accounting -------------------------------------------------

    @staticmethod
    def consume_tokens(user_id, amount):
        """
        Charges tokens against a user's allowance and returns how many remain.

        Affordability and the increment are one conditional database update.
        Concurrent requests therefore cannot both spend the same remaining
        allowance.
        """
        if not amount or amount <= 0:
            return UserService.tokens_remaining(user_id)

        updated = User.objects.filter(
            id=user_id,
            token_used__lte=F("token_limit") - amount,
        ).update(token_used=F("token_used") + amount)

        if not updated:
            if not User.objects.filter(id=user_id).exists():
                raise User.DoesNotExist(f"User {user_id} does not exist.")
            raise InsufficientTokensError(
                f"User {user_id} does not have enough tokens for a charge of {amount}."
            )

        return UserService.tokens_remaining(user_id)

    @staticmethod
    def add_purchased_tokens(user_id, amount):
        """
        Raises a user's ceiling by a purchased token pack.
        """
        if not amount or amount <= 0:
            return UserService.tokens_remaining(user_id)

        User.objects.filter(id=user_id).update(token_limit=F("token_limit") + amount)
        return UserService.tokens_remaining(user_id)

    @staticmethod
    def tokens_remaining(user_id):
        """
        For tokens the user may still spend this period, read straight from the DB
        so it is never a stale in-memory value.
        """
        row = (
            User.objects.filter(id=user_id).values("token_limit", "token_used").first()
        )
        if not row:
            return 0
        return max(0, row["token_limit"] - row["token_used"])

    def get_token_by_user_email(self, email):
        """
        Reports a user's allowance. A pure read: refilling is the job of
        `reconcile_membership`, which has already run for this request.
        """
        user = (
            User.objects.filter(email=email).values("token_used", "token_limit").first()
        )
        if not user:
            return None
        return {"token_used": user["token_used"], "token_limit": user["token_limit"]}

    def get_token_usage_by_user_email(self, email):
        """
        Fetches token usage history for a given user email.
        """
        user = User.objects.filter(email=email).first()
        if not user:
            return None
        return TokenUsage.objects.filter(user=user).order_by("-timestamp")
