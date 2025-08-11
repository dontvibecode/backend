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
    model_used = models.CharField(max_length=50)
    text = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.text
    
    class Meta:
        ordering = ['created_at']





