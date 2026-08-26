from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, override_settings

from chatbot.services.storage import ObjectStorageService


@override_settings(
    R2_ACCOUNT_ID="acct",
    R2_ACCESS_KEY_ID="key",
    R2_SECRET_ACCESS_KEY="secret",
    R2_BUCKET_NAME="profile-images",
    R2_PUBLIC_BASE_URL="https://images.example.com",
)
class ObjectStorageServiceTests(SimpleTestCase):
    def setUp(self):
        with patch("chatbot.services.storage.boto3.client") as client_factory:
            self.client = MagicMock()
            client_factory.return_value = self.client
            self.service = ObjectStorageService()

    def test_signed_url_uses_user_scoped_key(self):
        self.client.generate_presigned_url.return_value = "https://upload.example/signed"

        result = self.service.generate_upload_signed_url(42, "image/png")

        self.assertTrue(result["filename"].startswith("profile-pictures/42/profile_"))
        self.assertTrue(result["filename"].endswith(".png"))
        self.assertEqual(result["upload_url"], "https://upload.example/signed")
        self.assertTrue(result["public_url"].startswith("https://images.example.com/profile-pictures/42/"))
        self.client.generate_presigned_url.assert_called_once()
        params = self.client.generate_presigned_url.call_args.kwargs["Params"]
        self.assertEqual(params["Bucket"], "profile-images")
        self.assertEqual(params["ContentType"], "image/png")

    def test_rejects_disallowed_content_type(self):
        with self.assertRaises(ValueError):
            self.service.generate_upload_signed_url(42, "application/pdf")

    def test_only_deletes_urls_from_this_bucket(self):
        deleted = self.service.delete_old_profile_image(
            "https://images.example.com/profile-pictures/42/profile_abc.jpg"
        )
        skipped = self.service.delete_old_profile_image(
            "https://storage.googleapis.com/dontvibecode-dev/old.jpg"
        )

        self.assertTrue(deleted)
        self.assertFalse(skipped)
        self.client.delete_object.assert_called_once_with(
            Bucket="profile-images",
            Key="profile-pictures/42/profile_abc.jpg",
        )

    def test_managed_url_is_scoped_to_the_user(self):
        own = "https://images.example.com/profile-pictures/42/profile_abc.jpg"
        other = "https://images.example.com/profile-pictures/99/profile_abc.jpg"

        self.assertTrue(self.service.is_managed_profile_url(own, 42))
        self.assertFalse(self.service.is_managed_profile_url(other, 42))
