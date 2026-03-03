from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

import logging

from direct_messages.events import emit_dm_event
from direct_messages.models import DirectMessageOutbox, Message, OutboxStatus
from direct_messages.services import (
    attach_media,
    conversation_preview,
    get_next_message_sequence,
    resolve_media_items,
    serialize_message_attachments,
    update_inbox_index,
)


class Command(BaseCommand):
    help = "Process pending direct message outbox items"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.logger = logging.getLogger(__name__)

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=100)

    def handle(self, *args, **options):
        limit = options["limit"]
        pending = DirectMessageOutbox.objects.filter(status=OutboxStatus.PENDING).order_by(
            "created_at"
        )[:limit]
        for item in pending:
            self._process_item(item)

    def _process_item(self, item):
        with transaction.atomic():
            outbox = (
                DirectMessageOutbox.objects.select_for_update()
                .filter(id=item.id)
                .first()
            )
            if not outbox or outbox.status != OutboxStatus.PENDING:
                return

            outbox.status = OutboxStatus.PROCESSING
            outbox.attempt_count += 1
            outbox.save(update_fields=["status", "attempt_count"])

        try:
            sequence = get_next_message_sequence(outbox.conversation)
            message = Message.objects.create(
                conversation=outbox.conversation,
                sender=outbox.sender,
                message_type=outbox.message_type,
                body=outbox.body or "",
                payload=outbox.payload,
                sequence=sequence,
            )

            media_items = []
            if outbox.message_type == "media":
                media_ids = (outbox.payload or {}).get("media_ids") if isinstance(outbox.payload, dict) else None
                if not media_ids:
                    raise ValueError("media_ids is required for media messages")
                media_items = resolve_media_items(media_ids, outbox.sender)
            attach_media(message, media_items)

            preview = conversation_preview(outbox.message_type, outbox.body)
            outbox.conversation.last_message = message
            outbox.conversation.last_message_at = timezone.now()
            outbox.conversation.last_message_preview = preview
            outbox.conversation.save(
                update_fields=["last_message", "last_message_at", "last_message_preview"]
            )

            update_inbox_index(
                outbox.sender, outbox.conversation, message, preview, increment_unread=False
            )
            update_inbox_index(
                outbox.recipient,
                outbox.conversation,
                message,
                preview,
                increment_unread=True,
            )

            participant_ids = [outbox.sender_id, outbox.recipient_id]
            emit_dm_event(
                "message.created",
                {
                    "message_id": str(message.id),
                    "conversation_id": str(outbox.conversation.id),
                    "sender_id": str(outbox.sender_id),
                    "message_type": message.message_type,
                    "body": message.body,
                    "payload": message.payload,
                    "attachments": serialize_message_attachments(message),
                    "sequence": message.sequence,
                    "created_at": message.created_at.isoformat(),
                },
                user_ids=participant_ids,
            )
            emit_dm_event(
                "conversation.updated",
                {
                    "conversation_id": str(outbox.conversation.id),
                    "kind": outbox.conversation.kind,
                    "last_message_id": str(message.id),
                    "last_message_at": outbox.conversation.last_message_at.isoformat(),
                    "last_message_preview": outbox.conversation.last_message_preview,
                    "updated_at": outbox.conversation.updated_at.isoformat(),
                },
                user_ids=participant_ids,
            )
            if outbox.request_created:
                emit_dm_event(
                    "request.created",
                    {
                        "conversation_id": str(outbox.conversation.id),
                        "sender_id": str(outbox.sender_id),
                        "recipient_id": str(outbox.recipient_id),
                    },
                    user_ids=[outbox.recipient_id],
                )

            outbox.message = message
            outbox.status = OutboxStatus.DONE
            outbox.save(update_fields=["message", "status"])
            self.logger.info(
                "dm_outbox_processed item=%s conversation=%s",
                outbox.id,
                outbox.conversation_id,
            )
        except Exception as exc:
            outbox = DirectMessageOutbox.objects.filter(id=item.id).first()
            if not outbox:
                return
            outbox.status = OutboxStatus.FAILED
            outbox.last_error = str(exc)
            outbox.save(update_fields=["status", "last_error"])
            self.logger.exception(
                "dm_outbox_failed item=%s conversation=%s",
                outbox.id,
                outbox.conversation_id,
            )
