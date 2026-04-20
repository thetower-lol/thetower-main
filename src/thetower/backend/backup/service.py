"""Backup service entry point.

Runs as a persistent background service:
  - Tar backup:   on startup and daily at 08:00 UTC (uploads new tars to R2, deletes local copies)
  - DB backup:    on startup and daily at 08:00 UTC (VACUUM INTO → gzip → R2)
                  DB backup is idempotent: skips if today's R2 key already exists.
  - Bot DB backup: on startup and daily at 08:00 UTC (bot-config.sqlite3 → R2 under db/ with bot-config_ prefix)
                  Skipped if DISCORD_BOT_CONFIG env var is not set.

Environment variables required:
    R2_ACCOUNT_ID, R2_BUCKET_NAME
    R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY   (Edit+Read key for backup service)
    DJANGO_DATA     (path to /data/django — contains tower.sqlite3)
    CSV_DATA        (path to /data/results_cache — contains {league}_raw/)
    DISCORD_BOT_CONFIG  (optional — path to /data — contains bot-config.sqlite3)

Usage:
    python -m thetower.backend.backup.service
"""

import logging
import os
import time

import django
import schedule

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "thetower.backend.towerdb.settings")
django.setup()

from thetower.backend.backup.db_backup import backup_database, upload_pending_db_backups
from thetower.backend.backup.tar_backup import backup_new_tars
from thetower.backend.env_config import get_bot_config_data

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s UTC [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)


def run_tar_backup() -> None:
    logger.info("Tar backup: starting scan...")
    try:
        stats = backup_new_tars()
        logger.info(f"Tar backup complete: {stats}")
    except Exception:
        logger.exception("Tar backup run failed")


def run_db_backup() -> None:
    logger.info("DB backup: starting...")
    try:
        stats = backup_database(filename_prefix="django")
        logger.info(f"DB backup complete: {stats}")
    except Exception:
        logger.exception("DB backup run failed")


def run_bot_db_backup() -> None:
    bot_config = get_bot_config_data()
    if bot_config is None:
        logger.info("Bot DB backup: DISCORD_BOT_CONFIG not set, skipping")
        return
    bot_db_path = bot_config / "bot-config.sqlite3"
    if not bot_db_path.exists():
        logger.warning(f"Bot DB backup: {bot_db_path} not found, skipping")
        return
    logger.info(f"Bot DB backup: starting ({bot_db_path})...")
    try:
        stats = backup_database(db_path=bot_db_path, filename_prefix="bot-config")
        logger.info(f"Bot DB backup complete: {stats}")
    except Exception:
        logger.exception("Bot DB backup run failed")


def run_pending_db_uploads() -> None:
    logger.info("DB pending upload retry: scanning...")
    try:
        stats = upload_pending_db_backups(filename_prefix="django")
        if stats["checked"] > 0:
            logger.info(f"DB pending upload retry complete: {stats}")
    except Exception:
        logger.exception("DB pending upload retry failed")

    bot_config = get_bot_config_data()
    if bot_config is not None:
        try:
            stats = upload_pending_db_backups(filename_prefix="bot-config")
            if stats["checked"] > 0:
                logger.info(f"Bot DB pending upload retry complete: {stats}")
        except Exception:
            logger.exception("Bot DB pending upload retry failed")


def main() -> None:
    logger.info("Backup service starting")

    schedule.every().day.at("08:00").do(run_tar_backup)
    schedule.every().day.at("08:00").do(run_db_backup)
    schedule.every().day.at("08:00").do(run_bot_db_backup)
    schedule.every().hour.at(":15").do(run_pending_db_uploads)

    # Run both on startup; DB backup is idempotent and will skip if already done today
    run_tar_backup()
    run_db_backup()
    run_bot_db_backup()

    logger.info("Backup service running (tar + DB + bot-DB on startup and daily at 08:00 UTC; pending DB retry hourly at :15)")
    while True:
        schedule.run_pending()
        time.sleep(30)


if __name__ == "__main__":
    main()
