from google import genai
from google.genai import types
import json

from chatbot.models import Conversation, ExerciseFile, Message, Exercise, User
from chatbot.prompts import router_prompt, instructor_prompt, exercise_evaluator_prompt, exercise_generator_prompt


class LLMService:
    """
    A service class for processing user inputs and interacting with LLM APIs.
    """

    def __init__(self, conversation_id, user_id):
        self.client = genai.Client()
        self.grounding_tool = types.Tool(
            google_search=types.GoogleSearch()
        )
        self.conversation_id = conversation_id
        self.user_id = user_id

    def has_enough_tokens(self, user_id, prompt):
        """
        Checks if the user has enough tokens to perform the action.
        """
        user = User.objects.get(id=user_id)
        input_token_count = self.client.models.count_tokens(
            model="gemini-3-pro-preview",
            contents=prompt,
        )
        print("Input token count:", input_token_count.total_tokens)
        print("User token limit:", user.token_limit)
        print("User token used:", user.token_used)
        if input_token_count.total_tokens > user.token_limit - user.token_used:
            return False
        return True


    def respond_streaming(self, user_id, user_input, experience_level):
        """
        Generator that yields progress events including thought summaries.
        
        Yields:
            dict with keys:
                - stage: "routing" | "routing_thought" | "instructor" | "instructor_thought" | "complete" | "error"
                - data: thought text (for thought stages) or final message data (for complete)
        """
        # Setup conversation (same as before)
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
                yield {"stage": "error", "data": str(e)}
                return

        # Save user message
        Message.objects.create(
            from_user=True,
            conversation_id=conversation.id,
            text=user_input,
            model_used="gemini-3-pro-preview",
        )

        raw_history = self.get_conversation_history()
        history_text = "\n".join(
            [
                f"{history['role'].upper()}: {history['parts'][0]}"
                for history in raw_history[:-1]
            ]
        )

        # === STAGE 1: Router (with streaming thoughts) ===
        yield {"stage": "routing", "data": None}

        prompt = router_prompt.format(user_prompt=user_input, history=history_text)
        
        router_response_text = ""
        final_router_token_count = 0
        
        # Stream the router response
        for chunk in self.client.models.generate_content_stream(
            model="gemini-3-flash-preview",
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                tools=[self.grounding_tool],
                thinking_config=types.ThinkingConfig(
                    include_thoughts=True
                )
            ),
        ):
            if chunk.usage_metadata.total_token_count:
                print("Current chunk token count:", chunk.usage_metadata.total_token_count)
                final_router_token_count = chunk.usage_metadata.total_token_count

            # Process each chunk
            for part in chunk.candidates[0].content.parts:
                if not part.text:
                    continue
                if part.thought:
                    # This is a thought summary - stream it!
                    yield {"stage": "routing_thought", "data": part.text}
                else:
                    # This is the actual response - accumulate it
                    router_response_text += part.text

        user = User.objects.get(id=self.user_id)
        user.token_used += final_router_token_count
        user.save(update_fields=["token_used"])
        print("Total tokens used for router:", final_router_token_count)

        # Parse router response
        try:
            response_json = json.loads(router_response_text, strict=False)
            use_instructor = response_json.get("redirect", False)
        except json.JSONDecodeError as e:
            yield {"stage": "error", "data": f"Router JSON parse error: {str(e)}"}
            return

        # Update conversation title
        title = response_json.get("title")
        if title:
            conversation.title = title
            conversation.save(update_fields=["title"])

        if use_instructor:
            # === STAGE 2: Instructor (with streaming thoughts) ===
            yield {"stage": "instructor", "data": None}

            instructor_prompt_text = instructor_prompt.format(
                ability_level=experience_level,
                user_prompt=user_input,
                conversation_history=history_text,
            )

            instructor_response_text = ""
            final_instructor_token_count = 0

            # Stream the instructor response
            for chunk in self.client.models.generate_content_stream(
                model="gemini-3-pro-preview",
                contents=instructor_prompt_text,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    tools=[self.grounding_tool],
                    thinking_config=types.ThinkingConfig(
                        include_thoughts=True
                    )
                ),
            ):
                if chunk.usage_metadata.total_token_count:
                    print("Cumulative instructor token count:", chunk.usage_metadata.total_token_count)
                    final_instructor_token_count = chunk.usage_metadata.total_token_count

                for part in chunk.candidates[0].content.parts:
                    if not part.text:
                        continue
                    if part.thought:
                        # Stream thought summaries to frontend
                        yield {"stage": "instructor_thought", "data": part.text}
                    else:
                        # Accumulate the JSON response
                        instructor_response_text += part.text

            user = User.objects.get(id=self.user_id)
            user.token_used += final_instructor_token_count
            user.save(update_fields=["token_used"])
            print("Total tokens used for instructor:", final_instructor_token_count)
            
            # Parse instructor response
            try:
                final_response = json.loads(instructor_response_text, strict=False)
            except json.JSONDecodeError as e:
                yield {"stage": "error", "data": f"Instructor JSON parse error: {str(e)}"}
                return

            # Create message and exercises (same as before)
            message = Message.objects.create(
                from_user=False,
                conversation_id=conversation.id,
                model_used="gemini-3-pro-preview",
                json=final_response,
            )

            exercise_title = final_response.get("exercise_title")
            exercise_tags = final_response.get("exercise_tags", [])
            exercises = final_response.get("exercises", [])

            if exercises:
                new_exercise = Exercise.objects.create(
                    message=message,
                    title=exercise_title,
                    tags=exercise_tags,
                )
                for exercise_file in exercises:
                    ExerciseFile.objects.create(
                        exercise=new_exercise,
                        filename=exercise_file["filename"],
                        text=exercise_file["text"],
                        code=exercise_file["code"],
                    )

            conversation.tags = final_response.get("tags", [])
            conversation.save(update_fields=["tags"])

            # Import serializer here to avoid circular imports
            from chatbot.serializers import MessageSerializer
            yield {"stage": "complete", "data": MessageSerializer(message).data}

        else:
            # Router handled it directly (no instructor needed)
            message = Message.objects.create(
                from_user=False,
                conversation_id=conversation.id,
                model_used="gemini-3-pro-preview",
                text=response_json.get("response_text"),
            )

            from chatbot.serializers import MessageSerializer
            yield {"stage": "complete", "data": MessageSerializer(message).data}

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
            model_used="gemini-3-flash-preview",
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
            model="gemini-3-flash-preview",
            contents=prompt,
            config=types.GenerateContentConfig(response_mime_type="application/json", tools=[self.grounding_tool]),
        )

        tokens_used = response.usage_metadata.total_token_count
        print("Tokens used for router:", tokens_used)
        user = User.objects.get(id=self.user_id)
        user.token_used += tokens_used
        user.save(update_fields=["token_used"])

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
                model_used="gemini-3-pro-preview",
                json=final_response,
            )
            print("Message creation succeeded.")
            exercise_title = final_response["exercise_title"]
            exercise_tags = final_response["exercise_tags"]
            exercises = final_response["exercises"]
            print("Exercises to be created:", exercises)
            print("Type of exercises variable:", type(exercises))
            new_exercise = Exercise.objects.create(
                message=message,
                title=exercise_title,
                tags=exercise_tags,
            )
            for exercise_file in exercises:
                try:
                    ExerciseFile.objects.create(
                        exercise=new_exercise,
                        filename=exercise_file["filename"],
                        text=exercise_file["text"],
                        code=exercise_file["code"],
                    )
                except Exception as e:
                    print("Failed to create exercise:", e)
                    raise
            conversation.tags = final_response["tags"]
            print("Updating conversation tags to:", conversation.tags)
            conversation.save(update_fields=["tags"])
            print("Message created:", message)
            return message
        else:
            message = Message.objects.create(
                from_user=False,
                conversation_id=conversation.id,
                model_used="gemini-3-pro-preview",
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
            model="gemini-3-pro-preview",
            contents=prompt,
            config=types.GenerateContentConfig(response_mime_type="application/json", tools=[self.grounding_tool]),
        )

        tokens_used = response.usage_metadata.total_token_count
        print("Tokens used for instructor:", tokens_used)
        user = User.objects.get(id=self.user_id)
        user.token_used += tokens_used
        user.save(update_fields=["token_used"])

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

    def mark_exercise(self, ability_level, message, exercise_id, user_submission):
        """
        Gives feedback for a particular coding exercise when the user submits it.
        """
        exercise = Exercise.objects.get(id=exercise_id)
        original_exercise = json.dumps(list(exercise.files.values("filename", "text", "code")))
        prompt = exercise_evaluator_prompt.format(
            ability_level=ability_level,
            message=message,
            original_exercise=original_exercise,
            user_submission=user_submission,
        )

        if not self.has_enough_tokens(self.user_id, prompt):
            return {"warning": "Insufficient tokens"}

        print("Exercise Evaluator Prompt successfully created.")
        response = self.client.models.generate_content(
            model="gemini-3-pro-preview",
            contents=prompt,
            config=types.GenerateContentConfig(response_mime_type="application/json", tools=[self.grounding_tool]),
        )

        tokens_used = response.usage_metadata.total_token_count
        print("Tokens used for exercise evaluation:", tokens_used)
        user = User.objects.get(id=self.user_id)
        user.token_used += tokens_used
        user.save(update_fields=["token_used"])

        print("Exercise Evaluator response text:", response.text)
        response_json = json.loads(response.text, strict=False)
        exercise.feedback = response_json
        exercise.save(update_fields=["feedback"])
        exercise.correctness = response_json.get("correctness")
        print("Updating exercise correctness to:", exercise.correctness)
        exercise.save(update_fields=["correctness"])
        print("Exercise correctness updated.")
        return response.text
    
    def generate_exercise(self, ability_level, message, exercise_files_text):
        """
        Generates a new coding exercise based on the provided message and exercise files.
        """
        full_message = json.dumps(message.json)
        prompt = exercise_generator_prompt.format(
            ability_level=ability_level,
            message=full_message,
            exercise_files=exercise_files_text,
        )
        print("Exercise Generation Prompt successfully created.")
        
        if not self.has_enough_tokens(self.user_id, prompt):
            return {"warning": "Insufficient tokens"}

        response = self.client.models.generate_content(
            model="gemini-3-pro-preview",
            contents=prompt,
            config=types.GenerateContentConfig(response_mime_type="application/json", tools=[self.grounding_tool]),
        )

        tokens_used = response.usage_metadata.total_token_count
        print("Tokens used for exercise generation:", tokens_used)
        user = User.objects.get(id=self.user_id)
        user.token_used += tokens_used
        user.save(update_fields=["token_used"])

        print("Exercise Generation response text:", response.text)
        try:
            exercise_data = json.loads(response.text, strict=False)
            exercise_title = exercise_data["exercise_title"]
            exercise_tags = exercise_data["exercise_tags"]
            new_exercise = Exercise.objects.create(
                message=message,
                title=exercise_title,
                tags=exercise_tags,
            )
            for exercise in exercise_data["exercises"]:
                ExerciseFile.objects.create(
                    exercise=new_exercise,
                    filename=exercise["filename"],
                    text=exercise["text"],
                    code=exercise["code"],
                )
            final_response = {
                "id": new_exercise.id,
                "files": exercise_data["exercises"],
            }
            return final_response
        except json.JSONDecodeError as e:
            print("JSON decoding error during exercise generation:", e)
            raise e
    
    def save_user_submission(self, exercise_file_id, user_submission):
        """
        Saves the user's submission for a specific exercise file.
        """
        try:
            exercise_file = ExerciseFile.objects.get(id=exercise_file_id)
            exercise_file.user_submission = user_submission
            exercise_file.save(update_fields=["user_submission"])
            print("User submission saved for ExerciseFile ID:", exercise_file_id)
            return {"status": "success"}
        except ExerciseFile.DoesNotExist:
            print("ExerciseFile not found for ID:", exercise_file_id)
            return {"status": "error", "message": "Exercise file not found."}

    def update_token_used(self, user, token_used):
        """
        Updates the token used for a given user.
        """
        user.token_used += token_used
        user.save(update_fields=["token_used"])
        return user.token_used
