from rest_framework import serializers
from .models import Conversation, Message, Preferences, TokenUsage, User


class MessageSerializer(serializers.ModelSerializer):
    conversation = serializers.PrimaryKeyRelatedField(
        queryset=Conversation.objects.all(),
        allow_null=True,
    )
    experience_level = serializers.CharField(write_only=True, required=True)

    class Meta:
        model = Message
        fields = ["id", "created_at", "from_user", "conversation", "model_used", "text", "json", "experience_level"]
        read_only_fields = ["id", "created_at"]


class ConversationSerializer(serializers.ModelSerializer):
    exercises_count = serializers.IntegerField(read_only=True, default=0)
    exercises_almost_count = serializers.IntegerField(read_only=True, default=0)
    exercises_correct_count = serializers.IntegerField(read_only=True, default=0)

    class Meta:
        model = Conversation
        fields = ["id", "user", "title", "last_active", "pinned", "created_at", "tags", 
                  "exercises_count", "exercises_almost_count", "exercises_correct_count"]
        read_only_fields = ["id", "created_at"]

class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["username", "email", "method"]
        read_only_fields = ["id"]
    
class PreferencesSerializer(serializers.ModelSerializer):
    class Meta:
        model = Preferences
        fields = [
            "id",
            "user",
            "theme",
            "tab_size",
            "accent_color",
            "language",
            "profile_image",
            "email_notifications",
            "push_notifications",
            "in_app_notifications",
            "profile_visible",
            "share_data",
            "font_size",
            "compact_mode",
        ]
        read_only_fields = ["id", "user"]
    
class UserWithPreferencesSerializer(UserSerializer):
    preferences = PreferencesSerializer()

    class Meta:
        model = User
        fields = ["id", "username", "email", "preferences"]
        read_only_fields = ["id", "preferences"]
    
    def update(self, instance, validated_data):
        print("\n\n\nValidated data for update:", validated_data)
        preferences_data = validated_data.pop('preferences', None)

        print("\n\n\nPreferences data to update:", preferences_data)
        print("\n\n\nUser data after popping:", validated_data)

        validated_data.pop('email', None)

        for key, value in validated_data.items():
            setattr(instance, key, value)
        instance.save()

        if preferences_data:
            preferences = getattr(instance, 'preferences', None)
            if preferences:
                for key, value in preferences_data.items():
                    setattr(preferences, key, value)
                preferences.save()
        
        return instance
    

class ExerciseSubmissionSerializer(serializers.Serializer):
    ability_level = serializers.ChoiceField(
        choices=["beginner", "novice", "junior", "senior"],
        required=True
    )
    message_id = serializers.IntegerField(required=True)
    exercise_id = serializers.IntegerField(required=True)
    exercise_file_ids = serializers.ListField(child=serializers.IntegerField(), required=True)
    user_submissions = serializers.ListField(child=serializers.CharField(), required=True)
    

class TokenUsageSerializer(serializers.ModelSerializer):
    class Meta:
        model = TokenUsage
        fields = ["id", "user", "token_used", "timestamp", "tokens_remaining", "action"]
        read_only_fields = ["id", "user", "timestamp"]