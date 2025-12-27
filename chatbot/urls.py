from django.urls import path

from .views.exercise import ExerciseAPIView, ExerciseSubmissionAPIView, ExerciseSaveAPIView

from .views.user import UserWithPreferencesAPIView
from .views.user_preference import PreferencesAPIView
from .views.chat import ChatAPIView
from .views.conversation import ConversationAPIView

app_name = 'chatbot'

urlpatterns = [
    path('message/', ChatAPIView.as_view(), name='message'),
    path('conversations/messages/<int:pk>/', ChatAPIView.as_view(), name='conversation'),
    path('conversations/<str:email>/', ConversationAPIView.as_view(), name='conversations'),
    path('conversations/delete/<int:pk>/', ConversationAPIView.as_view(), name='delete conversation'),
    path('conversations/pin/<int:pk>/', ConversationAPIView.as_view(), name='delete conversation'),
    path('user/<str:email>/', UserWithPreferencesAPIView.as_view(), name='edit_user'),
    path('user/', UserWithPreferencesAPIView.as_view(), name='create_user'),
    path('user/preferences/<int:pk>/', PreferencesAPIView.as_view(), name='get_preferences_by_user_id'),
    path('exercise/<int:exercise_id>/', ExerciseAPIView.as_view(), name='get_exercise'),
    path('exercise/submit/', ExerciseSubmissionAPIView.as_view(), name='submit_exercise'),
    path('exercise/new/<int:message_id>', ExerciseAPIView.as_view(), name='create_exercise'),
    path('exercise/save/', ExerciseSaveAPIView.as_view(), name='save_exercise')
]
