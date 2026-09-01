#!/tourney/tourney_venv/bin/python
import datetime
import logging
import os
import time

import schedule

from thetower.backend.env_config import get_csv_data

from .constants import leagues
from .leaderboard_fetch import (
    count_rows,
    fetch_leaderboard,
    get_current_time__game_server,
    get_date_offset,
    keep_raw_response,
    parse_leaderboard,
)

logging.basicConfig(level=logging.INFO)


def get_last_date():
    utcnow = get_current_time__game_server()
    offset = get_date_offset()

    last_tourney_day = (utcnow - datetime.timedelta(days=offset)).day
    last_tourney_month = (utcnow - datetime.timedelta(days=offset)).month
    last_tourney_year = (utcnow - datetime.timedelta(days=offset)).year

    return f"{last_tourney_year}-{str(last_tourney_month).zfill(2)}-{str(last_tourney_day).zfill(2)}"


def get_file_name():
    return f"{get_last_date()}.csv.gz"


def get_file_path(file_name, league):
    csv_data = get_csv_data()
    return f"{csv_data}/{league}/{file_name}"


def execute(league):
    logging.info(f"Working on {league}.")
    file_path = get_file_path(get_file_name(), league)

    if os.path.isfile(file_path):
        logging.info(f"Using cached file {file_path}")
        return

    for attempt in range(1, 4):
        try:
            raw = fetch_leaderboard(league)
        except Exception as e:
            logging.error(f"Error fetching {league} (attempt {attempt}): {e}")
            return
        try:
            df = parse_leaderboard(raw)
        except Exception as e:
            raw_path = keep_raw_response(raw, file_path)
            logging.error(f"Error parsing the {league} response (attempt {attempt}): {e}; response kept at {raw_path}")
            return

        if not df.empty:
            break

        logging.warning(f"Empty response for {league} (attempt {attempt}/3), retrying in 30s...")
        time.sleep(30)
    else:
        if count_rows(raw):
            raw_path = keep_raw_response(raw, file_path)
            logging.error(f"All 3 attempts returned no usable rows for {league}; last response kept at {raw_path}")
        else:
            logging.error(f"All 3 attempts returned empty data for {league}, not saving file.")
        return

    os.makedirs(os.path.dirname(file_path), exist_ok=True)  # a new league has no folder yet
    df.to_csv(file_path, index=False, compression="gzip")
    logging.info(f"Successfully stored file {file_path}")

    dropped = count_rows(raw) - len(df)
    if dropped > 0:
        raw_path = keep_raw_response(raw, file_path)
        logging.warning(f"{dropped} rows of the {league} response were dropped while parsing; raw response kept at {raw_path}")

    return True


def get_results():
    date_offset = get_date_offset()
    current_time = get_current_time__game_server()
    current_hour = current_time.hour

    if date_offset == 0 or date_offset == 1 and current_hour < 5:
        logging.info("Skipping cause tourney day!!")
        return

    for league in leagues:
        try:
            execute(league)
        except Exception as e:
            logging.exception(e)
        time.sleep(2)


if __name__ == "__main__":
    now = datetime.datetime.now()
    logging.info(f"Started get_results at {now}.")

    schedule.every().hour.at(":00").do(get_results)
    schedule.every().hour.at(":30").do(get_results)
    logging.info(schedule.get_jobs())

    while True:
        n = schedule.idle_seconds()
        logging.info(f"Sleeping {n} seconds.")
        time.sleep(n)
        schedule.run_pending()
