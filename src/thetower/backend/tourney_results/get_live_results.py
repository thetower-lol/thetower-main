#!/tourney/tourney_venv/bin/python
import datetime
import logging
import time
from pathlib import Path

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
    return f"{utcnow.year}-{str(utcnow.month).zfill(2)}-{str(utcnow.day).zfill(2)}__{str(utcnow.hour).zfill(2)}_{str(utcnow.minute).zfill(2)}"


def get_file_name():
    return f"{get_last_date()}.csv.gz"


def get_file_path(file_name, league):
    csv_data = get_csv_data()
    return f"{csv_data}/current_tourney/{league}/{file_name}"


def execute(league):
    logging.info(f"Working on {league}.")
    file_path = Path(get_file_path(get_file_name(), league))
    file_path.parent.mkdir(parents=True, exist_ok=True)
    raw = fetch_leaderboard(league)
    try:
        df = parse_leaderboard(raw)
    except Exception:
        logging.error(f"Error parsing the {league} response; response kept at {keep_raw_response(raw, file_path)}")
        raise

    tmp_path = file_path.with_name(file_path.name + ".tmp")
    try:
        df.to_csv(str(tmp_path), index=False, compression="gzip")
        tmp_path.replace(file_path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise
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

    if date_offset > 1 or (date_offset == 1 and current_hour > 5):
        logging.info("Skipping because _not_ tourney day anymore!!")
        return

    if date_offset == 0 and current_hour == 0 and current_time.minute == 0:
        logging.info("Skipping because tourney *just* started.")
        return

    for league in leagues:
        try:
            execute(league)
        except Exception as e:
            logging.exception(e)
        time.sleep(2)


if __name__ == "__main__":
    now = datetime.datetime.now()
    logging.info(f"Started get_live_results at {now}.")

    schedule.every().hour.at(":00").do(get_results)
    schedule.every().hour.at(":30").do(get_results)
    logging.info(schedule.get_jobs())

    while True:
        n = schedule.idle_seconds()
        logging.info(f"Sleeping {n} seconds.")
        time.sleep(n)
        schedule.run_pending()
