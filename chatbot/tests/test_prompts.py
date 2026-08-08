from django.test import SimpleTestCase

from chatbot.services.llm import LLMService


class InstructorPromptTests(SimpleTestCase):
    def setUp(self):
        # This method only formats strings; bypass __init__ so no Gemini client
        # or credentials are needed for a true unit test.
        self.service = LLMService.__new__(LLMService)

    def test_current_request_is_present_without_critical_verbatim(self):
        contents = self.service._build_instructor_contents(
            prepared_context={
                "learning_summary": "The user is learning loops.",
                "user_level_notes": "Beginner",
            },
            user_input="How does a for loop work?",
            experience_level="beginner",
        )

        self.assertIn("=== CURRENT REQUEST ===", contents)
        self.assertIn("How does a for loop work?", contents)
        self.assertIn("beginner", contents)

    def test_critical_verbatim_is_optional_extra_context(self):
        contents = self.service._build_instructor_contents(
            prepared_context={
                "learning_summary": "The user is debugging.",
                "user_level_notes": "Junior",
                "critical_verbatim": "IndexError: list index out of range",
            },
            user_input="Why does this crash?",
            experience_level="junior",
        )

        self.assertIn("IndexError: list index out of range", contents)
        self.assertIn("Why does this crash?", contents)
