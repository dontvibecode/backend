from chatbot.models import Conversation, User


class ConversationService:
    @staticmethod
    def get_messages_from_conversation(conversation_id):
        conversation = Conversation.objects.get(id=conversation_id)
        messages = conversation.message_set.all().order_by("created_at")
        if conversation:
            return messages
        return None

    @staticmethod
    def conversation_message_count_limit(user_id, conversation_id):
        conversation = Conversation.objects.get(id=conversation_id)
        user = User.objects.get(id=user_id)
        count = conversation.message_set.count()
        if (user.membership == "free" and count < 6) or (
            user.membership == "pro" and count < 25
        ):
            return False
        return True