"""Shared verification infrastructure for image storage, OCR processing, and review queue.

This module contains all the shared verification logic used by both thetower-bot and thetower-web:
- Image storage (time-sharded, path from stem alone)
- submissions table DB operations (single source of truth)
- Main verification workflow

Both bot and web use these as backend services, implementing only presentation-layer logic.
"""

import json
import logging
import os
import re
import secrets
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
# Stem generation and validation
# ---------------------------------------------------------------------------

_STEM_RE = re.compile(r"^\d+_[0-9a-f]{8}$")


def generate_stem() -> str:
    return f"{int(time.time())}_{secrets.token_hex(4)}"


def is_valid_stem(stem: str) -> bool:
    return bool(_STEM_RE.match(stem))


# ---------------------------------------------------------------------------
# Image storage utilities
# ---------------------------------------------------------------------------


def get_image_shard(stem: str) -> str:
    """Return the shard directory name for a stem (first 4 digits of timestamp)."""
    return stem[:4]


def get_image_storage_path(stem: str, extension: str = ".png") -> Path:
    return UPLOAD_DIR / get_image_shard(stem) / f"{stem}{extension}"


def save_verification_image(stem: str, image_bytes: bytes, extension: str = ".png") -> Path:
    path = get_image_storage_path(stem, extension)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(image_bytes)
    return path


def find_verification_image(stem: str) -> Path | None:
    """Locate image file for a stem across all allowed extensions."""
    for ext in ALLOWED_EXTENSIONS:
        path = get_image_storage_path(stem, ext)
        if path.exists():
            return path
    return None


# ---------------------------------------------------------------------------
# Database — single submissions table
# ---------------------------------------------------------------------------


