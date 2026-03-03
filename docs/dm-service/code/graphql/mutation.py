from django.contrib.auth import get_user_model
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from graphene import Boolean, Field, ID, Mutation, ObjectType, String, Int
from graphene.types.json import JSONString

from friendship.models import Friend, Follow

from direct_messages.models import (
    Conversation,
    ConversationKind,
    Message,
    MessageType,
    ReadCursor,
    UserInboxIndex,
    DirectMessageOutbox,
)
from direct_messages.events import emit_dm_event
from direct_messages.graphql.types import ConversationType, MessageType as MessageGraphType
from direct_messages.services import (
    attach_media,
    conversation_preview,
    enforce_send_rate_limit,
    get_next_message_sequence,
    resolve_media_items,
    serialize_message_attachments,
    update_inbox_index,
)


def _get_relationship_sets(user):
    friends = set(Friend.objects.friends(user))
    following = set(Follow.objects.following(user))
    followers = set(Follow.objects.followers(user))
    return friends, following, followers


def _can_message(sender, recipient):
    if not recipient.is_private:
        return True
    friends, following, followers = _get_relationship_sets(sender)
    return recipient in friends or recipient in following or recipient in followers


def _is_primary_relationship(sender, recipient):
    friends, following, followers = _get_relationship_sets(sender)
    return recipient in friends or recipient in following or recipient in followers


def _message_event_payload(message, conversation):
    return {
        "message_id": str(message.id),
        "conversation_id": str(conversation.id),
        "sender_id": str(message.sender_id),
        "message_type": message.message_type,
        "body": message.body,
        "payload": message.payload,
        "attachments": serialize_message_attachments(message),
        "sequence": message.sequence,
        "created_at": message.created_at.isoformat(),
    }


def _conversation_event_payload(conversation):
    return {
        "conversation_id": str(conversation.id),
        "kind": conversation.kind,
        "last_message_id": str(conversation.last_message_id) if conversation.last_message_id else None,
        "last_message_at": conversation.last_message_at.isoformat() if conversation.last_message_at else None,
        "last_message_preview": conversation.last_message_preview,
        "updated_at": conversation.updated_at.isoformat(),
    }


def _update_inbox_index(user, conversation, message, preview, increment_unread=False):
    update_inbox_index(user, conversation, message, preview, increment_unread=increment_unread)


