"""Fetch and parse leaderboard CSVs from the game API — shared by get_results and get_live_results.

Also home to the tourney schedule helpers (UTC; tournaments start Wednesdays and
Saturdays) that the fetchers and the live importer key their run windows on.

The saved results file is the only copy of what the API sent, so parsing never rounds
or truncates wave values (v29 adds a fractional tiebreaker), and when parsing has to
drop rows the verbatim response is kept next to the cleaned file for troubleshooting.
"""

import datetime
import gzip
import io
import logging
import os
from pathlib import Path

import pandas as pd
import requests

from .archive_utils import STRING_COLUMN_DTYPES

HEADER = "player_id,name,avatar,relic,wave,bracket,tourney_number\n"
RAW_SUFFIX = ".csv_raw.gz"

# Tourney schedule: which start day (Wednesday=2 or Saturday=5) a weekday belongs to.
weekdays_sat = [5, 6, 0, 1]
weekdays_wed = [2, 3, 4]
wednesday = 2
saturday = 5


def get_current_time__game_server() -> datetime.datetime:
    """Game server runs on utc time."""
    return datetime.datetime.now(datetime.UTC)


def get_date_offset() -> int:
    """Days since the most recent tourney start day (0 = today is a tourney day)."""
    utcnow = get_current_time__game_server()

    if utcnow.weekday() in weekdays_wed:
        offset = utcnow.weekday() - wednesday
    elif utcnow.weekday() in weekdays_sat:
        offset = (utcnow.weekday() - saturday) % 7
    else:
        raise ValueError(f"Unexpected weekday: {utcnow.weekday()}")

    return offset


def fetch_leaderboard(league: str) -> str:
    """Return the CSV body the game API sent for a league, untouched."""
    base_url = os.getenv("NEW_LEADERBOARD_URL")
    params = {"tier": league, "pass": os.getenv("LEADERBOARD_PASS")}
    return requests.get(base_url, params=params).text


def count_rows(csv_contents: str) -> int:
    """Non-blank lines in a raw response — the rows the API sent, before any parsing."""
    return sum(1 for line in csv_contents.splitlines() if line.strip())


def parse_leaderboard(csv_contents: str) -> pd.DataFrame:
    """Parse a raw API response into the frame that gets saved.

    Waves stay the exact text the API sent; rows with a missing or non-numeric wave
    (and lines pandas cannot parse) are dropped. Compare ``len(result)`` with
    ``count_rows(csv_contents)`` to learn how many were lost.
    """
    df = pd.read_csv(io.StringIO((HEADER + csv_contents).strip()), on_bad_lines="warn", dtype={**STRING_COLUMN_DTYPES, "wave": str})
    waves = pd.to_numeric(df["wave"], errors="coerce")
    bad_waves = int(waves.isna().sum())
    if bad_waves:
        logging.warning(f"Dropping {bad_waves} rows with missing or non-numeric wave values.")
        df = df[waves.notna()]
    df = df.sort_values("wave", ascending=False, key=pd.to_numeric)
    df["name"] = df["name"].map(lambda x: x.strip())
    df["bracket"] = df["bracket"].map(lambda x: x.strip())
    logging.info(f"There are {len(df.query('name.str.len() == 0'))} blank tourney names.")
    df.loc[df["name"].str.len() == 0, "name"] = df["player_id"]
    return df


def keep_raw_response(csv_contents: str, file_path: str | Path) -> Path:
    """Store the API response verbatim beside the cleaned file and return its path.

    Named ``<name>.csv_raw.gz`` so neither the importer (which skips ``csv_raw``) nor the
    live pipeline's ``*.csv.gz`` globs ever pick it up as data.
    """
    path = Path(file_path)
    raw_path = path.with_name(path.name.replace(".csv.gz", RAW_SUFFIX))
    # Written as bytes so no newline translation can alter the response.
    raw_path.write_bytes(gzip.compress(csv_contents.encode("utf8")))
    return raw_path
