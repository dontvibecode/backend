from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db.models import Sum
from django.utils import timezone

from chatbot.models import SpeechClip, SpeechPlay
from chatbot.services.speech import BUDGET_WINDOW, characters_generated_since


class Command(BaseCommand):
    help = (
        "Summarise narration: clips generated, characters spent, and how often "
        "stored audio saved a generation."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--days", type=int, default=30, help="How far back to look (default 30)."
        )

    def handle(self, *args, **options):
        days = options["days"]
        now = timezone.now()
        since = now - timedelta(days=days)

        clips = SpeechClip.objects.filter(status=SpeechClip.READY, created_at__gte=since)
        characters = clips.aggregate(total=Sum("characters"))["total"] or 0
        plays = SpeechPlay.objects.filter(created_at__gte=since)
        total_plays = plays.count()
        hits = plays.filter(cache_hit=True).count()
        hit_rate = f"{hits / total_plays:.0%}" if total_plays else "n/a"
        budget_used = characters_generated_since(now - BUDGET_WINDOW)
        budget = settings.ELEVENLABS_MONTHLY_CHARACTER_BUDGET

        self.stdout.write(
            "\n".join(
                [
                    f"Narration over the last {days} days",
                    f"  Clips generated:     {clips.count()}",
                    f"  Characters spent:    {characters:,}",
                    f"  Served from cache:   {hits} of {total_plays} plays ({hit_rate})",
                    f"  Users generating:    {clips.values('user').distinct().count()}",
                    f"  30-day budget used:  {budget_used:,} of {budget:,} characters",
                ]
            )
        )
