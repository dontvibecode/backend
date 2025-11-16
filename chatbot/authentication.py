import os
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed
from google.oauth2 import id_token
from google.auth.transport import requests

# Import your custom models from this same app
from .models import User, Preferences

class GoogleIDTokenAuthentication(BaseAuthentication):
    """
    DRF Authenticator to validate Google ID Tokens (sent as Bearer tokens).
    
    This class is the "link" between your custom User model and
    DRF's authentication system.
    """

    def authenticate(self, request):
        print("Authenticating request with GoogleIDTokenAuthentication")
        # 1. Get the token from the "Authorization: Bearer <token>" header
        auth_header = request.headers.get('Authorization')
        if not auth_header or not auth_header.startswith('Bearer '):
            return None  # Not a Bearer token. Let another authenticator (if any) try.

        token = auth_header.split(' ')[1]
        print("Received token:", token)
        
        # 2. Get your Google Client ID from environment variables
        #    You MUST add this to your .env file
        client_id = os.getenv("GOOGLE_CLIENT_ID")
        if not client_id:
            # This is a server configuration error, not an auth failure
            raise AuthenticationFailed("GOOGLE_CLIENT_ID is not set on the server.")

        try:
            # 3. Verify the token with Google's servers.
            #    This checks the signature, expiration, and audience (our Client ID).
            #    This is the network call that proves the token is real.
            claims = id_token.verify_oauth2_token(
                token,
                requests.Request(), 
                client_id
            )

            print("Google ID Token claims:", claims)

            # 4. The token is valid. Get the user's email from the claims.
            email = claims.get('email')
            if not email:
                raise AuthenticationFailed("Token is valid but is missing an email claim.")

            # 5. This is the 10x "get or create" logic.
            #    It finds an existing user OR creates a new one,
            #    all in one efficient, safe database call.
            #    This handles both "login" and "register" in one step.
            user, created = User.objects.get_or_create(
                email=email,
                defaults={
                    'username': claims.get('name', email.split('@')[0]),
                    'method': 'google'
                }
            )

            if created:
                # If the user was just created, also create their
                # default preferences, just like your service layer.
                Preferences.objects.create(user=user)

            # 6. This is the "magic link" you were missing.
            #    The IsAuthenticated permission checks for `user.is_authenticated`.
            #    Since your model is a plain models.Model, it doesn't have this.
            #    We add it *to the in-memory object* for this request only.
            user.is_authenticated = True
            
            # 7. Success! Return the user.
            #    DRF will now attach this `user` object to `request.user`.
            #    Your `IsAuthenticated` permission will pass.
            return (user, None)  # (user, auth)

        except ValueError as e:
            # This triggers if verify_oauth2_token fails
            raise AuthenticationFailed(f"Token is invalid or expired: {e}")
        except Exception as e:
            # Catch all other potential errors
            raise AuthenticationFailed(f"An error occurred during authentication: {e}")