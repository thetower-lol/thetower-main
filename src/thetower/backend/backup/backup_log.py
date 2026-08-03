"""Structured JSONL event log for the backup service.

Appends one JSON object per line to DJANGO_DATA/backup_log.jsonl.
Rotates (truncates oldest entries) when the file exceeds MAX_LINES.

Event types:
    tar_upload           — successful tar upload (local cleanup is logged separately)
    tar_error            — failed tar upload or failed local cleanup
    tar_delete_skipped   — tar is safe in R2 but local cleanup was skipped (e.g. archive missing)
    db_upload            — successful DB generational key upload
    db_error             — failed DB upload
    run_summary          — end-of-run totals (one per backup_new_tars / backup_database call)
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from thetower.backend.env_config import get_django_data

logger = logging.getLogger(__name__)

MAX_LINES = 10_000
_LOG_FILENAME = "backup_log.jsonl"


def _log_path() -> Path:
    return get_django_data() / _LOG_FILENAME


def _write_event(event: dict) -> None:
    """Append a single event to the log file, rotating if needed."""
    event.setdefault("ts", datetime.now(timezone.utc).isoformat())
    line = json.dumps(event, default=str)
    path = _log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Read existing lines if rotation needed
        if path.exists() and path.stat().st_size > 0:
            existing = path.read_text(encoding="utf-8").splitlines()
            if len(existing) >= MAX_LINES:
                keep = existing[-(MAX_LINES - 1) :]
                path.write_text("\n".join(keep) + "\n", encoding="utf-8")
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        logger.exception("Failed to write backup log event")


def log_tar_upload(league: str, filename: str, size_bytes: int, sha256: str) -> None:
    _write_event({"type": "tar_upload", "league": league, "file": filename, "size": size_bytes, "sha256": sha256[:16]})


def log_tar_error(league: str, filename: str, error: str) -> None:
    _write_event({"type": "tar_error", "league": league, "file": filename, "error": error})


def log_tar_delete_skipped(league: str, filename: str, reason: str) -> None:
    _write_event({"type": "tar_delete_skipped", "league": league, "file": filename, "reason": reason})


def log_db_upload(key: str, compressed_size: int, sha256: str) -> None:
    _write_event({"type": "db_upload", "key": key, "size": compressed_size, "sha256": sha256[:16]})


def log_db_error(key: str, error: str) -> None:
    _write_event({"type": "db_error", "key": key, "error": error})


def log_run_summary(run_type: str, stats: dict) -> None:
    _write_event({"type": "run_summary", "run": run_type, **stats})


def read_events(last_n: int = 500) -> list[dict]:
    """Read the last N events from the log file. Returns newest-first."""
    path = _log_path()
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        events = []
        for line in reversed(lines[-last_n:]):
            line = line.strip()
            if line:
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        return events
    except Exception:
        logger.exception("Failed to read backup log")
        return []
