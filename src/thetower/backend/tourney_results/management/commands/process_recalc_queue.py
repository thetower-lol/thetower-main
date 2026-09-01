"""
Management command to process tournament recalculation queue.

This worker continuously processes tournaments marked for recalculation
in the background, preventing admin interface blocking.

Usage:
    python manage.py process_recalc_queue [--max-retries 3] [--delay 0.5]
"""

import logging
import time

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from ...models import TourneyResult
from ...tourney_utils import league_priority_expression, reposition

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Process tournament recalculation queue"

    def add_arguments(self, parser):
        parser.add_argument("--max-retries", type=int, default=3, help="Maximum number of retry attempts (default: 3)")
        parser.add_argument("--delay", type=float, default=0.5, help="Delay between processing attempts in seconds (default: 0.5)")
        parser.add_argument("--one-shot", action="store_true", help="Process queue once and exit (useful for testing)")

    def handle(self, *args, **options):
        max_retries = options["max_retries"]
        delay = options["delay"]
        one_shot = options["one_shot"]

        self.stdout.write(self.style.SUCCESS(f"Starting tournament recalculation worker " f"(max_retries={max_retries}, delay={delay}s)"))

        if one_shot:
            self.stdout.write("Running in one-shot mode")
            processed = self.process_next_tournament(max_retries)
            if processed:
                self.stdout.write(self.style.SUCCESS("Processed 1 tournament"))
            else:
                self.stdout.write("No tournaments to process")
            return

        # Continuous processing
        while True:
            try:
                processed = self.process_next_tournament(max_retries)
                if not processed:
                    time.sleep(delay)
            except KeyboardInterrupt:
                self.stdout.write(self.style.WARNING("\nShutting down worker..."))
                break
            except Exception as e:
                logger.error(f"Worker error: {e}")
                self.stdout.write(self.style.ERROR(f"Worker error: {e}"))
                time.sleep(5)  # Wait before retrying after error

    def process_next_tournament(self, max_retries):
        """
        Process the next tournament in the queue.

        Returns:
            bool: True if a tournament was processed, False if queue is empty
        """
        with transaction.atomic():
            # Get next tournament needing recalc with pessimistic lock
            # Prioritize by: 1) League importance (canonical `leagues` order), 2) Newest date first
            tournament = (
                TourneyResult.objects.select_for_update()
                .filter(needs_recalc=True, recalc_retry_count__lt=max_retries)
                .annotate(league_priority=league_priority_expression())
                .order_by("league_priority", "-date")
                .first()
            )  # High priority leagues first, then newest

            if not tournament:
                return False

            # Mark as processing (prevent other workers from picking it up)
            tournament.needs_recalc = False
            tournament.save()

        # Process outside the lock transaction
        try:
            self.stdout.write(f"Processing tournament {tournament.id} " f"({tournament.league} {tournament.date})")

            changes = reposition(tournament)

            # Mark successful completion
            with transaction.atomic():
                tournament.last_recalc_at = timezone.now()
                tournament.recalc_retry_count = 0
                tournament.save()

            self.stdout.write(self.style.SUCCESS(f"✓ Completed tournament {tournament.id}: {changes} position changes"))
            logger.info(f"Recalculated tournament {tournament.id}: {changes} changes")
            return True

        except Exception as e:
            # Handle failure - mark for retry
            with transaction.atomic():
                tournament.needs_recalc = True
                tournament.recalc_retry_count += 1
                tournament.save()

            self.stdout.write(
                self.style.ERROR(
                    f"✗ Failed to recalculate tournament {tournament.id} " f"(attempt {tournament.recalc_retry_count}/{max_retries}): {e}"
                )
            )
            logger.error(f"Failed to recalculate tournament {tournament.id}: {e}")

            if tournament.recalc_retry_count >= max_retries:
                self.stdout.write(self.style.WARNING(f"Tournament {tournament.id} exceeded max retries " f"and will not be retried automatically"))

            return True  # We did process something, even if it failed
