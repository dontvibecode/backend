import logging

from django.core.mail import send_mail

from api.settings import EMAIL_HOST_USER, FEEDBACK_RECIPIENT_EMAIL

logger = logging.getLogger(__name__)


class FeedbackService:
    """
    Sends user feedback on to the team inbox.

    Rate limiting is not done here. It used to count against the email address
    in the request body, which the sender picks and can change at will, and it
    counted in per-process memory that every worker and container held its own
    copy of. Both jobs now belong to DRF's throttle on FeedbackView, which keys
    on the caller's address and stores counters in the shared cache.
    """

    def send_user_feedback(self, email, message):
        send_mail(
            subject=f"Feedback from {email}",
            message=message,
            from_email=EMAIL_HOST_USER,
            recipient_list=[FEEDBACK_RECIPIENT_EMAIL],
        )
        logger.info("Feedback email sent on behalf of %s", email)

        return {"message": "Feedback sent successfully"}
