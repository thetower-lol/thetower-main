#!/usr/bin/env python
"""
Generate per-tourney placement cache files for live placement analysis.

This script groups live snapshots into tourneys (using a 42-hour gap), then
incrementally updates a single flat cache file per tourney (per league). It is
safe to run periodically (every 30 minutes) or once via --once.

Writes are atomic; the cache file contains a `last_processed_iso` marker so the
generator only processes new snapshots since the last run.
"""

import argparse
import datetime
import json
import logging
import logging.handlers
import os
import tempfile
import time
from pathlib import Path

import django
import pandas as pd
import psutil
import schedule

# Django setup
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "thetower.backend.towerdb.settings")
django.setup()

from thetower.backend.env_config import get_csv_data
from thetower.backend.tourney_results.archive_utils import STRING_COLUMN_DTYPES
from thetower.backend.tourney_results.constants import leagues
from thetower.backend.tourney_results.data import get_player_id_lookup
from thetower.backend.tourney_results.shun_config import include_shun_enabled_for
from thetower.backend.tourney_results.sus_config import include_sus_enabled_for
from thetower.backend.tourney_results.tourney_utils import get_time

logging.basicConfig(level=logging.INFO)

# Configuration
LIVE_BASE = Path(get_csv_data())
# Log resolved paths early to aid debugging when run under different envs
logging.info(f"Resolved LIVE_BASE: {LIVE_BASE}")
# place caches in the existing results_cache directory (requested):
# cache files will be written under LIVE_BASE to keep them alongside snapshots
CACHE_BASE = LIVE_BASE
# Cache schema versioning: bump when the on-disk JSON structure changes
SCHEMA_VERSION = 3  # v3 adds min/max (quantile 0.0/1.0) to quantile_data
# Tourney grouping: snapshots > 42 hours apart indicate a new tourney
GAP_HOURS = 42


def atomic_write(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent))
    try:
        # mkstemp creates with mode 0600; chmod to 0664 so the ACL mask (derived
        # from group bits) is rw-, allowing named ACL entries (e.g. rslsync-tower) to be effective.
        os.chmod(fd, 0o664)
        with os.fdopen(fd, "w", encoding="utf8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)  # Added indent=4 for pretty-printing
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, str(path))
    finally:
        # if replace failed for some reason, try cleanup
        if Path(tmp).exists():
            try:
                Path(tmp).unlink()
            except Exception:
                pass


def list_live_snapshots(league: str):
    """List non-empty live snapshots for a league, sorted chronologically."""
    staging_dir = LIVE_BASE / "current_tourney" / league
    logging.debug(f"Checking staging dir for league {league}: {staging_dir}")
    if not staging_dir.exists():
        logging.debug(f"Staging dir missing for league {league}: {staging_dir}")
        return []
    files = [p for p in staging_dir.glob("*.csv.gz") if p.stat().st_size > 0]
    files_sorted = sorted(files, key=get_time)
    if not files_sorted:
        logging.debug(f"No non-empty snapshots found for league {league} in {staging_dir}")
    return files_sorted


def group_snapshots_into_tourneys(files: list[Path]) -> list[list[Path]]:
    """Group snapshot files into tourneys by gap threshold.

    Each new tourney starts when the gap between consecutive snapshots is > GAP_HOURS.
    """
    groups = []
    if not files:
        return groups
    gap = datetime.timedelta(hours=GAP_HOURS)
    current = [files[0]]
    for prev, cur in zip(files, files[1:]):
        prev_t = get_time(prev)
        cur_t = get_time(cur)
        if (cur_t - prev_t) > gap:
            groups.append(current)
            current = [cur]
        else:
            current.append(cur)
    groups.append(current)
    return groups


def snapshot_iso(p: Path) -> str:
    return p.stem


