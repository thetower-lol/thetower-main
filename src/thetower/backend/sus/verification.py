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

# Terminal statuses (submissions that are complete and should not be re-actioned)
_TERMINAL_STATUSES = ("approved", "passed", "rejected", "abandoned", "failed")

# Try to import OCR utilities
try:
    from thetower.utils.ocr import analyze_verification_screenshot
    from thetower.utils.ocr import is_available as ocr_available

    OCR_ENABLED = ocr_available()
except ImportError:
    OCR_ENABLED = False
    analyze_verification_screenshot = None


# ---------------------------------------------------------------------------
# Pure Functions for OCR and Status Determination
# ---------------------------------------------------------------------------


def run_ocr(image_path: Path, player_id: str) -> dict[str, Any]:
    """Run OCR on verification image with error categorization (pure function - no DB access).

    Returns dict with OCR result fields:
        - player_id: str | None - Extracted player ID (validated format)
        - version: str | None - Game version
        - build: str | None - Game build
        - has_valid_labels: bool - True if correct screen detected
        - error: str | None - Error message if OCR failed
        - error_category: str | None - Error category for user-facing messages:
            - "ocr_disabled" - OCR not available
            - "ocr_technical" - OCR service error, image corrupt, etc.
            - "invalid_id_format" - OCR extracted text but not valid Tower ID
    """
    from thetower.backend.sus.services import is_valid_tower_id

    if not OCR_ENABLED:
        return {
            "player_id": None,
            "version": None,
            "build": None,
            "has_valid_labels": False,
            "error": "ocr_disabled",
            "error_category": "ocr_disabled",
        }

    try:
        ocr = analyze_verification_screenshot(str(image_path))

        # If OCR extracted a player_id, validate it's a valid Tower ID format
        if ocr.player_id and not is_valid_tower_id(ocr.player_id):
            logger.warning("OCR extracted invalid Tower ID format: %s", ocr.player_id)
            return {
                "player_id": None,  # Treat as failed extraction
                "version": ocr.version,
                "build": ocr.build,
                "has_valid_labels": ocr.has_valid_labels,
                "error": f"Invalid ID format: {ocr.player_id}",
                "error_category": "invalid_id_format",
            }

        # Categorize OCR errors
        error_category = None
        if ocr.error:
            # Analyze error message to categorize
            error_lower = ocr.error.lower()
            if "could not load image" in error_lower or "image" in error_lower:
                error_category = "ocr_technical"
            elif "tesseract" in error_lower or "ocr" in error_lower:
                error_category = "ocr_technical"
            else:
                error_category = "ocr_technical"  # Default to technical

        return {
            "player_id": ocr.player_id,
            "version": ocr.version,
            "build": ocr.build,
            "has_valid_labels": ocr.has_valid_labels,
            "error": ocr.error,
            "error_category": error_category,
        }
    except Exception as e:
        logger.exception("OCR failed for %s", image_path)
        return {
            "player_id": None,
            "version": None,
            "build": None,
            "has_valid_labels": False,
            "error": str(e),
            "error_category": "ocr_technical",
        }


def get_user_context(platform: str, account_id: str, submitted_player_id: str, id_change_reason: str | None) -> dict[str, Any]:
    """Determine user context for status determination (pure function - no DB updates).

    Returns dict with:
        - is_new_user: bool - True if user has no existing verified IDs
        - has_existing_ids: bool - True if user has other verified IDs (but not this one)
        - already_has_this_id: bool - True if user already verified this specific ID
        - intent: str | None - User's intent (id_change_reason)
        - is_self_service: bool - True if intent allows self-service (no mod review)
        - is_scenario_b: bool - True if existing user with intent (Scenario B from design doc)
    """
    from thetower.backend.sus.constants import IdChangeReason

    SELF_SERVICE_INTENTS = {IdChangeReason.NEW_GAME_INSTANCE.value, IdChangeReason.REFRESH_VERIFICATION.value}

    # Check if user has any existing IDs
    already_has_this_id = player_already_has_id(platform, account_id, submitted_player_id)
    has_existing_ids = player_has_existing_ids(platform, account_id, submitted_player_id)

    # New user: no existing IDs at all
    is_new_user = not has_existing_ids and not already_has_this_id

    # Self-service: intent allows auto-complete without mod review
    is_self_service = id_change_reason in SELF_SERVICE_INTENTS

    # Scenario B: existing user with intent set
    is_scenario_b = id_change_reason is not None

    return {
        "is_new_user": is_new_user,
        "has_existing_ids": has_existing_ids,
        "already_has_this_id": already_has_this_id,
        "intent": id_change_reason,
        "is_self_service": is_self_service,
        "is_scenario_b": is_scenario_b,
    }


def determine_initial_status(ocr_result: dict[str, Any], submitted_player_id: str, user_context: dict[str, Any]) -> dict[str, Any]:
    """Determine initial status based on OCR result and user context with specific error categories (implements Rules 1-3 from taxonomy).

    Returns dict with:
        - status: str - Initial status value
        - ocr_player_id: str | None - OCR-read player ID (for DB storage)
        - version: str | None - Game version (for DB storage)
        - build: str | None - Game build (for DB storage)
        - return_to_caller: dict[str, Any] - Response dict to return to web/Discord
    """
    ocr_player_id = ocr_result.get("player_id")
    ocr_error = ocr_result.get("error")
    error_category = ocr_result.get("error_category")
    has_valid_labels = ocr_result.get("has_valid_labels", False)

    # Extract user context
    is_self_service = user_context["is_self_service"]
    intent = user_context["intent"]

    # Rule 1: OCR Error → ocr_error status with specific category
    if ocr_error and error_category not in ("ocr_disabled",):
        # Determine user-facing reason based on error category
        if error_category == "invalid_id_format":
            reason = "invalid_id_format"
            user_message = "OCR extracted text but it doesn't match a valid Tower ID format"
        elif error_category == "ocr_technical":
            reason = "ocr_technical_error"
            user_message = "OCR service encountered an error processing the image"
        else:
            reason = "ocr_error"
            user_message = "OCR failed to process the image"

        return {
            "status": "ocr_error",
            "ocr_player_id": None,
            "version": ocr_result.get("version"),
            "build": ocr_result.get("build"),
            "return_to_caller": {
                "status": "ocr_error",
                "reason": reason,
                "ocr_error": ocr_error,  # Technical details for mods
                "user_message": user_message,  # User-friendly message
            },
        }

    # Rule 1b: OCR disabled → treat as exact match (trust user input)
    if error_category == "ocr_disabled":
        if is_self_service or user_context["is_new_user"]:
            # Self-service or new user: auto-complete (caller will handle)
            return {
                "status": "ocr_unavailable",  # Track that OCR was unavailable
                "ocr_player_id": None,
                "version": None,
                "build": None,
                "return_to_caller": {"status": "auto_complete", "reason": "ocr_unavailable"},
            }
        else:
            # Existing user with mod-review intent: skip OCR review, go to Intent review
            return {
                "status": "ocr_unavailable",  # Track that OCR was unavailable
                "ocr_player_id": None,
                "version": None,
                "build": None,
                "return_to_caller": {"status": "mod_intent_review", "reason": f"ocr_unavailable_{intent}"},
            }

    # Rule 2: Wrong screen → rejected (user uploaded wrong screen type)
    if not has_valid_labels:
        return {
            "status": "rejected",
            "ocr_player_id": None,
            "version": None,
            "build": None,
            "return_to_caller": {
                "status": "rejected",
                "reason": "wrong_screen",
                "user_message": "Please upload a screenshot of your Settings screen showing your Player ID",
            },
        }

    # Rule 2b: OCR extracted nothing → failed
    if not ocr_player_id:
        return {
            "status": "failed",
            "ocr_player_id": None,
            "version": ocr_result.get("version"),
            "build": ocr_result.get("build"),
            "return_to_caller": {
                "status": "failed",
                "reason": "ocr_no_id",
                "user_message": "Could not read Player ID from screenshot. Please ensure it's clearly visible.",
            },
        }

    # Rule 3: OCR exact match → auto-complete or Intent review
    if ocr_player_id == submitted_player_id:
        if is_self_service or user_context["is_new_user"]:
            # Self-service or new user: auto-complete immediately
            return {
                "status": "ocr_processing",  # Not used, caller will auto-complete
                "ocr_player_id": ocr_player_id,
                "version": ocr_result.get("version"),
                "build": ocr_result.get("build"),
                "return_to_caller": {"status": "auto_complete", "reason": "exact_match"},
            }
        else:
            # Existing user with mod-review intent: skip OCR review, go to Intent review
            return {
                "status": "mod_intent_review",  # Intent review: action selection
                "ocr_player_id": ocr_player_id,
                "version": ocr_result.get("version"),
                "build": ocr_result.get("build"),
                "return_to_caller": {"status": "mod_intent_review", "reason": f"exact_match_{intent}"},
            }

    # Rule 3b: Near-match → user_ocr_choice (user decides)
    # Covers both substitutions (same length) and insertions/deletions (length ±1 or ±2)
    if OCR_NEAR_MATCH_MAX > 0:
        len_diff = abs(len(ocr_player_id) - len(submitted_player_id))
        if len_diff == 0:
            # Same length: count substitutions
            diff = sum(a != b for a, b in zip(ocr_player_id, submitted_player_id))
        elif len_diff <= OCR_NEAR_MATCH_MAX:
            # Different length: use simple edit distance (insert/delete only, no transpositions)
            # We only need to know if it's ≤ threshold, so a fast approximation is fine.
            shorter, longer = sorted([ocr_player_id, submitted_player_id], key=len)
            # Check if shorter is a subsequence-style near-match of longer
            row = list(range(len(shorter) + 1))
            for ch in longer:
                new_row = [row[0] + 1]
                for j, c in enumerate(shorter):
                    new_row.append(min(new_row[-1] + 1, row[j + 1] + 1, row[j] + (ch != c)))
                row = new_row
            diff = row[-1]
        else:
            diff = len(ocr_player_id) + len(submitted_player_id)  # Definitely > threshold

        if 0 < diff <= OCR_NEAR_MATCH_MAX:
            return {
                "status": "user_ocr_choice",
                "ocr_player_id": ocr_player_id,
                "version": ocr_result.get("version"),
                "build": ocr_result.get("build"),
                "return_to_caller": {
                    "status": "user_ocr_choice",
                    "ocr_id": ocr_player_id,
                    "diff": diff,
                    "typed_id": submitted_player_id,
                    "user_message": f"Small discrepancy detected ({diff} character{'s' if diff > 1 else ''}). Please verify which ID is correct.",
                },
            }

    # Rule 3c: Large OCR mismatch → ocr_large_mismatch (mod OCR review review)
    return {
        "status": "ocr_large_mismatch",
        "ocr_player_id": ocr_player_id,
        "version": ocr_result.get("version"),
        "build": ocr_result.get("build"),
        "return_to_caller": {
            "status": "ocr_large_mismatch",
            "reason": f"id_mismatch_{intent}" if intent else "id_mismatch",
            "typed_id": submitted_player_id,
            "ocr_id": ocr_player_id,
            "user_message": f"Large discrepancy: You entered {submitted_player_id}, OCR read {ocr_player_id}. Requires mod review.",
        },
    }


