#!/tourney/tourney_venv/bin/python
import datetime
import io
import logging
import os
import time

import pandas as pd
import requests
import schedule

from thetower.backend.env_config import get_csv_data

from .constants import leagues

# Constants
weekdays_sat = [5, 6, 0, 1]
weekdays_wed = [2, 3, 4]
wednesday = 2
saturday = 5

logging.basicConfig(level=logging.INFO)


def get_current_time__game_server():
    """Game server runs on utc time"""
    return datetime.datetime.now(datetime.UTC)


def get_date_offset() -> int:
    """Figure out how far away from last tourney day current time is."""
    utcnow = get_current_time__game_server()

    if utcnow.weekday() in weekdays_wed:
        offset = utcnow.weekday() - wednesday
    elif utcnow.weekday() in weekdays_sat:
        offset = (utcnow.weekday() - saturday) % 7
    else:
        raise ValueError("wtf")

    return offset


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


def make_request(league):
    base_url = os.getenv("NEW_LEADERBOARD_URL")
    params = {"tier": league, "pass": os.getenv("LEADERBOARD_PASS")}

    csv_response = requests.get(base_url, params=params)
    csv_contents = csv_response.text

    header = "player_id,name,avatar,relic,wave,bracket,tourney_number\n"

    csv_contents = header + csv_contents
    df = pd.read_csv(io.StringIO(csv_contents.strip()), on_bad_lines="warn")
    nan_waves = df["wave"].isna().sum()
    if nan_waves:
        logging.warning(f"Dropping {nan_waves} rows with NaN wave values.")
        df = df.dropna(subset=["wave"])
    df["wave"] = df["wave"].astype(int)
    df = df.sort_values("wave", ascending=False)
    df["name"] = df["name"].map(lambda x: x.strip())
    df["bracket"] = df["bracket"].map(lambda x: x.strip())
    logging.info(f"There are {len(df.query('name.str.len() == 0'))} blank tourney names.")
    df.loc[df["name"].str.len() == 0, "name"] = df["player_id"]
    return df


def execute(league):
    logging.info(f"Working on {league}.")
    file_path = get_file_path(get_file_name(), league)

    if os.path.isfile(file_path):
        logging.info(f"Using cached file {file_path}")
        return

    for attempt in range(1, 4):
        try:
            df = make_request(league)
        except Exception as e:
            logging.error(f"Error in make_request (attempt {attempt}): {e}")
            return

        if not df.empty:
            break

        logging.warning(f"Empty response for {league} (attempt {attempt}/3), retrying in 30s...")
        time.sleep(30)
    else:
        logging.error(f"All 3 attempts returned empty data for {league}, not saving file.")
        return

    df.to_csv(file_path, index=False, compression="gzip")
    logging.info(f"Successfully stored file {file_path}")

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
