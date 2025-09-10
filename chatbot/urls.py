from django.urls import path
from .views import ChatAPIView, ConversationAPIView

app_name = 'chatbot'

urlpatterns = [
    path('message/', ChatAPIView.as_view(), name='message'),
    path('conversations/<int:conversation_id>/', ChatAPIView.as_view(), name='conversation'),
    path('conversations/', ConversationAPIView.as_view(), name='conversations'),
]