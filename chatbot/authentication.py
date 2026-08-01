import logging
import os

from google.auth.transport import requests
from google.oauth2 import id_token
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed

from .services.user import UserService

logger = logging.getLogger(__name__)

user_service = UserService()

# Reused across requests so Google's signing certificates stay cached instead
# of being re-fetched on every authenticated call.
google_transport = requests.Request()


class GoogleIDTokenAuthentication(BaseAuthentication):
    """
    DRF authenticator that validates Google ID tokens sent as Bearer tokens,
    and links them to our custom User model.
    """

    def authenticate(self, request):
        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            return None  # Not a Bearer token. Let another authenticator try.

        token = auth_header.split(" ")[1]

        client_id = os.getenv("GOOGLE_CLIENT_ID")
        if not client_id:
            # Server misconfiguration, not an auth failure.
            raise AuthenticationFailed("GOOGLE_CLIENT_ID is not set on the server.")

        try:
            # Verifies signature, expiry and audience against Google.
            claims = id_token.verify_oauth2_token(token, google_transport, client_id)
        except ValueError as e:
            raise AuthenticationFailed(f"Token is invalid or expired: {e}")

        email = claims.get("email")
        if not email:
            raise AuthenticationFailed("Token is valid but is missing an email claim.")

        user = user_service.get_or_create_google_user(email=email, name=claims.get("name"))

        # Every authenticated request is a chance to apply a due membership
        # transition, so an expired Pro downgrades and a free allowance refills
        # the moment the user opens the site rather than when they next chat.
        user_service.reconcile_membership(user)

        # `IsAuthenticated` checks `user.is_authenticated`, which a plain
        # models.Model does not provide. Set it on the in-memory object only.
        user.is_authenticated = True

        return (user, None)
