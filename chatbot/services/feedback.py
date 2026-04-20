from django.core.mail import send_mail
from django.core.cache import cache

from api.settings import EMAIL_HOST_USER, FEEDBACK_RECIPIENT_EMAIL
from chatbot.models import User

FEEDBACK_DAILY_LIMIT = 3


class FeedbackService:
    def _get_cache_key(self, email):
        return f"feedback_count:{email}"

    def _check_rate_limit(self, email):
        key = self._get_cache_key(email)
        count = cache.get(key, 0)
        if count >= FEEDBACK_DAILY_LIMIT:
            raise ValueError(
                f"You've reached the daily feedback limit ({FEEDBACK_DAILY_LIMIT}). Please try again tomorrow."
            )
        # Expire at end of day — 86400 seconds = 24 hours from first feedback
        cache.set(key, count + 1, timeout=86400)

    def send_user_feedback(self, email, message):
        self._check_rate_limit(email)

        send_mail(
            subject=f"Feedback from {email}",
            message=message,
            from_email=email,
            recipient_list=[FEEDBACK_RECIPIENT_EMAIL],
        )

        return {"message": "Feedback sent successfully"}