def build_player_index_from_df(df: pd.DataFrame) -> dict:
    """
    Build a safe player index from a dataframe.

    This is defensive: it tolerates missing columns and NaN values and will
    populate sensible defaults rather than raising exceptions which would
    otherwise cause the whole generator to skip writing a good cache.
    """
    res = {}
    if df is None or df.empty:
        return res

    # Ensure expected columns exist
    cols = set(df.columns)
    has_player = "player_id" in cols
    has_real = "real_name" in cols
    has_wave = "wave" in cols
    has_bracket = "bracket" in cols

    if not has_player:
        return res

    for pid, group in df.groupby("player_id"):
        try:
            # real_name: prefer first non-null, else empty string
            real_name = ""
            if has_real:
                rn = group["real_name"].dropna()
                if not rn.empty:
                    real_name = str(rn.iloc[0])

            # highest_wave: take numeric max if present
            highest_wave = None
            if has_wave:
                try:
                    max_wave = group["wave"].dropna().max()
                    if pd.notna(max_wave):
                        highest_wave = int(max_wave)
                except Exception:
                    highest_wave = None

            # bracket: first non-null bracket value
            bracket = None
            if has_bracket:
                b = group["bracket"].dropna()
                if not b.empty:
                    bracket = str(b.iloc[0])

            res[str(pid)] = {"real_name": real_name, "highest_wave": highest_wave, "bracket": bracket}
        except Exception:
            # defensive fallback for an unexpected per-player error
            res[str(pid)] = {"real_name": "", "highest_wave": None, "bracket": None}

    return res


def calculate_quantiles_for_cache(df: pd.DataFrame) -> dict:
    """
    Pre-compute quantile data for placement analysis.

    Calculates wave requirement quantiles for specific placement ranks across
    all brackets. This data is used by the live quantile analysis page.

    Args:
        df: DataFrame with columns: bracket, wave (at minimum)

    Returns:
        Dictionary with structure:
        {
            "ranks": [1, 2, 4, 6, 8, 10, 12, 15, 24],
            "quantiles": [0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95],
            "data": {
                "1": {"0.05": wave, "0.10": wave, ...},
                "2": {...},
                ...
            }
        }
    """
    ranks = [1, 2, 4, 6, 8, 10, 12, 15, 24]
    quantiles = [0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95]

    if df is None or df.empty:
        return {"ranks": ranks, "quantiles": quantiles, "data": {}}

    if "bracket" not in df.columns or "wave" not in df.columns:
        logging.warning("Missing bracket or wave column for quantile calculation")
        return {"ranks": ranks, "quantiles": quantiles, "data": {}}

    results = {}

    try:
        for rank in ranks:
            waves_at_rank = []

            # Collect wave values for this rank across all brackets
            for bracket in df["bracket"].unique():
                bracket_df = df[df["bracket"] == bracket]
                sorted_bracket = bracket_df.sort_values("wave", ascending=False)

                # Only include brackets with enough players for this rank
                if len(sorted_bracket) >= rank:
                    wave_at_rank = sorted_bracket.iloc[rank - 1]["wave"]
                    # Ensure it's a valid number
                    if pd.notna(wave_at_rank):
                        waves_at_rank.append(float(wave_at_rank))

            # Calculate quantiles for this rank
            if waves_at_rank:
                wave_series = pd.Series(waves_at_rank)
                rank_quantiles = {}
                for q in quantiles:
                    try:
                        rank_quantiles[str(q)] = float(wave_series.quantile(q))
                    except Exception:
                        rank_quantiles[str(q)] = None
                rank_quantiles["0.0"] = float(wave_series.min())
                rank_quantiles["1.0"] = float(wave_series.max())
                results[str(rank)] = rank_quantiles
            else:
                # No valid data for this rank
                results[str(rank)] = {str(q): None for q in quantiles}

    except Exception:
        logging.exception("Failed to calculate quantiles")
        return {"ranks": ranks, "quantiles": quantiles, "data": {}}

    return {"ranks": ranks, "quantiles": quantiles, "data": results}


