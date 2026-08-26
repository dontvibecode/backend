from django.urls import path


from .views.exercise import ExerciseAPIView, ExerciseAggregateAPIView, ExerciseBookmarkAPIView, ExerciseSubmissionAPIView, ExerciseSaveAPIView

from .views.user import UserWithPreferencesAPIView
from .views.chat import ChatAPIView, ChatStreamAPIView
from .views.conversation import ConversationAPIView
from .views.token import TokenAPIView, TokenUsageAPIView
from .views.upload import ProfileImageConfirmView, ProfileImageUploadURLView
from .views.payment import ResumeSubscriptionView, SubscribeView, BuyTokensView, CancelSubscriptionView, SetupIntentView, UpdatePaymentMethodView, StripeWebhookView
from .views.feedback import FeedbackView

app_name = 'chatbot'

urlpatterns = [
    path('message/stream/', ChatStreamAPIView.as_view(), name='message_with_streaming_thoughts'),
    path('conversations/messages/<int:pk>/', ChatAPIView.as_view(), name='get_messages_from_conversation'),
    path('conversations/', ConversationAPIView.as_view(), name='get_conversations'),
    path('conversations/delete/<int:pk>/', ConversationAPIView.as_view(), name='delete_conversation'),
    path('conversations/pin/<int:pk>/', ConversationAPIView.as_view(), name='pin_conversation'),
    path('user/', UserWithPreferencesAPIView.as_view(), name='user_profile'),
    path('exercise/<int:message_id>/', ExerciseAPIView.as_view(), name='get_exercises'),
    path('exercise/submit/', ExerciseSubmissionAPIView.as_view(), name='submit_exercise'),
    path('exercise/new/<int:message_id>/', ExerciseAPIView.as_view(), name='create_exercise'),
    path('exercise/save/', ExerciseSaveAPIView.as_view(), name='save_exercise'),
    path('exercise/bookmarks/', ExerciseBookmarkAPIView.as_view(), name='get_bookmarked_exercises'),
    path('exercise/bookmark/<int:exercise_id>/', ExerciseBookmarkAPIView.as_view(), name='bookmark_exercise'),
    path('exercise/aggregate/<int:conversation_id>/', ExerciseAggregateAPIView.as_view(), name='get_exercises_completion_status_for_conversation'),
    path('token/', TokenAPIView.as_view(), name='get_token_balance'),
    path('token/usage/', TokenUsageAPIView.as_view(), name='get_token_usage'),
    path('upload/profile-image-url/', ProfileImageUploadURLView.as_view(), name='profile-image-url'),
    path('upload/profile-image-confirm/', ProfileImageConfirmView.as_view(), name='profile-image-confirm'),
    path('payments/subscribe/', SubscribeView.as_view(), name='subscribe'),
    path('payments/tokens/', BuyTokensView.as_view(), name='buy-tokens'),
    path('payments/cancel/', CancelSubscriptionView.as_view(), name='cancel-subscription'),
    path('payments/resume/', ResumeSubscriptionView.as_view(), name='resume-subscription'),
    path('payments/setup-intent/', SetupIntentView.as_view(), name='setup-intent'),
    path('payments/update-method/', UpdatePaymentMethodView.as_view(), name='update-payment-method'),
    path('webhooks/stripe/', StripeWebhookView.as_view(), name='stripe-webhook'),
    path('feedback/', FeedbackView.as_view(), name='send-feedback'),
]
