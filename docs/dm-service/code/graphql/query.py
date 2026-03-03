from graphene import Field, ID, Int, List, ObjectType

from direct_messages.models import Conversation, ConversationKind, Message, UserInboxIndex
from direct_messages.graphql.types import ConversationType, MessageType, InboxEntryType


class DirectMessageInboxQuery(ObjectType):
    dm_inbox = List(ConversationType, offset=Int(default_value=0), limit=Int(default_value=20))
    dm_inbox_entries = List(InboxEntryType, offset=Int(default_value=0), limit=Int(default_value=20))

    def resolve_dm_inbox(self, info, offset, limit):
        user = info.context.user
        if not user or not user.is_authenticated:
            raise Exception("Please login to see direct messages")
        inbox_entries = (
            UserInboxIndex.objects.filter(
                user=user,
                kind=ConversationKind.PRIMARY,
            )
            .select_related("conversation")
            .order_by("-last_message_at", "-created_at")[offset : offset + limit]
        )
        return [entry.conversation for entry in inbox_entries]

    def resolve_dm_inbox_entries(self, info, offset, limit):
        user = info.context.user
        if not user or not user.is_authenticated:
            raise Exception("Please login to see direct messages")
        return (
            UserInboxIndex.objects.filter(
                user=user,
                kind=ConversationKind.PRIMARY,
            )
            .select_related("conversation", "other_user", "last_message", "last_message_sender")
            .order_by("-last_message_at", "-created_at")[offset : offset + limit]
        )


class DirectMessageRequestsQuery(ObjectType):
    dm_requests = List(ConversationType, offset=Int(default_value=0), limit=Int(default_value=20))
    dm_request_entries = List(InboxEntryType, offset=Int(default_value=0), limit=Int(default_value=20))

    def resolve_dm_requests(self, info, offset, limit):
        user = info.context.user
        if not user or not user.is_authenticated:
            raise Exception("Please login to see message requests")
        inbox_entries = (
            UserInboxIndex.objects.filter(
                user=user,
                kind=ConversationKind.REQUEST,
            )
            .select_related("conversation")
            .order_by("-last_message_at", "-created_at")[offset : offset + limit]
        )
        return [entry.conversation for entry in inbox_entries]

    def resolve_dm_request_entries(self, info, offset, limit):
        user = info.context.user
        if not user or not user.is_authenticated:
            raise Exception("Please login to see message requests")
        return (
            UserInboxIndex.objects.filter(
                user=user,
                kind=ConversationKind.REQUEST,
            )
            .select_related("conversation", "other_user", "last_message", "last_message_sender")
            .order_by("-last_message_at", "-created_at")[offset : offset + limit]
        )


class DirectMessageConversationQuery(ObjectType):
    dm_conversation = Field(ConversationType, conversation_id=ID(required=True))

    def resolve_dm_conversation(self, info, conversation_id):
        user = info.context.user
        if not user or not user.is_authenticated:
            raise Exception("Please login to view conversation")
        conversation = Conversation.objects.filter(id=conversation_id).first()
        if not conversation:
            raise Exception("Conversation not found")
        if not conversation.participants.filter(id=user.id).exists():
            raise Exception("Not authorized to view this conversation")
        return conversation


class DirectMessageMessagesQuery(ObjectType):
    dm_messages = List(
        MessageType,
        conversation_id=ID(required=True),
        offset=Int(default_value=0),
        limit=Int(default_value=20),
    )

    def resolve_dm_messages(self, info, conversation_id, offset, limit):
        user = info.context.user
        if not user or not user.is_authenticated:
            raise Exception("Please login to view messages")
        conversation = Conversation.objects.filter(id=conversation_id).first()
        if not conversation:
            raise Exception("Conversation not found")
        if not conversation.participants.filter(id=user.id).exists():
            raise Exception("Not authorized to view messages")
        return Message.objects.filter(conversation=conversation).order_by("-created_at")[
            offset : offset + limit
        ]
