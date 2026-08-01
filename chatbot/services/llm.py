import json
import logging
from datetime import timedelta

from google import genai
from google.genai import types
from django.utils import timezone

from chatbot.models import (
    Conversation,
    ExerciseFile,
    Message,
    Exercise,
    TokenUsage,
    User,
)
from chatbot.prompts import (
    router_system_instruction,
    instructor_system_instruction,
    exercise_evaluator_prompt,
    exercise_generator_prompt,
)
from chatbot.services.user import FREE_TIER, PRO_TIER, UserService

logger = logging.getLogger(__name__)

# Cheap model that classifies the request and answers simple turns itself.
ROUTER_MODEL = "gemini-3.1-flash-lite-preview"
# Expensive model that writes lessons and exercises.
INSTRUCTOR_MODEL = "gemini-3.1-pro-preview"


class LLMService:
    """
    A service class for processing user inputs and interacting with LLM APIs.

    Uses Gemini's explicit caching for system instructions to reduce token costs.
    The router and instructor system instructions are cached at the class level
    and shared across all instances.
    """

    # Class-level cache references (shared across all instances)
    # These are created lazily on first use and persist for 24 hours
    _router_cache = None
    _instructor_cache = None
    _cache_initialized = False

    def __init__(self, conversation_id, user_id):
        self.client = genai.Client()
        self.grounding_tool = types.Tool(google_search=types.GoogleSearch())
        self.conversation_id = conversation_id
        self.user_id = user_id

        # Initialize caches if not already done
        self._ensure_caches_initialized()

    @classmethod
    def _ensure_caches_initialized(cls):
        """
        Lazily initialize the system instruction caches.
        This is called once per process and the caches are reused across all requests.
        """
        if cls._cache_initialized:
            return

        try:
            client = genai.Client()

            # Define grounding tool for caches
            grounding_tool = types.Tool(google_search=types.GoogleSearch())

            # Create router cache (includes tools since they can't be in generate request)
            logger.info("Creating router system instruction cache...")
            cls._router_cache = client.caches.create(
                model=ROUTER_MODEL,
                config=types.CreateCachedContentConfig(
                    system_instruction=router_system_instruction,
                    tools=[grounding_tool],
                    ttl="86400s",  # 24 hours
                ),
            )
            logger.info("Router cache created: %s", cls._router_cache.name)

            # NOTE: We do NOT cache the instructor because grounding (google_search)
            # doesn't work reliably when baked into a cache. The instructor generates
            # lessons with links, so grounding is critical there.
            # The router cache saves tokens; instructor uses grounding for link quality.
            cls._instructor_cache = None
            logger.info(
                "Instructor cache skipped (grounding requires direct tool access)"
            )

            cls._cache_initialized = True
            logger.info("System instruction caches initialized successfully!")

        except Exception:
            logger.exception(
                "Failed to initialize caches; falling back to non-cached mode"
            )
            cls._cache_initialized = False

    def _build_instructor_contents(
        self, prepared_context, user_input, experience_level
    ):
        """
        Build the contents for the instructor call using the prepared_context from router.
        This is much smaller than full history - just the summary.

        Args:
            prepared_context: Dict with learning_summary, user_level_notes, critical_verbatim
            user_input: The user's current message
            experience_level: The user's ability level

        Returns:
            str: Formatted context string for the instructor
        """
        learning_summary = prepared_context.get(
            "learning_summary", "No prior context available."
        )
        user_level_notes = prepared_context.get("user_level_notes", "None provided.")
        critical_verbatim = prepared_context.get("critical_verbatim")

        contents = f"""=== LEARNING CONTEXT (from Router AI) ===

            LEARNING SUMMARY:
            {learning_summary}

            USER LEVEL NOTES:
            {user_level_notes}

        """

        if critical_verbatim:
            contents += f"""CRITICAL VERBATIM (exact text from user):
            {critical_verbatim}

            """

            contents += f"""=== CURRENT REQUEST ===

            ABILITY LEVEL: {experience_level}

            USER'S MESSAGE:
            {user_input}
        """

        return contents

    def has_enough_tokens(self, user_id, prompt):
        """
        Checks if the user has enough tokens to perform the action.

        Purely a check: membership expiry and allowance refills are applied by
        UserService.reconcile_membership when the request is authenticated.
        """
        input_token_count = self.client.models.count_tokens(
            model=INSTRUCTOR_MODEL,
            contents=prompt,
        )
        return input_token_count.total_tokens <= UserService.tokens_remaining(user_id)

    def respond_streaming(self, user_id, user_input, experience_level):
        """
        Generator that yields progress events including thought summaries.

        Uses cached system instructions for both router and instructor to reduce token costs.
        Router receives full history, but instructor only receives prepared_context (summary).

        Yields:
            dict with keys:
                - stage: "routing" | "routing_thought" | "instructor" | "instructor_thought" | "complete" | "error"
                - data: thought text (for thought stages) or final message data (for complete)
        """
        # Setup conversation
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
            model_used=INSTRUCTOR_MODEL,
        )

        # Build history contents for router (full history)
        raw_history = self.get_conversation_history()

        # === STAGE 1: Router (with streaming thoughts) ===
        yield {"stage": "routing", "data": None}

        router_response_text = ""
        final_router_token_count = 0
        # Build router config - use cache if available
        # Note: When using cached_content, tools must be in the cache (not here)
        if self._router_cache:
            router_config = types.GenerateContentConfig(
                cached_content=self._router_cache.name,
                response_mime_type="application/json",
                thinking_config=types.ThinkingConfig(include_thoughts=True),
            )
            logger.info("Using cached router system instruction")
        else:
            # Fallback: include system_instruction and tools directly
            router_config = types.GenerateContentConfig(
                system_instruction=router_system_instruction,
                response_mime_type="application/json",
                tools=[self.grounding_tool],
                thinking_config=types.ThinkingConfig(include_thoughts=True),
            )
            logger.info("Using non-cached router system instruction (fallback)")

        # Stream the router response
        for chunk in self.client.models.generate_content_stream(
            model=ROUTER_MODEL,
            contents=raw_history,  # Full history as contents
            config=router_config,
        ):
            if chunk.usage_metadata and chunk.usage_metadata.total_token_count:
                final_router_token_count = chunk.usage_metadata.total_token_count
                logger.debug(
                    "Router chunk tokens: %s (cached %s)",
                    chunk.usage_metadata.total_token_count,
                    chunk.usage_metadata.cached_content_token_count,
                )

            # Process each chunk
            if (
                chunk.candidates
                and chunk.candidates[0].content
                and chunk.candidates[0].content.parts
            ):
                for part in chunk.candidates[0].content.parts:
                    if not part.text:
                        continue
                    if part.thought:
                        # This is a thought summary - stream it!
                        yield {"stage": "routing_thought", "data": part.text}
                    else:
                        # This is the actual response - accumulate it
                        router_response_text += part.text

        tokens_remaining = UserService.consume_tokens(
            self.user_id, final_router_token_count
        )
        logger.info(
            "Router used %s tokens for user %s", final_router_token_count, self.user_id
        )

        # Parse router response
        try:
            response_json = json.loads(router_response_text, strict=False)
            use_instructor = response_json.get("redirect", False)
            prepared_context = response_json.get("prepared_context")
        except json.JSONDecodeError as e:
            yield {
                "stage": "error",
                "data": f"Router JSON parse error: {str(e)}. Response: {router_response_text[:500]}",
            }
            return

        # Update conversation title
        title = response_json.get("title")
        if title:
            conversation.title = title
            conversation.save(update_fields=["title"])

        if use_instructor:
            # === STAGE 2: Instructor (with streaming thoughts) ===
            yield {"stage": "instructor", "data": None}

            # Build instructor contents using prepared_context (NOT full history!)
            if prepared_context:
                instructor_contents = self._build_instructor_contents(
                    prepared_context, user_input, experience_level
                )
                logger.info(
                    f"Using prepared_context from router (approx {len(instructor_contents)} chars)"
                )
            else:
                # Fallback if router didn't provide prepared_context
                logger.info(
                    "Warning: No prepared_context from router, using basic context"
                )
                instructor_contents = f"""ABILITY LEVEL: {experience_level}

                    USER'S MESSAGE:
                    {user_input}
                """

            instructor_response_text = ""
            instructor_response_thought = ""
            final_instructor_token_count = 0

            # Build instructor config
            # Note: Instructor is NOT cached because grounding doesn't work reliably in caches
            # This ensures links in lessons are accurate via real-time Google Search
            if self._instructor_cache:
                instructor_config = types.GenerateContentConfig(
                    cached_content=self._instructor_cache.name,
                    response_mime_type="application/json",
                    thinking_config=types.ThinkingConfig(include_thoughts=True),
                )
                logger.info("Using cached instructor system instruction")
            else:
                # Use grounding tool for accurate, up-to-date links
                instructor_config = types.GenerateContentConfig(
                    system_instruction=instructor_system_instruction,
                    response_mime_type="application/json",
                    tools=[self.grounding_tool],
                    thinking_config=types.ThinkingConfig(include_thoughts=True),
                )
                logger.info("Using instructor with grounding (not cached)")

            # Stream the instructor response
            for chunk in self.client.models.generate_content_stream(
                model=INSTRUCTOR_MODEL,
                contents=instructor_contents,  # Only prepared_context, NOT full history!
                config=instructor_config,
            ):
                if chunk.usage_metadata:
                    final_instructor_token_count = (
                        chunk.usage_metadata.total_token_count
                    )
                    logger.debug(
                        "Instructor chunk tokens: %s (cached %s)",
                        chunk.usage_metadata.total_token_count,
                        chunk.usage_metadata.cached_content_token_count,
                    )

                if (
                    chunk.candidates
                    and chunk.candidates[0].content
                    and chunk.candidates[0].content.parts
                ):
                    for part in chunk.candidates[0].content.parts:
                        if not part.text:
                            continue
                        if part.thought:
                            # Stream thought summaries to frontend
                            instructor_response_thought += part.text
                            yield {"stage": "instructor_thought", "data": part.text}
                        else:
                            # Accumulate the JSON response
                            instructor_response_text += part.text

            tokens_remaining = UserService.consume_tokens(
                self.user_id, final_instructor_token_count
            )
            logger.info(
                "Instructor used %s tokens for user %s",
                final_instructor_token_count,
                self.user_id,
            )
            # Parse instructor response
            try:
                final_response = json.loads(instructor_response_text, strict=False)
            except json.JSONDecodeError as e:
                yield {
                    "stage": "error",
                    "data": f"Instructor JSON parse error: {str(e)}",
                }
                return

            # Create message and exercises (same as before)
            message = Message.objects.create(
                from_user=False,
                conversation_id=conversation.id,
                model_used=INSTRUCTOR_MODEL,
                json=final_response,
                thought=instructor_response_thought,
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

            TokenUsage.objects.create(
                user_id=self.user_id,
                token_used=final_router_token_count + final_instructor_token_count,
                tokens_remaining=tokens_remaining,
                action="instructor",
            )

            # Import serializer here to avoid circular imports
            from chatbot.serializers import MessageSerializer

            yield {"stage": "complete", "data": MessageSerializer(message).data}

        else:
            # Router handled it directly (no instructor needed)
            message = Message.objects.create(
                from_user=False,
                conversation_id=conversation.id,
                model_used=ROUTER_MODEL,
                text=response_json.get("response_text"),
            )

            TokenUsage.objects.create(
                user_id=self.user_id,
                token_used=final_router_token_count,
                tokens_remaining=tokens_remaining,
                action="router",
            )

            from chatbot.serializers import MessageSerializer

            yield {"stage": "complete", "data": MessageSerializer(message).data}

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
        ).order_by("-created_at")[:limit][::-1]

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

    def mark_exercise(self, ability_level, message, exercise_id, user_submission, user_id):
        """
        Gives feedback for a particular coding exercise when the user submits it.
        """
        user = User.objects.get(id=self.user_id)
        explain = True
        if user.membership == FREE_TIER:
            if user.free_feedback_for_exercises_refresh_at:
                if user.free_feedback_for_exercises_refresh_at > timezone.now():
                    explain = False
                else:
                    # Refresh period passed - grant new feedback and reset timer
                    user.free_feedback_for_exercises_refresh_at = (
                        timezone.now() + timedelta(hours=24)
                    )
                    user.save(update_fields=["free_feedback_for_exercises_refresh_at"])
            else:
                # First time - grant feedback and set timer
                user.free_feedback_for_exercises_refresh_at = (
                    timezone.now() + timedelta(hours=24)
                )
                user.save(update_fields=["free_feedback_for_exercises_refresh_at"])

        exercise = Exercise.objects.get(id=exercise_id, converation__message__user_id=user_id)
        original_exercise = json.dumps(
            list(exercise.files.values("filename", "text", "code"))
        )
        prompt = exercise_evaluator_prompt.format(
            ability_level=ability_level,
            explain=explain,
            message=message,
            original_exercise=original_exercise,
            user_submission=user_submission,
        )

        if not self.has_enough_tokens(self.user_id, prompt):
            return {"warning": "Insufficient tokens"}

        logger.info("Exercise Evaluator Prompt successfully created.")
        response = self.client.models.generate_content(
            model=INSTRUCTOR_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json", tools=[self.grounding_tool]
            ),
        )

        tokens_used = response.usage_metadata.total_token_count
        logger.info(
            "Exercise evaluation used %s tokens for user %s", tokens_used, self.user_id
        )
        tokens_remaining = UserService.consume_tokens(self.user_id, tokens_used)

        TokenUsage.objects.create(
            user_id=self.user_id,
            token_used=tokens_used,
            tokens_remaining=tokens_remaining,
            action="exercise_evaluator",
        )

        logger.info("Exercise Evaluator response text: %s", response.text)
        response_json = json.loads(response.text, strict=False)
        exercise.feedback = response_json
        exercise.save(update_fields=["feedback"])
        exercise.correctness = response_json.get("correctness")
        logger.info("Updating exercise correctness to: %s", exercise.correctness)
        exercise.save(update_fields=["correctness"])
        logger.info("Exercise correctness updated.")
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
        logger.info("Exercise Generation Prompt successfully created.")

        if not self.has_enough_tokens(self.user_id, prompt):
            return {"warning": "Insufficient tokens"}

        response = self.client.models.generate_content(
            model=INSTRUCTOR_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json", tools=[self.grounding_tool]
            ),
        )

        tokens_used = response.usage_metadata.total_token_count
        logger.info(
            "Exercise generation used %s tokens for user %s", tokens_used, self.user_id
        )
        tokens_remaining = UserService.consume_tokens(self.user_id, tokens_used)

        TokenUsage.objects.create(
            user_id=self.user_id,
            token_used=tokens_used,
            tokens_remaining=tokens_remaining,
            action="exercise_generator",
        )

        logger.info("Exercise Generation response text: %s", response.text)
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
            logger.info("JSON decoding error during exercise generation: %s", e)
            raise e

    def save_user_submission(self, exercise_file_id, user_submission, user_id):
        """
        Saves the user's submission for a specific exercise file.
        """
        try:
            exercise_file = ExerciseFile.objects.get(
                id=exercise_file_id, message__conversation__user_id=user_id
            )
            exercise_file.user_submission = user_submission
            exercise_file.save(update_fields=["user_submission"])
            logger.info(
                "User submission saved for ExerciseFile ID: %s", exercise_file_id
            )
            return {"status": "success"}
        except ExerciseFile.DoesNotExist:
            logger.info("ExerciseFile not found for ID: %s", exercise_file_id)
            return {"status": "error", "message": "Exercise file not found."}

    def exercise_count_limit_reached(self, user_id, message_id):
        """
        Checks if the user has reached the exercise count limit.
        """
        user = User.objects.get(id=user_id)
        message = Message.objects.get(id=message_id)
        if (user.membership == FREE_TIER and message.exercises.count() >= 2) or (
            user.membership == PRO_TIER and message.exercises.count() >= 10
        ):
            return True
        return False
