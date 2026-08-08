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
        fields = ["id", "created_at", "from_user", "conversation", "model_used", "text", "json", "experience_level", "thought"]
        read_only_fields = ["id", "created_at"]

    def get_fields(self):
        fields = super().get_fields()
        request = self.context.get("request")
        if request is not None and request.user.is_authenticated:
            # Only allow posting into the requesting user's own conversations.
            fields["conversation"].queryset = Conversation.objects.filter(
                user=request.user
            )
        return fields


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


class UserProfileUpdateSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=150, required=False)
    preferences = PreferencesSerializer(required=False)
    
    
class UserResponseSerializer(serializers.ModelSerializer):
    preferences = PreferencesSerializer(read_only=True)

    class Meta:
        model = User
        fields = ["id", "username", "email", "preferences", "membership", "subscription_active", "membership_expires_at"]
        read_only_fields = fields
    

class ExerciseFileSubmissionsSerializer(serializers.Serializer):
    exercise_file_ids = serializers.ListField(
        child=serializers.IntegerField(), required=True, allow_empty=False
    )
    user_submissions = serializers.ListField(
        child=serializers.CharField(), required=True, allow_empty=False
    )

    def validate(self, attrs):
        exercise_file_ids = attrs["exercise_file_ids"]
        user_submissions = attrs["user_submissions"]

        if len(exercise_file_ids) != len(user_submissions):
            raise serializers.ValidationError(
                "exercise_file_ids and user_submissions must have the same length."
            )

        if len(set(exercise_file_ids)) != len(exercise_file_ids):
            raise serializers.ValidationError(
                {"exercise_file_ids": "Exercise file IDs must be unique."}
            )

        return attrs


class ExerciseSubmissionSerializer(ExerciseFileSubmissionsSerializer):
    ability_level = serializers.ChoiceField(
        choices=["beginner", "novice", "junior", "senior"],
        required=True
    )
    message_id = serializers.IntegerField(required=True)
    exercise_id = serializers.IntegerField(required=True)
    

class FeedbackSerializer(serializers.Serializer):
    """
    Validates the public feedback form. The email is echoed into a mail
    subject, so it is checked here rather than trusted as free text.
    """

    email = serializers.EmailField(required=True)
    message = serializers.CharField(required=True, allow_blank=False, max_length=5000)


class TokenUsageSerializer(serializers.ModelSerializer):
    class Meta:
        model = TokenUsage
        fields = ["id", "user", "token_used", "timestamp", "tokens_remaining", "action"]
        read_only_fields = ["id", "user", "timestamp"]