from chatbot.models import Conversation, User
from chatbot.services.user import FREE_TIER

FREE_MESSAGES_PER_CONVERSATION = 6
PRO_MESSAGES_PER_CONVERSATION = 25


class ConversationService:
    @staticmethod
    def get_messages_from_conversation(conversation_id, user_id):
        """
        Messages of one of the user's own conversations.
        Raises Conversation.DoesNotExist if it is missing or not theirs.
        """
        conversation = Conversation.objects.get(id=conversation_id, user_id=user_id)
        return conversation.message_set.all().order_by("created_at")

    @staticmethod
    def conversation_message_count_limit(user_id, conversation_id):
        """
        True when the conversation has hit the message cap for the user's tier.
        """
        conversation = Conversation.objects.get(id=conversation_id, user_id=user_id)
        user = User.objects.get(id=user_id)
        cap = (
            FREE_MESSAGES_PER_CONVERSATION
            if user.membership == FREE_TIER
            else PRO_MESSAGES_PER_CONVERSATION
        )
        return conversation.message_set.count() >= cap
