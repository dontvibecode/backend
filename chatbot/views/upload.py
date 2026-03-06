from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from ..services.storage import GCSService
from ..services.user import UserService


class ProfileImageUploadURLView(APIView):
    """Generate a signed URL for profile image upload."""
    
    def __init__(self):
        self.gcs_service = GCSService()
        self.user_service = UserService()
    
    def post(self, request):
        """
        Request body: { "content_type": "image/jpeg" }
        Returns: { "upload_url": "...", "public_url": "...", "expires_in": 900 }
        """
        content_type = request.data.get("content_type")
        
        if not content_type:
            return Response(
                {"error": "content_type is required"},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Get user from auth (request.user is set by your auth middleware)
        user_email = getattr(request.user, 'email', None)
        if not user_email:
            return Response(
                {"error": "Authentication required"},
                status=status.HTTP_401_UNAUTHORIZED
            )
        
        user = self.user_service.get_user_by_email(user_email)
        if not user:
            return Response(
                {"error": "User not found"},
                status=status.HTTP_404_NOT_FOUND
            )
        
        try:
            result = self.gcs_service.generate_upload_signed_url(
                user_id=user.id,
                content_type=content_type
            )
            return Response(result, status=status.HTTP_200_OK)
        
        except ValueError as e:
            return Response(
                {"error": str(e)},
                status=status.HTTP_400_BAD_REQUEST
            )
        except Exception as e:
            return Response(
                {"error": "Failed to generate upload URL: " + str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class ProfileImageConfirmView(APIView):
    """Confirm upload and update user profile with new image URL."""
    
    def __init__(self):
        self.gcs_service = GCSService()
        self.user_service = UserService()
    
    def post(self, request):
        """
        Called after successful upload to GCS.
        Request body: { "public_url": "https://storage.googleapis.com/..." }
        """
        public_url = request.data.get("public_url")
        
        if not public_url:
            return Response(
                {"error": "public_url is required"},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        user_email = getattr(request.user, 'email', None)
        if not user_email:
            return Response(
                {"error": "Authentication required"},
                status=status.HTTP_401_UNAUTHORIZED
            )
        
        user = self.user_service.get_user_by_email_with_preferences(user_email)
        if not user:
            return Response(
                {"error": "User not found"},
                status=status.HTTP_404_NOT_FOUND
            )
        
        try:
            # Delete old image if exists
            old_url = user.preferences.profile_image
            print(f"old_url: {old_url}")
            if old_url:
                self.gcs_service.delete_old_profile_image(old_url)
            print(f"public_url: {public_url}")
            # Update preferences with new URL
            user.preferences.profile_image = public_url
            user.preferences.save()
            
            return Response(
                {"profile_image": public_url},
                status=status.HTTP_200_OK
            )
        
        except Exception as e:
            return Response(
                {"error": "Failed to update profile image, " + str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )