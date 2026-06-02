"""Shared verification infrastructure for image storage, OCR processing, and review queue.

This module contains all the shared verification logic used by both thetower-bot and thetower-web:
- Image storage and sharding
- OCR status file management (.ocr.json files)
- Review queue database operations
- Submission logging
- Main verification workflow

Both bot and web use these as backend services, implementing only presentation-layer logic.
"""

import json
import logging
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

UPLOAD_DIR = Path(os.getenv("WEB_UPLOAD_DIR", "/data/verification_images"))
ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
OCR_NEAR_MATCH_MAX = int(os.getenv("WEB_OCR_NEAR_MATCH_MAX", "2"))
MAX_UPLOAD_BYTES = int(os.getenv("WEB_MAX_UPLOAD_BYTES", str(10 * 1024 * 1024)))

REVIEW_DB_PATH = UPLOAD_DIR / "review_queue.db"

# Try to import OCR utilities
try:
    from thetower.utils.ocr import analyze_verification_screenshot, is_available as ocr_available

    OCR_ENABLED = ocr_available()
except ImportError:
    OCR_ENABLED = False
    analyze_verification_screenshot = None


# ---------------------------------------------------------------------------
# Image Storage Utilities
# ---------------------------------------------------------------------------


def get_image_storage_path(player_id: str, stem: str, extension: str = ".png") -> Path:
    """Return the standard sharded storage path for a verification image.

    Args:
        player_id: Tower player ID (used for sharding)
        stem: Timestamp or unique identifier
        extension: File extension (should include leading dot)

    Returns:
        Path object: {UPLOAD_DIR}/{id[:2]}/{id}/{stem}{extension}
    """
    shard_dir = UPLOAD_DIR / player_id[:2] / player_id
    return shard_dir / f"{stem}{extension}"


def save_verification_image(player_id: str, stem: str, image_bytes: bytes, extension: str = ".png") -> Path:
    """Save a verification image to the standard sharded location.

    Args:
        player_id: Tower player ID
        stem: Timestamp or unique identifier
        image_bytes: Image file content
        extension: File extension (should include leading dot)

    Returns:
        Path where the image was saved
    """
    image_path = get_image_storage_path(player_id, stem, extension)
    image_path.parent.mkdir(parents=True, exist_ok=True)
    image_path.write_bytes(image_bytes)
    return image_path


# ---------------------------------------------------------------------------
# OCR Status File Management
# ---------------------------------------------------------------------------


def get_status_path(player_id: str, stem: str) -> Path:
    """Return the path to the OCR status JSON file for a submission."""
    return UPLOAD_DIR / player_id[:2] / player_id / f"{stem}.ocr.json"


def write_status(player_id: str, stem: str, data: dict) -> None:
    """Write the initial status JSON, always recording an ocr_initial event."""
    ts = int(time.time())
    event = {"type": "ocr_initial", "ts": ts, **{k: v for k, v in data.items() if k != "events"}}
    full_data = {**data, "events": [event]}
    path = get_status_path(player_id, stem)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(full_data))


def read_status(player_id: str, stem: str) -> dict:
    """Read the OCR status JSON file for a submission, or return default if not found."""
    try:
        return json.loads(get_status_path(player_id, stem).read_text())
    except Exception:
        return {"status": "pending"}


def append_ocr_event(player_id: str, stem: str, event: dict) -> None:
    """Append an event dict to the events list in the JSON file (creates the file if missing)."""
    path = get_status_path(player_id, stem)
    try:
        data = json.loads(path.read_text())
    except Exception:
        data = {"status": "pending"}
    events = list(data.get("events") or [])
    events.append(event)
    data["events"] = events
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def merge_status(player_id: str, stem: str, updates: dict) -> None:
    """Merge top-level fields into the JSON without touching the events list."""
    path = get_status_path(player_id, stem)
    try:
        data = json.loads(path.read_text())
    except Exception:
        data = {"status": "pending", "events": []}
    data.update(updates)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def move_verification_files(from_player_id: str, to_player_id: str, stem: str) -> None:
    """Move both the image and .ocr.json from one player_id directory to another."""
    if from_player_id == to_player_id:
        return
    dst_dir = UPLOAD_DIR / to_player_id[:2] / to_player_id
    dst_dir.mkdir(parents=True, exist_ok=True)
    for ext in ALLOWED_EXTENSIONS:
        src = UPLOAD_DIR / from_player_id[:2] / from_player_id / f"{stem}{ext}"
        if src.exists():
            src.rename(dst_dir / f"{stem}{ext}")
            break
    json_src = get_status_path(from_player_id, stem)
    if json_src.exists():
        json_src.rename(dst_dir / f"{stem}.ocr.json")


