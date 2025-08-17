from django.urls import path
from .views import ChatAPIView

urlpatterns = [
    path('chat/', ChatAPIView.as_view()),
    path('chat/<int:conversation_id>/', ChatAPIView.as_view()),
]