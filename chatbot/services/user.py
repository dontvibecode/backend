from ..models import TokenUsage, User, Preferences
from dateutil.relativedelta import relativedelta
from django.utils import timezone


class UserService:
    """
    Houses all business logic related to User and Preferences models.
    """

    def get_user_by_email_with_preferences(self, email):
        """
        Fetches a single user and their related preferences
        using an efficient SQL JOIN.
        """
        try:
            return User.objects.select_related('preferences').get(email=email)
        except User.DoesNotExist:
            return None
        except Exception as e:
            print(f"Error fetching user by email '{email}': {e}")
            raise e

    def get_user_by_id(self, user_id):
        """
        Fetches a user by their primary key.
        """
        try:
            return User.objects.get(user_id=user_id)
        except User.DoesNotExist:
            return None
        except Exception as e:
            print(f"Error fetching user by ID '{user_id}': {e}")
            raise e
        
    def get_user_by_email(self, email):
        """
        Fetches a user by their email.
        """
        try:
            return User.objects.get(email=email)
        except User.DoesNotExist:
            return None
        except Exception as e:
            print(f"Error fetching user by email '{email}': {e}")
            raise e
    
    def create_user(self, **kwargs):
        """
        Creates a new user and their default preferences.
        """
        username = kwargs.get("username")
        email = kwargs.get("email")
        method = kwargs.get("method")
        
        if not username or not email:
            raise ValueError("Username and email are required to create a user.")

        user = User.objects.create(username=username, email=email, method=method)
        Preferences.objects.create(user=user)  # Create default preferences
        return user

    def get_preferences_by_user_id(self, user_id):
        """
        Fetches preferences for a given user ID.
        """
        try:
            return Preferences.objects.get(user__id=user_id)
        except Preferences.DoesNotExist:
            return None
        except Exception as e:
            print(f"Error fetching preferences for user ID '{user_id}': {e}")
            raise e
    
    def get_token_by_user_email(self, email):
        """
        Fetches token used and token limit for a given user email.
        """
        try:
            user = User.objects.get(email=email)
            print(f"User: {user.__dict__}")
            token_reset_date = user.membership_updated_at + relativedelta(months=1)
            if token_reset_date < timezone.now():
                user.token_used = 0
                user.token_limit = 50000 if user.membership == 'free' else 5000000
                user.membership_updated_at = timezone.now()
                user.save()
            token_used = user.token_used
            token_limit = user.token_limit
            return {
                "token_used": token_used,
                "token_limit": token_limit
            }
        except User.DoesNotExist:
            return None
        except Exception as e:
            print(f"Error fetching token for user email '{email}': {e}")
    
    def update_user_and_preferences(self, email, user_data):
        """
        Updates an existing User and Preferences instance with new data.
        Expects user_data to be a dict containing user fields and a nested 'preferences' dict.
        """
        user = self.get_user_by_email_with_preferences(email)
        if not user:
            raise User.DoesNotExist("User not found")

        # Update user fields
        user_fields = ['username', 'email', 'method']
        for key in user_fields:
            if key in user_data:
                setattr(user, key, user_data[key])
        user.save()

        # Update preferences fields
        preferences_data = user_data.get('preferences')
        preferences = getattr(user, 'preferences', None)
        if preferences and preferences_data:
            for key, value in preferences_data.items():
                setattr(preferences, key, value)
            preferences.save()

        return user

    def get_token_usage_by_user_email(self, email):
        """
        Fetches token usage for a given user email.
        """
        try:
            user = User.objects.get(email=email)
            return TokenUsage.objects.filter(user=user).order_by('-timestamp')
        except User.DoesNotExist:
            return None
        except Exception as e:
            print(f"Error fetching token usage for user email '{email}': {e}")