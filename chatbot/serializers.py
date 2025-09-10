from rest_framework import serializers
from .models import Conversation, Message


class ChatInputSerializer(serializers.ModelSerializer):
    prompt = serializers.CharField(required=True, allow_blank=False)
    experience_level = serializers.ChoiceField(
        choices=["beginner", "novice", "junior", "senior"], required=True
    )
    conversation_id = serializers.IntegerField()


class MessageSerializer(serializers.ModelSerializer):
    conversation = serializers.PrimaryKeyRelatedField(
        queryset=Conversation.objects.all(),
        allow_null=True,
    )
    class Meta:
        model = Message
        fields = ["from_user", "conversation", "model_used", "text", "json"]
        read_only_fields = ["id", "created_at"]


class ConversationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Conversation
        fields = ["id", "user", "title", "last_active", "created_at"]
        read_only_fields = ["id", "created_at"]