class SendDirectMessage(Mutation):
    success = Boolean()
    message = Field(MessageGraphType)
    conversation = Field(ConversationType)
    error = String()

    class Arguments:
        recipient_id = ID(required=True)
        message_type = String(required=False)
        body = String(required=False)
        payload = JSONString(required=False)

    def mutate(self, info, recipient_id, message_type=None, body=None, payload=None):
        user = info.context.user
        if not user or not user.is_authenticated:
            return SendDirectMessage(success=False, error="Please login to send messages")

        recipient = get_user_model().objects.filter(id=recipient_id).first()
        if not recipient:
            return SendDirectMessage(success=False, error="Recipient not found")
        if recipient.id == user.id:
            return SendDirectMessage(success=False, error="Cannot message yourself")

        if not _can_message(user, recipient):
            return SendDirectMessage(success=False, error="You cannot message this user")

        try:
            enforce_send_rate_limit(user.id, settings.DM_SEND_RATE_LIMIT_PER_MIN)
        except ValueError as exc:
            return SendDirectMessage(success=False, error=str(exc))

        message_type_value = message_type or MessageType.TEXT
        valid_message_types = {choice for choice, _ in MessageType.choices}
        if message_type_value not in valid_message_types:
            return SendDirectMessage(success=False, error="Invalid message type")

        if message_type_value == MessageType.TEXT and not (body or "").strip():
            return SendDirectMessage(success=False, error="Message body is required")
        if message_type_value != MessageType.TEXT and not payload:
            return SendDirectMessage(success=False, error="Payload is required for this message type")
        if message_type_value == MessageType.MEDIA:
            media_ids = (payload or {}).get("media_ids") if isinstance(payload, dict) else None
            if not media_ids or not isinstance(media_ids, list):
                return SendDirectMessage(success=False, error="media_ids is required for media messages")

        is_primary = _is_primary_relationship(user, recipient)
        kind = ConversationKind.PRIMARY if is_primary else ConversationKind.REQUEST

        conversation_created = False
        with transaction.atomic():
            conversation = (
                Conversation.objects.filter(participants=user)
                .filter(participants=recipient)
                .distinct()
                .first()
            )
            if not conversation:
                conversation = Conversation.objects.create(kind=kind)
                conversation.participants.add(user, recipient)
                conversation_created = True
            elif conversation.kind == ConversationKind.REQUEST and is_primary:
                conversation.kind = ConversationKind.PRIMARY
                conversation.save(update_fields=["kind"])

            media_items = []
            if message_type_value == MessageType.MEDIA:
                try:
                    media_items = resolve_media_items(media_ids, user)
                except ValueError as exc:
                    return SendDirectMessage(success=False, error=str(exc))

            if settings.DM_ASYNC_PERSISTENCE:
                DirectMessageOutbox.objects.create(
                    conversation=conversation,
                    sender=user,
                    recipient=recipient,
                    message_type=message_type_value,
                    body=body or "",
                    payload=payload,
                    request_created=conversation_created
                    and conversation.kind == ConversationKind.REQUEST,
                )
                return SendDirectMessage(success=True, message=None, conversation=conversation)

            sequence = get_next_message_sequence(conversation)
            message = Message.objects.create(
                conversation=conversation,
                sender=user,
                message_type=message_type_value,
                body=body or "",
                payload=payload,
                sequence=sequence,
            )

            attach_media(message, media_items)

            preview = conversation_preview(message_type_value, body)
            conversation.last_message = message
            conversation.last_message_at = timezone.now()
            conversation.last_message_preview = preview
            conversation.save(
                update_fields=[
                    "last_message",
                    "last_message_at",
                    "last_message_preview",
                ]
            )

            _update_inbox_index(user, conversation, message, preview, increment_unread=False)
            _update_inbox_index(recipient, conversation, message, preview, increment_unread=True)

        participant_ids = list(conversation.participants.values_list("id", flat=True))
        emit_dm_event(
            "message.created",
            _message_event_payload(message, conversation),
            user_ids=participant_ids,
        )
        emit_dm_event(
            "conversation.updated",
            _conversation_event_payload(conversation),
            user_ids=participant_ids,
        )
        if conversation_created and conversation.kind == ConversationKind.REQUEST:
            emit_dm_event(
                "request.created",
                {
                    "conversation_id": str(conversation.id),
                    "sender_id": str(user.id),
                    "recipient_id": str(recipient.id),
                },
                user_ids=[recipient.id],
            )
        return SendDirectMessage(success=True, message=message, conversation=conversation)


class AcceptMessageRequest(Mutation):
    success = Boolean()
    conversation = Field(ConversationType)
    error = String()

    class Arguments:
        conversation_id = ID(required=True)

    def mutate(self, info, conversation_id):
        user = info.context.user
        if not user or not user.is_authenticated:
            return AcceptMessageRequest(success=False, error="Please login to accept requests")

        conversation = Conversation.objects.filter(id=conversation_id).first()
        if not conversation:
            return AcceptMessageRequest(success=False, error="Conversation not found")
        if not conversation.participants.filter(id=user.id).exists():
            return AcceptMessageRequest(success=False, error="Not authorized for this conversation")
        if conversation.kind != ConversationKind.REQUEST:
            return AcceptMessageRequest(success=False, error="Conversation is not a request")

        conversation.kind = ConversationKind.PRIMARY
        conversation.save(update_fields=["kind"])
        for participant in conversation.participants.all():
            UserInboxIndex.objects.filter(
                user=participant,
                conversation=conversation,
            ).update(kind=ConversationKind.PRIMARY)
        participant_ids = list(conversation.participants.values_list("id", flat=True))
        emit_dm_event(
            "conversation.updated",
            _conversation_event_payload(conversation),
            user_ids=participant_ids,
        )
        emit_dm_event(
            "request.accepted",
            {
                "conversation_id": str(conversation.id),
                "accepted_by": str(user.id),
            },
            user_ids=participant_ids,
        )
        return AcceptMessageRequest(success=True, conversation=conversation)


