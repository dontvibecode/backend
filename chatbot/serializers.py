from rest_framework import serializers
from .models import Message


class ChatInputSerializer(serializers.Serializer):
    prompt = serializers.CharField(required=True, allow_blank=False)
    experience_level = serializers.ChoiceField(
        choices=["beginner", "novice", "junior", "senior"], required=True
    )
    conversation_id = serializers.IntegerField()


class MessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = Message
        fields = ["from_user", "conversation", "model_used", "text", "json"]
        read_only_fields = ["id", "created_at"]
