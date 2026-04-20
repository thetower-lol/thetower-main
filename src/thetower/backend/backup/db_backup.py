"""SQLite database backup to Cloudflare R2.

Uses VACUUM INTO to produce a clean, WAL-safe snapshot of the live database,
compresses it with gzip, saves it locally as a pending file, then uploads to
the appropriate generational prefixes.  If the upload fails, the pending file
is kept locally and retried hourly by upload_pending_db_backups().

R2 key layout (r2_prefix="db" for all databases):
    db/daily/django_YYYY-MM-DD.db.gz        — tower.sqlite3, every run
    db/daily/bot-config_YYYY-MM-DD.db.gz    — bot-config.sqlite3, every run
    db/weekly/django_YYYY-MM-DD.db.gz       — Sundays only
    db/monthly/django_YYYY-MM-DD.db.gz      — first day of month only

Local pending files (in DJANGO_DATA/db_backup_pending/):
    django_YYYY-MM-DD.db.gz          — tower.sqlite3
    bot-config_YYYY-MM-DD.db.gz      — bot-config.sqlite3

Lifecycle expiry (configured in Cloudflare dashboard, not here):
    db/daily/   → 9 days
    db/weekly/  → 36 days
    db/monthly/ → 13 months
"""

import gzip
import hashlib
import logging
import shutil
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from botocore.exceptions import ClientError

from thetower.backend.backup.backup_log import log_db_error, log_db_upload, log_run_summary
from thetower.backend.backup.r2_client import get_r2_bucket, get_r2_client
from thetower.backend.env_config import get_django_data

logger = logging.getLogger(__name__)


