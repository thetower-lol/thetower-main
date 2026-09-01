"""archive_utils.py — Delta-compressed tourney archive builder and reader.

Archive format
--------------
One gzip-compressed CSV **per tourney per league**, stored alongside the live
snapshots as ``{league}_live/{date}_archive.csv.gz`` where ``date`` is the
date of the first snapshot in that tourney's run.

Columns: snapshot_time, player_id, name, avatar, relic, wave, bracket, tourney_number

Only rows where a player's wave *changed* (or the player *first appeared*) are
written — this is the delta-compressed representation.  A full snapshot at any
point in time can be reconstructed via ``reconstruct_at`` or
``reconstruct_all_snapshots``.

Key functions
-------------
- ``group_snapshots_by_tourney``   — split a flat list of snapshot Paths into per-tourney groups
- ``build_tourney_archive``        — build a delta archive from one tourney's snapshot list
- ``append_snapshot_to_archive``   — incrementally append one snapshot's delta rows to an archive
- ``build_all_archives``           — build all missing per-tourney archives for a league live dir
- ``bundle_tourney_to_raw``        — tar a completed tourney's snapshots into cold storage
- ``get_raw_path``                 — return the ``{league}_raw/`` directory path for a league
- ``list_archives``                — list existing ``*_archive.csv.gz`` files
- ``read_archive``                 — read one archive file to a DataFrame
- ``reconstruct_at``               — full leaderboard state at a point in time
- ``reconstruct_all_snapshots``    — full timeline DataFrame (matches ``get_live_df`` output shape)
"""

import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

# Force string dtype for columns holding arbitrary hex/user strings so pandas never
# runs numeric inference on them: a player_id like "9E7816514276BF83" is otherwise
# parsed as 9e7816514276, whose exponent overflows and segfaults the interpreter in
# the cp314 Linux builds of pandas 2.3.3.  The integer keys cover header-less legacy
# result files (0=id, 1=tourney_name); pandas ignores dtype keys for absent columns.
STRING_COLUMN_DTYPES = {"player_id": str, "name": str, "bracket": str, 0: str, 1: str}

# Matches both zero-padded (08_30) and unpadded (8_30) snapshot filenames.
_SNAP_STEM_RE = re.compile(r"(?P<date>\d{4}-\d{2}-\d{2})__(?P<h>\d{1,2})_(?P<m>\d{1,2})")


def _parse_snapshot_time(path: Path) -> datetime:
    """Parse datetime from a snapshot filename, supporting both .csv.gz and .csv."""
    stem = path.stem
    if stem.endswith(".csv"):
        stem = stem[:-4]
    m = _SNAP_STEM_RE.search(stem)
    if not m:
        raise ValueError(f"Cannot parse timestamp from filename: {path.name}")
    return datetime(
        *map(int, m.group("date").split("-")),
        int(m.group("h")),
        int(m.group("m")),
    )


# Gap larger than this between consecutive snapshots means a new tourney has started.
_TOURNEY_GAP_HOURS = 8


def list_snapshots(live_path: Path) -> list[Path]:
    """Return non-empty snapshot .csv.gz files sorted chronologically by filename timestamp."""
    if not live_path.exists():
        return []
    files = [p for p in live_path.glob("*.csv.gz") if p.stat().st_size > 0 and "_archive" not in p.name]
    return sorted(files, key=lambda p: _parse_snapshot_time(p))


def list_archives(live_path: Path) -> list[Path]:
    """Return existing ``*_archive.csv.gz`` files sorted chronologically."""
    if not live_path.exists():
        return []
    files = [p for p in live_path.glob("*_archive.csv.gz") if p.stat().st_size > 0]
    return sorted(files, key=lambda p: p.stem.replace("_archive", ""))


def _read_tourney_number(path: Path) -> Optional[int]:
    """Read the tourney_number from the first data row of a snapshot file.

    Returns None if the file cannot be read or is missing the column.
    """
    try:
        row = pd.read_csv(path, nrows=1, dtype=STRING_COLUMN_DTYPES)
        if "tourney_number" in row.columns and not row.empty:
            return int(row["tourney_number"].iloc[0])
    except Exception:
        pass
    return None


def group_snapshots_by_tourney(snapshots: list[Path]) -> list[list[Path]]:
    """Split a sorted snapshot list into per-tourney groups.

    Groups by the ``tourney_number`` column read from each snapshot — the
    authoritative game-provided tourney identifier.  Every snapshot in a group
    shares the same ``tourney_number``.  This correctly handles stale snapshots
    from a completed tourney that were taken just before the next one started
    (which would otherwise fool an 8-hour-gap heuristic).

    Falls back to the time-gap heuristic (``_TOURNEY_GAP_HOURS``) for any
    snapshot whose ``tourney_number`` cannot be read.

    Returns a list of groups, each group being a sorted (by timestamp)
    non-empty list of Path objects.
    """
    if not snapshots:
        return []

    # Build (tourney_number, timestamp, path) triples.
    triples: list[tuple] = []
    for snap in snapshots:
        try:
            ts = _parse_snapshot_time(snap)
        except Exception:
            logger.warning(f"Cannot parse timestamp from {snap.name}; skipping in grouping")
            continue
        tn = _read_tourney_number(snap)
        triples.append((tn, ts, snap))

    if not triples:
        return []

    # Sort by timestamp (snapshots should already be sorted, but be safe).
    triples.sort(key=lambda t: t[1])

    # Group by tourney_number; fall back to gap-heuristic when tn is None.
    groups: list[list[Path]] = []
    current_group: list[Path] = [triples[0][2]]

    for (tn, ts, snap), (prev_tn, prev_ts, _) in zip(triples[1:], triples):
        gap_hours = (ts - prev_ts).total_seconds() / 3600

        if tn is not None and prev_tn is not None:
            # Both readable: split on tourney_number change.
            if tn != prev_tn:
                groups.append(current_group)
                current_group = [snap]
            else:
                current_group.append(snap)
        else:
            # At least one unreadable: fall back to time-gap heuristic.
            if gap_hours > _TOURNEY_GAP_HOURS:
                groups.append(current_group)
                current_group = [snap]
            else:
                current_group.append(snap)

    groups.append(current_group)
    return groups


def _archive_name_for_group(group: list[Path]) -> str:
    """Return the archive filename stem (``YYYY-MM-DD``) for a tourney group."""
    first_date = _parse_snapshot_time(group[0]).strftime("%Y-%m-%d")
    return f"{first_date}_archive.csv.gz"


def build_tourney_archive(snapshots: list[Path], write_path: Optional[Path] = None) -> pd.DataFrame:
    """Build a delta-compressed archive DataFrame from one tourney's snapshot list.

    Parameters
    ----------
    snapshots:
        Ordered list of ``YYYY-MM-DD__HH_MM.csv.gz`` snapshot Paths for a single tourney.
    write_path:
        If provided, the resulting DataFrame is written to this path as a
        gzip-compressed CSV (atomic write via a temp file in the same dir).

    Returns
    -------
    pd.DataFrame with columns:
        snapshot_time, player_id, name, avatar, relic, wave, bracket, tourney_number

    Only first-appearance rows and rows where wave changed are included.
    """
    if not snapshots:
        return pd.DataFrame()

    rows: list[dict] = []
    last_wave: dict[str, float] = {}

    for snap in snapshots:
        try:
            snap_time = _parse_snapshot_time(snap)
        except Exception:
            logger.warning(f"Could not parse timestamp from {snap.name}; skipping")
            continue
        try:
            df = pd.read_csv(snap, dtype=STRING_COLUMN_DTYPES)
        except Exception as exc:
            logger.warning(f"Failed to read {snap}: {exc}; skipping")
            continue

        if df.empty:
            continue

        snap_time_str = snap_time.isoformat()

        # Deduplicate players appearing in multiple brackets (ghost-bracket entries).
        # A ghost bracket inserts the same player_id with wave=1 alongside their real
        # bracket entry in the same snapshot.  Keep only the row with the highest wave
        # per player so the archive delta stream is clean and last_wave tracking stays
        # accurate.  For tie-breaking on static columns, prefer the row with the higher
        # wave (i.e. the real bracket entry).
        best: dict[str, tuple] = {}  # pid -> (wave, row)
        for row in df.itertuples(index=False):
            pid = str(row.player_id)
            try:
                wave = float(row.wave)
            except (ValueError, TypeError):
                continue
            if pid not in best or wave > best[pid][0]:
                best[pid] = (wave, row)

        for pid, (wave, row) in best.items():
            if pid not in last_wave or last_wave[pid] != wave:
                rows.append(
                    {
                        "snapshot_time": snap_time_str,
                        "player_id": pid,
                        "name": getattr(row, "name", ""),
                        "avatar": getattr(row, "avatar", ""),
                        "relic": getattr(row, "relic", ""),
                        "wave": wave,
                        "bracket": getattr(row, "bracket", ""),
                        "tourney_number": getattr(row, "tourney_number", ""),
                    }
                )
                last_wave[pid] = wave

    if not rows:
        return pd.DataFrame()

    out = pd.DataFrame(rows)
    out["snapshot_time"] = pd.to_datetime(out["snapshot_time"])
    out = out.sort_values("snapshot_time").reset_index(drop=True)

    if write_path is not None:
        _atomic_write(out, write_path)

    return out


def build_all_archives(live_path: Path, force: bool = False) -> list[Path]:
    """Build per-tourney archive files for all tourneys found in ``live_path``.

    Archives are written as ``{live_path}/{YYYY-MM-DD}_archive.csv.gz`` where
    the date is taken from the first snapshot in each tourney group.

    Parameters
    ----------
    live_path:
        Directory containing ``YYYY-MM-DD__HH_MM.csv.gz`` snapshot files.
    force:
        If True, rebuild archives even if the file already exists.  The in-progress
        tourney's archive (the most recent group) is always rebuilt so it stays current.

    Returns
    -------
    List of Path objects for every archive file written.
    """
    snapshots = list_snapshots(live_path)
    if not snapshots:
        logger.warning(f"No snapshots found in {live_path}")
        return []

    groups = group_snapshots_by_tourney(snapshots)
    written: list[Path] = []

    for i, group in enumerate(groups):
        archive_path = live_path / _archive_name_for_group(group)
        is_current = i == len(groups) - 1  # Always rebuild the live tourney
        if archive_path.exists() and not force and not is_current:
            logger.debug(f"Archive already exists, skipping: {archive_path.name}")
            continue
        logger.info(f"Building archive for {archive_path.name} ({len(group)} snapshots)...")
        build_tourney_archive(group, write_path=archive_path)
        written.append(archive_path)

    return written


def read_archive(path: Path) -> pd.DataFrame:
    """Read a ``{date}_archive.csv.gz`` file and parse snapshot_time as datetime."""
    df = pd.read_csv(path, dtype=STRING_COLUMN_DTYPES)
    df["snapshot_time"] = pd.to_datetime(df["snapshot_time"], format="ISO8601")
    return df


def reconstruct_at(archive: pd.DataFrame, at: datetime) -> pd.DataFrame:
    """Reconstruct the full leaderboard state at a given point in time.

    Returns a DataFrame with the same columns as a single live snapshot:
        player_id, name, avatar, relic, wave, bracket, tourney_number, datetime

    Only players whose first appearance is on or before ``at`` are included,
    and each player carries their most-recent wave as of ``at``.
    """
    if archive.empty:
        return pd.DataFrame()

    subset = archive[archive["snapshot_time"] <= at]
    if subset.empty:
        return pd.DataFrame()

    # Last known state per player
    latest = subset.sort_values("snapshot_time").groupby("player_id").last().reset_index()
    latest = latest.rename(columns={"snapshot_time": "datetime"})
    cols = ["player_id", "name", "avatar", "relic", "wave", "bracket", "tourney_number", "datetime"]
    return latest[[c for c in cols if c in latest.columns]]


