from rest_framework import serializers
from .models import Conversation, Message


class MessageSerializer(serializers.ModelSerializer):
    conversation = serializers.PrimaryKeyRelatedField(
        queryset=Conversation.objects.all(),
        allow_null=True,
    )
    experience_level = serializers.CharField(write_only=True, required=True)

    class Meta:
        model = Message
        fields = ["from_user", "conversation", "model_used", "text", "json", "experience_level"]
        read_only_fields = ["id", "created_at"]


class ConversationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Conversation
        fields = ["id", "user", "title", "last_active", "created_at"]
        read_only_fields = ["id", "created_at"]
