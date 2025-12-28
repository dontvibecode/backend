from django.db import models


class User(models.Model):
    id = models.AutoField(primary_key=True)
    username = models.CharField(max_length=150, unique=True)
    method = models.CharField(choices=[('google', 'Google')], default="google")
    email = models.EmailField(unique=True)

    def __str__(self):
        return f"User with id:{self.id}"

class Preferences(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='preferences')
    theme = models.CharField(
        max_length=10,
        choices=[('light', 'Light'), ('dark', 'Dark'), ('system', 'System')],
        default='light'
    )
    accent_color = models.CharField(max_length=20, default='blue')
    language = models.CharField(max_length=10, default='en')
    profile_image = models.CharField(max_length=255, null=True, blank=True)
    email_notifications = models.BooleanField(default=True)
    push_notifications = models.BooleanField(default=True)
    in_app_notifications = models.BooleanField(default=True)
    profile_visible = models.BooleanField(default=True)
    share_data = models.BooleanField(default=False)
    font_size = models.CharField(
        max_length=10,
        choices=[('small', 'Small'), ('medium', 'Medium'), ('large', 'Large')],
        default='medium'
    )
    compact_mode = models.BooleanField(default=False)

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

    def __str__(self):
        if self.from_user:
            return f"User: {self.text}"
        return f"AI: {self.json or self.text}"

    class Meta:
        ordering = ["created_at"]


class Exercise(models.Model):
    id = models.AutoField(primary_key=True)
    message = models.ForeignKey(Message, on_delete=models.CASCADE, related_name='exercises')
    correctness = models.IntegerField(null=True, blank=True, choices=[(0, 'Incorrect'), (1, 'Nearly'), (2, 'Correct')])
    bookmarked = models.BooleanField(default=False)
    
    def __str__(self):
        return f"Exercise {self.filename}"

class ExerciseFile(models.Model):
    id = models.AutoField(primary_key=True)
    exercise = models.ForeignKey(Exercise, on_delete=models.CASCADE, related_name='files')
    filename = models.CharField(max_length=255)
    text = models.TextField()
    code = models.TextField()
    user_submission = models.TextField(null=True, blank=True)

    def __str__(self):
        return f"File {self.filename} for Exercise {self.exercise.id}"