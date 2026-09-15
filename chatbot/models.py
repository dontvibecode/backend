from django.db import models
from django.utils import timezone


class User(models.Model):
    id = models.AutoField(primary_key=True)
    username = models.CharField(max_length=150, unique=True)
    method = models.CharField(choices=[("google", "Google")], default="google")
    email = models.EmailField(unique=True)
    token_limit = models.IntegerField(default=100000)
    token_used = models.IntegerField(default=0)
    membership = models.CharField(
        choices=[("free", "Free"), ("pro", "Pro")], default="free"
    )
    membership_expires_at = models.DateTimeField(null=True, blank=True)
    subscription_active = models.BooleanField(null=True, default=None)
    membership_updated_at = models.DateTimeField(blank=True, default=timezone.now)
    stripe_customer_id = models.CharField(max_length=100, null=True, blank=True)
    free_feedback_for_exercises_refresh_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"User with id:{self.id}"


class TokenUsage(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    token_used = models.IntegerField()
    timestamp = models.DateTimeField(auto_now_add=True)
    tokens_remaining = models.IntegerField()
    action = models.CharField(
        choices=[
            ("router", "Message"),
            ("instructor", "Message with lesson generated"),
            ("exercise_evaluator", "Exercise marking"),
            ("exercise_generator", "Exercise Generation"),
        ],
        max_length=20,
    )


class Preferences(models.Model):
    user = models.OneToOneField(
        User, on_delete=models.CASCADE, related_name="preferences"
    )
    theme = models.CharField(
        max_length=10,
        choices=[("light", "Light"), ("dark", "Dark"), ("system", "System")],
        default="light",
    )
    tab_size = models.IntegerField(default=4)
    accent_color = models.CharField(max_length=20, default="blue")
    language = models.CharField(max_length=10, default="en")
    profile_image = models.CharField(max_length=255, null=True, blank=True)
    email_notifications = models.BooleanField(default=True)
    push_notifications = models.BooleanField(default=True)
    in_app_notifications = models.BooleanField(default=True)
    profile_visible = models.BooleanField(default=True)
    share_data = models.BooleanField(default=False)
    font_size = models.CharField(
        max_length=10,
        choices=[("small", "Small"), ("medium", "Medium"), ("large", "Large")],
        default="medium",
    )
    compact_mode = models.BooleanField(default=False)
    # Read replies aloud as they arrive. Off by default: every new clip spends
    # from a small shared ElevenLabs credit pool, and browsers block audio the
    # user didn't ask for.
    voice_enabled = models.BooleanField(default=False)
    # Blank means the server's default voice (ELEVENLABS_DEFAULT_VOICE_ID).
    voice_id = models.CharField(max_length=64, blank=True, default="")
    speech_rate = models.FloatField(default=1.0)

    def __str__(self):
        return f"Preferences for User {self.user.id}"


class Conversation(models.Model):
    id = models.AutoField(primary_key=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    title = models.CharField(max_length=255)
    tags = models.JSONField(default=list, blank=True)
    pinned = models.BooleanField(default=False)
    last_active = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Conversation {self.id} for User {self.user.id}"


class Message(models.Model):
    id = models.AutoField(primary_key=True)
    from_user = models.BooleanField(default=True)
    conversation = models.ForeignKey(
        Conversation, on_delete=models.CASCADE, null=True, blank=True
    )
    model_used = models.CharField(max_length=50, null=True)
    text = models.TextField(null=True, blank=True)
    json = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    thought = models.TextField(null=True, blank=True)

    def __str__(self):
        if self.from_user:
            return f"User: {self.text}"
        return f"AI: {self.json or self.text}"

    class Meta:
        ordering = ["created_at"]


class Exercise(models.Model):
    id = models.AutoField(primary_key=True)
    message = models.ForeignKey(
        Message, on_delete=models.CASCADE, related_name="exercises"
    )
    correctness = models.IntegerField(
        null=True, blank=True, choices=[(0, "Incorrect"), (1, "Nearly"), (2, "Correct")]
    )
    feedback = models.JSONField(null=True, blank=True)
    bookmarked = models.BooleanField(default=False)
    title = models.CharField(max_length=255, null=True)
    tags = models.JSONField(default=list, null=True)

    def __str__(self):
        return f"Exercise {self.filename}"


class ExerciseFile(models.Model):
    id = models.AutoField(primary_key=True)
    exercise = models.ForeignKey(
        Exercise, on_delete=models.CASCADE, related_name="files"
    )
    filename = models.CharField(max_length=255)
    text = models.TextField()
    code = models.TextField()
    user_submission = models.TextField(null=True, blank=True)

    def __str__(self):
        return f"File {self.filename} for Exercise {self.exercise.id}"


class ProcessedStripeEvent(models.Model):
    event_id = models.CharField(max_length=255, unique=True)  # the unique= is what enforces idempotency
    processed_at = models.DateTimeField(auto_now_add=True)


class SpeechClip(models.Model):
    """
    One narration generated by ElevenLabs and stored in R2.

    The table does three jobs. A READY row is the cache: that audio already
    exists, so replaying it costs no credits. Every row is the spending ledger
    the daily and monthly character allowances are summed from. And the unique
    constraint is the lock: only the request that manages to insert the PENDING
    row calls ElevenLabs, so two tabs asking for one clip pay for it once.
    """

    PENDING = "pending"
    READY = "ready"

    message = models.ForeignKey(
        Message, on_delete=models.CASCADE, related_name="speech_clips"
    )
    # Who paid. Stored rather than joined through the conversation so the
    # per-user daily sum is one indexed query.
    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="speech_clips"
    )
    scope = models.CharField(
        max_length=10, choices=[("summary", "Summary"), ("full", "Full")]
    )
    voice_id = models.CharField(max_length=64)
    model_id = models.CharField(max_length=64)
    # Hash of the exact text sent. Changing how text is prepared changes the
    # hash, so outdated audio is regenerated instead of replayed.
    text_hash = models.CharField(max_length=64)
    characters = models.PositiveIntegerField()
    status = models.CharField(
        max_length=10,
        choices=[(PENDING, "Pending"), (READY, "Ready")],
        default=PENDING,
    )
    storage_key = models.CharField(max_length=255, blank=True, default="")
    duration_seconds = models.FloatField(null=True, blank=True)
    # [{"field": "explanation", "line": 7, "start": 12.34}, ...] in play order.
    marks = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["message", "scope", "voice_id", "model_id", "text_hash"],
                name="unique_speech_clip",
            )
        ]
        indexes = [
            models.Index(fields=["user", "created_at"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self):
        return f"SpeechClip {self.id} ({self.scope}) for Message {self.message_id}"


class SpeechPlay(models.Model):
    """
    One request for a clip. `cache_hit` records whether it was served from
    storage or had to be generated: the evidence that caching pays for itself.
    """

    clip = models.ForeignKey(
        SpeechClip, on_delete=models.CASCADE, related_name="plays"
    )
    cache_hit = models.BooleanField()
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    def __str__(self):
        return f"SpeechPlay {self.id} of SpeechClip {self.clip_id}"
