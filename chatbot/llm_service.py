from google import genai
import json

from chatbot.prompts import router_prompt, instructor_prompt


class LLMService:
    """
    A service class for processing user inputs and interacting with LLM APIs.
    """

    def __init__(self):
        self.client = genai.Client()

    def respond(self, user_input, experience_level):
        prompt = router_prompt.format(user_input)
        response = self.client.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            temperature=0.2,
        )
        try:
            json.loads(response.text)
            final_response = self.use_instructor(user_input, experience_level)
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
        except json.JSONDecodeError:
            return response.text
