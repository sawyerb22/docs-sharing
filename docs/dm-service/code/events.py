import logging

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer


logger = logging.getLogger(__name__)


def emit_dm_event(event_type, payload, user_ids=None):
    logger.info("dm_event %s %s", event_type, payload)
    if not user_ids:
        return

    channel_layer = get_channel_layer()
    if channel_layer is None:
        return

    for user_id in user_ids:
        async_to_sync(channel_layer.group_send)(
            f"user_{user_id}",
            {
                "type": "dm.event",
                "event_type": event_type,
                "payload": payload,
            },
        )
