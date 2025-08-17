from google import genai
import json

from chatbot.models import Conversation, Message
from chatbot.prompts import router_prompt, instructor_prompt


class LLMService:
    """
    A service class for processing user inputs and interacting with LLM APIs.
    """

    def __init__(self, conversation_id):
        self.client = genai.Client()
        self.conversation_id = conversation_id


    def respond(self, user_input, experience_level):
        if self.conversation_id:
            conversation = Conversation.objects.get(id=self.conversation_id)
        else:
            conversation = Conversation.objects.create()

        Message.objects.create(
            from_user=True,
            conversation=conversation,
            text=user_input
        )
        
        prompt = router_prompt.format(user_input)
        response = self.client.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            temperature=0.2,
        )

        try:
            json.loads(response.text)
            final_response = self.use_instructor(user_input, experience_level)
            Message.objects.create(
                from_user=False,
                conversation=conversation,
                model_used="gemini-2.5-pro",
                json=final_response
            )
            return final_response
        except json.JSONDecodeError:
            return response.text


    def use_instructor(self, user_input, experience_level):
        prompt = instructor_prompt.format(experience_level, user_input)
        response = self.client.generate_content(
            model="gemini-2.5-pro",
            contents=prompt,
            temperature=0.2,
        )
        try:
            final_response = json.loads(response.text)
            return final_response
        except json.JSONDecodeError as e:
            raise e