def auto_complete_if_eligible(stem: str) -> dict[str, Any]:
    """Attempt to auto-complete submission if eligible (implements Rule 4 from taxonomy).

    Checks if submission is eligible for auto-complete:
    - New user (no old_player_id) → create player, mark approved
    - Existing user with REFRESH_VERIFICATION → mark approved (player already exists)
    - Existing user with NEW_GAME_INSTANCE → create new instance, mark approved
    - Existing user with mod-review intent → return ineligible (requires Intent review)

    Returns dict with:
        - eligible: bool - True if auto-completed, False if requires Intent review
        - status: str - Result status ("approved", "failed", or submission status if ineligible)
        - message: str - Human-readable message
    """
    from thetower.backend.sus.constants import IdChangeReason
    from thetower.backend.sus.services import create_or_update_player

    row = get_submission(stem)
    if not row:
        return {"eligible": False, "status": "error", "message": "Submission not found"}

    # Check terminal state
    if row.get("final_outcome"):
        return {"eligible": False, "status": row["status"], "message": f"Already complete: {row['final_outcome']}"}

    # Extract context
    verified_player_id = row.get("verified_player_id")
    old_player_id = row.get("old_player_id")
    intent = row.get("id_change_reason")
    platform = row.get("platform")
    account_id = row.get("account_id")
    submitter_name = row.get("submitter_name", "")

    if not verified_player_id:
        return {"eligible": False, "status": row["status"], "message": "No verified_player_id set (OCR not verified yet)"}

    # Rule 4a: New user → auto-complete
    if not old_player_id:
        logger.info("Auto-completing new user submission: stem=%s verified_id=%s", stem, verified_player_id)
        try:
            result = create_or_update_player(
                platform,
                account_id,
                submitter_name,
                verified_player_id,
                update_role_source=False,
                action_type=None,  # New user
                old_player_id=None,
            )

            if "error" in result:
                error_code = result.get("error")
                existing_player_pk = result.get("existing_player_pk")

                if error_code == "no_platform_link" and existing_player_pk:
                    # Player exists but has no active links on any platform.
                    # Escalate to mod intent review so a mod can approve re-linking
                    # the submitting Discord account → this existing KnownPlayer.
                    try:
                        from thetower.backend.sus.models import LinkedAccount as _LA

                        was_previously_linked = _LA.objects.filter(platform=platform, account_id=account_id, player_id=existing_player_pk).exists()
                    except Exception:
                        was_previously_linked = False

                    review_reason = "relink_original_owner" if was_previously_linked else "relink_new_discord"
                    update_submission(
                        stem,
                        status="mod_intent_review",
                        id_change_reason="relink",
                        review_reason=review_reason,
                        verified_player_id=verified_player_id,
                    )
                    add_event(
                        stem,
                        {
                            "type": "relink_escalated",
                            "ts": int(time.time()),
                            "existing_player_pk": existing_player_pk,
                            "was_previously_linked": was_previously_linked,
                            "review_reason": review_reason,
                        },
                    )
                    log_submission_update(stem)
                    logger.info(
                        "Relink escalated to mod intent review: stem=%s was_previously_linked=%s",
                        stem,
                        was_previously_linked,
                    )
                    return {
                        "eligible": False,
                        "status": "mod_intent_review",
                        "message": "No active platform links — escalated to mod review for relink decision",
                    }

                # All other errors → fail
                update_submission(stem, status="failed", final_outcome="failed")
                add_event(stem, {"type": "failed", "ts": int(time.time()), "reason": result["error"]})
                return {"eligible": True, "status": "failed", "message": f"Failed: {result['error']}"}

            update_submission(stem, status="approved", final_outcome="approved")
            add_event(stem, {"type": "approved", "ts": int(time.time()), "new_user": True})
            return {"eligible": True, "status": "approved", "message": "New user verified"}

        except Exception as e:
            logger.exception("Failed to auto-complete new user submission: stem=%s", stem)
            update_submission(stem, status="failed", final_outcome="failed")
            add_event(stem, {"type": "failed", "ts": int(time.time()), "reason": str(e)})
            return {"eligible": True, "status": "failed", "message": f"Exception: {str(e)}"}

    # Rule 4b: REFRESH_VERIFICATION → guard rail check then approve (no player changes needed)
    if intent == IdChangeReason.REFRESH_VERIFICATION.value:
        # Guard rail: verified_player_id must be one of the player's existing Tower IDs.
        # If the user submitted a different ID under the refresh intent, reject.
        try:
            from thetower.backend.sus.models import LinkedAccount, PlayerId

            link = LinkedAccount.objects.filter(platform=platform, account_id=account_id, active=True).select_related("player").first()
            if link:
                existing_ids = set(PlayerId.objects.filter(game_instance__player=link.player).values_list("id", flat=True))
                if verified_player_id.upper() not in {pid.upper() for pid in existing_ids}:
                    # ID not in player's existing IDs — reject
                    update_submission(
                        stem,
                        status="rejected",
                        mod_verdict="rejected_id_mismatch",
                        final_outcome="rejected",
                        review_reason="refresh_id_mismatch",
                    )
                    add_event(
                        stem,
                        {
                            "type": "auto_rejected",
                            "ts": int(time.time()),
                            "reason": "refresh_id_mismatch",
                            "verified_id": verified_player_id,
                            "existing_ids": list(existing_ids),
                        },
                    )
                    log_submission_update(stem)
                    logger.warning(
                        "Refresh verification guard: verified_id=%s not in existing IDs for %s:%s stem=%s",
                        verified_player_id,
                        platform,
                        account_id,
                        stem,
                    )
                    return {
                        "eligible": True,
                        "status": "rejected",
                        "message": "Refresh rejected: verified ID does not match any existing Tower ID on this account",
                    }
        except Exception as e:
            # If guard rail check fails (e.g. no linked account), fall through to reject
            logger.exception("Refresh guard rail check failed: stem=%s", stem)
            update_submission(stem, status="failed", final_outcome="failed")
            add_event(stem, {"type": "failed", "ts": int(time.time()), "reason": f"refresh_guard_check_failed: {e}"})
            return {"eligible": True, "status": "failed", "message": f"Refresh guard check failed: {e}"}

        logger.info("Auto-completing refresh verification: stem=%s verified_id=%s", stem, verified_player_id)
        update_submission(stem, status="approved", final_outcome="approved")
        add_event(stem, {"type": "approved", "ts": int(time.time()), "refresh": True})
        return {"eligible": True, "status": "approved", "message": "Verification refreshed"}

    # Rule 4c: NEW_GAME_INSTANCE → create new instance
    if intent == IdChangeReason.NEW_GAME_INSTANCE.value:
        logger.info("Auto-completing new game instance: stem=%s verified_id=%s", stem, verified_player_id)
        try:
            result = create_or_update_player(
                platform,
                account_id,
                submitter_name,
                verified_player_id,
                update_role_source=False,
                action_type="new_instance_keep_old",
                old_player_id=old_player_id,
            )

            if "error" in result:
                update_submission(stem, status="failed", final_outcome="failed")
                add_event(stem, {"type": "failed", "ts": int(time.time()), "reason": result["error"]})
                return {"eligible": True, "status": "failed", "message": f"Failed: {result['error']}"}

            update_submission(stem, status="approved", final_outcome="approved")
            add_event(stem, {"type": "approved", "ts": int(time.time()), "new_instance": True})
            return {"eligible": True, "status": "approved", "message": "New game instance created"}

        except Exception as e:
            logger.exception("Failed to auto-complete new game instance: stem=%s", stem)
            update_submission(stem, status="failed", final_outcome="failed")
            add_event(stem, {"type": "failed", "ts": int(time.time()), "reason": str(e)})
            return {"eligible": True, "status": "failed", "message": f"Exception: {str(e)}"}

    # Rule 4d: Mod-review intent → ineligible, requires Intent review
    logger.info("Submission requires Intent review (mod intent review): stem=%s intent=%s", stem, intent)
    return {
        "eligible": False,
        "status": row["status"],
        "message": f"Requires Intent review for intent: {intent}",
    }


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
                verified_player_id  TEXT,

                status              TEXT    NOT NULL DEFAULT 'ocr_processing',

                id_change_reason    TEXT,
                old_player_id       TEXT,

                review_reason       TEXT,
                mod_verdict         TEXT,
                mod_resolved_by     TEXT,
                mod_resolved_name    TEXT,
                mod_notes           TEXT,

                discord_log_message_id  TEXT,
                discord_notification_message_id  TEXT,
                -- TODO: once all callers are migrated to use discord_log_url, consider replacing
                -- discord_log_message_id with discord_log_url and parsing the ID from the URL.
                discord_log_url         TEXT,

                final_outcome       TEXT,
                events              TEXT    NOT NULL DEFAULT '[]',

                version             TEXT,
                build               TEXT,

                created_at          INTEGER NOT NULL,
                updated_at          INTEGER NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sub_account_status ON submissions (platform, account_id, status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sub_status_created ON submissions (status, created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sub_player ON submissions (submitted_player_id)")

        # Drop legacy columns that are no longer used (idempotent — ignore errors if already removed)
        for col in ("final_player_id", "manual_player_id", "mod_review_stage", "discord_message_id"):
            try:
                conn.execute(f"ALTER TABLE submissions DROP COLUMN {col}")
                logger.info("Dropped legacy column: %s", col)
            except Exception:
                pass  # Column already removed or not present

        # Add new columns (idempotent — ignore errors if already present)
        for add_col in ("discord_log_url TEXT", "mod_resolved_name TEXT"):
            try:
                conn.execute(f"ALTER TABLE submissions ADD COLUMN {add_col}")
                logger.info("Added column: %s", add_col)
            except Exception:
                pass  # Column already exists

        # Table for tracking failed Discord message refresh attempts (for retry)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS pending_discord_refreshes (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                stem        TEXT    NOT NULL,
                mod_user    TEXT    NOT NULL,
                error       TEXT    NOT NULL,
                created_at  INTEGER NOT NULL,
                retry_count INTEGER NOT NULL DEFAULT 0
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pending_refresh_stem ON pending_discord_refreshes (stem)")

        # Account links table — tracks all Discord account merges/links across platforms.
        # Covers: web merges (instant), Discord request-accept flow, mod actions, and link removals.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS account_links (
                id                   INTEGER PRIMARY KEY AUTOINCREMENT,
                stem                 TEXT    UNIQUE,

                -- Initiating account (who started the link)
                initiator_platform   TEXT    NOT NULL,
                initiator_account_id TEXT    NOT NULL,
                initiator_name       TEXT    NOT NULL DEFAULT '',

                -- Receiving account (being linked)
                target_platform      TEXT    NOT NULL,
                target_account_id    TEXT    NOT NULL,
                target_name          TEXT    NOT NULL DEFAULT '',

                -- Player being linked to (Django KnownPlayer pk)
                player_pk            INTEGER NOT NULL,

                -- Link type: 'web_merge' | 'discord_request' | 'mod_action'
                link_type            TEXT    NOT NULL,

                -- Status: 'pending' | 'completed' | 'rejected' | 'cancelled' | 'removed'
                --   pending   = Discord request sent, awaiting target acceptance
                --   completed = Link created (instant web merge or Discord accept)
                --   rejected  = Target declined Discord request
                --   cancelled = Initiator cancelled before target responded
                --   removed   = Mod destroyed an existing link
                status               TEXT    NOT NULL DEFAULT 'pending',

                -- Who performed the final action (mod user for mod_action, else NULL)
                resolved_by          TEXT,

                created_at           INTEGER NOT NULL,
                updated_at           INTEGER NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_links_initiator ON account_links (initiator_platform, initiator_account_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_links_target ON account_links (target_platform, target_account_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_links_player ON account_links (player_pk)")


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
        cursor = conn.execute(
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

    # Only notify on first insert — INSERT OR IGNORE is a no-op for duplicate stems,
    # and the caller (process_verification) fires log_submission_update after OCR anyway.
    if cursor.rowcount > 0:
        log_submission_update(stem)


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


def get_submission_history(platform: str, account_id: str, limit: int = 10) -> list[dict]:
    """Return recent terminal or active submissions for a user, newest-first.

    Returns submissions that are:
    - In a terminal state (final_outcome is set: approved/rejected/failed/abandoned/blocked_by_ban/etc.)
    - OR in an active state requiring user or mod action

    Used by both /account and /verify routes to show verification history.
    """
    if not REVIEW_DB_PATH.exists():
        return []

    # Active statuses that should be shown in history (user-pending or mod-pending)
    active_statuses = (
        "user_ocr_choice",  # User needs to choose submitted vs OCR ID
        "near_match_confirmed",
        "near_match_ocr",
        "ocr_large_mismatch",
        "near_match_timeout",
        "ocr_error",
        "mod_intent_review",
    )

    with sqlite3.connect(str(REVIEW_DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        placeholders = ",".join("?" * len(active_statuses))
        rows = conn.execute(
            f"""SELECT * FROM submissions
               WHERE json_extract(platform_ids, '$.' || ?) = ?
               AND (final_outcome IS NOT NULL OR status IN ({placeholders}))
               ORDER BY created_at DESC LIMIT ?""",
            (platform, account_id) + active_statuses + (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


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


def set_awaiting_mod_with_intent(stem: str, error_type: str, intent_reason: str, player_id: str | None = None) -> None:
    """After user selects intent following OCR error/mismatch, set intent and apply workflow.

    Args:
        stem: The submission stem
        error_type: Either "ocr_error" or "id_mismatch"
        intent_reason: The IdChangeReason value (e.g., "GAME_CHANGED_ID", "FIXING_TYPO", etc.)
        player_id: The ID the user wants to use (optional, will use submitted_player_id from submission if not provided)
    """
    # Get submission to retrieve submitted_player_id if needed
    if not player_id:
        row = get_submission(stem)
        if not row:
            logger.warning("set_awaiting_mod_with_intent: submission not found for stem=%s", stem)
            return
        player_id = row.get("submitted_player_id")

    # Update submission with intent and verified ID (trust user input since OCR failed/mismatched)
    update_submission(stem, id_change_reason=intent_reason, verified_player_id=player_id)
    add_event(stem, {"type": "intent_selected", "ts": int(time.time()), "intent": intent_reason, "error_type": error_type})

    # Apply auto-completion logic (will set status to "approved" or "mod_intent_review" based on intent)
    auto_complete_if_eligible(stem)

    # Notify Discord (and any other registered platforms) of the state change
    log_submission_update(stem)


def abandon_submission(stem: str, abandoned_by: str | None = None, final_outcome: str | None = None) -> bool:
    """Mark a submission as abandoned (user cancelled or system blocked).

    Sets status to "abandoned", records an audit event, and calls log_submission_update().
    Safe to call on already-terminal submissions — returns False without making changes.

    Args:
        stem: Submission identifier
        abandoned_by: Platform identifier of who abandoned (e.g., "discord:123456789") or None
        final_outcome: Semantic reason for abandonment. If None, stored as "abandoned".
            Pass a specific value like "blocked_by_ban" to record why the submission was abandoned
            rather than just that it was.

    Returns:
        True if the submission was abandoned, False if it was already in a terminal state or not found.
    """
    row = get_submission(stem)
    if not row:
        logger.warning("abandon_submission called on non-existent stem %s", stem)
        return False

    if row.get("status") in _TERMINAL_STATUSES:
        logger.info("abandon_submission called on already-terminal submission %s (status=%s) — skipping", stem, row.get("status"))
        return False

    resolved_outcome = final_outcome or "abandoned"
    update_submission(stem, status="abandoned", final_outcome=resolved_outcome)
    event: dict = {"type": "abandoned", "ts": int(time.time())}
    if abandoned_by:
        event["by"] = abandoned_by
    if final_outcome and final_outcome != "abandoned":
        event["reason"] = final_outcome
    add_event(stem, event)
    log_submission_update(stem, actor=abandoned_by)
    logger.info("Submission abandoned: stem=%s by=%s final_outcome=%s", stem, abandoned_by, resolved_outcome)
    return True


def fail_submission(stem: str, failed_by: str | None = None, final_outcome: str | None = None) -> bool:
    """Mark a submission as failed (validation error or system-detected block).

    Sets status to "failed", records an audit event, and calls log_submission_update().
    Safe to call on already-terminal submissions — returns False without making changes.

    Args:
        stem: Submission identifier
        failed_by: Platform identifier of the actor (e.g., "discord:123456789") or None
        final_outcome: Semantic reason for failure (e.g., "id_already_registered").
            If None, stored as "failed".

    Returns:
        True if the submission was failed, False if it was already in a terminal state or not found.
    """
    row = get_submission(stem)
    if not row:
        logger.warning("fail_submission called on non-existent stem %s", stem)
        return False

    if row.get("status") in _TERMINAL_STATUSES:
        logger.info("fail_submission called on already-terminal submission %s (status=%s) — skipping", stem, row.get("status"))
        return False

    resolved_outcome = final_outcome or "failed"
    update_submission(stem, status="failed", final_outcome=resolved_outcome)
    event: dict = {"type": "failed", "ts": int(time.time())}
    if failed_by:
        event["by"] = failed_by
    if final_outcome and final_outcome != "failed":
        event["reason"] = final_outcome
    add_event(stem, event)
    log_submission_update(stem, actor=failed_by)
    logger.info("Submission failed: stem=%s by=%s final_outcome=%s", stem, failed_by, resolved_outcome)
    return True


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
# Pending Discord refresh tracking (for retry when bot is unavailable)
# ---------------------------------------------------------------------------


def save_pending_discord_refresh(stem: str, mod_user: str, error: str) -> None:
    """Save a failed Discord message refresh attempt for later retry.

    Args:
        stem: Submission identifier
        mod_user: Moderator who triggered the action
        error: Error message from the failed attempt
    """
    ensure_db()
    with sqlite3.connect(str(REVIEW_DB_PATH)) as conn:
        conn.execute(
            """INSERT INTO pending_discord_refreshes (stem, mod_user, error, created_at)
               VALUES (?, ?, ?, ?)""",
            (stem, mod_user, error, int(time.time())),
        )
    logger.info(f"Saved pending Discord refresh for submission {stem} (error: {error})")


def get_pending_discord_refreshes(limit: int = 100) -> list[dict]:
    """Get pending Discord refresh attempts for retry.

    Returns pending refreshes ordered by retry_count ASC (fewest retries first)
    to prioritize newer/fresher failures.

    Args:
        limit: Maximum number of pending refreshes to return

    Returns:
        List of pending refresh dicts with keys: id, stem, mod_user, error, created_at, retry_count
    """
    if not REVIEW_DB_PATH.exists():
        return []
    with sqlite3.connect(str(REVIEW_DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """SELECT * FROM pending_discord_refreshes
               ORDER BY retry_count ASC, created_at ASC
               LIMIT ?""",
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def delete_pending_discord_refresh(refresh_id: int) -> None:
    """Delete a pending Discord refresh after successful retry.

    Args:
        refresh_id: Primary key of the pending refresh record
    """
    ensure_db()
    with sqlite3.connect(str(REVIEW_DB_PATH)) as conn:
        conn.execute("DELETE FROM pending_discord_refreshes WHERE id = ?", (refresh_id,))
    logger.debug(f"Deleted pending Discord refresh ID {refresh_id}")


def increment_pending_discord_refresh_retry_count(refresh_id: int) -> None:
    """Increment retry count for a pending Discord refresh.

    Args:
        refresh_id: Primary key of the pending refresh record
    """
    ensure_db()
    with sqlite3.connect(str(REVIEW_DB_PATH)) as conn:
        conn.execute("UPDATE pending_discord_refreshes SET retry_count = retry_count + 1 WHERE id = ?", (refresh_id,))


# ---------------------------------------------------------------------------


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
               WHERE status NOT IN ('passed', 'approved', 'rejected', 'failed', 'abandoned')
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
               AND status NOT IN ('passed', 'approved', 'rejected', 'failed', 'abandoned')
               LIMIT 1""",
            (submitted_player_id,),
        ).fetchone()
    return dict(row) if row else None


def get_pending_submission_for_known_player(platform: str, account_id: str) -> dict | None:
    """Return any non-terminal submission for the KnownPlayer linked to this platform account.

    Looks up all linked accounts (any platform) for the same KnownPlayer and checks each
    for a pending submission. Returns the first one found, or None if the account has no
    KnownPlayer (i.e. new/unverified user — caller should fall back to get_pending_submission).

    Raises if the Django DB is unreachable — callers should treat this as fail-safe.
    """
    from thetower.backend.sus.models import LinkedAccount

    link = LinkedAccount.objects.filter(platform=platform, account_id=account_id, active=True).select_related("player").first()
    if not link:
        return None  # No KnownPlayer for this account — new user, skip cross-platform check

    all_accounts = LinkedAccount.objects.filter(player=link.player).values_list("platform", "account_id")
    for acct_platform, acct_id in all_accounts:
        pending = get_pending_submission(acct_platform, acct_id)
        if pending:
            return pending
    return None


def get_mod_queue(
    limit: int = 100, reason_filter: str | None = None, stem_filter: str | None = None, tower_id_filter: str | None = None
) -> list[dict]:
    """Return submissions needing mod review (OCR review and Intent review), oldest first.

    Note: reason_filter is kept for API compatibility but maps to status filtering in new taxonomy.
    """
    if not REVIEW_DB_PATH.exists():
        return []

    # OCR review + Intent review statuses
    mod_review_statuses = ("near_match_confirmed", "near_match_ocr", "ocr_large_mismatch", "near_match_timeout", "ocr_error", "mod_intent_review")

    placeholders = ",".join("?" * len(mod_review_statuses))
    clauses = [f"status IN ({placeholders})"]
    params: list = list(mod_review_statuses)

    # reason_filter now maps to status filter for backwards compatibility
    if reason_filter:
        # If reason_filter is passed, treat it as a status filter override
        clauses = ["status = ?"]
        params = [reason_filter]

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
    """Return mod queue counts by status (OCR review and Intent review)."""
    if not REVIEW_DB_PATH.exists():
        return {"total": 0, "by_status": {}}

    # OCR review statuses: OCR verification needed
    # Intent review statuses: Intent/action selection needed
    mod_review_statuses = ("near_match_confirmed", "near_match_ocr", "ocr_large_mismatch", "near_match_timeout", "ocr_error", "mod_intent_review")

    with sqlite3.connect(str(REVIEW_DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        placeholders = ",".join("?" * len(mod_review_statuses))
        query = f"SELECT status, COUNT(*) AS n FROM submissions WHERE status IN ({placeholders}) GROUP BY status"
        rows = conn.execute(query, mod_review_statuses).fetchall()

    by_status = {row["status"]: row["n"] for row in rows}
    return {"total": sum(by_status.values()), "by_status": by_status}


def get_non_terminal_submissions() -> list[dict]:
    """Return all submissions that are not in a terminal state.

    Terminal states are: approved, passed (legacy), rejected, failed, abandoned.
    Used for Discord embed sync on bot startup.
    """
    if not REVIEW_DB_PATH.exists():
        return []

    TERMINAL_STATES = ("approved", "passed", "rejected", "failed", "abandoned")
    placeholders = ",".join("?" * len(TERMINAL_STATES))

    with sqlite3.connect(str(REVIEW_DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(f"SELECT * FROM submissions WHERE status NOT IN ({placeholders}) ORDER BY created_at DESC", TERMINAL_STATES).fetchall()

    return [dict(r) for r in rows]


def get_terminal_submissions_with_notifications() -> list[dict]:
    """Return terminal submissions that still have a mod-queue notification message to delete.

    When a submission moves to a terminal state, any outstanding notification message in
    the mod channel should be deleted.  Running ``sync_verification_discord_state()`` on
    these handles both the log embed update (final state, no buttons) and the notification
    delete in one pass.
    """
    if not REVIEW_DB_PATH.exists():
        return []

    TERMINAL_STATES = ("approved", "passed", "rejected", "failed", "abandoned")
    placeholders = ",".join("?" * len(TERMINAL_STATES))

    with sqlite3.connect(str(REVIEW_DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"""SELECT * FROM submissions
                WHERE status IN ({placeholders})
                AND discord_notification_message_id IS NOT NULL
                ORDER BY created_at DESC""",
            TERMINAL_STATES,
        ).fetchall()

    return [dict(r) for r in rows]


def get_terminal_submissions_never_logged() -> list[dict]:
    """Return terminal submissions that have no Discord log channel entry yet.

    These are submissions that completed while the bot was offline, or before log
    sync was wired up.  Running ``sync_verification_discord_state()`` on each will
    create a log entry and set ``discord_log_message_id``.

    Submissions intentionally excluded from backfill should have
    ``discord_log_message_id`` set to a sentinel (e.g. ``'1'``) rather than left
    NULL, so they are not picked up by this query.
    """
    if not REVIEW_DB_PATH.exists():
        return []

    TERMINAL_STATES = ("approved", "passed", "rejected", "failed", "abandoned")
    placeholders = ",".join("?" * len(TERMINAL_STATES))

    with sqlite3.connect(str(REVIEW_DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"""SELECT * FROM submissions
                WHERE status IN ({placeholders})
                AND discord_log_message_id IS NULL
                ORDER BY created_at ASC""",
            TERMINAL_STATES,
        ).fetchall()

    return [dict(r) for r in rows]


def get_mod_queue_for_player(player_pk: int) -> list[dict]:
    """Return submissions needing mod review for all accounts linked to a player."""
    from thetower.backend.sus.models import LinkedAccount

    pairs: list[tuple[str, str]] = list(LinkedAccount.objects.filter(player_id=player_pk).values_list("platform", "account_id"))
    if not pairs or not REVIEW_DB_PATH.exists():
        return []

    # OCR review + Intent review statuses
    mod_review_statuses = ("near_match_confirmed", "near_match_ocr", "ocr_large_mismatch", "near_match_timeout", "ocr_error", "mod_intent_review")

    with sqlite3.connect(str(REVIEW_DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        results = []
        placeholders = ",".join("?" * len(mod_review_statuses))
        for platform, account_id in pairs:
            query = f"""SELECT * FROM submissions
                       WHERE status IN ({placeholders})
                       AND json_extract(platform_ids, '$.' || ?) = ?
                       ORDER BY created_at ASC"""
            rows = conn.execute(query, mod_review_statuses + (platform, account_id)).fetchall()
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
# Account Links — backend functions
# ---------------------------------------------------------------------------

_LINK_TERMINAL_STATUSES = ("completed", "rejected", "cancelled", "removed")


def generate_link_stem() -> str:
    """Generate a unique stem for an account link record."""
    return f"link_{int(time.time())}_{secrets.token_hex(4)}"


def create_account_link(
    initiator_platform: str,
    initiator_account_id: str,
    initiator_name: str,
    target_platform: str,
    target_account_id: str,
    target_name: str,
    player_pk: int,
    link_type: str,
    status: str = "pending",
) -> str:
    """Create an account link record and return the stem.

    Args:
        initiator_platform: Platform of the account initiating the link (e.g. 'discord')
        initiator_account_id: Account ID of the initiator
        initiator_name: Display name of the initiator
        target_platform: Platform of the account being linked (e.g. 'discord')
        target_account_id: Account ID of the target
        target_name: Display name of the target
        player_pk: Django KnownPlayer primary key the target will be linked to
        link_type: 'web_merge' | 'discord_request' | 'mod_action'
        status: Initial status. Defaults to 'pending'. Use 'completed' for instant merges
            (web or mod).

    Returns:
        The stem (unique key) for the new account link record.
    """
    ensure_db()
    stem = generate_link_stem()
    now = int(time.time())
    with sqlite3.connect(str(REVIEW_DB_PATH)) as conn:
        conn.execute(
            """INSERT INTO account_links
               (stem, initiator_platform, initiator_account_id, initiator_name,
                target_platform, target_account_id, target_name,
                player_pk, link_type, status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                stem,
                initiator_platform,
                initiator_account_id,
                initiator_name,
                target_platform,
                target_account_id,
                target_name,
                player_pk,
                link_type,
                status,
                now,
                now,
            ),
        )
    logger.info(
        "Account link created: stem=%s type=%s status=%s initiator=%s:%s target=%s:%s player=%d",
        stem,
        link_type,
        status,
        initiator_platform,
        initiator_account_id,
        target_platform,
        target_account_id,
        player_pk,
    )
    return stem


def get_account_link(stem: str) -> dict | None:
    """Return an account link record by stem, or None if not found."""
    if not REVIEW_DB_PATH.exists():
        return None
    with sqlite3.connect(str(REVIEW_DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM account_links WHERE stem = ?", (stem,)).fetchone()
    return dict(row) if row else None


def get_pending_link(target_platform: str, target_account_id: str) -> dict | None:
    """Return the most recent pending link request targeting a given account, or None."""
    if not REVIEW_DB_PATH.exists():
        return None
    with sqlite3.connect(str(REVIEW_DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """SELECT * FROM account_links
               WHERE target_platform = ? AND target_account_id = ? AND status = 'pending'
               ORDER BY created_at DESC LIMIT 1""",
            (target_platform, target_account_id),
        ).fetchone()
    return dict(row) if row else None


def is_account_verified(platform: str, account_id: str) -> bool:
    """Return True if the given platform account is already linked to a verified KnownPlayer.

    Used to guard link-request creation: we do not allow sending a link invite to an
    account that already has its own verified identity.
    """
    from thetower.backend.sus.models import LinkedAccount

    return LinkedAccount.objects.filter(platform=platform, account_id=account_id, active=True).exists()


def resolve_account_link(stem: str, new_status: str, resolved_by: str | None = None) -> bool:
    """Resolve an account link request.

    Args:
        stem: The link stem to resolve
        new_status: Terminal status: 'completed' | 'rejected' | 'cancelled' | 'removed'
        resolved_by: Platform:account_id of who resolved it (e.g. 'discord:123456789')

    Returns:
        True if resolved, False if not found or already terminal.
    """
    if not REVIEW_DB_PATH.exists():
        return False
    now = int(time.time())
    with sqlite3.connect(str(REVIEW_DB_PATH)) as conn:
        existing = conn.execute("SELECT status FROM account_links WHERE stem = ?", (stem,)).fetchone()
        if not existing:
            logger.warning("resolve_account_link: stem %s not found", stem)
            return False
        if existing[0] in _LINK_TERMINAL_STATUSES:
            logger.info("resolve_account_link: stem %s already in terminal state %s", stem, existing[0])
            return False
        conn.execute(
            "UPDATE account_links SET status = ?, resolved_by = ?, updated_at = ? WHERE stem = ?",
            (new_status, resolved_by, now, stem),
        )
    logger.info("Account link resolved: stem=%s status=%s resolved_by=%s", stem, new_status, resolved_by)
    return True


def cancel_pending_links_by_initiator(initiator_platform: str, initiator_account_id: str) -> int:
    """Cancel all pending link requests sent by a given account.

    Returns the number of links cancelled.
    """
    if not REVIEW_DB_PATH.exists():
        return 0
    now = int(time.time())
    with sqlite3.connect(str(REVIEW_DB_PATH)) as conn:
        result = conn.execute(
            """UPDATE account_links SET status = 'cancelled', updated_at = ?
               WHERE initiator_platform = ? AND initiator_account_id = ? AND status = 'pending'""",
            (now, initiator_platform, initiator_account_id),
        )
    count = result.rowcount
    if count:
        logger.info(
            "Cancelled %d pending link(s) for %s:%s",
            count,
            initiator_platform,
            initiator_account_id,
        )
    return count


def get_outgoing_pending_links(initiator_platform: str, initiator_account_id: str) -> list[dict]:
    """Return all pending link requests sent by a given account.

    Returns list of dicts with keys: stem, target_platform, target_account_id, target_name, created_at.
    """
    if not REVIEW_DB_PATH.exists():
        return []
    with sqlite3.connect(str(REVIEW_DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """SELECT stem, target_platform, target_account_id, target_name, created_at
               FROM account_links
               WHERE initiator_platform = ? AND initiator_account_id = ? AND status = 'pending'
               ORDER BY created_at DESC""",
            (initiator_platform, initiator_account_id),
        ).fetchall()
    return [dict(row) for row in rows]


def get_pending_links_for_player(player_pk: int) -> list[dict]:
    """Return all pending link requests for a player (any initiator account belonging to that player_pk).

    Returns list of dicts with: stem, initiator_account_id, target_account_id, target_name, created_at.
    Used by ManageDiscordAccountsView to show pending link buttons.
    """
    if not REVIEW_DB_PATH.exists():
        return []
    with sqlite3.connect(str(REVIEW_DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """SELECT stem, initiator_platform, initiator_account_id, target_platform,
                      target_account_id, target_name, created_at
               FROM account_links
               WHERE player_pk = ? AND status = 'pending'
               ORDER BY created_at DESC""",
            (player_pk,),
        ).fetchall()
    return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# Submission Phase Inference
# ---------------------------------------------------------------------------


def get_submission_phase(stem: str) -> str:
    """Return a canonical phase label for a submission based on its current state.

    Infers the current workflow phase from ``status`` and ``verified_player_id``
    without needing a ``mod_review_stage`` column.

    Phase labels:
        - ``"pending"``           — submission created, OCR not yet run
        - ``"ocr_processing"``    — OCR running (transient)
        - ``"awaiting_user_ocr"`` — near-match, waiting for user to choose typed vs OCR ID
        - ``"awaiting_mod_ocr"``  — large mismatch or near-match timeout, mod must review OCR
        - ``"awaiting_mod_intent"`` — OCR resolved, mod must select action type
        - ``"approved"``          — complete, approved (auto or mod)
        - ``"rejected"``          — complete, rejected by mod
        - ``"failed"``            — complete, failed (OCR failure, player creation error)
        - ``"abandoned"``         — complete, user cancelled or system blocked (check ``final_outcome``
          for the specific reason, e.g. ``"abandoned"`` for user-cancelled or ``"blocked_by_ban"``
          when an active ban prevented completion)
        - ``"unknown"``           — unrecognised status (should not occur)

    TODO (2026-06-13): When implementing the web mod review UI, use this function to decide
    which action buttons to render. Replace any platform-specific state-derivation logic
    (e.g., discord_sync._determine_target_state()) with calls to this function so both
    Discord and web always agree on the current phase.

    Args:
        stem: Submission identifier

    Returns:
        One of the phase label strings above.
    """
    row = get_submission(stem)
    if not row:
        return "unknown"

    status = row.get("status", "")

    if status in ("passed", "approved"):
        return "approved"
    if status == "rejected":
        return "rejected"
    if status == "failed":
        return "failed"
    if status == "abandoned":
        return "abandoned"
    if status in ("pending", "ocr_processing", "ocr_unavailable"):
        return status
    if status == "user_ocr_choice":
        return "awaiting_user_ocr"
    if status in ("near_match_confirmed", "near_match_ocr"):
        # User chose, OCR soft-flagged — treat as awaiting_mod_intent if id_change_reason requires review,
        # otherwise awaiting_mod_ocr (soft audit flag for new users)
        return "awaiting_mod_intent" if row.get("id_change_reason") else "awaiting_mod_ocr"
    if status in ("ocr_large_mismatch", "near_match_timeout", "ocr_error"):
        return "awaiting_mod_ocr"
    if status == "mod_intent_review":
        return "awaiting_mod_intent"

    return "unknown"


# ---------------------------------------------------------------------------
# Mod resolution
# ---------------------------------------------------------------------------


def mod_resolve_submission(stem: str, verdict: str, resolved_by: str, player_id: str | None = None, resolved_name: str | None = None) -> None:
    """Resolve a mod review: update status, store verdict, and create player if needed.

    .. deprecated:: 2026-06-13
        Use ``mod_resolve_ocr()`` instead. This single-stage function predates the two-stage
        OCR + Intent review model and does not advance submissions to Intent review after OCR
        approval. The web mod routes currently call this; they should be updated to call
        ``mod_resolve_ocr()`` and ``mod_resolve_intent()`` directly.

        TODO (2026-06-13): Update thetower-web routes/mod.py to call mod_resolve_ocr() and
        mod_resolve_intent(), then remove this function.

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
    if resolved_name:
        fields["mod_resolved_name"] = resolved_name
    if player_id:
        fields["verified_player_id"] = player_id

    update_submission(stem, **fields)
    add_event(
        stem,
        {
            "type": "mod_action",
            "ts": int(time.time()),
            "verdict": verdict,
            "mod": resolved_by,
            **({"verified_id": player_id} if player_id else {}),
        },
    )

    # For approved/fix_id verdicts on new_instance_* reviews, create the player now
    if verdict in ("approved", "fix_id") and row.get("review_reason", "").startswith("new_instance_"):
        player_id_to_create = player_id or row.get("verified_player_id") or row.get("submitted_player_id")
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

    # Notify Discord (and any other registered platforms) of the state change
    log_submission_update(stem, actor=resolved_by)


def mod_resolve_ocr(
    stem: str, verdict: str, resolved_by: str, verified_player_id: str | None = None, resolved_name: str | None = None
) -> dict[str, Any]:
    """Resolve OCR review: Verify OCR result (refactored to use auto_complete_if_eligible).

    Args:
        stem: Submission identifier
        verdict: "approved" (OCR correct), "fix_id" (mod entered correct ID), or "rejected_fake" (invalid screenshot)
        resolved_by: Mod identifier (platform:account_id)
        verified_player_id: The ID verified from screenshot (required for approved/fix_id)

    Returns:
        Dict with keys:
        - status: "ok" if action taken, "already_complete" if submission already in terminal state
        - current_status: If already_complete, the current status of the submission
        - message: Human-readable message for the mod
    """
    row = get_submission(stem)
    if not row:
        logger.warning("mod_resolve_ocr called on non-existent stem %s", stem)
        return {"status": "error", "message": "Submission not found"}

    # Check if already in terminal state
    current_status = row.get("status")
    if current_status in _TERMINAL_STATUSES:
        logger.info(f"mod_resolve_ocr called on already-complete submission {stem} (status={current_status}) by {resolved_by} - skipping action")
        status_messages = {
            "approved": f"This verification was already approved (status: {current_status})",
            "rejected": f"This verification was already rejected (status: {current_status})",
            "failed": f"This verification failed (status: {current_status})",
            "abandoned": f"This verification was abandoned by the user (status: {current_status})",
        }
        return {
            "status": "already_complete",
            "current_status": current_status,
            "message": status_messages.get(current_status, f"Already {current_status}"),
        }

    if verdict == "rejected_fake":
        # OCR review rejection: Screenshot is fake/invalid, stop here
        update_submission(
            stem,
            status="rejected",
            mod_verdict=verdict,
            mod_resolved_by=resolved_by,
            mod_resolved_name=resolved_name,
            final_outcome="rejected",
        )
        add_event(stem, {"type": "mod_ocr_reject", "ts": int(time.time()), "mod": resolved_by})
        logger.info("OCR review rejected as fake: stem=%s mod=%s", stem, resolved_by)
        return {"status": "ok", "message": "Verification rejected"}

    if not verified_player_id:
        logger.error("mod_resolve_ocr called without verified_player_id for verdict %s stem %s", verdict, stem)
        return {"status": "error", "message": "Missing verified_player_id"}

    # Store verified_player_id (signals OCR review complete)
    update_submission(stem, verified_player_id=verified_player_id, mod_resolved_name=resolved_name)

    # Add OCR review complete event
    add_event(
        stem,
        {
            "type": "mod_ocr_complete",
            "ts": int(time.time()),
            "mod": resolved_by,
            "verdict": verdict,
            "verified_id": verified_player_id,
        },
    )

    # Attempt auto-complete (implements Rule 4: checks if new user or self-service intent)
    auto_result = auto_complete_if_eligible(stem)

    if auto_result["eligible"]:
        # Successfully auto-completed (new user or self-service intent)
        logger.info(
            "OCR review complete with auto-approval: stem=%s verified_id=%s mod=%s status=%s",
            stem,
            verified_player_id,
            resolved_by,
            auto_result["status"],
        )
        log_submission_update(stem, actor=resolved_by)
        return {"status": "ok", "message": f"OCR review complete: {auto_result['message']}"}
    else:
        # Requires Intent review (existing user with mod-review intent)
        # Update status to mod_intent_review
        update_submission(stem, status="mod_intent_review")
        logger.info("OCR review complete, advancing to Intent review: stem=%s verified_id=%s mod=%s", stem, verified_player_id, resolved_by)
        result = {"status": "ok", "message": "OCR review complete, advanced to Intent review for action selection"}

    log_submission_update(stem, actor=resolved_by)
    return result


def mod_resolve_intent(stem: str, action_type: str, resolved_by: str, resolved_name: str | None = None) -> dict[str, Any]:
    """Resolve intent review: Apply action type to verified ID (updated with new status values).

    Args:
        stem: Submission identifier
        action_type: "replace", "new_instance_keep_old", "add_alt", "merge_ids", "new_instance_retire_old", or "reject"
        resolved_by: Mod identifier (platform:account_id)

    Returns:
        Dict with keys:
        - status: "ok" if action taken, "already_complete" if submission already in terminal state
        - current_status: If already_complete, the current status of the submission
        - message: Human-readable message for the mod
    """
    from thetower.backend.sus.services import create_or_update_player

    row = get_submission(stem)
    if not row:
        logger.warning("mod_resolve_intent called on non-existent stem %s", stem)
        return {"status": "error", "message": "Submission not found"}

    # Check if already in terminal state
    current_status = row.get("status")
    if current_status in _TERMINAL_STATUSES:
        logger.info(f"mod_resolve_intent called on already-complete submission {stem} (status={current_status}) by {resolved_by} - skipping action")
        status_messages = {
            "approved": f"This verification was already approved (status: {current_status})",
            "rejected": f"This verification was already rejected (status: {current_status})",
            "failed": f"This verification failed (status: {current_status})",
            "abandoned": f"This verification was abandoned by the user (status: {current_status})",
        }
        return {
            "status": "already_complete",
            "current_status": current_status,
            "message": status_messages.get(current_status, f"Already {current_status}"),
        }

    verified_player_id = row.get("verified_player_id")
    if not verified_player_id and action_type != "reject":
        logger.error("mod_resolve_intent: No verified_player_id for stem %s action %s", stem, action_type)
        return {"status": "error", "message": "Missing verified_player_id"}

    if action_type == "reject":
        # Intent review rejection: Don't apply changes, mark as rejected
        update_submission(
            stem,
            status="rejected",
            mod_verdict="rejected_no_action",
            mod_resolved_by=resolved_by,
            mod_resolved_name=resolved_name,
            final_outcome="rejected",
        )
        add_event(stem, {"type": "mod_intent_reject", "ts": int(time.time()), "mod": resolved_by})
        logger.info("Intent review rejected (no action): stem=%s mod=%s", stem, resolved_by)
        log_submission_update(stem, actor=resolved_by)
        return {"status": "ok", "message": "Intent review rejected - no changes applied"}

    # Apply the action type
    platform = row.get("platform")
    account_id = row.get("account_id")
    submitter_name = row.get("submitter_name", "")
    old_player_id = row.get("old_player_id")

    if not platform or not account_id:
        logger.error("mod_resolve_intent: Missing platform/account for stem %s", stem)
        return {"status": "error", "message": "Missing platform/account"}

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
            update_submission(stem, status="failed", final_outcome="failed")
            add_event(stem, {"type": "failed", "ts": int(time.time()), "reason": result["error"]})
            return {"status": "error", "message": f"Failed: {result['error']}"}
        else:
            # Map action_type to final_outcome
            outcome_map = {
                "replace": "approved",
                "new_instance_keep_old": "approved",
                "add_alt": "approved",
                "merge_ids": "approved",
                "new_instance_retire_old": "approved",
                "relink": "approved",
            }
            final_outcome = outcome_map.get(action_type, "approved")

            update_submission(
                stem,
                status="approved",
                mod_verdict=action_type,
                mod_resolved_by=resolved_by,
                mod_resolved_name=resolved_name,
                final_outcome=final_outcome,
            )
            add_event(
                stem,
                {
                    "type": "mod_intent_complete",
                    "ts": int(time.time()),
                    "mod": resolved_by,
                    "action": action_type,
                    "verified_id": verified_player_id,
                },
            )
            logger.info("Intent review complete: stem=%s action=%s verified_id=%s mod=%s", stem, action_type, verified_player_id, resolved_by)
            result = {"status": "ok", "message": f"Action {action_type} applied successfully"}

        log_submission_update(stem, actor=resolved_by)
        return result
    except Exception as e:
        logger.exception("Exception applying action %s for stem %s", action_type, stem)
        update_submission(stem, status="failed", final_outcome="failed")
        add_event(stem, {"type": "failed", "ts": int(time.time()), "reason": "player_action_failed"})
        return {"status": "error", "message": f"Exception: {str(e)}"}


# ---------------------------------------------------------------------------
# Stale escalation
# ---------------------------------------------------------------------------


def auto_escalate_stale_pending_submissions(stale_threshold_seconds: int = 3600) -> int:
    """Escalate submissions stuck in user_ocr_choice for >threshold seconds (updated with new status values and near-match support).

    Timeout threshold:
        - user_ocr_choice (near-match): 1 hour default (3600 seconds)

    Returns number escalated.
    """
    if not REVIEW_DB_PATH.exists():
        return 0
    cutoff_ts = int(time.time()) - stale_threshold_seconds
    now = int(time.time())

    with sqlite3.connect(str(REVIEW_DB_PATH)) as conn:
        # Find rows that need escalation (near-match user_ocr_choice that timed out)
        rows = conn.execute(
            """SELECT stem, status, id_change_reason FROM submissions
               WHERE status = 'user_ocr_choice'
               AND created_at < ?""",
            (cutoff_ts,),
        ).fetchall()

        if not rows:
            return 0

        count = 0
        escalated_stems = []
        for stem, status, intent in rows:
            # Escalate to near_match_timeout (mod OCR review)
            conn.execute(
                """UPDATE submissions
                   SET status = 'near_match_timeout', updated_at = ?
                   WHERE stem = ? AND status NOT IN ('approved', 'rejected', 'failed', 'abandoned')""",
                (now, stem),
            )
            count += 1
            escalated_stems.append(stem)

            # Add auto_escalated event
            try:
                row = conn.execute("SELECT events FROM submissions WHERE stem = ?", (stem,)).fetchone()
                if row:
                    events = json.loads(row[0] or "[]")
                    events.append({"type": "auto_escalated", "ts": now, "reason": "user_ocr_choice_timeout"})
                    conn.execute("UPDATE submissions SET events = ? WHERE stem = ?", (json.dumps(events), stem))
            except Exception:
                pass

        logger.info("Auto-escalated %d user_ocr_choice submissions to near_match_timeout", count)

    # Trigger Discord sync for each escalated submission so the log embed reflects the new status
    for stem in escalated_stems:
        log_submission_update(stem)

    return count


# ---------------------------------------------------------------------------
# Submission Sync — callback registry
# ---------------------------------------------------------------------------

# Registered sync callbacks: callables that accept (stem, actor) and schedule
# the Discord log embed update.  Typically one entry (the bot's sync function),
# but the list supports multiple consumers (e.g. bot + web socket push).
_sync_callbacks: list = []


def register_sync_callback(callback) -> None:
    """Register a callable to be invoked by log_submission_update().

    The callable should accept (stem: str, actor: str | None) and handle
    scheduling the async Discord sync on the event loop.  Duplicates are ignored.

    Typically called once during bot startup:
        verification.register_sync_callback(cog.schedule_discord_sync)
    """
    if callback not in _sync_callbacks:
        _sync_callbacks.append(callback)
        logger.debug("Registered sync callback: %s (total callbacks: %d)", callback, len(_sync_callbacks))
    else:
        logger.debug("Sync callback already registered: %s", callback)


def unregister_sync_callback(callback) -> None:
    """Remove a previously registered sync callback.  No-op if not found."""
    try:
        _sync_callbacks.remove(callback)
        logger.info("unregister_sync_callback: removed %s (remaining callbacks: %d)", callback, len(_sync_callbacks))
    except ValueError:
        logger.debug("unregister_sync_callback: callback not found: %s", callback)


def log_submission_update(stem: str, actor: str | None = None) -> None:
    """Notify all registered platforms that a submission has changed state.

    Calls every callback registered via register_sync_callback().  Each callback
    is responsible for scheduling the async Discord embed update on its own event
    loop.  If no callbacks are registered (e.g. in web-only mode), logs a debug
    message and returns silently.

    Args:
        stem: Submission identifier
        actor: Optional actor who triggered the change (e.g., "discord:123456789")
    """
    logger.debug("log_submission_update: stem=%s actor=%s callbacks=%d", stem, actor, len(_sync_callbacks))
    if not _sync_callbacks:
        logger.warning("log_submission_update: no callbacks registered — Discord embed will NOT update (stem=%s)", stem)
        return
    for cb in _sync_callbacks:
        try:
            cb(stem, actor)
        except Exception:
            logger.exception("log_submission_update: callback %s raised for stem=%s", cb, stem)


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


def get_primary_player_id(platform: str, account_id: str) -> str | None:
    """Return the primary Tower ID for this account, or None if no verified IDs exist."""
    from thetower.backend.sus.models import LinkedAccount

    link = LinkedAccount.objects.filter(platform=platform, account_id=account_id, active=True).select_related("player").first()
    if not link:
        return None
    primary_pid = link.player.get_primary_player_id()
    return primary_pid.id if primary_pid else None


def user_resolve_near_match(stem: str, chosen_player_id: str, user_chose_typed: bool) -> dict[str, Any]:
    """User resolved near-match by choosing typed ID or OCR ID (implements near-match resolution flow).

    Sets verified_player_id to user's choice and calls auto_complete_if_eligible to determine next status.

    Args:
        stem: Submission identifier
        chosen_player_id: The player ID the user selected (either submitted or OCR)
        user_chose_typed: True if user chose their typed ID, False if chose OCR ID

    Returns:
        Dict with keys:
        - status: str - Result status ("approved", "mod_intent_review", "failed", or "error")
        - message: str - Human-readable message
        - auto_completed: bool - True if submission was auto-completed
    """
    row = get_submission(stem)
    if not row:
        logger.warning("user_resolve_near_match called on non-existent stem %s", stem)
        return {"status": "error", "message": "Submission not found"}

    # Verify submission is in user_ocr_choice status
    if row.get("status") != "user_ocr_choice":
        return {"status": "error", "message": f"Submission not in user_ocr_choice status (current: {row.get('status')})"}

    # Set verified_player_id to user's choice
    # Also update status to indicate user's choice (near_match_confirmed or near_match_ocr)
    choice_status = "near_match_confirmed" if user_chose_typed else "near_match_ocr"
    update_submission(stem, verified_player_id=chosen_player_id, status=choice_status)
    add_event(
        stem,
        {
            "type": "user_near_match_choice",
            "ts": int(time.time()),
            "chosen_id": chosen_player_id,
            "chose_typed": user_chose_typed,
        },
    )
    logger.info(
        "User resolved near-match: stem=%s chose_typed=%s verified_id=%s",
        stem,
        user_chose_typed,
        chosen_player_id,
    )

    # Try auto-complete (will succeed for new users and self-service intents)
    auto_result = auto_complete_if_eligible(stem)

    if auto_result["eligible"]:
        result = {
            "status": auto_result["status"],
            "message": auto_result["message"],
            "auto_completed": True,
        }
    else:
        # Requires Intent review (existing user with mod-review intent)
        # Update status to mod_intent_review
        update_submission(stem, status="mod_intent_review")
        logger.info("Near-match resolved, advancing to Intent review: stem=%s verified_id=%s", stem, chosen_player_id)
        result = {
            "status": "mod_intent_review",
            "message": "Your choice has been recorded. A moderator will review your intent.",
            "auto_completed": False,
        }

    log_submission_update(stem)
    return result


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
    """Run OCR on a verification image and create/update player if eligible (refactored to use pure functions).

    Args:
        id_change_reason: For Scenario B (users with existing instances), the intent they selected.
                          NULL for Scenario A (new users).

    Returns:
        dict with keys: status, and optionally reason, ocr_id, diff, etc.
    """
    try:
        # Guard: one active submission at a time.
        # Three checks, applied in order. The UI layer should also enforce Guards 1 and 2,
        # but the backend enforces all three defensively.

        # Guard 2: game ID uniqueness — blocks any two submissions (same or different people)
        # from claiming the same Tower ID concurrently.
        existing_by_id = get_pending_submission_for_player_id(player_id)
        if existing_by_id and existing_by_id.get("stem") != stem:
            logger.warning(
                "Blocking process_verification: Tower ID %s already has active submission %s",
                player_id,
                existing_by_id.get("stem"),
            )
            return {
                "status": "error",
                "reason": "active_submission_exists",
                "existing_stem": existing_by_id.get("stem"),
                "existing_status": existing_by_id.get("status"),
            }

        # Guard 1: same platform account — fast SQLite short-circuit before the Django lookup.
        existing_by_account = get_pending_submission(platform, account_id)
        if existing_by_account and existing_by_account.get("stem") != stem:
            logger.warning(
                "Blocking process_verification: account %s:%s already has active submission %s",
                platform,
                account_id,
                existing_by_account.get("stem"),
            )
            return {
                "status": "error",
                "reason": "active_submission_exists",
                "existing_stem": existing_by_account.get("stem"),
                "existing_status": existing_by_account.get("status"),
            }

        # Guard 3: KnownPlayer cross-platform — catches the same verified user submitting
        # via a different linked platform account (e.g. Discord + Reddit). Skipped for new
        # users (no KnownPlayer yet). Fails safe if the Django DB is unreachable.
        try:
            existing_by_player = get_pending_submission_for_known_player(platform, account_id)
            if existing_by_player and existing_by_player.get("stem") != stem:
                logger.warning(
                    "Blocking process_verification: KnownPlayer for %s:%s already has active submission %s via another platform",
                    platform,
                    account_id,
                    existing_by_player.get("stem"),
                )
                return {
                    "status": "error",
                    "reason": "active_submission_exists",
                    "existing_stem": existing_by_player.get("stem"),
                    "existing_status": existing_by_player.get("status"),
                }
        except Exception:
            logger.exception(
                "Guard 3 (KnownPlayer) lookup failed for %s:%s — failing safe, blocking submission %s",
                platform,
                account_id,
                stem,
            )
            return {
                "status": "error",
                "reason": "db_unavailable",
            }

        # Check if user already has this ID verified (for re-verification attempts)
        old_player_id = player_id if player_already_has_id(platform, account_id, player_id) else None

        # Enforce intent requirement for existing users.
        # New users (no existing Tower IDs → old_player_id is None) may proceed without intent.
        # Existing users MUST declare intent — omitting it is a protocol violation that could
        # allow bypassing the intent selection step via a compromised client.
        if old_player_id and not id_change_reason:
            logger.warning(
                "Blocking process_verification: existing user missing id_change_reason: %s:%s tower_id=%s stem=%s",
                platform,
                account_id,
                player_id,
                stem,
            )
            return {
                "status": "error",
                "reason": "intent_required",
                "message": "Existing users must declare intent (id_change_reason) before submitting verification.",
            }

        # NOTE: callers (web route, bot) must call create_submission() before process_verification().
        # The call was removed from here because INSERT OR IGNORE with AUTOINCREMENT burns the next
        # ID even when the row is ignored, causing every submission to have an odd ID.

        # Phase 1: Run OCR (pure function)
        ocr_result = run_ocr(image_path, player_id)

        # Phase 2: Get user context (pure function)
        user_context = get_user_context(platform, account_id, player_id, id_change_reason)

        # Phase 3: Determine initial status (pure function, implements Rules 1-3)
        status_decision = determine_initial_status(ocr_result, player_id, user_context)

        # Phase 4: Update submission with OCR results and status
        update_fields: dict = {
            "status": status_decision["status"],
        }
        if status_decision["ocr_player_id"] is not None:
            update_fields["ocr_player_id"] = status_decision["ocr_player_id"]
        if status_decision.get("version"):
            update_fields["version"] = status_decision["version"]
        if status_decision.get("build"):
            update_fields["build"] = status_decision["build"]

        # For exact match or OCR disabled scenarios, set verified_player_id
        # - auto_complete: so auto_complete_if_eligible() can proceed immediately
        # - mod_intent_review: OCR verified (or trusted when unavailable), ready for Intent Review
        if status_decision["return_to_caller"].get("status") in ("auto_complete", "mod_intent_review"):
            update_fields["verified_player_id"] = status_decision.get("ocr_player_id") or player_id

        # If OCR unavailable but advancing to Intent Review, update status accordingly
        if status_decision["status"] == "ocr_unavailable" and status_decision["return_to_caller"].get("status") == "mod_intent_review":
            update_fields["status"] = "mod_intent_review"

        # Set final_outcome for terminal states (rejected, failed)
        if status_decision["status"] in ("rejected", "failed"):
            update_fields["final_outcome"] = status_decision["status"]

        update_submission(stem, **update_fields)

        # Log event (use original status to track ocr_unavailable in audit trail)
        event_type = status_decision["status"]  # This preserves "ocr_unavailable" in event history
        event_data = {"type": event_type, "ts": int(time.time())}
        if "reason" in status_decision["return_to_caller"]:
            event_data["reason"] = status_decision["return_to_caller"]["reason"]
        if status_decision.get("ocr_player_id"):
            event_data["ocr_id"] = status_decision["ocr_player_id"]
        add_event(stem, event_data)

        # Notify Discord (and any other registered platforms) of the status change
        log_submission_update(stem)

        logger.info(
            "OCR complete for %s %s: status=%s ocr_id=%s version=%s",
            platform,
            account_id,
            status_decision["status"],
            status_decision.get("ocr_player_id"),
            status_decision.get("version"),
        )

        # Phase 5: Auto-complete if eligible (implements Rule 4)
        if status_decision["return_to_caller"].get("status") == "auto_complete":
            auto_result = auto_complete_if_eligible(stem)
            if auto_result["eligible"]:
                # Successfully auto-completed — notify Discord of the final state
                log_submission_update(stem)
                return {
                    "status": auto_result["status"],
                    "player_created": True,
                    "auto_complete": True,
                }
            else:
                # auto_complete_if_eligible returned ineligible — can happen legitimately
                # when a player exists but has no active platform links (relink escalation).
                logger.info(
                    "Auto-complete returned ineligible after OCR match: stem=%s status=%s message=%s",
                    stem,
                    auto_result["status"],
                    auto_result.get("message"),
                )
                return {"status": auto_result["status"], "reason": auto_result.get("message", "")}

        # Return status to caller (web/Discord)
        return status_decision["return_to_caller"]

    except Exception as exc:
        logger.exception("Verification processing failed for %s %s tower_id=%s", platform, account_id, player_id)
        update_submission(stem, status="failed", final_outcome="failed")
        add_event(stem, {"type": "failed", "ts": int(time.time()), "reason": "internal_error"})
        log_submission_update(stem)
        return {"status": "failed", "reason": "internal_error", "error": str(exc)}