def reconstruct_all_snapshots(archive: pd.DataFrame, extra_timestamps=None) -> pd.DataFrame:
    """Reconstruct a concatenated DataFrame spanning every snapshot in the archive.

    The output matches the shape produced by the existing ``get_live_df``:
        player_id, name, avatar, relic, wave, bracket, tourney_number, datetime

    Each unique ``snapshot_time`` becomes a ``datetime`` value, and every player
    carries their correct wave at each moment (forward-filled from the last
    recorded change).

    This is the drop-in replacement for reading ~48 individual snapshot CSVs.

    Args:
        archive: Delta-compressed archive DataFrame.
        extra_timestamps: Optional sequence of additional datetime values (e.g.
            "silent" snapshots where no waves changed).  These will be included
            in the output via forward-fill so the timeline covers the full
            tourney window.
    """
    if archive.empty:
        return pd.DataFrame()

    times = sorted(archive["snapshot_time"].unique())
    if extra_timestamps is not None:
        all_times = sorted(set(times) | {pd.Timestamp(t) for t in extra_timestamps})
    else:
        all_times = times

    # Pivot: rows = snapshot_time, columns = player_id, values = wave
    pivot = archive.pivot_table(index="snapshot_time", columns="player_id", values="wave", aggfunc="last")
    pivot = pivot.reindex(all_times).ffill()

    # Melt back to long form
    long = pivot.reset_index().melt(id_vars=["snapshot_time"], var_name="player_id", value_name="wave")
    long = long.dropna(subset=["wave"])  # players not yet appeared at this time
    long["wave"] = pd.to_numeric(long["wave"])

    # Join static columns (last known values per player — they never change anyway)
    # avatar and relic are excluded: they are never displayed on any live page
    static_cols = ["name", "bracket", "tourney_number"]
    static = archive.sort_values("snapshot_time").groupby("player_id").last()[static_cols]
    long = long.join(static, on="player_id")

    long = long.rename(columns={"snapshot_time": "datetime"})
    long = long.sort_values(["datetime", "wave"], ascending=[True, False]).reset_index(drop=True)
    long["player_id"] = long["player_id"].astype("category")
    long["bracket"] = long["bracket"].astype("category")
    # name/tourney_number repeat once per snapshot timestamp per player, so
    # category dtype shrinks the frame (and its st.cache_data pickles) substantially
    long["name"] = long["name"].astype("category")
    long["tourney_number"] = long["tourney_number"].astype("category")
    return long


def get_raw_path(league: str, csv_data: str | Path) -> Path:
    """Return the cold-storage raw tar directory for ``league``.

    Mirrors the ``{league}_live`` convention: the returned path is
    ``{csv_data}/{league}_raw/``.  The directory is not created here.
    """
    return Path(csv_data) / f"{league}_raw"


def append_snapshot_to_archive(snapshot_path: Path, archive_path: Path) -> int:
    """Incrementally append one snapshot's delta rows to an existing (or new) archive.

    This is the streaming equivalent of ``build_tourney_archive``: instead of
    rebuilding the full archive from all snapshots, it reads one new snapshot,
    computes the delta against the last-known state in the archive, and appends
    only the changed rows.  Suitable for calling after every snapshot write.

    Ghost-bracket deduplication is applied to the incoming snapshot (same logic
    as ``build_tourney_archive``: keep the highest-wave row per player).

    Parameters
    ----------
    snapshot_path:
        Path to a single ``YYYY-MM-DD__HH_MM.csv.gz`` snapshot file.
    archive_path:
        Path to the ``{date}_archive.csv.gz`` file to update.  Need not exist yet;
        if absent the snapshot is treated as the first in a new tourney.

    Returns
    -------
    Number of new delta rows appended (0 if nothing changed).
    """
    try:
        snap_time = _parse_snapshot_time(snapshot_path)
    except ValueError:
        raise

    try:
        snap_df = pd.read_csv(snapshot_path, dtype=STRING_COLUMN_DTYPES)
    except Exception as exc:
        raise RuntimeError(f"Failed to read snapshot {snapshot_path}: {exc}") from exc

    if snap_df.empty:
        return 0

    # Deduplicate ghost-bracket entries — keep highest-wave row per player.
    best: dict[str, tuple] = {}
    for row in snap_df.itertuples(index=False):
        pid = str(row.player_id)
        try:
            wave = float(row.wave)
        except (ValueError, TypeError):
            continue
        if pid not in best or wave > best[pid][0]:
            best[pid] = (wave, row)

    # Load last known wave per player from the existing archive.
    last_wave: dict[str, float] = {}
    existing: Optional[pd.DataFrame] = None
    if archive_path.exists():
        existing = read_archive(archive_path)
        if not existing.empty:
            last = existing.sort_values("snapshot_time").groupby("player_id")["wave"].last()
            last_wave = last.to_dict()

    snap_time_str = snap_time.isoformat()
    new_rows: list[dict] = []
    for pid, (wave, row) in best.items():
        if pid not in last_wave or last_wave[pid] != wave:
            new_rows.append(
                {
                    "snapshot_time": snap_time_str,
                    "player_id": pid,
                    "name": getattr(row, "name", ""),
                    "avatar": getattr(row, "avatar", ""),
                    "relic": getattr(row, "relic", ""),
                    "wave": wave,
                    "bracket": getattr(row, "bracket", ""),
                    "tourney_number": getattr(row, "tourney_number", ""),
                }
            )

    if not new_rows:
        return 0

    new_df = pd.DataFrame(new_rows)
    new_df["snapshot_time"] = pd.to_datetime(new_df["snapshot_time"])

    if existing is not None and not existing.empty:
        combined = pd.concat([existing, new_df], ignore_index=True)
    else:
        combined = new_df

    combined = combined.sort_values("snapshot_time").reset_index(drop=True)
    _atomic_write(combined, archive_path)
    return len(new_rows)


