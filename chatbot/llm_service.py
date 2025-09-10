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
        if self.conversation_id is not None:
            conversation = Conversation.objects.get(id=self.conversation_id)
        else:
            try:
                conversation = Conversation.objects.create(
                    user_id=0,
                    title="New Conversation",
                )
                self.conversation_id = conversation.id
            except Exception as e:
                raise e

        Message.objects.create(
            from_user=True,
            conversation_id=conversation.id,
            text=user_input,
            model_used="gemini-2.5-pro",
        )
        prompt = router_prompt.format(user_prompt=user_input)
        response = self.client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
        )
        cleaned_response_text = response.text[7:-3].strip()
        try:
            response_json = json.loads(cleaned_response_text)
            if response_json["is_coding_question"]:
                use_instructor = True
            else:
                use_instructor = False
        except Exception as e:
            raise e
        
        title = response_json.get("title")
        if title:
            try:
                conversation.title = title
                conversation.save(update_fields=["title"])
            except Exception as e:
                print("Failed to update conversation title:", e)
                raise

        if use_instructor:
            print("Using instructor model")
            final_response = self.use_instructor(user_input, experience_level)
            print("Final response from instructor:", final_response)
            message = Message.objects.create(
                from_user=False,
                conversation_id=conversation.id,
                model_used="gemini-2.5-pro",
                json=final_response,
            )
            print("Message created:", message)
            return message
        else:
            message = Message.objects.create(
                from_user=False,
                conversation_id=conversation.id,
                model_used="gemini-2.5-pro",
                text=response_json["response_text"],
            )
            return message

    def use_instructor(self, user_input, experience_level):
        prompt = instructor_prompt.format(
            ability_level=experience_level, user_prompt=user_input
        )
        response = self.client.models.generate_content(
            model="gemini-2.5-pro",
            contents=prompt,
        )
        cleaned_response_text = response.text[7:-3].strip()
        try:
            final_response = json.loads(cleaned_response_text)
            return final_response
        except json.JSONDecodeError as e:
            raise e