def _sha256_file(path: Path) -> str:
    """Compute SHA-256 hex digest of a file in streaming chunks."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _r2_keys_for_date(dt: datetime, r2_prefix: str = "db", filename_prefix: str | None = None) -> list[str]:
    """Return all R2 keys this backup should be uploaded to based on the UTC date."""
    stem = f"{filename_prefix}_{dt.strftime('%Y-%m-%d')}" if filename_prefix else dt.strftime("%Y-%m-%d")
    keys = [f"{r2_prefix}/daily/{stem}.db.gz"]
    if dt.weekday() == 6:  # Sunday
        keys.append(f"{r2_prefix}/weekly/{stem}.db.gz")
    if dt.day == 1:  # First of month
        keys.append(f"{r2_prefix}/monthly/{stem}.db.gz")
    return keys


def _object_exists(client, bucket: str, key: str) -> bool:
    try:
        client.head_object(Bucket=bucket, Key=key)
        return True
    except ClientError as e:
        if e.response["Error"]["Code"] in ("404", "NoSuchKey"):
            return False
        raise


def _get_pending_dir(override: Path | None = None) -> Path:
    if override is not None:
        return override
    return get_django_data() / "db_backup_pending"


def _pending_path_for_date(dt: datetime, pending_dir: Path | None = None, filename_prefix: str | None = None) -> Path:
    stem = f"{filename_prefix}_{dt.strftime('%Y-%m-%d')}" if filename_prefix else dt.strftime("%Y-%m-%d")
    return _get_pending_dir(pending_dir) / f"{stem}.db.gz"


def _upload_from_path(gz_path: Path, dt: datetime, client, bucket: str, r2_prefix: str = "db", filename_prefix: str | None = None) -> dict:
    """Upload a compressed DB file to all applicable R2 generational keys for dt.

    Returns stats: keys_uploaded, keys_skipped, compressed_size_bytes, errors.
    """
    r2_keys = _r2_keys_for_date(dt, r2_prefix, filename_prefix)
    stats = {"keys_uploaded": 0, "keys_skipped": 0, "compressed_size_bytes": 0, "errors": 0}

    compressed_size = gz_path.stat().st_size
    sha256 = _sha256_file(gz_path)
    stats["compressed_size_bytes"] = compressed_size

    for key in r2_keys:
        if _object_exists(client, bucket, key):
            logger.info(f"Already exists in R2: {key} — skipping")
            stats["keys_skipped"] += 1
            continue
        try:
            logger.info(f"Uploading {key} ({compressed_size:,} bytes)...")
            client.upload_file(
                str(gz_path),
                bucket,
                key,
                ExtraArgs={
                    "Metadata": {
                        "sha256": sha256,
                        "compressed_size": str(compressed_size),
                        "backup_date": dt.isoformat(),
                    }
                },
            )

            # Verify
            head = client.head_object(Bucket=bucket, Key=key)
            if head["ContentLength"] != compressed_size:
                raise ValueError(f"Size mismatch: expected {compressed_size}, got {head['ContentLength']}")
            if head.get("Metadata", {}).get("sha256") != sha256:
                raise ValueError("SHA-256 mismatch after upload")

            logger.info(f"Uploaded and verified: {key}")
            stats["keys_uploaded"] += 1
            log_db_upload(key, compressed_size, sha256)

        except Exception as exc:
            logger.exception(f"Failed to upload DB backup to {key}")
            log_db_error(key, str(exc))
            stats["errors"] += 1

    return stats


def upload_pending_db_backups(r2_prefix: str = "db", pending_dir: Path | None = None, filename_prefix: str | None = None) -> dict:
    """Scan for locally-saved DB backups that failed to upload and retry them.

    Returns overall stats: checked, uploaded, skipped, deleted, errors.
    """
    resolved_pending_dir = _get_pending_dir(pending_dir)
    overall: dict = {"checked": 0, "uploaded": 0, "skipped": 0, "deleted": 0, "errors": 0}

    if not resolved_pending_dir.exists():
        return overall

    client = get_r2_client()
    bucket = get_r2_bucket()

    glob_pattern = f"{filename_prefix}_*.db.gz" if filename_prefix else "*.db.gz"
    date_suffix = ".db.gz"
    date_prefix_strip = f"{filename_prefix}_" if filename_prefix else ""

    for gz_path in sorted(resolved_pending_dir.glob(glob_pattern)):
        overall["checked"] += 1
        try:
            date_str = gz_path.name.removeprefix(date_prefix_strip).removesuffix(date_suffix)
            dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            logger.warning(f"Unrecognised pending DB backup filename: {gz_path.name} — skipping")
            continue

        logger.info(f"Retrying pending DB backup: {gz_path.name}")
        stats = _upload_from_path(gz_path, dt, client, bucket, r2_prefix, filename_prefix)
        overall["uploaded"] += stats["keys_uploaded"]
        overall["skipped"] += stats["keys_skipped"]
        overall["errors"] += stats["errors"]

        if stats["errors"] == 0:
            gz_path.unlink(missing_ok=True)
            overall["deleted"] += 1
            logger.info(f"Pending DB backup {gz_path.name} fully uploaded, removed")
        else:
            logger.warning(f"Pending DB backup {gz_path.name} still has errors — will retry next hour")

    log_run_summary(f"{r2_prefix}_pending", overall)
    return overall


def backup_database(
    db_path: Path | None = None,
    r2_prefix: str = "db",
    pending_dir: Path | None = None,
    filename_prefix: str | None = None,
) -> dict:
    """Create a compressed SQLite snapshot, save it locally, then upload to R2.

    The compressed file is persisted locally before the upload attempt.  If the
    upload fails, the file stays there and upload_pending_db_backups() will retry
    it hourly.

    Args:
        db_path: Path to the SQLite file.  Defaults to DJANGO_DATA/tower.sqlite3.
        r2_prefix: R2 key prefix, e.g. "db" or "bot-db".  Determines the key layout.
        pending_dir: Directory to store locally-saved pending uploads.  Defaults to
            DJANGO_DATA/db_backup_pending/.
        filename_prefix: Optional prefix for the pending file name, e.g. "bot-config".
            Produces ``bot-config_YYYY-MM-DD.db.gz``.  None → ``YYYY-MM-DD.db.gz``.

    Returns a stats dict: keys_uploaded, keys_skipped, compressed_size_bytes, errors.
    """
    now = datetime.now(timezone.utc)
    client = get_r2_client()
    bucket = get_r2_bucket()
    stats = {"keys_uploaded": 0, "keys_skipped": 0, "compressed_size_bytes": 0, "errors": 0}

    if db_path is None:
        db_path = get_django_data() / "tower.sqlite3"
    resolved_pending_dir = _get_pending_dir(pending_dir)
    pending_path = _pending_path_for_date(now, resolved_pending_dir, filename_prefix)

    # If a pending file already exists for today, upload it directly (avoids re-vacuuming)
    if pending_path.exists():
        logger.info(f"Found existing pending DB backup: {pending_path.name} — attempting upload")
        upload_stats = _upload_from_path(pending_path, now, client, bucket, r2_prefix, filename_prefix)
        stats.update(upload_stats)
        if upload_stats["errors"] == 0:
            pending_path.unlink(missing_ok=True)
            logger.info("Pending DB backup successfully uploaded, removed local file")
        else:
            logger.warning(f"Upload had errors — keeping {pending_path.name} for retry")
        log_run_summary(r2_prefix, stats)
        return stats

    # Idempotent: if today's daily already exists in R2, nothing to do
    daily_stem = f"{filename_prefix}_{now.strftime('%Y-%m-%d')}" if filename_prefix else now.strftime("%Y-%m-%d")
    daily_key = f"{r2_prefix}/daily/{daily_stem}.db.gz"
    if _object_exists(client, bucket, daily_key):
        logger.info(f"Daily backup already present in R2: {daily_key} — skipping")
        stats["keys_skipped"] += len(_r2_keys_for_date(now, r2_prefix, filename_prefix))
        log_run_summary(r2_prefix, stats)
        return stats

    if not db_path.exists():
        logger.error(f"Database not found at {db_path}")
        stats["errors"] += 1
        log_run_summary(r2_prefix, stats)
        return stats

    resolved_pending_dir.mkdir(parents=True, exist_ok=True)
    # Use a temp dir on the same partition as the database to avoid filling tmpfs
    tmp_dir = Path(tempfile.mkdtemp(prefix="tower_dbbackup_", dir=db_path.parent))
    try:
        # Step 1: VACUUM INTO — WAL-safe clean copy
        vacuum_path = tmp_dir / f"tower_{now.strftime('%Y%m%d_%H%M%S')}.db"
        logger.info(f"VACUUM INTO {vacuum_path.name} from {db_path}...")
        conn = sqlite3.connect(str(db_path), timeout=60)
        conn.execute(f"VACUUM INTO '{vacuum_path}'")
        conn.close()
        logger.info(f"VACUUM complete: {vacuum_path.stat().st_size:,} bytes")

        # Step 2: gzip compress
        gz_tmp = tmp_dir / (vacuum_path.name + ".gz")
        logger.info(f"Compressing to {gz_tmp.name}...")
        with open(vacuum_path, "rb") as f_in, gzip.open(gz_tmp, "wb", compresslevel=6) as f_out:
            shutil.copyfileobj(f_in, f_out)
        vacuum_path.unlink()  # free space immediately

        # Step 3: move to persistent pending location before attempting upload
        shutil.move(str(gz_tmp), str(pending_path))
        logger.info(f"DB backup saved to pending: {pending_path} ({pending_path.stat().st_size:,} bytes)")

    except Exception:
        logger.exception("Failed to create DB backup")
        stats["errors"] += 1
        log_run_summary(r2_prefix, stats)
        return stats
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    # Step 4: upload from the now-persistent pending location
    stats["compressed_size_bytes"] = pending_path.stat().st_size
    upload_stats = _upload_from_path(pending_path, now, client, bucket, r2_prefix, filename_prefix)
    for k in ("keys_uploaded", "keys_skipped", "errors"):
        stats[k] += upload_stats[k]

    if stats["errors"] == 0:
        pending_path.unlink(missing_ok=True)
        logger.info("DB backup uploaded and verified, removed local pending file")
    else:
        logger.warning(f"Upload had errors — keeping {pending_path.name} for hourly retry")

    log_run_summary(r2_prefix, stats)
    return stats
