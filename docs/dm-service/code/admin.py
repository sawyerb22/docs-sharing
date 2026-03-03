from django.contrib import admin

from direct_messages import models


@admin.register(models.Conversation)
class ConversationAdmin(admin.ModelAdmin):
    list_display = ("id", "kind", "last_message_at", "is_muted")
    list_filter = ("kind", "is_muted")
    search_fields = ("id",)


@admin.register(models.Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ("id", "conversation", "sender", "message_type", "created_at")
    list_filter = ("message_type",)
    search_fields = ("id", "conversation__id", "sender__username")


@admin.register(models.ReadCursor)
class ReadCursorAdmin(admin.ModelAdmin):
    list_display = ("id", "conversation", "user", "last_read_sequence")
    search_fields = ("conversation__id", "user__username")


@admin.register(models.UserInboxIndex)
class UserInboxIndexAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "user",
        "other_user",
        "conversation",
        "kind",
        "last_message_at",
        "unread_count",
    )
    list_filter = ("kind",)
    search_fields = ("user__username", "other_user__username", "conversation__id")


@admin.register(models.DirectMessageOutbox)
class DirectMessageOutboxAdmin(admin.ModelAdmin):
    list_display = ("id", "conversation", "sender", "recipient", "status", "created_at")
    list_filter = ("status",)
    search_fields = ("conversation__id", "sender__username", "recipient__username")


@admin.register(models.DirectMessageMedia)
class DirectMessageMediaAdmin(admin.ModelAdmin):
    list_display = ("id", "message", "media", "created_at")
    search_fields = ("message__id", "media__uuid")
