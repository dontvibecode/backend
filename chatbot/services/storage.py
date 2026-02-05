from google.cloud import storage
from django.conf import settings
import os
import uuid
from datetime import timedelta
from api.settings import BUCKET_NAME

class GCSService:
    """Service for generating signed URLs for GCP Cloud Storage uploads."""
    
    ALLOWED_CONTENT_TYPES = ["image/jpeg", "image/png", "image/gif", "image/webp"]
    MAX_FILE_SIZE = 5 * 1024 * 1024  # 5MB
    
    def __init__(self):
        # In production, use default credentials (App Engine/Cloud Run)
        # In development, set GOOGLE_APPLICATION_CREDENTIALS env var
        self.client = storage.Client()
        self.bucket = self.client.bucket(BUCKET_NAME)
        
    
    def generate_upload_signed_url(self, user_id: int, content_type: str) -> dict:
        """
        Generate a signed URL for uploading a profile image.
        
        Returns:
            {
                "upload_url": str,  # Signed URL for PUT request
                "public_url": str,  # Final public URL after upload
                "expires_in": int   # Seconds until URL expires
            }
        """
        if content_type not in self.ALLOWED_CONTENT_TYPES:
            raise ValueError(f"Content type {content_type} not allowed")
        
        # Generate unique filename: users/{user_id}/profile_{uuid}.{ext}
        extension = content_type.split("/")[1]
        if extension == "jpeg":
            extension = "jpg"
        filename = f"profile-pictures/{user_id}/profile_{uuid.uuid4().hex[:12]}.{extension}"
        
        blob = self.bucket.blob(filename)
        
        # Generate signed URL valid for 15 minutes
        expiration = timedelta(minutes=15)
        upload_url = blob.generate_signed_url(
            version="v4",
            expiration=expiration,
            method="PUT",
            content_type=content_type,
        )
        
        # Public URL (bucket must have public read or use signed URLs for reading)
        public_url = f"https://storage.googleapis.com/{BUCKET_NAME}/{filename}"
        
        return {
            "upload_url": upload_url,
            "public_url": public_url,
            "filename": filename,
            "expires_in": int(expiration.total_seconds()),
        }
    
    def delete_old_profile_image(self, old_url: str) -> bool:
        """Delete the old profile image from GCS when user uploads a new one."""
        if not old_url or BUCKET_NAME not in old_url:
            return False
        
        try:
            # Extract blob name from URL
            prefix = f"https://storage.googleapis.com/{BUCKET_NAME}/"
            if old_url.startswith(prefix):
                blob_name = old_url[len(prefix):]
                blob = self.bucket.blob(blob_name)
                blob.delete()
                return True
        except Exception as e:
            print(f"Error deleting old profile image: {e}")
        
        return False