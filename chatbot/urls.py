from django.urls import path
from .views import ChatAPIView

app_name = 'chatbot'

urlpatterns = [
    path('message/', ChatAPIView.as_view(), name='message'),
    path('<int:conversation_id>/', ChatAPIView.as_view(), name='conversation'),
]