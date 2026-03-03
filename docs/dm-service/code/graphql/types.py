import graphene
from graphene import Field, List
from graphene.types.json import JSONString
from graphene_django import DjangoObjectType

from direct_messages import models
from system.graphql.types import S3MediaModelType
from user.graphql.types import UserType


class MessageType(DjangoObjectType):
    sender = Field(UserType)
    payload = JSONString()
    attachments = List(S3MediaModelType)

    class Meta:
        model = models.Message
        fields = (
            "id",
            "conversation",
            "sender",
            "message_type",
            "body",
            "payload",
            "sequence",
            "is_deleted",
            "deleted_at",
            "created_at",
            "updated_at",
        )

    def resolve_attachments(self, info):
        return [item.media for item in self.media_items.select_related("media").all()]


class ConversationType(DjangoObjectType):
    participants = List(UserType)
    last_message = Field(MessageType)

    class Meta:
        model = models.Conversation
        fields = (
            "id",
            "participants",
            "kind",
            "is_muted",
            "last_message",
            "last_message_at",
            "last_message_preview",
            "created_at",
            "updated_at",
        )

    def resolve_participants(self, info):
        return self.participants.all()


class ReadCursorType(DjangoObjectType):
    class Meta:
        model = models.ReadCursor
        fields = (
            "id",
            "conversation",
            "user",
            "last_read_sequence",
            "created_at",
            "updated_at",
        )


class InboxEntryType(DjangoObjectType):
    other_user = Field(UserType)

    class Meta:
        model = models.UserInboxIndex
        fields = (
            "id",
            "user",
            "other_user",
            "other_username",
            "other_display_name",
            "other_avatar_url",
            "conversation",
            "kind",
            "last_message",
            "last_message_sender",
            "last_message_at",
            "last_message_preview",
            "unread_count",
            "last_seen_at",
            "created_at",
            "updated_at",
        )
