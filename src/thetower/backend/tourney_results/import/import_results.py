#!/tourney/tourney_venv/bin/python
import datetime
import logging
import os
import threading
import time
from glob import glob

import django
import schedule

# Django setup must come first
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "thetower.backend.towerdb.settings")
django.setup()

from django.core.files.uploadedfile import SimpleUploadedFile

from thetower.backend.env_config import get_csv_data

from ..constants import leagues
from ..get_results import get_file_name, get_last_date
from ..models import BattleCondition, TourneyResult
from ..overview_cache import regenerate_overview_cache
from ..tourney_utils import create_tourney_rows, get_summary

# Graceful thetower_bcs import handling
try:
    from thetower_bcs import TournamentPredictor, predict_future_tournament

    TOWERBCS_AVAILABLE = True
    logging.info("thetower_bcs package loaded successfully")
except ImportError as e:
    logging.warning(f"thetower_bcs package not available: {e}")
    logging.warning("Battle condition predictions will be skipped")
    # Create dummy functions to prevent errors
    TOWERBCS_AVAILABLE = False

    def predict_future_tournament(tourney_id, league):
        return []

    class TournamentPredictor:
        @staticmethod
        def get_tournament_info(date):
            return None, date, 0


logging.basicConfig(level=logging.INFO)


def update_summary(result):
    summary = get_summary(result.date)
    result.overview = summary
    result.save()


def execute():
    for league in leagues:
        last_date = get_last_date()

        logging.info(f"Trying to upload results for {league=} and {last_date=}")

        last_results = TourneyResult.objects.filter(date=last_date, league=league)

        if last_results:
            logging.info(f"Nothing new, results are already uploaded for {last_date=}")
            continue

        logging.info("Something new")
        csv_data = get_csv_data()
        last_files = sorted([file_name for file_name in glob(f"{csv_data}/{league}/{last_date}*") if "csv_raw" not in file_name])

        if not last_files:
            logging.info("Apparently we're checking the files before the download script could get them, try later.")
            continue

        last_file = last_files[-1]

        # Get tournament info and conditions (skip if thetower_bcs not available)
        conditions = []
        if TOWERBCS_AVAILABLE:
            try:
                tourney_id, tourney_date, days_until, _ = TournamentPredictor.get_tournament_info(last_date)
                conditions = predict_future_tournament(tourney_id, league)
                logging.info(f"Predicted {len(conditions)} battle conditions for {league}")
            except Exception as e:
                logging.error(f"Error predicting battle conditions: {e}")
                conditions = []
        else:
            logging.info("Skipping battle condition prediction (thetower_bcs not available)")

        try:
            with open(last_file, "rb") as infile:
                contents = infile.read()
        except FileNotFoundError:
            logging.info(f"{last_file=} not found, maybe later")
            continue

        logging.info("Creating file object")
        csv_file = SimpleUploadedFile(
            name=get_file_name(),
            content=contents,
            content_type="text/csv",
        )
        logging.info("Creating tourney_result")
        result, _ = TourneyResult.objects.update_or_create(
            date=last_date,
            league=league,
            defaults=dict(
                result_file=csv_file,
                public=True,  # Make results public by default
            ),
        )

        # Apply battle conditions if any were predicted
        if conditions:
            condition_ids = BattleCondition.objects.filter(name__in=conditions).values_list("id", flat=True)
            result.conditions.set(condition_ids)
            logging.info(f"Applied {len(condition_ids)} battle conditions to tournament result")
        else:
            logging.info("No battle conditions to apply")

        create_tourney_rows(result)

        # Generate summary for Legend league results
        if league == "Legend":
            logging.info("Generating summary for Legends league results")
            thread = threading.Thread(target=update_summary, args=(result,))
            thread.start()

    # Regenerate overview cache after all leagues have been imported so the
    # page stats are up to date without any DB queries on the next page load.
    logging.info("Regenerating overview cache after import")
    regenerate_overview_cache()
    logging.info("Overview cache regeneration complete")


if __name__ == "__main__":
    now = datetime.datetime.now()
    logging.info(f"Started import_results at {now}.")

    schedule.every().hour.at(":05").do(execute)
    logging.info(schedule.get_jobs())

    while True:

        n = schedule.idle_seconds()
        logging.info(f"Sleeping {n} seconds.")
        time.sleep(n)
        schedule.run_pending()
