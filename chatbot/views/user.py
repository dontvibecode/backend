import json
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, generics
from django.utils import timezone
from dateutil.relativedelta import relativedelta

from ..models import User
from ..services.user import UserService

from ..serializers import UserSerializer, UserWithPreferencesSerializer


class UserAPIView(APIView):
    """
    API endpoint for managing conversations.
    """
    def __init__(self):
        self.service = UserService()

    def get(self, **kwargs):
        user_id = kwargs.get("user_id")
        try:
            user = self.service.get_user_by_id(user_id=user_id)
            if not user:
                return Response(
                    {"error": "User not found"}, status=status.HTTP_404_NOT_FOUND
                )
            output_serializer = UserSerializer(user)
            return Response(output_serializer.data, status=status.HTTP_200_OK)
        except:
            return Response(
                {"error": "Error when fetching user"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class UserWithPreferencesAPIView(APIView):
    """
    API endpoint for retrieving user along with preferences.
    """
    def __init__(self):
        self.service = UserService()

    def get(self, request, email, **kwargs):
        """
        Handle GET requests to fetch a user and preferences by email.
        
        This method now fully replaces the default 'retrieve'
        behavior of RetrieveAPIView.
        """
        try:
            user = self.service.get_user_by_email_with_preferences(email=email)
            if not user:
                return Response(
                    {"error": "User not found"}, status=status.HTTP_404_NOT_FOUND
                )
            output_serializer = UserWithPreferencesSerializer(user)
            return Response(output_serializer.data, status=status.HTTP_200_OK)
        
        except Exception as e:
            return Response(
                {"error": "An error occurred when fetching user data."}, 
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    def post(self, request):
        input_serializer = UserSerializer(data=request.data)
        if not input_serializer.is_valid():
            return Response(input_serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        validated_data = input_serializer.validated_data
        try:
            user = self.service.create_user(
                username=validated_data["username"],
                email=validated_data["email"],
                method=validated_data.get("method"),
            )
            userWithPreferences = User.objects.select_related('preferences').get(id=user.id)
            output_serializer = UserWithPreferencesSerializer(userWithPreferences)
            return Response(output_serializer.data, status=status.HTTP_201_CREATED)
        except ValueError as ve:
            return Response({"error": str(ve)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            return Response(
                {"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    def put(self, request, email):
        try:
            user_instance = self.service.get_user_by_email(email=email)
            print("\n\n\nFetched user instance for update:", user_instance)
        except User.DoesNotExist:
            return Response(
                {"error": "User not found"}, status=status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            return Response(
                {"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
        input_serializer = UserWithPreferencesSerializer(
            instance=user_instance,
            data=request.data,
            partial=True  # Allow partial updates
        )

        print("\n\n\nInput data for update:", request.data)

        if not input_serializer.is_valid():
            return Response(input_serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        try:
            updated_user = input_serializer.save()
            print("\n\n\nUpdated user:", updated_user.__str__())
            output_serializer = UserWithPreferencesSerializer(updated_user)
            return Response(output_serializer.data, status=status.HTTP_200_OK)
        except Exception as e:
            return Response(
                {"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class UserMembershipAPIView(APIView):
    """
    API endpoint for managing user membership.
    """
    def __init__(self):
        self.service = UserService()

    def post(self, request):
        try:
            user = self.service.get_user_by_id(email=request.user.id)
            if user.membership == 'free':
                user.membership = 'pro'
                user.token_limit = 1000000
                user.token_used = 0
                user.membership_updated_at = timezone.now()
                user.membership_expires_at = None
            else:
                user.membership_expires_at = user.membership_updated_at + relativedelta(months=1)
            user.save()
            return Response(
                {"message": "User membership updated successfully."},
                status=status.HTTP_200_OK,
            )
        except User.DoesNotExist:
            return Response(
                {"error": "User not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