def bundle_tourney_to_raw(group: list[Path], raw_path: Path) -> Path:
    """Bundle a completed tourney's snapshot files into a single tar for cold storage.

    The tar is written atomically (temp file → ``os.replace``) to avoid leaving
    a partially-written archive on disk.  The snapshot files themselves are NOT
    deleted here; deletion is the caller's responsibility after confirming the
    tar is intact.

    Parameters
    ----------
    group:
        Ordered list of ``YYYY-MM-DD__HH_MM.csv.gz`` snapshot Paths for one tourney.
    raw_path:
        Directory where the tar file will be written (e.g. ``{league}_raw/``).
        Created automatically if it does not exist.

    Returns
    -------
    Path to the written tar file (``{raw_path}/{YYYY-MM-DD}_raw.tar``).
    """
    import os
    import tarfile
    import tempfile

    if not group:
        raise ValueError("Cannot bundle an empty group")

    raw_path.mkdir(parents=True, exist_ok=True)
    first_date = _parse_snapshot_time(group[0]).strftime("%Y-%m-%d")
    tar_path = raw_path / f"{first_date}_raw.tar"

    tmp_fd, tmp_path = tempfile.mkstemp(dir=raw_path, suffix=".tar.tmp")
    try:
        os.close(tmp_fd)
        with tarfile.open(tmp_path, "w") as tf:
            for snap in group:
                tf.add(snap, arcname=snap.name)
        os.chmod(tmp_path, 0o644)
        os.replace(tmp_path, tar_path)
        logger.info(f"Bundled {len(group)} snapshots to {tar_path}")
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

    return tar_path


