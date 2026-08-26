from django.test import SimpleTestCase

from chatbot.serializers import ChatStreamRequestSerializer


class ChatStreamRequestSerializerTests(SimpleTestCase):
    def test_exposes_only_supported_input_fields(self):
        serializer = ChatStreamRequestSerializer(
            data={
                "text": "Explain transactions",
                "conversation": None,
                "experience_level": "beginner",
                "from_user": False,
                "model_used": "client-choice",
                "json": {"ignored": True},
            }
        )

        serializer.is_valid(raise_exception=True)

        self.assertEqual(
            serializer.validated_data,
            {
                "text": "Explain transactions",
                "conversation": None,
                "experience_level": "beginner",
            },
        )
