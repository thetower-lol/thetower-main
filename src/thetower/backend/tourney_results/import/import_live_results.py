#!/usr/bin/env python
"""
Import live tourney snapshot data into delta archives.

Runs at :01 and :31 each hour (1 minute after get_live_results.py at :00/:30),
ensuring each freshly-written snapshot is picked up promptly.

During the tourney window, for each league:
  - Any staging snapshots in current_tourney/{league}/ not yet appended to the
    delta archive are processed via append_snapshot_to_archive().

After the tourney window closes, for each league still holding staging snapshots:
  - Archive fidelity is verified (row-for-row reconstruction check).
  - Snapshots are bundled into a raw tar in {league}_raw/.
  - Tar contents are verified (byte-for-byte).

The web UI picks up new data on its own: its live-data caches are keyed by the
latest snapshot, so appending here invalidates them without any signal.

Staging snapshots are NOT deleted here.  The backup service deletes them
(together with the tar) only after the tar is verified uploaded to R2, so a
crash, failed upload, or full disk can never destroy the only copy.
"""

import datetime
import logging
import os
import time
from pathlib import Path

import django
import pandas as pd
import schedule

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "thetower.backend.towerdb.settings")
django.setup()

from thetower.backend.env_config import get_csv_data
from thetower.backend.tourney_results.archive_utils import (
    append_snapshot_to_archive,
    bundle_tourney_to_raw,
    get_raw_path,
    group_snapshots_by_tourney,
    list_snapshots,
    read_archive,
    verify_archive_fidelity,
    verify_tar_contents,
)
from thetower.backend.tourney_results.constants import leagues
from thetower.backend.tourney_results.leaderboard_fetch import get_current_time__game_server, get_date_offset
from thetower.backend.tourney_results.tourney_utils import get_time

logging.basicConfig(level=logging.INFO)

LIVE_BASE = Path(get_csv_data())


def _in_tourney_window() -> bool:
    """True while the tourney is running (entry open or extended period)."""
    date_offset = get_date_offset()
    current_hour = get_current_time__game_server().hour
    return date_offset == 0 or (date_offset == 1 and current_hour <= 5)


def _cleanup_completed_tourney(group: list[Path], archive_path: Path, raw_dir: Path, league: str, tourney_date: str) -> None:
    """Bundle a completed tourney's staging snapshots into a verified raw tar.

    The staging snapshots are left in place: the backup service deletes them
    (and the tar) only once the tar is verified uploaded to R2.
    """
    tar_path = raw_dir / f"{tourney_date}_raw.tar"
    if tar_path.exists():
        # Already bundled and verified; awaiting R2 upload + cleanup by the backup service.
        return

    logging.info(f"Starting post-tourney bundling for {league} {tourney_date} ({len(group)} snapshots)")

    # Step 1: verify archive fidelity before touching anything
    logging.info(f"Verifying archive fidelity for {league} {tourney_date}...")
    ok, errors = verify_archive_fidelity(group, archive_path)
    if not ok:
        for err in errors:
            logging.error(f"Archive fidelity [{league} {tourney_date}]: {err}")
        logging.error(f"Aborting bundling for {league} {tourney_date} — archive verification failed")
        return
    logging.info(f"Archive fidelity OK for {league} {tourney_date}")

    # Step 2: bundle snapshots to raw tar
    try:
        tar_path = bundle_tourney_to_raw(group, raw_dir)
    except Exception:
        logging.exception(f"Failed to bundle {league} {tourney_date} to raw tar; aborting bundling")
        return

    # Step 3: verify tar contents; a bad tar is removed so the next run rebundles it
    logging.info(f"Verifying tar contents for {league} {tourney_date}...")
    ok, errors = verify_tar_contents(tar_path, group)
    if not ok:
        for err in errors:
            logging.error(f"Tar verification [{league} {tourney_date}]: {err}")
        logging.error(f"Tar verification failed for {league} {tourney_date} — removing bad tar; will rebundle next run")
        try:
            tar_path.unlink()
        except OSError:
            logging.exception(f"Failed to remove bad tar {tar_path}")
        return
    logging.info(f"Tar contents verified for {league} {tourney_date}; snapshots will be removed after R2 upload")


def process_league(league: str, in_window: bool) -> bool:
    """Process one league: append new snapshots and optionally clean up after tourney.

    Returns True if any archive was updated.
    """
    staging_dir = LIVE_BASE / "current_tourney" / league
    live_dir = LIVE_BASE / f"{league}_live"
    raw_dir = get_raw_path(league, LIVE_BASE)

    live_dir.mkdir(parents=True, exist_ok=True)

    staging_snapshots = list_snapshots(staging_dir)
    if not staging_snapshots:
        return False

    # Group into tourneys in case service lagged across a tourney boundary.
    # In normal operation there is exactly one group (the current tourney).
    groups = group_snapshots_by_tourney(staging_snapshots)
    archive_updated = False

    for i, group in enumerate(groups):
        # Only clean up a tourney group once a newer group exists (i.e. the next
        # tourney has started and produced its own snapshots).  This keeps the
        # most-recent tourney's snapshots in place until the next one begins,
        # so the live-results page can still serve data between tourneys.
        is_completed = i < len(groups) - 1

        tourney_date = get_time(group[0]).strftime("%Y-%m-%d")
        archive_path = live_dir / f"{tourney_date}_archive.csv.gz"

        # Determine which snapshots have not yet been appended.
        last_archived_time: pd.Timestamp | None = None
        if archive_path.exists():
            try:
                arc_df = read_archive(archive_path)
                if not arc_df.empty:
                    last_archived_time = arc_df["snapshot_time"].max()
            except Exception:
                logging.exception(f"Failed to read existing archive {archive_path}; will reprocess all snapshots")

        new_rows = 0
        for snap in group:
            snap_ts = pd.Timestamp(get_time(snap))
            if last_archived_time is None or snap_ts > last_archived_time:
                try:
                    count = append_snapshot_to_archive(snap, archive_path)
                    new_rows += count
                    last_archived_time = snap_ts
                    logging.info(f"Appended {count} delta rows from {snap.name} to {archive_path.name}")
                except Exception:
                    logging.exception(f"Failed to append {snap.name} to archive; skipping")
                    break  # stop processing further snapshots; retry next run

        if new_rows > 0:
            archive_updated = True

        if is_completed:
            _cleanup_completed_tourney(group, archive_path, raw_dir, league, tourney_date)

    return archive_updated


def execute():
    logging.info("import_live_results: starting run")
    in_window = _in_tourney_window()
    logging.info(f"import_live_results: tourney_window={in_window}")

    for league in leagues:
        try:
            process_league(league, in_window)
        except Exception:
            logging.exception(f"import_live_results: unhandled error processing league {league}")
        time.sleep(1)

    logging.info("import_live_results: run complete")


if __name__ == "__main__":
    now = datetime.datetime.now()
    logging.info(f"Started import_live_results at {now}.")

    execute()

    schedule.every().hour.at(":01").do(execute)
    schedule.every().hour.at(":31").do(execute)
    logging.info(schedule.get_jobs())

    while True:
        n = schedule.idle_seconds()
        logging.info(f"Sleeping {n} seconds.")
        time.sleep(n)
        schedule.run_pending()
