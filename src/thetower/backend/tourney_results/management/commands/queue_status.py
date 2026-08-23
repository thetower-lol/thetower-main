"""
Management command to monitor tournament recalculation queue status.

Shows pending work, failed jobs, and recent processing statistics.

Usage:
    python manage.py queue_status [--detailed]
"""

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db.models import Count
from django.utils import timezone

from .models import TourneyResult


class Command(BaseCommand):
    help = "Show tournament recalculation queue status"

    def add_arguments(self, parser):
        parser.add_argument("--detailed", action="store_true", help="Show detailed information about failed jobs")

    def handle(self, *args, **options):
        detailed = options["detailed"]

        # Get queue statistics
        pending = TourneyResult.objects.filter(needs_recalc=True).count()
        failed = TourneyResult.objects.filter(recalc_retry_count__gte=3).count()

        # Recent processing (last 24 hours)
        yesterday = timezone.now() - timedelta(hours=24)
        recent_processed = TourneyResult.objects.filter(last_recalc_at__gte=yesterday).count()

        # Recent failures (last 24 hours)
        recent_failures = TourneyResult.objects.filter(recalc_retry_count__gt=0, last_recalc_at__gte=yesterday).count()

        self.stdout.write(self.style.SUCCESS("Tournament Recalculation Queue Status"))
        self.stdout.write("=" * 50)
        self.stdout.write(f"Pending recalculations: {pending}")
        self.stdout.write(f"Failed (max retries):    {failed}")
        self.stdout.write(f"Processed (24h):        {recent_processed}")
        self.stdout.write(f"Failed attempts (24h):  {recent_failures}")

        if pending > 0:
            # Show breakdown by league (ordered by priority)
            pending_by_league = (
                TourneyResult.objects.filter(needs_recalc=True)
                .extra(
                    select={
                        "league_priority": """
                        CASE league
                            WHEN 'Legend' THEN 1
                            WHEN 'Champion' THEN 2
                            WHEN 'Platinum' THEN 3
                            WHEN 'Gold' THEN 4
                            WHEN 'Silver' THEN 5
                            WHEN 'Copper' THEN 6
                            ELSE 7
                        END
                    """
                    }
                )
                .values("league")
                .annotate(count=Count("id"))
                .order_by("league_priority")
            )

            self.stdout.write("\nPending by league (priority order):")
            for item in pending_by_league:
                self.stdout.write(f"  {item['league']:>10}: {item['count']}")

            # Show next few tournaments to be processed
            next_tournaments = (
                TourneyResult.objects.filter(needs_recalc=True, recalc_retry_count__lt=3)
                .extra(
                    select={
                        "league_priority": """
                        CASE league
                            WHEN 'Legend' THEN 1
                            WHEN 'Champion' THEN 2
                            WHEN 'Platinum' THEN 3
                            WHEN 'Gold' THEN 4
                            WHEN 'Silver' THEN 5
                            WHEN 'Copper' THEN 6
                            ELSE 7
                        END
                    """
                    }
                )
                .order_by("league_priority", "-date")[:5]
            )

            if next_tournaments:
                self.stdout.write("\nNext tournaments to process (priority order):")
                for i, t in enumerate(next_tournaments, 1):
                    self.stdout.write(f"  {i}. {t.league} {t.date} (ID: {t.id})")

        if failed > 0:
            self.stdout.write(self.style.WARNING(f"\n⚠️  {failed} tournaments have failed max retries"))

            if detailed:
                failed_tournaments = TourneyResult.objects.filter(recalc_retry_count__gte=3).order_by("-recalc_retry_count", "date")[:10]

                self.stdout.write("\nFailed tournaments (showing first 10):")
                for t in failed_tournaments:
                    self.stdout.write(f"  ID {t.id:>4}: {t.league} {t.date} " f"(retries: {t.recalc_retry_count})")

                self.stdout.write(
                    "\nTo reset failed tournaments:\n"
                    '  python manage.py shell -c "\n'
                    "  from .models import TourneyResult;\n"
                    "  TourneyResult.objects.filter(recalc_retry_count__gte=3).update(\n"
                    '      needs_recalc=True, recalc_retry_count=0)"'
                )

        if pending == 0 and failed == 0:
            self.stdout.write(self.style.SUCCESS("\n✓ Queue is empty and healthy!"))
