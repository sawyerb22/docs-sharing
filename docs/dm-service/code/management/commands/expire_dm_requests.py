from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from direct_messages.models import Conversation, ConversationKind


class Command(BaseCommand):
    help = "Expire message request conversations older than configured threshold"

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=None)

    def handle(self, *args, **options):
        days = options["days"] or settings.DM_REQUEST_EXPIRY_DAYS
        cutoff = timezone.now() - timedelta(days=days)
        queryset = Conversation.objects.filter(kind=ConversationKind.REQUEST)
        stale = queryset.filter(last_message_at__lte=cutoff) | queryset.filter(
            last_message_at__isnull=True, created_at__lte=cutoff
        )
        count = stale.count()
        stale.delete()
        self.stdout.write(self.style.SUCCESS(f"Expired {count} request conversations"))
