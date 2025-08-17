from django.db import models


class User(models.Model):
    id = models.AutoField(primary_key=True)

    def __str__(self):
        return self.id


class Conversation(models.Model):
    id = models.AutoField(primary_key=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Conversation {self.id} for User {self.user.id}"


class Message(models.Model):
    id = models.AutoField(primary_key=True)
    from_user = models.BooleanField(default=True)
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE)
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