# ---------------------------------------------------------------------------
# Review Queue Database Operations
# ---------------------------------------------------------------------------


def ensure_review_db() -> None:
    """Create/migrate DB tables (idempotent)."""
    REVIEW_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(REVIEW_DB_PATH)) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS verification_reviews (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                player_id       TEXT    NOT NULL,
                stem            TEXT    NOT NULL,
                review_reason   TEXT    NOT NULL,
                platform        TEXT    NOT NULL,
                account_id      TEXT    NOT NULL,
                created_at      INTEGER NOT NULL,
                resolved        INTEGER NOT NULL DEFAULT 0,
                resolved_by     TEXT,
                notes           TEXT,
                typed_id        TEXT    NOT NULL DEFAULT '',
                ocr_id          TEXT    NOT NULL DEFAULT '',
                verdict         TEXT,
                submitter_name  TEXT    NOT NULL DEFAULT ''
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_vr_unresolved ON verification_reviews (resolved, created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_vr_player ON verification_reviews (player_id)")
        # Migrate existing DBs: add columns if they don't exist yet
        for col, definition in [
            ("typed_id", "TEXT NOT NULL DEFAULT ''"),
            ("ocr_id", "TEXT NOT NULL DEFAULT ''"),
            ("verdict", "TEXT"),
            ("submitter_name", "TEXT NOT NULL DEFAULT ''"),
        ]:
            try:
                conn.execute(f"ALTER TABLE verification_reviews ADD COLUMN {col} {definition}")
            except sqlite3.OperationalError:
                pass  # Column already exists

        conn.execute("""
            CREATE TABLE IF NOT EXISTS mod_notifications (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                platform    TEXT    NOT NULL,
                account_id  TEXT    NOT NULL,
                player_id   TEXT    NOT NULL,
                stem        TEXT    NOT NULL,
                verdict     TEXT    NOT NULL,
                message     TEXT    NOT NULL,
                created_at  INTEGER NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_mn_account ON mod_notifications (platform, account_id)")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS submission_log (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                platform   TEXT    NOT NULL,
                account_id TEXT    NOT NULL,
                player_id  TEXT    NOT NULL,
                stem       TEXT    NOT NULL,
                created_at INTEGER NOT NULL,
                UNIQUE (player_id, stem)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sl_account ON submission_log (platform, account_id)")
        for col, definition in [
            ("submitter_name", "TEXT NOT NULL DEFAULT ''"),
            ("ocr_player_id", "TEXT NOT NULL DEFAULT ''"),
            ("resolved_player_id", "TEXT NOT NULL DEFAULT ''"),
        ]:
            try:
                conn.execute(f"ALTER TABLE submission_log ADD COLUMN {col} {definition}")
            except sqlite3.OperationalError:
                pass  # Column already exists


def record_review(
    player_id: str, stem: str, review_reason: str, platform: str, account_id: str, typed_id: str = "", ocr_id: str = "", submitter_name: str = ""
) -> None:
    """Append a row to the review queue. Safe to call from a thread pool worker."""
    ensure_review_db()
    with sqlite3.connect(str(REVIEW_DB_PATH)) as conn:
        conn.execute(
            "INSERT INTO verification_reviews (player_id, stem, review_reason, platform, account_id, created_at, typed_id, ocr_id, submitter_name)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (player_id, stem, review_reason, platform, account_id, int(time.time()), typed_id, ocr_id, submitter_name),
        )


def get_review_queue_counts() -> dict[str, Any]:
    """Return unresolved review queue counts."""
    if not REVIEW_DB_PATH.exists():
        return {"total": 0, "by_reason": {}}
    with sqlite3.connect(str(REVIEW_DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT review_reason, COUNT(*) AS n FROM verification_reviews WHERE resolved = 0 GROUP BY review_reason").fetchall()
    by_reason = {row["review_reason"]: row["n"] for row in rows}
    return {"total": sum(by_reason.values()), "by_reason": by_reason}


def get_pending_near_match_submissions(platform: str, account_id: str) -> list[dict[str, Any]]:
    """Return unresolved near-match submissions for a user (cross-platform).

    Allows web users to see and resolve near-match OCR results from Discord bot.
    Returns list of dicts with: player_id, stem, typed_id, ocr_id, review_reason, created_at.
    """
    if not REVIEW_DB_PATH.exists():
        return []
    with sqlite3.connect(str(REVIEW_DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """SELECT player_id, stem, typed_id, ocr_id, review_reason, created_at
               FROM verification_reviews
               WHERE platform = ? AND account_id = ? AND resolved = 0
                 AND review_reason = 'near_match'
               ORDER BY created_at ASC""",
            (platform, account_id),
        ).fetchall()
    return [dict(row) for row in rows]


def resolve_review_entry(player_id: str, stem: str, verdict: str = "user_resolved", notes: str = "") -> None:
    """Mark a review queue entry as resolved (called when user confirms/corrects on web).

    Args:
        player_id: The player ID (typed or OCR-corrected)
        stem: The submission timestamp
        verdict: Resolution type (e.g., "user_resolved", "user_confirmed", "user_corrected")
        notes: Optional notes about the resolution
    """
    if not REVIEW_DB_PATH.exists():
        return
    with sqlite3.connect(str(REVIEW_DB_PATH)) as conn:
        conn.execute(
            """UPDATE verification_reviews
               SET resolved = 1, verdict = ?, notes = ?
               WHERE player_id = ? AND stem = ? AND resolved = 0""",
            (verdict, notes, player_id, stem),
        )


def queue_notification(platform: str, account_id: str, player_id: str, stem: str, verdict: str, message: str) -> None:
    """Insert a pending mod notification for the submitting user."""
    ensure_review_db()
    with sqlite3.connect(str(REVIEW_DB_PATH)) as conn:
        conn.execute(
            "INSERT INTO mod_notifications (platform, account_id, player_id, stem, verdict, message, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (platform, account_id, player_id, stem, verdict, message, int(time.time())),
        )


# ---------------------------------------------------------------------------
# Submission Log Operations
# ---------------------------------------------------------------------------


def record_submission_log(player_id: str, stem: str, platform: str, account_id: str, submitter_name: str = "") -> None:
    """Record a submission in the permanent log. Idempotent (UNIQUE on player_id+stem)."""
    ensure_review_db()
    with sqlite3.connect(str(REVIEW_DB_PATH)) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO submission_log (platform, account_id, player_id, stem, created_at, submitter_name)" " VALUES (?, ?, ?, ?, ?, ?)",
            (platform, account_id, player_id, stem, int(time.time()), submitter_name),
        )


def update_submission_log_ocr_id(player_id: str, stem: str, ocr_player_id: str) -> None:
    """Record what OCR read for a submission (may differ from submitted_player_id in near-match cases)."""
    if not REVIEW_DB_PATH.exists():
        return
    with sqlite3.connect(str(REVIEW_DB_PATH)) as conn:
        conn.execute(
            "UPDATE submission_log SET ocr_player_id = ? WHERE UPPER(player_id) = UPPER(?) AND stem = ?",
            (ocr_player_id, player_id, stem),
        )


def update_submission_log_player_id(old_player_id: str, stem: str, new_player_id: str) -> None:
    """Update resolved_player_id in submission_log after files are moved to a new player_id dir."""
    if not REVIEW_DB_PATH.exists():
        return
    with sqlite3.connect(str(REVIEW_DB_PATH)) as conn:
        conn.execute(
            "UPDATE submission_log SET resolved_player_id = ? WHERE UPPER(player_id) = UPPER(?) AND stem = ?",
            (new_player_id, old_player_id, stem),
        )


# ---------------------------------------------------------------------------
# Helper Functions
# ---------------------------------------------------------------------------


def player_has_existing_ids(platform: str, account_id: str, new_player_id: str = "") -> bool:
    """Return True if this account already owns Tower IDs AND new_player_id is not already one of them.

    Returns False when:
    - The player has no existing Tower IDs (first-time verification), or
    - new_player_id is already owned by this player (re-verification is idempotent, not a new instance).
    Returns True only when the player has IDs and the submitted one is genuinely new to them.
    """
    from thetower.backend.sus.models import LinkedAccount, PlayerId

    link = LinkedAccount.objects.filter(platform=platform, account_id=account_id, active=True).select_related("player").first()
    if not link:
        return False
    all_ids = PlayerId.objects.filter(game_instance__player=link.player)
    if not all_ids.exists():
        return False
    # Re-submitting an ID already on this account is idempotent, not a new instance.
    if new_player_id and all_ids.filter(id__iexact=new_player_id).exists():
        return False
    return True


# ---------------------------------------------------------------------------
# Main Verification Workflow
# ---------------------------------------------------------------------------


def process_verification(
    image_path: Path,
    player_id: str,
    stem: str,
    platform: str,
    account_id: str,
    display_name: str,
) -> dict[str, Any]:
    """Run OCR on a verification image and create the player record if it passes.

    This is the main shared verification workflow used by both bot and web.

    Args:
        image_path: Path to the saved verification image
        player_id: Tower ID that the user typed/submitted
        stem: Timestamp or unique identifier for this submission
        platform: "discord", "reddit", etc.
        account_id: Platform-specific account ID
        display_name: User's display name for player creation

    Returns:
        dict with status information:
        - {"status": "passed"} - Verification successful, player record created
        - {"status": "failed", "reason": ...} - Verification failed
        - {"status": "near_match", "ocr_id": ...} - OCR nearly matched, needs user confirmation
        - {"status": "new_instance_pending"} - User has existing IDs, needs to confirm new instance
    """
    from thetower.backend.sus.services import create_or_update_player

    # Permanently record this submission so history survives Django cleanup
    record_submission_log(player_id, stem, platform, account_id, display_name)

    try:
        # If OCR is not available, skip to player creation
        if not OCR_ENABLED:
            if player_has_existing_ids(platform, account_id, player_id):
                write_status(player_id, stem, {"status": "new_instance_pending"})
                return {"status": "new_instance_pending"}

            result = create_or_update_player(platform, account_id, display_name, player_id)
            if "error" in result:
                write_status(player_id, stem, {"status": "failed", "reason": result["error"]})
                return {"status": "failed", "reason": result["error"]}
            else:
                write_status(player_id, stem, {"status": "passed", "ocr_skipped": True})
                return {"status": "passed", "ocr_skipped": True}

        # Run OCR analysis
        ocr = analyze_verification_screenshot(str(image_path))
        if ocr.player_id:
            update_submission_log_ocr_id(player_id, stem, ocr.player_id)

        logger.info(
            "OCR result for %s: player_id=%s version=%s labels=%s error=%s",
            player_id,
            ocr.player_id,
            ocr.version,
            ocr.has_valid_labels,
            ocr.error,
        )

        # Handle OCR errors
        if ocr.error:
            logger.warning("OCR error for %s: %s", player_id, ocr.error)
            if player_has_existing_ids(platform, account_id, player_id):
                write_status(player_id, stem, {"status": "new_instance_pending"})
                return {"status": "new_instance_pending"}

            result = create_or_update_player(platform, account_id, display_name, player_id)
            if "error" in result:
                write_status(player_id, stem, {"status": "failed", "reason": result["error"]})
                return {"status": "failed", "reason": result["error"]}
            else:
                write_status(player_id, stem, {"status": "passed", "ocr_error": ocr.error})
                return {"status": "passed", "ocr_error": ocr.error}

        # Check if OCR found valid labels
        if not ocr.has_valid_labels:
            write_status(player_id, stem, {"status": "failed", "reason": "wrong_screen"})
            return {"status": "failed", "reason": "wrong_screen"}

        # Check if OCR successfully extracted a player ID
        if not ocr.player_id:
            write_status(player_id, stem, {"status": "failed", "reason": "ocr_no_id"})
            return {"status": "failed", "reason": "ocr_no_id"}

        # Compare OCR result to submitted ID
        if ocr.player_id != player_id:
            if OCR_NEAR_MATCH_MAX > 0 and len(ocr.player_id) == len(player_id):
                diff = sum(a != b for a, b in zip(ocr.player_id, player_id))
                if 0 < diff <= OCR_NEAR_MATCH_MAX:
                    # Near-match: pause and ask user to confirm before creating the record.
                    write_status(player_id, stem, {"status": "near_match", "ocr_id": ocr.player_id, "diff": diff})
                    # Record to review queue so web interface can show pending submissions
                    record_review(player_id, stem, "near_match", platform, account_id, player_id, ocr.player_id, display_name)
                    return {"status": "near_match", "ocr_id": ocr.player_id, "diff": diff}

            write_status(player_id, stem, {"status": "failed", "reason": "id_mismatch", "ocr_id": ocr.player_id})
            return {"status": "failed", "reason": "id_mismatch", "ocr_id": ocr.player_id}

        # Check if user has existing IDs
        if player_has_existing_ids(platform, account_id, player_id):
            write_status(player_id, stem, {"status": "new_instance_pending"})
            return {"status": "new_instance_pending"}

        # All checks passed - create player record
        result = create_or_update_player(platform, account_id, display_name, player_id)
        if "error" in result:
            write_status(player_id, stem, {"status": "failed", "reason": result["error"]})
            return {"status": "failed", "reason": result["error"]}
        else:
            logger.info("Player verified: %s %s tower_id=%s new=%s", platform, account_id, player_id, result.get("created"))
            write_status(player_id, stem, {"status": "passed"})
            return {"status": "passed", "player_created": result.get("created", False)}

    except Exception as exc:
        logger.exception("Verification processing failed for %s %s tower_id=%s", platform, account_id, player_id)
        write_status(player_id, stem, {"status": "failed", "reason": "internal_error"})
        return {"status": "failed", "reason": "internal_error", "error": str(exc)}