def ensure_db() -> None:
    """Create submissions table (idempotent). Drops old tables on first run. Migrates schema as needed."""
    REVIEW_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(REVIEW_DB_PATH)) as conn:
        # Check if submissions table already exists
        table_exists = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='submissions'").fetchone() is not None

        if table_exists:
            logger.debug("Submissions table already exists, checking for schema updates")
        else:
            logger.info("Creating submissions table (first run)")

        # Drop legacy tables (clean cutover, no migration)
        for old_table in ("submission_log", "verification_reviews", "mod_notifications"):
            dropped = conn.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{old_table}'").fetchone() is not None
            if dropped:
                conn.execute(f"DROP TABLE {old_table}")
                logger.info("Dropped legacy table: %s", old_table)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS submissions (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                stem                TEXT    NOT NULL UNIQUE,

                platform            TEXT    NOT NULL,
                account_id          TEXT    NOT NULL,
                platform_ids        TEXT    NOT NULL DEFAULT '{}',
                submitter_name      TEXT    NOT NULL DEFAULT '',
                submission_source   TEXT    NOT NULL DEFAULT 'web',

                submitted_player_id TEXT    NOT NULL,
                ocr_player_id       TEXT    NOT NULL DEFAULT '',
                final_player_id     TEXT    NOT NULL DEFAULT '',

                status              TEXT    NOT NULL DEFAULT 'pending',

                id_change_reason    TEXT,
                old_player_id       TEXT,

                review_reason       TEXT,
                mod_verdict         TEXT,
                mod_resolved_by     TEXT,
                mod_notes           TEXT,

                discord_message_id  TEXT,

                events              TEXT    NOT NULL DEFAULT '[]',

                created_at          INTEGER NOT NULL,
                updated_at          INTEGER NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sub_account_status ON submissions (platform, account_id, status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sub_status_created ON submissions (status, created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sub_player ON submissions (submitted_player_id)")

        # Schema migrations: Add new columns if they don't exist
        cursor = conn.execute("PRAGMA table_info(submissions)")
        existing_columns = {row[1] for row in cursor.fetchall()}

        migrations = [
            ("verified_player_id", "ALTER TABLE submissions ADD COLUMN verified_player_id TEXT"),
            ("mod_review_stage", "ALTER TABLE submissions ADD COLUMN mod_review_stage INTEGER"),
            ("discord_log_message_id", "ALTER TABLE submissions ADD COLUMN discord_log_message_id TEXT"),
            ("discord_notification_message_id", "ALTER TABLE submissions ADD COLUMN discord_notification_message_id TEXT"),
            ("final_outcome", "ALTER TABLE submissions ADD COLUMN final_outcome TEXT"),
            ("version", "ALTER TABLE submissions ADD COLUMN version TEXT"),
            ("build", "ALTER TABLE submissions ADD COLUMN build TEXT"),
        ]

        for col_name, alter_sql in migrations:
            if col_name not in existing_columns:
                logger.info("Adding column %s to submissions table", col_name)
                conn.execute(alter_sql)


# Keep old name as alias so existing callers don't break during migration
ensure_review_db = ensure_db


# ---------------------------------------------------------------------------
# Submission CRUD
# ---------------------------------------------------------------------------


def create_submission(
    stem: str,
    platform: str,
    account_id: str,
    submitter_name: str,
    submission_source: str,
    submitted_player_id: str,
    additional_platform_ids: dict | None = None,
    old_player_id: str | None = None,
    id_change_reason: str | None = None,
    version: str | None = None,
    build: str | None = None,
) -> None:
    """Insert a new submission row. Builds platform_ids JSON automatically.

    Args:
        old_player_id: If this player_id was already verified for this user before submission,
                       pass the same value here. Used to prevent deleting verified IDs on rejection.
        id_change_reason: For Scenario B (users with existing instances), the intent they selected
                          from IdChangeReason enum. NULL for Scenario A (new users).
        version: Game version extracted from screenshot (e.g., "28.2.1").
        build: Game build extracted from screenshot (shown only to bot owner).
    """
    ensure_db()
    ids: dict = {} if platform == "web" else {platform: account_id}
    ids.update(additional_platform_ids or {})
    now = int(time.time())
    with sqlite3.connect(str(REVIEW_DB_PATH)) as conn:
        conn.execute(
            """INSERT OR IGNORE INTO submissions
               (stem, platform, account_id, platform_ids, submitter_name, submission_source,
                submitted_player_id, old_player_id, id_change_reason, version, build, status, events, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', '[]', ?, ?)""",
            (
                stem,
                platform,
                account_id,
                json.dumps(ids),
                submitter_name,
                submission_source,
                submitted_player_id,
                old_player_id,
                id_change_reason,
                version,
                build,
                now,
                now,
            ),
        )


def get_submission(stem: str) -> dict | None:
    """Return a single submission row as a dict, or None."""
    if not REVIEW_DB_PATH.exists():
        return None
    with sqlite3.connect(str(REVIEW_DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM submissions WHERE stem = ?", (stem,)).fetchone()
    return dict(row) if row else None


def get_submission_by_id(sub_id: int) -> dict | None:
    """Return a submission row by integer primary key."""
    if not REVIEW_DB_PATH.exists():
        return None
    with sqlite3.connect(str(REVIEW_DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM submissions WHERE id = ?", (sub_id,)).fetchone()
    return dict(row) if row else None


def update_submission(stem: str, **fields) -> None:
    """Update named columns on a submission row and set updated_at."""
    if not fields:
        return
    if not REVIEW_DB_PATH.exists():
        return
    now = int(time.time())
    set_clauses = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [now, stem]
    with sqlite3.connect(str(REVIEW_DB_PATH)) as conn:
        conn.execute(
            f"UPDATE submissions SET {set_clauses}, updated_at = ? WHERE stem = ?",
            values,
        )


def set_awaiting_mod_with_intent(stem: str, error_type: str, intent_reason: str, final_player_id: str | None = None) -> None:
    """After user selects intent, update submission to awaiting_mod with combined review_reason.

    Args:
        stem: The submission stem
        error_type: Either "ocr_error" or "id_mismatch"
        intent_reason: The IdChangeReason value (e.g., "GAME_CHANGED_ID", "FIXING_TYPO", etc.)
        final_player_id: The ID the user wants to use (optional, will use typed_id from submission if not provided)
    """
    # Combine error_type and intent into review_reason (e.g., "ocr_error_game_changed_id")
    review_reason = f"{error_type}_{intent_reason.lower()}"
    fields = {"status": "awaiting_mod", "review_reason": review_reason}
    if final_player_id:
        fields["final_player_id"] = final_player_id
    update_submission(stem, **fields)
    add_event(stem, {"type": "intent_selected", "ts": int(time.time()), "intent": intent_reason})


def add_event(stem: str, event: dict) -> None:
    """Append an event dict to the events JSON array atomically."""
    if not REVIEW_DB_PATH.exists():
        return
    with sqlite3.connect(str(REVIEW_DB_PATH)) as conn:
        row = conn.execute("SELECT events FROM submissions WHERE stem = ?", (stem,)).fetchone()
        if not row:
            return
        try:
            events = json.loads(row[0])
        except Exception:
            events = []
        events.append(event)
        conn.execute(
            "UPDATE submissions SET events = ?, updated_at = ? WHERE stem = ?",
            (json.dumps(events), int(time.time()), stem),
        )


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------

_TERMINAL_STATUSES = ("passed", "failed", "abandoned")


def get_pending_submission(platform: str, account_id: str) -> dict | None:
    """Return the most recent non-terminal submission for a platform/account pair.

    Queries platform_ids JSON exclusively — works for any platform.
    """
    if not REVIEW_DB_PATH.exists():
        return None
    with sqlite3.connect(str(REVIEW_DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """SELECT * FROM submissions
               WHERE status NOT IN ('passed', 'failed', 'abandoned')
               AND json_extract(platform_ids, '$.' || ?) = ?
               ORDER BY created_at DESC LIMIT 1""",
            (platform, account_id),
        ).fetchone()
    return dict(row) if row else None


def get_pending_submission_for_player_id(submitted_player_id: str) -> dict | None:
    """Return any non-terminal submission for a given Tower ID (no platform filter)."""
    if not REVIEW_DB_PATH.exists():
        return None
    with sqlite3.connect(str(REVIEW_DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """SELECT * FROM submissions
               WHERE submitted_player_id = ?
               AND status NOT IN ('passed', 'failed', 'abandoned')
               LIMIT 1""",
            (submitted_player_id,),
        ).fetchone()
    return dict(row) if row else None


def get_pending_near_matches(platform: str, account_id: str) -> list[dict]:
    """Return unresolved near-match submissions for a platform/account pair."""
    if not REVIEW_DB_PATH.exists():
        return []
    with sqlite3.connect(str(REVIEW_DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """SELECT * FROM submissions
               WHERE status = 'near_match'
               AND json_extract(platform_ids, '$.' || ?) = ?
               ORDER BY created_at ASC""",
            (platform, account_id),
        ).fetchall()
    return [dict(r) for r in rows]


def get_mod_queue(
    limit: int = 100, reason_filter: str | None = None, stem_filter: str | None = None, tower_id_filter: str | None = None
) -> list[dict]:
    """Return awaiting_mod and awaiting_mod_action submissions, oldest first."""
    if not REVIEW_DB_PATH.exists():
        return []
    clauses = ["(status = 'awaiting_mod' OR status = 'awaiting_mod_action')"]
    params: list = []
    if reason_filter:
        clauses.append("review_reason = ?")
        params.append(reason_filter)
    if stem_filter:
        clauses.append("stem = ?")
        params.append(stem_filter)
    if tower_id_filter:
        tid = tower_id_filter.upper()
        clauses.append("(UPPER(submitted_player_id) = ? OR UPPER(ocr_player_id) = ?)")
        params.extend([tid, tid])
    where = " AND ".join(clauses)
    params.append(limit)
    with sqlite3.connect(str(REVIEW_DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"SELECT * FROM submissions WHERE {where} ORDER BY created_at ASC LIMIT ?",
            params,
        ).fetchall()
    return [dict(r) for r in rows]


def get_mod_queue_counts() -> dict[str, Any]:
    """Return mod queue counts by reason."""
    if not REVIEW_DB_PATH.exists():
        return {"total": 0, "by_reason": {}}
    with sqlite3.connect(str(REVIEW_DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT review_reason, COUNT(*) AS n FROM submissions WHERE status = 'awaiting_mod' GROUP BY review_reason").fetchall()
    by_reason = {row["review_reason"]: row["n"] for row in rows}
    return {"total": sum(by_reason.values()), "by_reason": by_reason}


def get_mod_queue_for_player(player_pk: int) -> list[dict]:
    """Return awaiting_mod and awaiting_mod_action submissions for all accounts linked to a player."""
    from thetower.backend.sus.models import LinkedAccount

    pairs: list[tuple[str, str]] = list(LinkedAccount.objects.filter(player_id=player_pk).values_list("platform", "account_id"))
    if not pairs or not REVIEW_DB_PATH.exists():
        return []
    with sqlite3.connect(str(REVIEW_DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        results = []
        for platform, account_id in pairs:
            rows = conn.execute(
                """SELECT * FROM submissions
                   WHERE (status = 'awaiting_mod' OR status = 'awaiting_mod_action')
                   AND json_extract(platform_ids, '$.' || ?) = ?
                   ORDER BY created_at ASC""",
                (platform, account_id),
            ).fetchall()
            results.extend(dict(r) for r in rows)
    return results


def get_submissions_page(
    page: int = 1,
    limit: int = 25,
    player_id_filter: str | None = None,
    account_id_filter: str | None = None,
    name_filter: str | None = None,
    status_filter: str | None = None,
) -> tuple[list[dict], int]:
    """Return a paginated slice of submissions (newest-first) and total count."""
    if not REVIEW_DB_PATH.exists():
        return [], 0
    where_clauses: list[str] = []
    params: list = []
    if player_id_filter:
        where_clauses.append("UPPER(submitted_player_id) LIKE UPPER(?)")
        params.append(f"%{player_id_filter}%")
    if account_id_filter:
        where_clauses.append("account_id LIKE ?")
        params.append(f"%{account_id_filter}%")
    if name_filter:
        where_clauses.append("UPPER(submitter_name) LIKE UPPER(?)")
        params.append(f"%{name_filter}%")
    if status_filter:
        where_clauses.append("status = ?")
        params.append(status_filter)
    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
    offset = (page - 1) * limit
    with sqlite3.connect(str(REVIEW_DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        total = conn.execute(f"SELECT COUNT(*) FROM submissions {where_sql}", params).fetchone()[0]
        rows = conn.execute(
            f"SELECT * FROM submissions {where_sql} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()
    return [dict(r) for r in rows], total


# ---------------------------------------------------------------------------
# Mod resolution
# ---------------------------------------------------------------------------


def mod_resolve_submission(stem: str, verdict: str, resolved_by: str, final_player_id: str | None = None) -> None:
    """Resolve a mod review: update status, store verdict, and create player if needed.

    For new_instance_* submissions awaiting mod approval, this will call create_or_update_player()
    to actually create the Django GameInstance/PlayerId records.
    """
    from thetower.backend.sus.services import create_or_update_player

    # Get the submission row to check review_reason and other details
    row = get_submission(stem)
    if not row:
        logger.warning("mod_resolve_submission called on non-existent stem %s", stem)
        return

    if verdict == "approved":
        new_status = "passed"
    elif verdict == "fix_id":
        new_status = "passed"
    else:  # rejected_fake
        new_status = "failed"

    fields: dict = {
        "mod_verdict": verdict,
        "mod_resolved_by": resolved_by,
        "status": new_status,
    }
    if final_player_id:
        fields["final_player_id"] = final_player_id

    update_submission(stem, **fields)
    add_event(
        stem,
        {
            "type": "mod_action",
            "ts": int(time.time()),
            "verdict": verdict,
            "mod": resolved_by,
            **({"final_id": final_player_id} if final_player_id else {}),
        },
    )

    # For approved/fix_id verdicts on new_instance_* reviews, create the player now
    if verdict in ("approved", "fix_id") and row.get("review_reason", "").startswith("new_instance_"):
        player_id_to_create = final_player_id or row.get("final_player_id") or row.get("submitted_player_id")
        if player_id_to_create:
            platform = row.get("platform")
            account_id = row.get("account_id")
            submitter_name = row.get("submitter_name", "")
            if platform and account_id:
                try:
                    result = create_or_update_player(platform, account_id, submitter_name, player_id_to_create, update_role_source=False)
                    if "error" in result:
                        logger.error("Failed to create player after mod approval for stem %s: %s", stem, result.get("error"))
                        # Revert status to failed
                        update_submission(stem, status="failed")
                        add_event(stem, {"type": "failed", "ts": int(time.time()), "reason": result["error"]})
                    else:
                        logger.info("Player created after mod approval: %s %s tower_id=%s stem=%s", platform, account_id, player_id_to_create, stem)
                except Exception:
                    logger.exception("Exception creating player after mod approval for stem %s", stem)
                    update_submission(stem, status="failed")
                    add_event(stem, {"type": "failed", "ts": int(time.time()), "reason": "player_creation_failed"})


def mod_resolve_stage1(stem: str, verdict: str, resolved_by: str, verified_player_id: str | None = None) -> None:
    """Resolve Stage 1 of two-stage mod review (Scenario B): Verify OCR result.

    Args:
        stem: Submission identifier
        verdict: "approved" (OCR correct), "fix_id" (mod entered correct ID), or "rejected_fake" (invalid screenshot)
        resolved_by: Mod identifier (platform:account_id)
        verified_player_id: The ID verified from screenshot (required for approved/fix_id)
    """
    row = get_submission(stem)
    if not row:
        logger.warning("mod_resolve_stage1 called on non-existent stem %s", stem)
        return

    if verdict == "rejected_fake":
        # Stage 1 rejection: Screenshot is fake/invalid, stop here
        update_submission(
            stem,
            status="failed",
            mod_verdict=verdict,
            mod_resolved_by=resolved_by,
            final_outcome="rejected_fake",
        )
        add_event(stem, {"type": "mod_stage1_reject", "ts": int(time.time()), "mod": resolved_by})
        logger.info("Stage 1 rejected as fake: stem=%s mod=%s", stem, resolved_by)
        return

    if not verified_player_id:
        logger.error("mod_resolve_stage1 called without verified_player_id for verdict %s stem %s", verdict, stem)
        return

    # Store verified_player_id and transition to Stage 2
    update_submission(
        stem,
        status="awaiting_mod_action",
        mod_review_stage=2,
        verified_player_id=verified_player_id,
    )
    add_event(
        stem,
        {
            "type": "mod_stage1_complete",
            "ts": int(time.time()),
            "mod": resolved_by,
            "verdict": verdict,
            "verified_id": verified_player_id,
        },
    )
    logger.info("Stage 1 complete: stem=%s verified_id=%s mod=%s → Stage 2", stem, verified_player_id, resolved_by)


def mod_resolve_stage2(stem: str, action_type: str, resolved_by: str) -> None:
    """Resolve Stage 2 of two-stage mod review (Scenario B): Apply action type to verified ID.

    Args:
        stem: Submission identifier
        action_type: "replace", "merge", "add_alt", or "reject"
        resolved_by: Mod identifier (platform:account_id)
    """
    from thetower.backend.sus.services import create_or_update_player

    row = get_submission(stem)
    if not row:
        logger.warning("mod_resolve_stage2 called on non-existent stem %s", stem)
        return

    verified_player_id = row.get("verified_player_id")
    if not verified_player_id and action_type != "reject":
        logger.error("mod_resolve_stage2: No verified_player_id for stem %s action %s", stem, action_type)
        return

    if action_type == "reject":
        # Stage 2 rejection: Don't apply changes, mark as failed
        update_submission(
            stem,
            status="failed",
            mod_verdict="rejected_no_action",
            mod_resolved_by=resolved_by,
            final_outcome="rejected_no_action",
        )
        add_event(stem, {"type": "mod_stage2_reject", "ts": int(time.time()), "mod": resolved_by})
        logger.info("Stage 2 rejected (no action): stem=%s mod=%s", stem, resolved_by)
        return

    # Apply the action type
    platform = row.get("platform")
    account_id = row.get("account_id")
    submitter_name = row.get("submitter_name", "")
    old_player_id = row.get("old_player_id")

    if not platform or not account_id:
        logger.error("mod_resolve_stage2: Missing platform/account for stem %s", stem)
        return

    try:
        result = create_or_update_player(
            platform,
            account_id,
            submitter_name,
            verified_player_id,
            update_role_source=False,
            action_type=action_type,
            old_player_id=old_player_id,
        )

        if "error" in result:
            logger.error("Failed to apply action %s for stem %s: %s", action_type, stem, result.get("error"))
            update_submission(stem, status="failed")
            add_event(stem, {"type": "failed", "ts": int(time.time()), "reason": result["error"]})
        else:
            # Map action_type to final_outcome
            outcome_map = {
                "replace": "instance_updated_replace",
                "merge": "instance_updated_merge",
                "add_alt": "instance_updated_add_alt",
            }
            final_outcome = outcome_map.get(action_type, "instance_updated_replace")

            update_submission(
                stem,
                status="passed",
                mod_verdict=action_type,
                mod_resolved_by=resolved_by,
                final_outcome=final_outcome,
            )
            add_event(
                stem,
                {
                    "type": "mod_stage2_complete",
                    "ts": int(time.time()),
                    "mod": resolved_by,
                    "action": action_type,
                    "verified_id": verified_player_id,
                },
            )
            logger.info("Stage 2 complete: stem=%s action=%s verified_id=%s mod=%s", stem, action_type, verified_player_id, resolved_by)
    except Exception:
        logger.exception("Exception applying action %s for stem %s", action_type, stem)
        update_submission(stem, status="failed")
        add_event(stem, {"type": "failed", "ts": int(time.time()), "reason": "player_action_failed"})


# ---------------------------------------------------------------------------
# Review queue (legacy name forwarded to mod queue)
# ---------------------------------------------------------------------------


def record_review(
    player_id: str,
    stem: str,
    review_reason: str,
    platform: str,
    account_id: str,
    typed_id: str = "",
    ocr_id: str = "",
    submitter_name: str = "",
) -> None:
    """Move a submission into awaiting_mod state with the given reason."""
    update_submission(
        stem,
        status="awaiting_mod",
        review_reason=review_reason,
        ocr_player_id=ocr_id or "",
    )


# ---------------------------------------------------------------------------
# Stale escalation
# ---------------------------------------------------------------------------


def auto_escalate_stale_pending_submissions(stale_threshold_seconds: int = 86400) -> int:
    """Escalate submissions stuck in pending/new_instance_pending for >threshold seconds.

    Returns number escalated.
    """
    if not REVIEW_DB_PATH.exists():
        return 0
    cutoff_ts = int(time.time()) - stale_threshold_seconds
    now = int(time.time())

    with sqlite3.connect(str(REVIEW_DB_PATH)) as conn:
        # Find rows that need escalation
        rows = conn.execute(
            """SELECT stem, status FROM submissions
               WHERE status IN ('pending', 'new_instance_pending')
               AND created_at < ?""",
            (cutoff_ts,),
        ).fetchall()

        if not rows:
            return 0

        count = 0
        for stem, status in rows:
            review_reason = "new_instance_pending_stale" if status == "new_instance_pending" else "pending_stale"
            conn.execute(
                """UPDATE submissions
                   SET status = 'awaiting_mod', review_reason = ?, updated_at = ?
                   WHERE stem = ? AND status NOT IN ('awaiting_mod', 'passed', 'failed', 'abandoned')""",
                (review_reason, now, stem),
            )
            count += 1

        # Append auto_escalated events outside the update for atomicity per row
        for stem, status in rows:
            try:
                row = conn.execute("SELECT events FROM submissions WHERE stem = ?", (stem,)).fetchone()
                if row:
                    events = json.loads(row[0] or "[]")
                    events.append({"type": "auto_escalated", "ts": now})
                    conn.execute("UPDATE submissions SET events = ? WHERE stem = ?", (json.dumps(events), stem))
            except Exception:
                pass

    return count


# ---------------------------------------------------------------------------
# Helper Functions
# ---------------------------------------------------------------------------


def player_has_existing_ids(platform: str, account_id: str, new_player_id: str = "") -> bool:
    """Return True if this account already owns Tower IDs AND new_player_id is not already one of them."""
    from thetower.backend.sus.models import LinkedAccount, PlayerId

    link = LinkedAccount.objects.filter(platform=platform, account_id=account_id, active=True).select_related("player").first()
    if not link:
        return False
    all_ids = PlayerId.objects.filter(game_instance__player=link.player)
    if not all_ids.exists():
        return False
    if new_player_id and all_ids.filter(id__iexact=new_player_id).exists():
        return False
    return True


def player_already_has_id(platform: str, account_id: str, player_id: str) -> bool:
    """Return True if this account already has this specific player_id verified."""
    from thetower.backend.sus.models import LinkedAccount, PlayerId

    link = LinkedAccount.objects.filter(platform=platform, account_id=account_id, active=True).select_related("player").first()
    if not link:
        return False
    return PlayerId.objects.filter(game_instance__player=link.player, id__iexact=player_id).exists()


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
    submission_source: str = "web",
    additional_platform_ids: dict | None = None,
    id_change_reason: str | None = None,
) -> dict[str, Any]:
    """Run OCR on a verification image and create the player record if it passes.

    Args:
        id_change_reason: For Scenario B (users with existing instances), the intent they selected.
                          NULL for Scenario A (new users).

    Returns:
        dict with keys: status, and optionally reason, ocr_id, diff, ocr_skipped, etc.
    """
    from thetower.backend.sus.services import create_or_update_player

    # Check if user already has this ID verified (for re-verification attempts)
    old_player_id = player_id if player_already_has_id(platform, account_id, player_id) else None

    # Create the DB row (idempotent via INSERT OR IGNORE on stem UNIQUE)
    create_submission(
        stem, platform, account_id, display_name, submission_source, player_id, additional_platform_ids, old_player_id, id_change_reason
    )

    try:
        # Determine if this is Scenario A (no intent) or Scenario B (has intent)
        row = get_submission(stem)
        is_scenario_b = row and row.get("id_change_reason") is not None
        # Self-service intents: NEW_GAME_INSTANCE and REFRESH_VERIFICATION
        is_self_service_intent = row and row.get("id_change_reason") in ("new_game_instance", "refresh_verification")

        if not OCR_ENABLED:
            if is_scenario_b and not is_self_service_intent:
                # Scenario B (requires mod review): Always needs two-stage mod review
                update_submission(stem, status="awaiting_mod", review_reason="ocr_disabled", mod_review_stage=1)
                return {"status": "awaiting_mod", "review_reason": "ocr_disabled"}
            elif player_has_existing_ids(platform, account_id, player_id):
                # Should not happen (intent should be set), but handle gracefully
                update_submission(stem, status="new_instance_pending")
                return {"status": "new_instance_pending"}

            result = create_or_update_player(platform, account_id, display_name, player_id)
            if "error" in result:
                update_submission(stem, status="failed")
                add_event(stem, {"type": "failed", "ts": int(time.time()), "reason": result["error"]})
                return {"status": "failed", "reason": result["error"]}
            else:
                update_submission(stem, status="passed", final_player_id=player_id, verified_player_id=player_id)
                add_event(stem, {"type": "passed", "ts": int(time.time()), "ocr_skipped": True})
                return {"status": "passed", "ocr_skipped": True}

        ocr = analyze_verification_screenshot(str(image_path))
        if ocr.player_id:
            update_fields = {"ocr_player_id": ocr.player_id}
            if ocr.version:
                update_fields["version"] = ocr.version
            if ocr.build:
                update_fields["build"] = ocr.build
            update_submission(stem, **update_fields)

        logger.info(
            "OCR result for %s: player_id=%s version=%s labels=%s error=%s",
            player_id,
            ocr.player_id,
            ocr.version,
            ocr.has_valid_labels,
            ocr.error,
        )

        if ocr.error:
            logger.warning("OCR error for %s: %s", player_id, ocr.error)
            # OCR failed — must go to mod review
            if is_scenario_b and not is_self_service_intent:
                # Scenario B (non-NEW_GAME_INSTANCE): Two-stage mod review
                reason_suffix = f"_{row['id_change_reason'].lower()}" if row else ""
                update_submission(stem, status="awaiting_mod", review_reason=f"ocr_error{reason_suffix}", mod_review_stage=1)
                add_event(stem, {"type": "awaiting_mod", "ts": int(time.time()), "reason": f"ocr_error{reason_suffix}"})
                return {"status": "awaiting_mod", "review_reason": f"ocr_error{reason_suffix}", "ocr_error": ocr.error}
            else:
                # Scenario A or NEW_GAME_INSTANCE: Single-stage mod review
                update_submission(stem, status="awaiting_mod", review_reason="ocr_error")
                add_event(stem, {"type": "awaiting_mod", "ts": int(time.time()), "reason": "ocr_error"})
                return {"status": "awaiting_mod", "review_reason": "ocr_error", "ocr_error": ocr.error}

        if not ocr.has_valid_labels:
            update_submission(stem, status="failed")
            add_event(stem, {"type": "failed", "ts": int(time.time()), "reason": "wrong_screen"})
            return {"status": "failed", "reason": "wrong_screen"}

        if not ocr.player_id:
            update_submission(stem, status="failed")
            add_event(stem, {"type": "failed", "ts": int(time.time()), "reason": "ocr_no_id"})
            return {"status": "failed", "reason": "ocr_no_id"}

        if ocr.player_id != player_id:
            if OCR_NEAR_MATCH_MAX > 0 and len(ocr.player_id) == len(player_id):
                diff = sum(a != b for a, b in zip(ocr.player_id, player_id))
                if 0 < diff <= OCR_NEAR_MATCH_MAX:
                    update_submission(stem, status="near_match", ocr_player_id=ocr.player_id)
                    add_event(stem, {"type": "near_match", "ts": int(time.time()), "diff": diff, "ocr_id": ocr.player_id})
                    return {"status": "near_match", "ocr_id": ocr.player_id, "diff": diff}

            # Large difference — must go to mod review
            # Always use two-stage review for id_mismatch to ensure OCR verification
            if is_scenario_b and not is_self_service_intent:
                # Scenario B (requires mod review): Two-stage mod review
                reason_suffix = f"_{row['id_change_reason'].lower()}" if row else ""
                update_submission(
                    stem, status="awaiting_mod", review_reason=f"id_mismatch{reason_suffix}", ocr_player_id=ocr.player_id, mod_review_stage=1
                )
                add_event(stem, {"type": "awaiting_mod", "ts": int(time.time()), "reason": f"id_mismatch{reason_suffix}", "ocr_id": ocr.player_id})
                return {"status": "awaiting_mod", "review_reason": f"id_mismatch{reason_suffix}", "typed_id": player_id, "ocr_id": ocr.player_id}
            else:
                # Scenario A or NEW_GAME_INSTANCE: Also use Stage 1 for consistent OCR verification
                # Stage 1 allows mods to approve OCR result, fix it, or reject
                update_submission(stem, status="awaiting_mod", review_reason="id_mismatch", ocr_player_id=ocr.player_id, mod_review_stage=1)
                add_event(stem, {"type": "awaiting_mod", "ts": int(time.time()), "reason": "id_mismatch", "ocr_id": ocr.player_id})
                return {"status": "awaiting_mod", "review_reason": "id_mismatch", "typed_id": player_id, "ocr_id": ocr.player_id}

        # OCR exact match: player_id == ocr.player_id
        if is_scenario_b and not is_self_service_intent:
            # Scenario B (requires mod review): Even with exact match, needs two-stage mod review
            reason_suffix = f"_{row['id_change_reason'].lower()}" if row else ""
            update_submission(stem, status="awaiting_mod", review_reason=f"exact_match{reason_suffix}", mod_review_stage=1)
            add_event(stem, {"type": "awaiting_mod", "ts": int(time.time()), "reason": f"exact_match{reason_suffix}"})
            return {"status": "awaiting_mod", "review_reason": f"exact_match{reason_suffix}"}
        elif player_has_existing_ids(platform, account_id, player_id):
            # Self-service intents (NEW_GAME_INSTANCE, REFRESH_VERIFICATION): Create immediately
            update_submission(stem, status="new_instance_pending")
            return {"status": "new_instance_pending"}

        # Scenario A: Create player immediately
        result = create_or_update_player(platform, account_id, display_name, player_id)
        if "error" in result:
            update_submission(stem, status="failed")
            add_event(stem, {"type": "failed", "ts": int(time.time()), "reason": result["error"]})
            return {"status": "failed", "reason": result["error"]}
        else:
            logger.info("Player verified: %s %s tower_id=%s new=%s", platform, account_id, player_id, result.get("created"))
            update_submission(stem, status="passed", final_player_id=player_id, verified_player_id=player_id)
            add_event(stem, {"type": "passed", "ts": int(time.time())})
            return {"status": "passed", "player_created": result.get("created", False)}

    except Exception as exc:
        logger.exception("Verification processing failed for %s %s tower_id=%s", platform, account_id, player_id)
        update_submission(stem, status="failed")
        add_event(stem, {"type": "failed", "ts": int(time.time()), "reason": "internal_error"})
        return {"status": "failed", "reason": "internal_error", "error": str(exc)}
