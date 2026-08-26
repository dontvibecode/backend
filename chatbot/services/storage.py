import logging
import uuid
from urllib.parse import quote, unquote

import boto3
from botocore.config import Config
from django.conf import settings


logger = logging.getLogger(__name__)


class ObjectStorageService:
    """Cloudflare R2 adapter using its S3-compatible API."""

    ALLOWED_CONTENT_TYPES = ["image/jpeg", "image/png", "image/gif", "image/webp"]
    MAX_FILE_SIZE = 5 * 1024 * 1024  # 5MB

    def __init__(self):
        required_settings = {
            "R2_ACCOUNT_ID": settings.R2_ACCOUNT_ID,
            "R2_ACCESS_KEY_ID": settings.R2_ACCESS_KEY_ID,
            "R2_SECRET_ACCESS_KEY": settings.R2_SECRET_ACCESS_KEY,
            "R2_PUBLIC_BASE_URL": settings.R2_PUBLIC_BASE_URL,
        }
        missing = [name for name, value in required_settings.items() if not value]
        if missing:
            raise RuntimeError(
                f"Missing Cloudflare R2 settings: {', '.join(missing)}"
            )

        self.bucket_name = settings.R2_BUCKET_NAME
        self.public_base_url = settings.R2_PUBLIC_BASE_URL.rstrip("/")
        self.client = boto3.client(
            "s3",
            endpoint_url=f"https://{settings.R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
            aws_access_key_id=settings.R2_ACCESS_KEY_ID,
            aws_secret_access_key=settings.R2_SECRET_ACCESS_KEY,
            region_name="auto",
            config=Config(signature_version="s3v4"),
        )

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

        extension = content_type.split("/")[1]
        if extension == "jpeg":
            extension = "jpg"
        filename = f"profile-pictures/{user_id}/profile_{uuid.uuid4().hex[:12]}.{extension}"

        expires_in = 15 * 60
        upload_url = self.client.generate_presigned_url(
            "put_object",
            Params={
                "Bucket": self.bucket_name,
                "Key": filename,
                "ContentType": content_type,
            },
            ExpiresIn=expires_in,
        )
        public_url = f"{self.public_base_url}/{quote(filename, safe='/')}"

        return {
            "upload_url": upload_url,
            "public_url": public_url,
            "filename": filename,
            "expires_in": expires_in,
        }

    def delete_old_profile_image(self, old_url: str) -> bool:
        """Delete a previous profile image if it belongs to this bucket."""
        prefix = f"{self.public_base_url}/"
        if not old_url or not old_url.startswith(prefix):
            return False

        try:
            object_key = unquote(old_url[len(prefix):])
            self.client.delete_object(Bucket=self.bucket_name, Key=object_key)
            return True
        except Exception:
            logger.exception("Failed to delete old profile image")

        return False

    def is_managed_profile_url(self, public_url: str, user_id: int) -> bool:
        """Return whether a URL points to this user's profile-image prefix."""
        expected_prefix = (
            f"{self.public_base_url}/profile-pictures/{user_id}/profile_"
        )
        return bool(public_url and public_url.startswith(expected_prefix))
