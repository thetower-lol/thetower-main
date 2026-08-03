"""Upload completed raw tar archives to Cloudflare R2.

Scans all {league}_raw/ directories for *.tar files and uploads any not yet
present in R2 with SHA-256 integrity verification.  Local source data — the
staging snapshots bundled in the tar, then the tar itself — is deleted only
once the R2 copy is confirmed to match the local tar byte-for-byte AND the
delta archive for that tourney exists.  Every tar on disk is re-checked every
run, so a tar whose cleanup was skipped (missing archive, crash, transient
error) is retried on the next run rather than stranded.

R2 key layout:  tar/{league}/{filename}
    e.g.        tar/champion/2025-01-15_raw.tar

Bucket lock (configured in Cloudflare dashboard, not here):
    Prefix tar/ → Indefinite retention

Set NO_DELETE to upload without deleting any local files.
"""

import hashlib
import logging
import os
import tarfile
from pathlib import Path

from botocore.exceptions import ClientError

from thetower.backend.backup.backup_log import log_run_summary, log_tar_delete_skipped, log_tar_error, log_tar_upload
from thetower.backend.backup.r2_client import get_r2_bucket, get_r2_client
from thetower.backend.env_config import get_csv_data
from thetower.backend.tourney_results.archive_utils import get_raw_path
from thetower.backend.tourney_results.constants import leagues

logger = logging.getLogger(__name__)


def _sha256_file(path: Path) -> str:
    """Compute the SHA-256 hex digest of a file in streaming chunks."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _r2_key(league: str, filename: str) -> str:
    return f"tar/{league}/{filename}"


def _head_object(client, bucket: str, key: str) -> dict | None:
    """Return the head_object response for key, or None if it does not exist."""
    try:
        return client.head_object(Bucket=bucket, Key=key)
    except ClientError as e:
        if e.response["Error"]["Code"] in ("404", "NoSuchKey"):
            return None
        raise


def _delete_staging_snapshots(tar_path: Path, league: str, live_base: Path) -> int:
    """Delete staging snapshots that are byte-identical members of a backed-up tar.

    Raises ValueError on any discrepancy (unexpected member, content mismatch)
    so the caller keeps all local files for inspection instead of deleting.
    Returns the number of snapshot files removed.
    """
    staging_dir = live_base / "current_tourney" / league
    if not staging_dir.exists():
        return 0

    removed = 0
    with tarfile.open(tar_path, "r") as tf:
        for member in tf.getmembers():
            if not member.isfile() or Path(member.name).name != member.name:
                raise ValueError(f"Unexpected member {member.name!r} in {tar_path.name}")
            snap = staging_dir / member.name
            if not snap.exists():
                continue
            extracted = tf.extractfile(member)
            if extracted is None or extracted.read() != snap.read_bytes():
                raise ValueError(f"Staging snapshot {member.name} differs from its copy in {tar_path.name}")
            snap.unlink()
            removed += 1
    return removed


def backup_new_tars() -> dict:
    """Scan all league raw directories; upload tars to R2 and clean up local copies.

    For every local tar (whether uploaded this run or found already in R2), the
    local staging snapshots and the tar are deleted only after the R2 copy's
    size and SHA-256 match the local file and the tourney's delta archive exists.

    Returns a stats dict: checked, uploaded, skipped, deleted, snapshots_deleted,
    delete_skipped, errors.
    """
    client = get_r2_client()
    bucket = get_r2_bucket()
    live_base = Path(get_csv_data())
    no_delete = bool(os.environ.get("NO_DELETE"))
    stats = {"checked": 0, "uploaded": 0, "skipped": 0, "deleted": 0, "snapshots_deleted": 0, "delete_skipped": 0, "errors": 0}

    for league in leagues:
        raw_dir = get_raw_path(league, live_base)
        if not raw_dir.exists():
            continue

        for tar_path in sorted(raw_dir.glob("*.tar")):
            stats["checked"] += 1
            key = _r2_key(league, tar_path.name)

            try:
                size = tar_path.stat().st_size
                sha256 = _sha256_file(tar_path)

                head = _head_object(client, bucket, key)
                uploaded = head is None
                if uploaded:
                    logger.info(f"Uploading {league}/{tar_path.name} ({size:,} bytes)...")
                    client.upload_file(
                        str(tar_path),
                        bucket,
                        key,
                        ExtraArgs={
                            "Metadata": {
                                "sha256": sha256,
                                "original_size": str(size),
                                "league": league,
                            }
                        },
                    )
                    head = client.head_object(Bucket=bucket, Key=key)
                else:
                    logger.debug(f"Already in R2: {key}")

                # No local file is deleted unless the R2 copy matches the local tar exactly
                remote_size = head["ContentLength"]
                remote_sha256 = head.get("Metadata", {}).get("sha256", "")
                if remote_size != size:
                    raise ValueError(f"Size mismatch between R2 and local for {key}: R2 {remote_size}, local {size}")
                if remote_sha256 != sha256:
                    raise ValueError(f"SHA-256 mismatch between R2 and local for {key}: R2 {remote_sha256}, local {sha256}")

                if uploaded:
                    logger.info(f"Verified: {key}")
                    stats["uploaded"] += 1
                    log_tar_upload(league, tar_path.name, size, sha256)
                else:
                    stats["skipped"] += 1

                # Ensure the archive.csv.gz has been generated before removing any local data
                date_prefix = tar_path.stem.replace("_raw", "")  # e.g. "2025-01-15"
                archive_path = live_base / f"{league}_live" / f"{date_prefix}_archive.csv.gz"
                if not archive_path.exists():
                    logger.warning(f"Archive not yet generated for {league}/{date_prefix} — keeping local tar and snapshots")
                    log_tar_delete_skipped(league, tar_path.name, "archive not yet generated")
                    stats["delete_skipped"] += 1
                    continue

                if no_delete:
                    logger.info(f"NO_DELETE is set — keeping local tar and snapshots for {league}/{tar_path.name}")
                    stats["delete_skipped"] += 1
                    continue

                # Snapshots first, tar last: a crash in between leaves the tar in
                # place, and the next run finishes the cleanup.
                removed = _delete_staging_snapshots(tar_path, league, live_base)
                if removed:
                    logger.info(f"Deleted {removed} staging snapshots covered by {league}/{tar_path.name}")
                    stats["snapshots_deleted"] += removed

                tar_path.unlink()
                logger.info(f"Deleted local tar: {tar_path}")
                stats["deleted"] += 1

            except Exception as exc:
                logger.exception(f"Failed to backup {league}/{tar_path.name}")
                log_tar_error(league, tar_path.name, str(exc))
                stats["errors"] += 1

    log_run_summary("tar", stats)
    return stats
