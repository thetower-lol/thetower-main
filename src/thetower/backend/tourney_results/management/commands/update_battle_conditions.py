import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from django.core.management.base import BaseCommand

from ...models import BattleCondition, TourneyResult

# Graceful thetower_bcs import handling
try:
    from thetower_bcs import TournamentPredictor, predict_future_tournament

    TOWERBCS_AVAILABLE = True
except ImportError:
    TOWERBCS_AVAILABLE = False

    def predict_future_tournament(tourney_id, league):
        return []

    class TournamentPredictor:
        @staticmethod
        def get_tournament_info(date):
            return None, date, 0


logging.basicConfig(level=logging.INFO)


class Command(BaseCommand):
    help = "Update battle conditions for tournament results after October 17, 2024"

    def add_arguments(self, parser):
        parser.add_argument(
            "--confirm",
            action="store_true",
            help="Actually update the database (default is dry-run)",
        )
        parser.add_argument(
            "--verbose",
            action="store_true",
            help="Show all tournaments being processed, not just ones needing changes",
        )
        parser.add_argument(
            "--fix",
            "--change",
            action="store_true",
            dest="fix",
            help="Show/fix mismatched conditions (requires --confirm to apply changes)",
        )
        parser.add_argument(
            "--add",
            "--missing",
            action="store_true",
            dest="add",
            help="Show/add missing conditions (requires --confirm to apply changes)",
        )

    def handle(self, *args, **options):
        # Check if thetower_bcs is available
        if not TOWERBCS_AVAILABLE:
            self.stdout.write(
                self.style.ERROR(
                    "Error: thetower-bcs package is not available.\n"
                    "This command requires thetower-bcs to predict battle conditions.\n"
                    "Please install thetower-bcs and try again."
                )
            )
            return

        is_live = options["confirm"]
        is_verbose = options["verbose"]
        fix_mode = options["fix"]
        add_mode = options["add"]

        # If neither mode is specified, show both
        if not fix_mode and not add_mode:
            fix_mode = add_mode = True

        modes = []
        if fix_mode:
            modes.append("FIX MISMATCHES")
        if add_mode:
            modes.append("ADD MISSING")
        mode = f"{'LIVE' if is_live else 'DRY RUN'} ({' & '.join(modes)})"

        if (fix_mode or add_mode) and not is_live:
            self.stdout.write("\nWarning: --fix and --add require --confirm to make changes")

        self.stdout.write(f"\nRunning in {mode} mode")
        self.stdout.write("=" * 50)

        start_date = datetime(2024, 10, 17).replace(tzinfo=ZoneInfo("UTC")).date()

        tournaments = TourneyResult.objects.filter(date__gt=start_date).order_by("date", "league")

        changes_needed = False
        changes_count = 0
        total_count = 0

        for tournament in tournaments:
            total_count += 1
            tourney_date = datetime.combine(tournament.date, datetime.min.time()).replace(tzinfo=ZoneInfo("UTC"))

            tourney_id, _, _, _ = TournamentPredictor.get_tournament_info(tourney_date)
            predicted_conditions = set(predict_future_tournament(tourney_id, tournament.league))
            existing_conditions = set(tournament.conditions.values_list("name", flat=True))

            # Normalize 'None' conditions
            predicted_is_none = not predicted_conditions or predicted_conditions == {"None"} or predicted_conditions == {"none"}
            existing_is_none = not existing_conditions

            # Skip if both are effectively "none"
            if predicted_is_none and existing_is_none:
                if is_verbose:
                    self.stdout.write(f"\nProcessing {tournament.league} tournament from {tournament.date}")
                    self.stdout.write("  ✓ Correctly has no conditions")
                continue

            show_tournament = False
            will_update = False

            # Check for missing conditions
            if existing_is_none and not predicted_is_none and add_mode:
                show_tournament = True
                will_update = is_live
                update_type = "missing"
            # Check for mismatched conditions
            elif not existing_is_none and existing_conditions != predicted_conditions and fix_mode:
                show_tournament = True
                will_update = is_live
                update_type = "mismatch"

            if show_tournament or is_verbose:
                self.stdout.write(f"\nProcessing {tournament.league} tournament from {tournament.date}")

            if show_tournament:
                changes_needed = True
                changes_count += 1
                if update_type == "missing":
                    self.stdout.write("  Missing conditions:")
                else:
                    self.stdout.write("  Conditions mismatch:")
                self.stdout.write(f"    Existing: {existing_conditions or 'None'}")
                self.stdout.write(f"    Predicted: {predicted_conditions or 'None'}")

                if will_update:
                    self._update_conditions(tournament, predicted_conditions)
            elif is_verbose:
                self.stdout.write("  ✓ Conditions correct")

        self.stdout.write("\n" + "=" * 50)
        self.stdout.write(f"\nProcessed {total_count} tournaments")
        self.stdout.write(f"Found {changes_count} tournaments needing updates")

        if changes_needed:
            if not is_live:
                self.stdout.write("\nRun with --confirm and --fix/--add to apply changes.")
        else:
            self.stdout.write("\nNo changes needed.")

    def _update_conditions(self, tournament, conditions):
        """Update the tournament conditions"""
        if not conditions or conditions == {"None"} or conditions == {"none"}:
            tournament.conditions.clear()
            self.stdout.write("  ✓ Cleared all conditions")
        else:
            condition_ids = BattleCondition.objects.filter(name__in=conditions).values_list("id", flat=True)
            tournament.conditions.set(condition_ids)
            self.stdout.write(f"  ✓ Updated conditions to: {conditions}")
