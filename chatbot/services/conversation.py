from chatbot.models import Conversation


class ConversationService:
    @staticmethod    
    def get_messages_from_conversation(conversation_id):
        conversation = Conversation.objects.get(id=conversation_id)
        messages = conversation.message_set.all().order_by("created_at")
        if conversation:
            return messages
        return None