def process_tourney_group(league: str, group: list[Path], include_shun: bool = False, include_sus: bool = False):
    """Process a single tourney group (chronological list of snapshot Paths).

    Writes a flat cache file named {tourney_date}_placement_cache.json under
    the live results cache for the league, next to snapshots:
    LIVE_BASE/{league}_live/{tourney_date}_placement_cache.json
    """
    if not group:
        return
    first = group[0]
    tourney_date = get_time(first).date().isoformat()  # YYYY-MM-DD
    # Place cache files alongside snapshots under the league_live folder so
    # operators find them next to the snapshots (matching get_live_results.py layout)
    cache_file = LIVE_BASE / f"{league}_live" / f"{tourney_date}_placement_cache.json"

    # Load existing cache if present
    last_processed_iso = None
    bracket_times = {}
    player_index = {}
    existing_snapshot_iso = None

    if cache_file.exists():
        try:
            payload = json.loads(cache_file.read_text(encoding="utf8"))
            last_processed_iso = payload.get("last_processed_iso")
            bracket_times = payload.get("bracket_creation_times", {}) or {}
            player_index = payload.get("player_index", {}) or {}
            existing_snapshot_iso = payload.get("snapshot_iso")
            # If schema is outdated, force a full regeneration so new fields are present
            try:
                existing_schema = int(payload.get("schema_version", 1))
            except Exception:
                existing_schema = 1
            if existing_schema < SCHEMA_VERSION:
                logging.info(f"Outdated cache schema (v{existing_schema} < v{SCHEMA_VERSION}) for {league} {tourney_date}; forcing full regen")
                last_processed_iso = None
                bracket_times = {}
                player_index = {}
            elif payload.get("include_shun") != include_shun or payload.get("include_sus", False) != include_sus:
                logging.info(f"include_shun/include_sus changed for {league} {tourney_date}; forcing full regen")
                last_processed_iso = None
                bracket_times = {}
                player_index = {}
        except Exception:
            logging.exception("Failed to load existing cache, regenerating")
            last_processed_iso = None
            bracket_times = {}
            player_index = {}

    # Build list of snapshots to process (those after last_processed_iso)
    to_process = []
    if last_processed_iso:
        try:
            last_dt = get_time(Path(last_processed_iso))
        except Exception:
            last_dt = None
    else:
        last_dt = None

    for p in group:
        p_dt = get_time(p)
        if (last_dt is None) or (p_dt > last_dt):
            to_process.append(p)

    if not to_process:
        logging.info(f"Cache up-to-date for {league} {tourney_date} (snapshot {existing_snapshot_iso})")
        return

    logging.info(f"Processing {len(to_process)} new snapshots for {league} {tourney_date}")

    for snap in to_process:
        try:
            # Read only this snapshot
            df = pd.read_csv(snap, dtype=STRING_COLUMN_DTYPES)
            # store full snapshot path so resume logic is robust
            snap_iso = str(snap.resolve())
            snap_time = get_time(snap).isoformat()

            # Record bracket first-seen time if new
            for br in df["bracket"].unique():
                if br not in bracket_times:
                    bracket_times[br] = snap_time

            # After processing snapshot, advance last_processed_iso
            last_processed_iso = snap_iso

            # Persist progress after each snapshot to make the generator resumable
            payload = {
                "schema_version": SCHEMA_VERSION,
                "tourney_date": tourney_date,
                # snapshot_iso and last_processed_iso are full snapshot path strings
                "snapshot_iso": last_processed_iso,
                "last_processed_iso": last_processed_iso,
                "include_shun": include_shun,
                # use timezone-aware UTC timestamp
                "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "bracket_creation_times": bracket_times,
                "player_index": player_index,
                "meta": {"num_brackets": len(bracket_times)},
            }
            atomic_write(cache_file, payload)
            logging.info(f"Wrote progress for {league} {tourney_date} at {last_processed_iso}")

        except Exception as e:
            logging.exception(f"Failed to process snapshot {snap} for {league} {tourney_date}: {e}")
            # stop processing further snapshots to retry later
            return

    # After processing all snapshots, update player_index from the latest snapshot
    # that actually contains player rows. Iterate from the end backwards so if
    # the final file for some reason is empty or malformed we pick the last
    # good snapshot instead of producing an empty player index.
    try:
        df_latest = None
        for snap in reversed(group):
            try:
                cand = pd.read_csv(snap, dtype=STRING_COLUMN_DTYPES)
                if cand is not None and not cand.empty and "player_id" in cand.columns:
                    df_latest = cand
                    break
            except Exception:
                # skip malformed snapshot and try the previous one
                continue

        if df_latest is None:
            logging.warning(f"No valid latest snapshot found for {league} {tourney_date}; keeping existing player_index")
        else:
            # Normalize and enrich the latest dataframe so build_player_index_from_df
            # has the columns it expects. Incoming live snapshots commonly have a
            # `name` column (tourney display name) rather than `real_name`.
            # Map player_id -> real_name using the same lookup used elsewhere
            # in the codebase to keep caches consistent with live views.
            try:
                lookup = get_player_id_lookup()
            except Exception:
                lookup = {}

            # populate real_name from lookup (fall back to snapshot name if present)
            if "name" in df_latest.columns:
                df_latest["real_name"] = [lookup.get(pid, name) for pid, name in zip(df_latest.player_id, df_latest.name)]
            else:
                df_latest["real_name"] = [lookup.get(pid, "") for pid in df_latest.player_id]

            # normalize bracket strings and coerce wave to numeric where possible
            if "bracket" in df_latest.columns:
                df_latest["bracket"] = df_latest["bracket"].astype(str).map(lambda x: x.strip())
            if "wave" in df_latest.columns:
                df_latest["wave"] = pd.to_numeric(df_latest["wave"], errors="coerce")

            player_index = build_player_index_from_df(df_latest)

        # Calculate quantile data from the latest snapshot for quantile analysis page
        quantile_data = {}
        if df_latest is not None and not df_latest.empty:
            try:
                quantile_data = calculate_quantiles_for_cache(df_latest)
                logging.info(f"Calculated quantile data for {league} {tourney_date}")
            except Exception:
                logging.exception(f"Failed to calculate quantiles for {league} {tourney_date}")

        payload = {
            "schema_version": SCHEMA_VERSION,
            "tourney_date": tourney_date,
            "snapshot_iso": last_processed_iso,
            "last_processed_iso": last_processed_iso,
            "include_shun": include_shun,
            "include_sus": include_sus,
            "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "bracket_creation_times": bracket_times,
            "player_index": player_index,
            "quantile_data": quantile_data,
            "meta": {"num_brackets": len(bracket_times), "num_players": len(player_index)},
        }
        atomic_write(cache_file, payload)
        logging.info(f"Finalized cache for {league} {tourney_date} (snap {last_processed_iso})")
    except Exception:
        logging.exception("Failed to update player_index from latest snapshot")


