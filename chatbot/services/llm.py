import json
import logging
import threading
from datetime import timedelta
from pydantic import BaseModel, Field, ValidationError

from google import genai
from google.genai import types
from django.db import transaction
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
from chatbot.services.user import (
    FREE_TIER,
    PRO_TIER,
    InsufficientTokensError,
    UserService,
)

logger = logging.getLogger(__name__)

# Cheap model that classifies the request and answers simple turns itself.
ROUTER_MODEL = "gemini-3.1-flash-lite-preview"
# Expensive model that writes lessons and exercises.
INSTRUCTOR_MODEL = "gemini-3.1-pro-preview"


class ExerciseFile(BaseModel):
    filename: str
    text: str
    code: str


class ExerciseGeneration(BaseModel):
    exercise_title: str
    exercise_tags: list[str] = Field(min_length=2, max_length=5)
    exercises: list[ExerciseFile] = Field(min_length=1, max_length=1)  # prompt says exactly one


class LLMService:
    """
    A service class for processing user inputs and interacting with LLM APIs.

    The router's system instruction is uploaded to Gemini once and referenced by
    handle on later calls, so we do not resend it with every request.

    NOTE: the instructor is deliberately NOT cached. Grounding (google_search)
    does not work reliably when baked into a cache, and the instructor writes
    lessons containing links, so accurate real-time search matters more there
    than the token saving.
    """

    # How long Gemini keeps the uploaded content.
    CACHE_TTL = timedelta(hours=24)
    # Replace the handle this long before it expires, so a request that is
    # already in flight never holds one that dies mid-call.
    CACHE_REFRESH_MARGIN = timedelta(minutes=30)
    # After a failed creation, fall back to uncached mode for this long rather
    # than retrying on every single request.
    CACHE_FAILURE_BACKOFF = timedelta(minutes=5)

    # Shared by every instance in this process.
    _router_cache = None
    # The expiry is stored alongside the handle: a handle on its own says
    # nothing about whether it still works.
    _router_cache_expires_at = None
    # Set after a failure so we stop hammering the API.
    _cache_retry_after = None
    # Only one request may create a cache; the rest wait and reuse the result.
    _cache_lock = threading.Lock()

    def __init__(self, conversation_id, user_id):
        self.client = genai.Client()
        self.grounding_tool = types.Tool(google_search=types.GoogleSearch())
        self.conversation_id = conversation_id
        self.user_id = user_id

    @classmethod
    def _router_cache_is_usable(cls, now):
        return (
            cls._router_cache is not None
            and cls._router_cache_expires_at is not None
            and now < cls._router_cache_expires_at - cls.CACHE_REFRESH_MARGIN
        )

    @classmethod
    def _get_router_cache(cls):
        """
        Returns a cache handle we are confident still works, or None meaning
        "send the full system instruction on this request instead".

        Callers must treat None as normal: the cache is a cost saving, so
        losing it has to degrade the bill, never the product.
        """
        now = timezone.now()

        # Fast path, and the answer almost every time: no lock, no network.
        if cls._router_cache_is_usable(now):
            return cls._router_cache

        # Creation failed recently, so don't try again yet.
        if cls._cache_retry_after and now < cls._cache_retry_after:
            return None

        with cls._cache_lock:
            # Re-check inside the lock: while we were waiting, the request
            # ahead of us has probably already created a fresh cache. Without
            # this, every concurrent request at startup creates its own.
            if cls._router_cache_is_usable(now):
                return cls._router_cache

            try:
                logger.info("Creating router system instruction cache...")
                cls._router_cache = genai.Client().caches.create(
                    model=ROUTER_MODEL,
                    config=types.CreateCachedContentConfig(
                        system_instruction=router_system_instruction,
                        # Tools have to live in the cache; they cannot be passed
                        # on a request that references cached content.
                        tools=[types.Tool(google_search=types.GoogleSearch())],
                        ttl=f"{int(cls.CACHE_TTL.total_seconds())}s",
                    ),
                )
                cls._router_cache_expires_at = now + cls.CACHE_TTL
                cls._cache_retry_after = None
                logger.info(
                    "Router cache %s created, refreshing before %s",
                    cls._router_cache.name,
                    cls._router_cache_expires_at - cls.CACHE_REFRESH_MARGIN,
                )
            except Exception:
                logger.exception(
                    "Router cache creation failed; using uncached mode for %s",
                    cls.CACHE_FAILURE_BACKOFF,
                )
                cls._router_cache = None
                cls._router_cache_expires_at = None
                cls._cache_retry_after = now + cls.CACHE_FAILURE_BACKOFF

            return cls._router_cache

    @classmethod
    def _discard_router_cache(cls):
        """
        Forgets the current handle after the API has rejected it, so the next
        request creates a new one instead of reusing a dead reference.
        """
        with cls._cache_lock:
            cls._router_cache = None
            cls._router_cache_expires_at = None

    @staticmethod
    def _is_dead_cache_error(error):
        """
        True when the API rejected our cache handle because it no longer
        exists. Matched on the message because the SDK raises a generic client
        error rather than a dedicated exception type for this case.
        """
        text = str(error).lower()
        return "cachedcontent" in text or "cached_content" in text

    def _router_config(self, cache):
        """
        Request config for the router, with or without a cache handle.
        """
        if cache is not None:
            return types.GenerateContentConfig(
                cached_content=cache.name,
                response_mime_type="application/json",
                thinking_config=types.ThinkingConfig(include_thoughts=True),
            )
        return types.GenerateContentConfig(
            system_instruction=router_system_instruction,
            response_mime_type="application/json",
            tools=[self.grounding_tool],
            thinking_config=types.ThinkingConfig(include_thoughts=True),
        )

    def _stream_router(self, contents):
        """
        Streams the router response, transparently retrying without the cache
        if Gemini rejects the handle.

        Proactive refresh narrows the window but cannot close it: Gemini may
        drop cached content early. The retry only happens if nothing has been
        yielded yet, so a mid-stream failure is never replayed as duplicate
        output.
        """
        cache = self._get_router_cache()
        logger.info(
            "Router using %s system instruction",
            "cached" if cache else "inline (uncached)",
        )

        produced_output = False
        try:
            for chunk in self.client.models.generate_content_stream(
                model=ROUTER_MODEL,
                contents=contents,
                config=self._router_config(cache),
            ):
                produced_output = True
                yield chunk
            return
        except Exception as e:
            if produced_output or cache is None or not self._is_dead_cache_error(e):
                raise
            logger.warning(
                "Router cache %s was rejected by the API; retrying uncached",
                cache.name,
            )
            self._discard_router_cache()

        yield from self.client.models.generate_content_stream(
            model=ROUTER_MODEL,
            contents=contents,
            config=self._router_config(None),
        )

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

        # Stream the router response. _stream_router picks up a live cache
        # handle (or falls back to the full instruction) and retries on its own
        # if the handle turns out to be dead.
        for chunk in self._stream_router(raw_history):
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

        try:
            tokens_remaining = UserService.consume_tokens(
                self.user_id, final_router_token_count
            )
        except InsufficientTokensError:
            yield {"stage": "error", "data": "Insufficient tokens"}
            return
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

            # The instructor is never cached (see the class docstring): it needs
            # the grounding tool passed directly so its lesson links are real.
            instructor_config = types.GenerateContentConfig(
                system_instruction=instructor_system_instruction,
                response_mime_type="application/json",
                tools=[self.grounding_tool],
                thinking_config=types.ThinkingConfig(include_thoughts=True),
            )

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

            try:
                tokens_remaining = UserService.consume_tokens(
                    self.user_id, final_instructor_token_count
                )
            except InsufficientTokensError:
                yield {"stage": "error", "data": "Insufficient tokens"}
                return
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

    def mark_exercise(
        self,
        ability_level,
        message_id,
        exercise_id,
        exercise_file_ids,
        user_submissions,
    ):
        """
        Evaluates and saves a user's submissions for one of their exercises.

        The message, exercise, and files are checked as one ownership chain
        before the billable LLM call. Once the call succeeds, feedback, token
        accounting, and all file submissions are persisted atomically.
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

        exercise = (
            Exercise.objects.select_related("message")
            .prefetch_related("files")
            .get(
                id=exercise_id,
                message_id=message_id,
                message__conversation__user_id=self.user_id,
            )
        )
        files_by_id = {exercise_file.id: exercise_file for exercise_file in exercise.files.all()}
        if any(file_id not in files_by_id for file_id in exercise_file_ids):
            raise ExerciseFile.DoesNotExist(
                "One or more exercise files do not belong to this exercise."
            )

        original_exercise = json.dumps(
            [
                {
                    "filename": exercise_file.filename,
                    "text": exercise_file.text,
                    "code": exercise_file.code,
                }
                for exercise_file in files_by_id.values()
            ]
        )
        prompt = exercise_evaluator_prompt.format(
            ability_level=ability_level,
            explain=explain,
            message=json.dumps(exercise.message.json),
            original_exercise=original_exercise,
            user_submission=json.dumps(user_submissions),
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

        logger.info("Exercise Evaluator response text: %s", response.text)
        response_json = json.loads(response.text, strict=False)

        with transaction.atomic():
            locked_exercise = Exercise.objects.select_for_update().get(
                id=exercise_id,
                message_id=message_id,
                message__conversation__user_id=self.user_id,
            )
            submission_files = list(
                ExerciseFile.objects.select_for_update().filter(
                    id__in=exercise_file_ids,
                    exercise_id=locked_exercise.id,
                )
            )
            if len(submission_files) != len(exercise_file_ids):
                raise ExerciseFile.DoesNotExist(
                    "One or more exercise files do not belong to this exercise."
                )

            submissions_by_file_id = dict(zip(exercise_file_ids, user_submissions))
            for exercise_file in submission_files:
                exercise_file.user_submission = submissions_by_file_id[exercise_file.id]

            locked_exercise.feedback = response_json
            locked_exercise.correctness = response_json.get("correctness")
            locked_exercise.save(update_fields=["feedback", "correctness"])
            ExerciseFile.objects.bulk_update(
                submission_files, ["user_submission"]
            )

            tokens_remaining = UserService.consume_tokens(self.user_id, tokens_used)
            TokenUsage.objects.create(
                user_id=self.user_id,
                token_used=tokens_used,
                tokens_remaining=tokens_remaining,
                action="exercise_evaluator",
            )

        logger.info("Exercise %s evaluation saved.", exercise_id)
        return response_json

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
        try:
            ExerciseGeneration.model_validate_json(response.text)
            tokens_remaining = UserService.consume_tokens(self.user_id, tokens_used)
        except InsufficientTokensError:
            return {"warning": "Insufficient tokens"}

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

    def save_user_submissions(
        self, exercise_file_ids, user_submissions, exercise_id=None
    ):
        """
        Atomically saves submissions for exercise files owned by this user.
        """
        if (
            len(exercise_file_ids) != len(user_submissions)
            or len(set(exercise_file_ids)) != len(exercise_file_ids)
        ):
            raise ValueError(
                "Exercise file IDs must be unique and match the submissions."
            )

        with transaction.atomic():
            files_query = ExerciseFile.objects.select_for_update().filter(
                id__in=exercise_file_ids,
                exercise__message__conversation__user_id=self.user_id,
            )
            if exercise_id is not None:
                files_query = files_query.filter(exercise_id=exercise_id)

            exercise_files = list(files_query)
            if len(exercise_files) != len(exercise_file_ids):
                raise ExerciseFile.DoesNotExist(
                    "One or more exercise files were not found."
                )

            submissions_by_file_id = dict(zip(exercise_file_ids, user_submissions))
            for exercise_file in exercise_files:
                exercise_file.user_submission = submissions_by_file_id[exercise_file.id]

            ExerciseFile.objects.bulk_update(
                exercise_files, ["user_submission"]
            )

        logger.info("Saved submissions for %s exercise files.", len(exercise_files))

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
