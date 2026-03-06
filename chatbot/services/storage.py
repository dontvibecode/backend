import google.auth
from google.cloud import storage
from google.auth.transport.requests import Request
from google.oauth2 import service_account
import uuid
from datetime import timedelta
from api.settings import BUCKET_NAME


IAM_SIGNING_SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]


class GCSService:
    """Service for generating signed URLs for GCP Cloud Storage uploads."""
    
    ALLOWED_CONTENT_TYPES = ["image/jpeg", "image/png", "image/gif", "image/webp"]
    MAX_FILE_SIZE = 5 * 1024 * 1024  # 5MB
    
    def __init__(self):
        self.client = storage.Client()
        self.credentials = self.client._credentials
        self.signing_credentials, _ = google.auth.default(scopes=IAM_SIGNING_SCOPES)
        self.bucket = self.client.bucket(BUCKET_NAME)

    def _get_signed_url_kwargs(self) -> dict:
        """
        Local development commonly uses a service account JSON key file, which
        contains a private key and can sign URLs directly.

        Cloud Run usually uses token-based runtime credentials with no private
        key. In that case we pass the access token + service account email so
        the storage library uses IAM SignBlob instead of local signing.
        """
        if isinstance(self.credentials, service_account.Credentials):
            return {}

        auth_request = Request()
        self.signing_credentials.refresh(auth_request)

        service_account_email = getattr(
            self.signing_credentials, "service_account_email", None
        )
        access_token = getattr(self.signing_credentials, "token", None)

        if not service_account_email or not access_token:
            raise RuntimeError(
                "Unable to generate a signed URL with the current credentials. "
                "Expected either a service account key file locally or a Cloud "
                "Run service account with IAM signing access."
            )

        return {
            "service_account_email": service_account_email,
            "access_token": access_token,
        }
    
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
            **self._get_signed_url_kwargs(),
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