_memory_logger = logging.getLogger("tower.memory")
_memory_logger_configured = False


def _setup_memory_logger() -> None:
    global _memory_logger_configured
    if _memory_logger_configured:
        return
    log_file = LIVE_BASE / "memory.log"
    handler = logging.handlers.TimedRotatingFileHandler(
        log_file,
        when="d",
        backupCount=14,
        encoding="utf-8",
        utc=True,
    )
    handler.setFormatter(logging.Formatter("%(message)s"))
    _memory_logger.addHandler(handler)
    _memory_logger.setLevel(logging.INFO)
    _memory_logger.propagate = False
    _memory_logger_configured = True


def _get_service_name(proc: psutil.Process) -> str | None:
    """Return a human-readable service name based on process cmdline, or None if not a known tower service."""
    try:
        cmdline = " ".join(proc.cmdline())
    except (psutil.AccessDenied, psutil.NoSuchProcess):
        return None
    if not cmdline:
        return None

    if "streamlit" in cmdline and "pages.py" in cmdline:
        port = ""
        try:
            env_raw = Path(f"/proc/{proc.pid}/environ").read_bytes()
            for kv in env_raw.split(b"\x00"):
                if kv.startswith(b"STREAMLIT_SERVER_PORT="):
                    port = kv.split(b"=", 1)[1].decode("utf-8", errors="replace")
                    break
        except Exception:
            pass
        if port == "8501":
            return "streamlit_public"
        if port == "8503":
            return "streamlit_hidden"
        return f"streamlit_{port}" if port else "streamlit_unknown"

    patterns = [
        ("thetower-botui", "bot_ui"),
        ("thetower-bot", "discord_bot"),
        ("thetower-web", "web_platform"),
        ("thetower-backup", "backup"),
        ("generate_placement_cache", "placement_cache"),
        ("process_recalc_queue", "recalc_worker"),
        ("process_zendesk_queue", "zendesk_queue"),
        ("import_live_results", "import_live"),
        ("import_results", "import_results"),
        ("get_live_results", "get_live_results"),
        ("get_results", "get_results"),
    ]
    for pattern, name in patterns:
        if pattern in cmdline:
            return name
    return None


