from django.urls import path

from .views.exercise import ExerciseAPIView, ExerciseBookmarkAPIView, ExerciseSubmissionAPIView, ExerciseSaveAPIView

from .views.user import UserWithPreferencesAPIView
from .views.user_preference import PreferencesAPIView
from .views.chat import ChatAPIView, ChatStreamAPIView
from .views.conversation import ConversationAPIView
from .views.token import TokenAPIView

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
    path('exercise/<int:message_id>/', ExerciseAPIView.as_view(), name='get_exercises'),
    path('exercise/submit/', ExerciseSubmissionAPIView.as_view(), name='submit_exercise'),
    path('exercise/new/<int:message_id>', ExerciseAPIView.as_view(), name='create_exercise'),
    path('exercise/save/', ExerciseSaveAPIView.as_view(), name='save_exercise'),
    path('exercise/bookmark/<str:user_email>/', ExerciseBookmarkAPIView.as_view(), name='get_bookmarked_exercises_for_user'),
    path('exercise/bookmark/<int:exercise_id>', ExerciseBookmarkAPIView.as_view(), name='bookmark_exercise'),
    path('token/<str:email>/', TokenAPIView.as_view(), name='get_token'),
]
