from django.conf import settings
from django.db import models
from django.utils import timezone

from core.mixins import TimestampMixin
from core.models import BaseModel


class ConversationKind(models.TextChoices):
    PRIMARY = "primary", "Primary"
    REQUEST = "request", "Request"


class Conversation(BaseModel, TimestampMixin):
    participants = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        related_name="dm_conversations",
    )
    kind = models.CharField(
        max_length=10,
        choices=ConversationKind.choices,
        default=ConversationKind.PRIMARY,
    )
    is_muted = models.BooleanField(default=False)
    last_message = models.ForeignKey(
        "direct_messages.Message",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    last_message_at = models.DateTimeField(null=True, blank=True)
    last_message_preview = models.CharField(max_length=280, blank=True, default="")

    class Meta:
        ordering = ["-last_message_at", "-created_at"]
        indexes = [
            models.Index(fields=["kind", "last_message_at"]),
        ]


class MessageType(models.TextChoices):
    TEXT = "text", "Text"
    MEDIA = "media", "Media"
    POST = "post", "Post"


class Message(BaseModel, TimestampMixin):
    conversation = models.ForeignKey(
        Conversation,
        on_delete=models.CASCADE,
        related_name="messages",
    )
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="sent_messages",
    )
    message_type = models.CharField(
        max_length=10,
        choices=MessageType.choices,
        default=MessageType.TEXT,
    )
    body = models.TextField(blank=True)
    payload = models.JSONField(null=True, blank=True)
    sequence = models.PositiveBigIntegerField(null=True, blank=True)
    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["conversation", "sequence"]),
            models.Index(fields=["conversation", "created_at"]),
        ]

    def mark_deleted(self):
        self.is_deleted = True
        self.deleted_at = timezone.now()
        self.save(update_fields=["is_deleted", "deleted_at"])


class ReadCursor(BaseModel, TimestampMixin):
    conversation = models.ForeignKey(
        Conversation,
        on_delete=models.CASCADE,
        related_name="read_cursors",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="dm_read_cursors",
    )
    last_read_sequence = models.PositiveBigIntegerField(default=0)

    class Meta:
        unique_together = ("conversation", "user")
        indexes = [
            models.Index(fields=["conversation", "user"]),
        ]


class UserInboxIndex(BaseModel, TimestampMixin):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="dm_inbox_index",
    )
    other_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="dm_inbox_as_other",
        null=True,
        blank=True,
    )
    other_username = models.CharField(max_length=30, blank=True, default="")
    other_display_name = models.CharField(max_length=120, blank=True, default="")
    other_avatar_url = models.CharField(max_length=1000, blank=True, default="")
    conversation = models.ForeignKey(
        Conversation,
        on_delete=models.CASCADE,
        related_name="inbox_entries",
    )
    kind = models.CharField(
        max_length=10,
        choices=ConversationKind.choices,
        default=ConversationKind.PRIMARY,
    )
    last_message = models.ForeignKey(
        "direct_messages.Message",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    last_message_sender = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    last_message_at = models.DateTimeField(null=True, blank=True)
    last_message_preview = models.CharField(max_length=280, blank=True, default="")
    unread_count = models.PositiveIntegerField(default=0)
    last_seen_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ("user", "conversation")
        indexes = [
            models.Index(fields=["user", "kind", "last_message_at"]),
        ]


class OutboxStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    PROCESSING = "processing", "Processing"
    FAILED = "failed", "Failed"
    DONE = "done", "Done"


class DirectMessageOutbox(BaseModel, TimestampMixin):
    conversation = models.ForeignKey(
        Conversation,
        on_delete=models.CASCADE,
        related_name="outbox_items",
    )
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="dm_outbox_sent",
    )
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="dm_outbox_received",
    )
    message = models.ForeignKey(
        "direct_messages.Message",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    message_type = models.CharField(
        max_length=10,
        choices=MessageType.choices,
        default=MessageType.TEXT,
    )
    body = models.TextField(blank=True)
    payload = models.JSONField(null=True, blank=True)
    status = models.CharField(
        max_length=15,
        choices=OutboxStatus.choices,
        default=OutboxStatus.PENDING,
    )
    attempt_count = models.PositiveIntegerField(default=0)
    last_error = models.TextField(blank=True, default="")
    request_created = models.BooleanField(default=False)

    class Meta:
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["status", "created_at"]),
        ]


class DirectMessageMedia(BaseModel, TimestampMixin):
    message = models.ForeignKey(
        Message,
        on_delete=models.CASCADE,
        related_name="media_items",
    )
    media = models.ForeignKey(
        "system.S3Media",
        on_delete=models.CASCADE,
        related_name="dm_media",
    )

    class Meta:
        unique_together = ("message", "media")
        indexes = [
            models.Index(fields=["message", "media"]),
        ]
