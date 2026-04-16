from django.urls import path

from .views.exercise import ExerciseAPIView, ExerciseAggregateAPIView, ExerciseBookmarkAPIView, ExerciseSubmissionAPIView, ExerciseSaveAPIView

from .views.user import UserMembershipAPIView, UserWithPreferencesAPIView
from .views.user_preference import PreferencesAPIView
from .views.chat import ChatAPIView, ChatStreamAPIView
from .views.conversation import ConversationAPIView
from .views.token import TokenAPIView, TokenUsageAPIView
from .views.upload import ProfileImageConfirmView, ProfileImageUploadURLView
from .views.payment import ResumeSubscriptionView, SubscribeView, BuyTokensView, CancelSubscriptionView, SetupIntentView, UpdatePaymentMethodView, StripeWebhookView

app_name = 'chatbot'

urlpatterns = [
    path('message/', ChatAPIView.as_view(), name='message'),
    path('message/stream/', ChatStreamAPIView.as_view(), name='message_with_streaming_thoughts'),
    path('conversations/messages/<int:pk>/', ChatAPIView.as_view(), name='get_messages_from_conversation'),
    path('conversations/<str:email>/', ConversationAPIView.as_view(), name='get_conversations_for_user'),
    path('conversations/delete/<int:pk>/', ConversationAPIView.as_view(), name='delete_conversation'),
    path('conversations/pin/<int:pk>/', ConversationAPIView.as_view(), name='pin_conversation'),
    path('user/<str:email>/', UserWithPreferencesAPIView.as_view(), name='edit_user_with_preferences'),
    path('user/', UserWithPreferencesAPIView.as_view(), name='create_user_with_preferences'),
    path('user/preferences/<int:pk>/', PreferencesAPIView.as_view(), name='get_preferences_by_user_id'),
    path('user/membership/<str:email>/', UserMembershipAPIView.as_view(), name='get_user_membership'),
    path('exercise/<int:message_id>/', ExerciseAPIView.as_view(), name='get_exercises'),
    path('exercise/submit/', ExerciseSubmissionAPIView.as_view(), name='submit_exercise'),
    path('exercise/new/<int:message_id>', ExerciseAPIView.as_view(), name='create_exercise'),
    path('exercise/save/', ExerciseSaveAPIView.as_view(), name='save_exercise'),
    path('exercise/bookmark/<str:user_email>/', ExerciseBookmarkAPIView.as_view(), name='get_bookmarked_exercises_for_user'),
    path('exercise/bookmark/<int:exercise_id>', ExerciseBookmarkAPIView.as_view(), name='bookmark_exercise'),
    path('exercise/aggregate/<int:conversation_id>', ExerciseAggregateAPIView.as_view(), name='get_exercises_completion_status_for_conversation'),
    path('token/<str:email>/', TokenAPIView.as_view(), name='get_token'),
    path('token/usage/<str:email>/', TokenUsageAPIView.as_view(), name='get_token_usage_for_user'),
    path('upload/profile-image-url/', ProfileImageUploadURLView.as_view(), name='profile-image-url'),
    path('upload/profile-image-confirm/', ProfileImageConfirmView.as_view(), name='profile-image-confirm'),
    path('payments/subscribe/', SubscribeView.as_view(), name='subscribe'),
    path('payments/tokens/', BuyTokensView.as_view(), name='buy-tokens'),
    path('payments/cancel/', CancelSubscriptionView.as_view(), name='cancel-subscription'),
    path('payments/resume/', ResumeSubscriptionView.as_view(), name='resume-subscription'),
    path('payments/setup-intent/', SetupIntentView.as_view(), name='setup-intent'),
    path('payments/update-method/', UpdatePaymentMethodView.as_view(), name='update-payment-method'),
    path('webhooks/stripe/', StripeWebhookView.as_view(), name='stripe-webhook'),
]
