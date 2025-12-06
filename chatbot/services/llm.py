from google import genai
from google.genai import types
import json

from chatbot.models import Conversation, Message
from chatbot.prompts import router_prompt, instructor_prompt


class LLMService:
    """
    A service class for processing user inputs and interacting with LLM APIs.
    """

    def __init__(self, conversation_id, user_id):
        self.client = genai.Client()
        self.conversation_id = conversation_id
        self.user_id = user_id

    def respond(self, user_input, experience_level):
        if self.conversation_id is not None:
            conversation = Conversation.objects.get(id=self.conversation_id)
        else:
            try:
                conversation = Conversation.objects.create(
                    user_id=self.user_id,
                    title="New Conversation",
                )
                self.conversation_id = conversation.id
            except Exception as e:
                raise e

        Message.objects.create(
            from_user=True,
            conversation_id=conversation.id,
            text=user_input,
            model_used="gemini-2.5-flash-lite",
        )

        raw_history = self.get_conversation_history()
        history_text = "\n".join(
            [
                f"{history['role'].upper()}: {history['parts'][0]}"
                for history in raw_history[:-1]
            ]
        )

        prompt = router_prompt.format(user_prompt=user_input, history=history_text)
        response = self.client.models.generate_content(
            model="gemini-2.5-pro",
            contents=prompt,
            config=types.GenerateContentConfig(response_mime_type="application/json"),
        )
        try:
            response_json = json.loads(response.text, strict=False)
            if response_json["redirect"]:
                use_instructor = True
            else:
                use_instructor = False
        except Exception as e:
            raise e

        title = response_json.get("title")
        print("Updating conversation title to:", title)
        if title:
            try:
                conversation.title = title
                conversation.save(update_fields=["title"])
            except Exception as e:
                print("Failed to update conversation title:", e)
                raise

        if use_instructor:
            print("Using instructor model")
            final_response = self.use_instructor(
                user_input, experience_level, history_text
            )
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

    def use_instructor(self, user_input, experience_level, history):
        prompt = instructor_prompt.format(
            ability_level=experience_level,
            user_prompt=user_input,
            conversation_history=history,
        )
        print("Instructor Prompt successfully created.")
        response = self.client.models.generate_content(
            model="gemini-2.5-pro",
            contents=prompt,
            config=types.GenerateContentConfig(response_mime_type="application/json"),
        )
        print("Cleaned response text:", response.text)
        try:
            final_response = json.loads(response.text, strict=False)
            return final_response
        except json.JSONDecodeError as e:
            print("JSON decoding error:", e)
            raise e

    def get_conversation_history(self, limit=20):
        """
        Fetches the last N messages and formats them for the Gemini API.
        10x Tip: We limit to the last 20-50 messages to prevent
        latency bloat, effectively creating a 'Sliding Window' of memory.
        """
        if not self.conversation_id:
            return []

        # Fetch messages in chronological order
        messages = Message.objects.filter(
            conversation_id=self.conversation_id
        ).order_by("created_at")[:limit]

        formatted_history = []
        for msg in messages:
            role = "user" if msg.from_user else "model"

            # If it's an AI message, we prefer the JSON content
            # (or the text representation of it) so the AI knows what it sent previously.
            if msg.json:
                content = json.dumps(msg.json)
            else:
                content = msg.text or ""

            formatted_history.append({"role": role, "parts": [content]})

        return formatted_history
