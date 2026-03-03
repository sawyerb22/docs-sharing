import logging

from django.core.cache import cache
from django.utils import timezone

from system.models import S3Media

from django.db.models import Max

from direct_messages.models import DirectMessageMedia, Message, MessageType, UserInboxIndex


logger = logging.getLogger(__name__)


def conversation_preview(message_type, body):
    if message_type == MessageType.TEXT:
        return (body or "").strip()[:280]
    if message_type == MessageType.POST:
        return "Shared a post"
    return "Sent a media"


def resolve_avatar_url(profile):
    if not profile or not profile.avatar_image:
        return ""
    url = profile.avatar_image.get_absolute_url()
    return url or ""


def update_inbox_index(user, conversation, message, preview, increment_unread=False):
    other_user = conversation.participants.exclude(id=user.id).first()
    if not other_user:
        return

    other_profile = getattr(other_user, "profile", None)
    other_display_name = ""
    if other_profile and other_profile.full_name:
        other_display_name = other_profile.full_name
    else:
        other_display_name = other_user.full_name

    entry, created = UserInboxIndex.objects.get_or_create(
        user=user,
        conversation=conversation,
        defaults={
            "kind": conversation.kind,
            "other_user": other_user,
            "other_username": other_user.username,
            "other_display_name": other_display_name,
            "other_avatar_url": resolve_avatar_url(other_profile),
            "last_message": message,
            "last_message_sender": message.sender,
            "last_message_at": message.created_at,
            "last_message_preview": preview,
            "unread_count": 0,
        },
    )

    if not created:
        entry.kind = conversation.kind
        entry.other_user = other_user
        entry.other_username = other_user.username
        entry.other_display_name = other_display_name
        entry.other_avatar_url = resolve_avatar_url(other_profile)
        entry.last_message = message
        entry.last_message_sender = message.sender
        entry.last_message_at = message.created_at
        entry.last_message_preview = preview
        if not increment_unread:
            entry.unread_count = 0

    if increment_unread:
        entry.unread_count = (entry.unread_count or 0) + 1

    entry.save(
        update_fields=[
            "kind",
            "other_user",
            "other_username",
            "other_display_name",
            "other_avatar_url",
            "last_message",
            "last_message_sender",
            "last_message_at",
            "last_message_preview",
            "unread_count",
        ]
    )


def resolve_media_items(media_ids, sender):
    if not media_ids:
        return []
    media = list(S3Media.objects.filter(uuid__in=media_ids, user=sender))
    if len(media) != len(set(media_ids)):
        raise ValueError("One or more media items are invalid")
    return media


def attach_media(message, media_items):
    if not media_items:
        return
    DirectMessageMedia.objects.bulk_create(
        [DirectMessageMedia(message=message, media=item) for item in media_items],
        ignore_conflicts=True,
    )


def get_next_message_sequence(conversation):
    Message.objects.select_for_update().filter(conversation=conversation).order_by("id")[:1]
    max_value = (
        Message.objects.filter(conversation=conversation)
        .aggregate(max_sequence=Max("sequence"))
        .get("max_sequence")
    )
    return (max_value or 0) + 1


def serialize_message_attachments(message):
    attachments = []
    for item in message.media_items.select_related("media").all():
        media = item.media
        if not media:
            continue
        attachments.append(
            {
                "id": str(media.id),
                "mediaUrl": media.get_absolute_url(),
                "mediaType": media.media_type,
            }
        )
    return attachments


def enforce_send_rate_limit(user_id, limit_per_minute):
    if not limit_per_minute:
        return
    window = timezone.now().strftime("%Y%m%d%H%M")
    cache_key = f"dm:rate:{user_id}:{window}"
    count = cache.get(cache_key)
    if count is None:
        cache.set(cache_key, 1, timeout=70)
        return
    if count >= limit_per_minute:
        logger.warning("dm_rate_limited user=%s count=%s", user_id, count)
        raise ValueError("Rate limit exceeded. Please slow down.")
    cache.incr(cache_key)