def _atomic_write(df: pd.DataFrame, path: Path) -> None:
    """Write df to path as gzip CSV, using a sibling temp file for atomicity."""
    import shutil
    import tempfile

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".csv.gz.tmp")
    try:
        import os

        os.close(tmp_fd)
        df.to_csv(tmp_path, index=False, compression="gzip")
        os.chmod(tmp_path, 0o644)
        shutil.move(tmp_path, path)
        logger.info(f"Wrote archive to {path} ({len(df):,} delta rows)")
    except Exception:
        try:
            import os

            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def verify_archive_fidelity(snapshots: list[Path], archive_path: Path) -> tuple[bool, list[str]]:
    """Verify that a delta archive faithfully represents the given snapshot list.

    For each snapshot, reconstructs the leaderboard state from the archive at that
    snapshot's timestamp and checks two properties:

    1. Every player present in the raw snapshot also appears in the archive
       reconstruction with a matching wave value.
    2. Wave values in the archive match the raw (highest-wave ghost-dedup applied).

    Note: the archive's carry-forward design means the reconstruction at time T may
    include players from earlier snapshots not present in the raw at T (e.g. a sparse
    early-tournament capture).  This is expected behaviour and is NOT treated as an
    error — only missing or incorrect data in the archive is reported.

    Parameters
    ----------
    snapshots:
        Ordered list of snapshot Paths belonging to one tourney.
    archive_path:
        Path to the ``{date}_archive.csv.gz`` file to verify against.

    Returns
    -------
    Tuple of (ok, errors) where ok is True if no discrepancies were found and
    errors is a (possibly empty) list of descriptive error strings.
    """
    errors: list[str] = []

    if not archive_path.exists():
        return False, [f"Archive not found: {archive_path}"]

    archive = read_archive(archive_path)
    if archive.empty:
        return False, ["Archive is empty"]

    for snap in snapshots:
        try:
            snap_time = _parse_snapshot_time(snap)
        except Exception as exc:
            errors.append(f"Cannot parse time from {snap.name}: {exc}")
            continue

        try:
            snap_df = pd.read_csv(snap, dtype=STRING_COLUMN_DTYPES)
        except Exception as exc:
            errors.append(f"Cannot read {snap.name}: {exc}")
            continue

        if snap_df.empty:
            continue

        # Apply same ghost-bracket dedup as build_tourney_archive: highest wave per player.
        best: dict[str, float] = {}
        for row in snap_df.itertuples(index=False):
            pid = str(row.player_id)
            try:
                wave = float(row.wave)
            except (ValueError, TypeError):
                continue
            if pid not in best or wave > best[pid]:
                best[pid] = wave

        # Reconstruct from archive at this snapshot's timestamp.
        reconstructed = reconstruct_at(archive, snap_time)
        recon_waves: dict[str, float] = {}
        if not reconstructed.empty:
            recon_waves = dict(zip(reconstructed["player_id"].astype(str), reconstructed["wave"].astype(float)))

        for pid, wave in best.items():
            if pid not in recon_waves:
                errors.append(f"{snap.name}: player {pid} (wave={wave}) missing from archive reconstruction at {snap_time}")
            elif recon_waves[pid] != wave:
                errors.append(f"{snap.name}: player {pid} wave mismatch: raw={wave}, archive={recon_waves[pid]}")

    return len(errors) == 0, errors


def verify_tar_contents(tar_path: Path, snapshots: list[Path]) -> tuple[bool, list[str]]:
    """Verify that a tar file contains all expected snapshots with identical content.

    Compares byte-for-byte each snapshot against the corresponding entry in the tar.

    Parameters
    ----------
    tar_path:
        Path to the ``{date}_raw.tar`` file to verify.
    snapshots:
        List of original snapshot Paths that should be in the tar.

    Returns
    -------
    Tuple of (ok, errors) where ok is True if all files match and errors is a
    (possibly empty) list of descriptive error strings.
    """
    import tarfile

    errors: list[str] = []

    if not tar_path.exists():
        return False, [f"Tar not found: {tar_path}"]

    expected = {snap.name: snap for snap in snapshots}

    try:
        with tarfile.open(tar_path, "r") as tf:
            tar_members = {m.name: m for m in tf.getmembers() if m.isfile()}

            for name, snap in expected.items():
                if name not in tar_members:
                    errors.append(f"Missing from tar: {name}")
                    continue
                try:
                    f = tf.extractfile(tar_members[name])
                    if f is None:
                        errors.append(f"Cannot extract {name} from tar")
                        continue
                    tar_content = f.read()
                    orig_content = snap.read_bytes()
                    if tar_content != orig_content:
                        errors.append(f"Content mismatch for {name}: tar={len(tar_content)}b orig={len(orig_content)}b")
                except Exception as exc:
                    errors.append(f"Error reading {name} from tar: {exc}")

            for name in tar_members:
                if name not in expected:
                    errors.append(f"Unexpected file in tar: {name}")
    except Exception as exc:
        return False, [f"Failed to open tar {tar_path}: {exc}"]

    return len(errors) == 0, errors
