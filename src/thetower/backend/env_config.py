"""
Environment configuration utilities for thetower project.

This module provides centralized validation and access to required environment variables,
avoiding redundant validation code throughout the codebase.
"""

import os
from pathlib import Path


def get_csv_data() -> str:
    """
    Get and validate the CSV_DATA environment variable.

    Returns:
        str: Path to the CSV results cache directory (e.g., /data/results_cache)

    Raises:
        RuntimeError: If CSV_DATA is not set
    """
    csv_data = os.getenv("CSV_DATA")
    if not csv_data:
        raise RuntimeError(
            "CSV_DATA environment variable is not set. "
            "Please set it to the path where tournament CSV files should be stored (e.g., /data/results_cache)."
        )
    return csv_data


def get_django_data() -> Path:
    """
    Get and validate the DJANGO_DATA environment variable.

    Returns:
        Path: Path object to the Django data directory (e.g., /data/django)

    Raises:
        RuntimeError: If DJANGO_DATA is not set
    """
    django_data = os.getenv("DJANGO_DATA")
    if not django_data:
        raise RuntimeError(
            "DJANGO_DATA environment variable is not set. "
            "Please set it to the path where Django database and static files should be stored (e.g., /data/django)."
        )
    return Path(django_data)


def get_bot_config_data() -> Path | None:
    """
    Get the DISCORD_BOT_CONFIG environment variable as a Path.

    Returns:
        Path to the bot config directory, or None if not set.
    """
    val = os.getenv("DISCORD_BOT_CONFIG")
    return Path(val) if val else None


def get_r2_config() -> dict:
    """Get and validate all required Cloudflare R2 environment variables.

    Returns:
        dict with keys: account_id, bucket, access_key_id, secret_access_key

    Raises:
        RuntimeError: If any required R2 variable is not set.
    """
    required = {
        "account_id": "R2_ACCOUNT_ID",
        "bucket": "R2_BUCKET_NAME",
        "access_key_id": "R2_ACCESS_KEY_ID",
        "secret_access_key": "R2_SECRET_ACCESS_KEY",
    }
    config = {}
    missing = []
    for key, env_var in required.items():
        val = os.getenv(env_var)
        if val:
            config[key] = val
        else:
            missing.append(env_var)
    if missing:
        raise RuntimeError(f"Missing required R2 environment variables: {', '.join(missing)}")
    return config