class IgnoreMessageRequest(Mutation):
    success = Boolean()
    error = String()

    class Arguments:
        conversation_id = ID(required=True)

    def mutate(self, info, conversation_id):
        user = info.context.user
        if not user or not user.is_authenticated:
            return IgnoreMessageRequest(success=False, error="Please login to ignore requests")

        conversation = Conversation.objects.filter(id=conversation_id).first()
        if not conversation:
            return IgnoreMessageRequest(success=False, error="Conversation not found")
        if not conversation.participants.filter(id=user.id).exists():
            return IgnoreMessageRequest(success=False, error="Not authorized for this conversation")
        if conversation.kind != ConversationKind.REQUEST:
            return IgnoreMessageRequest(success=False, error="Conversation is not a request")

        conversation.delete()
        return IgnoreMessageRequest(success=True)


class MarkConversationRead(Mutation):
    success = Boolean()
    read_cursor = Field("direct_messages.graphql.types.ReadCursorType")
    error = String()

    class Arguments:
        conversation_id = ID(required=True)
        last_read_sequence = Int(required=True)

    def mutate(self, info, conversation_id, last_read_sequence):
        user = info.context.user
        if not user or not user.is_authenticated:
            return MarkConversationRead(success=False, error="Please login to mark read")

        conversation = Conversation.objects.filter(id=conversation_id).first()
        if not conversation:
            return MarkConversationRead(success=False, error="Conversation not found")
        if not conversation.participants.filter(id=user.id).exists():
            return MarkConversationRead(success=False, error="Not authorized for this conversation")

        read_cursor, _ = ReadCursor.objects.get_or_create(
            conversation=conversation,
            user=user,
            defaults={"last_read_sequence": last_read_sequence},
        )
        if read_cursor.last_read_sequence != last_read_sequence:
            read_cursor.last_read_sequence = last_read_sequence
            read_cursor.save(update_fields=["last_read_sequence"])

        UserInboxIndex.objects.filter(
            user=user,
            conversation=conversation,
        ).update(unread_count=0, last_seen_at=timezone.now())

        other_user_id = (
            conversation.participants.exclude(id=user.id)
            .values_list("id", flat=True)
            .first()
        )
        emit_dm_event(
            "message.read",
            {
                "conversation_id": str(conversation.id),
                "reader_id": str(user.id),
                "last_read_sequence": last_read_sequence,
                "read_at": timezone.now().isoformat(),
            },
            user_ids=[other_user_id] if other_user_id else None,
        )

        return MarkConversationRead(success=True, read_cursor=read_cursor)


class MuteConversation(Mutation):
    success = Boolean()
    conversation = Field(ConversationType)
    error = String()

    class Arguments:
        conversation_id = ID(required=True)
        is_muted = Boolean(required=True)

    def mutate(self, info, conversation_id, is_muted):
        user = info.context.user
        if not user or not user.is_authenticated:
            return MuteConversation(success=False, error="Please login to mute conversations")

        conversation = Conversation.objects.filter(id=conversation_id).first()
        if not conversation:
            return MuteConversation(success=False, error="Conversation not found")
        if not conversation.participants.filter(id=user.id).exists():
            return MuteConversation(success=False, error="Not authorized for this conversation")

        conversation.is_muted = is_muted
        conversation.save(update_fields=["is_muted"])
        return MuteConversation(success=True, conversation=conversation)


class DeleteConversation(Mutation):
    success = Boolean()
    conversation_id = ID()
    error = String()

    class Arguments:
        conversation_id = ID(required=True)

    def mutate(self, info, conversation_id):
        user = info.context.user
        if not user or not user.is_authenticated:
            return DeleteConversation(success=False, error="Please login to delete conversations")

        conversation = Conversation.objects.filter(id=conversation_id).first()
        if not conversation:
            return DeleteConversation(success=False, error="Conversation not found")
        if not conversation.participants.filter(id=user.id).exists():
            return DeleteConversation(success=False, error="Not authorized for this conversation")

        UserInboxIndex.objects.filter(user=user, conversation=conversation).delete()
        ReadCursor.objects.filter(user=user, conversation=conversation).delete()

        return DeleteConversation(success=True, conversation_id=conversation_id)


class DirectMessageMutations(ObjectType):
    send_direct_message = SendDirectMessage.Field()
    accept_message_request = AcceptMessageRequest.Field()
    ignore_message_request = IgnoreMessageRequest.Field()
    mark_conversation_read = MarkConversationRead.Field()
    mute_conversation = MuteConversation.Field()
    delete_conversation = DeleteConversation.Field()