def _log_memory_snapshot() -> None:
    """Append an RSS memory snapshot for all known tower processes to the rotating memory log."""
    _setup_memory_logger()
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    my_uid = os.getuid()
    try:
        for proc in psutil.process_iter(["pid", "uids"]):
            try:
                if proc.info["uids"].real != my_uid:
                    continue
                name = _get_service_name(proc)
                if name is None:
                    continue
                rss_kb = proc.memory_info().rss // 1024
                _memory_logger.info("%s,%s,%d,%d", now, name, proc.pid, rss_kb)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    except Exception:
        logging.exception("Memory snapshot scan failed")


def execute_once():
    logging.info("Starting placement cache generation run")
    # Read current desired include_shun value for placement cache pages so we
    # generate caches that match the UI configuration. This ensures that when
    # include_shun is flipped in `include_shun.json` the generator will produce
    # a cache with the matching payload and the consumer will accept it.
    # This setting is used by both live_placement_analysis and live_quantile_analysis pages.
    include_shun = include_shun_enabled_for("live_placement_cache")
    include_sus = include_sus_enabled_for("live_placement_cache")
    logging.info(f"Placement cache generation: include_shun={include_shun}, include_sus={include_sus}")
    for league in leagues:
        try:
            snaps = list_live_snapshots(league)
            groups = group_snapshots_into_tourneys(snaps)
            logging.info(f"Found {len(groups)} tourney groups for league {league}")
            for group in groups:
                process_tourney_group(league, group, include_shun=include_shun, include_sus=include_sus)
        except Exception:
            logging.exception(f"Failed processing league {league}")
    logging.info("Placement cache generation run complete")
    _log_memory_snapshot()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="Run once and exit")
    args = parser.parse_args()

    if args.once:
        execute_once()
        return

    # run once immediately so the long-running process kicks off work
    # as soon as it starts, then fall into scheduled runs on :01 and :31
    # (this follows the schedule usage in get_live_results.py)
    execute_once()

    # schedule at :01 and :31 each hour (30-minute cycles anchored to the clock)
    schedule.every().hour.at(":01").do(execute_once)
    schedule.every().hour.at(":31").do(execute_once)
    logging.info("Scheduled placement cache generation on :01 and :31 each hour")

    while True:
        n = schedule.idle_seconds()
        if n is None:
            # no jobs scheduled? sleep a short while and re-evaluate
            n = 60
        logging.debug(f"Sleeping {n} seconds")
        time.sleep(min(n, 60))
        schedule.run_pending()


if __name__ == "__main__":
    main()
