"""Backup Status admin page.

Shows Cloudflare R2 backup health by querying the bucket live using the
read-only R2 credentials. Data is cached for 5 minutes. Also shows a
structured activity log from the backup service's JSONL event file.
"""

import logging
import os
from datetime import datetime, timezone

import streamlit as st

from thetower.web.util import fmt_dt

logger = logging.getLogger(__name__)

_PREFIXES = {
    "tar": {"label": "Raw Tars", "icon": "📦", "description": "Snapshot tar archives (indefinite lock)"},
    "db/daily": {"label": "DB Daily", "icon": "📅", "description": "Daily database backups (9-day expiry)"},
    "db/weekly": {"label": "DB Weekly", "icon": "🗓️", "description": "Weekly database backups (36-day expiry)"},
    "db/monthly": {"label": "DB Monthly", "icon": "📆", "description": "Monthly database backups (13-month expiry)"},
}


def _fmt_bytes(n: int) -> str:
    if n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.2f} GB"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f} MB"
    if n >= 1_000:
        return f"{n / 1_000:.1f} KB"
    return f"{n} B"


def _time_ago(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    diff = datetime.now(timezone.utc) - dt
    days = diff.days
    seconds = diff.seconds
    if days > 1:
        return f"{days} days ago"
    if days == 1:
        return "1 day ago"
    if seconds >= 3600:
        hours = seconds // 3600
        return f"{hours}h ago"
    if seconds >= 60:
        return f"{seconds // 60}m ago"
    return "just now"


def _credentials_available() -> bool:
    return bool(os.getenv("R2_ACCOUNT_ID") and os.getenv("R2_ACCESS_KEY_ID") and os.getenv("R2_SECRET_ACCESS_KEY") and os.getenv("R2_BUCKET_NAME"))


@st.cache_data(ttl=300)
def _fetch_prefix_stats(prefix: str) -> dict:
    """Fetch and aggregate object stats for a given R2 prefix. Cached 5 minutes."""
    try:
        from thetower.backend.backup.r2_client import get_r2_bucket, get_r2_client

        client = get_r2_client()
        bucket = get_r2_bucket()
        paginator = client.get_paginator("list_objects_v2")

        objects = []
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix + "/"):
            for obj in page.get("Contents", []):
                objects.append(obj)

        objects.sort(key=lambda o: o["LastModified"], reverse=True)
        total_size = sum(o["Size"] for o in objects)
        return {"count": len(objects), "total_size": total_size, "objects": objects, "error": None}

    except Exception as exc:
        logger.exception(f"Failed to fetch R2 stats for prefix {prefix!r}")
        return {"count": 0, "total_size": 0, "objects": [], "error": str(exc)}


def _check_backup_coverage() -> list[dict]:
    """Return a list of backup sources with their current status and any blocking reason."""
    from pathlib import Path

    from thetower.backend.env_config import get_bot_config_data, get_django_data

    sources = []

    # --- Django DB (tower.sqlite3) ---
    try:
        django_data = get_django_data()
        db_path = django_data / "tower.sqlite3"
        if db_path.exists():
            size = db_path.stat().st_size
            sources.append(
                {
                    "name": "Django DB (tower.sqlite3)",
                    "icon": "🗄️",
                    "status": "ok",
                    "detail": f"Found at `{db_path}` ({_fmt_bytes(size)})",
                    "r2_prefix": "db/daily",
                    "filename_prefix": "django",
                }
            )
        else:
            sources.append(
                {
                    "name": "Django DB (tower.sqlite3)",
                    "icon": "🗄️",
                    "status": "error",
                    "detail": f"File not found at `{db_path}` — backup service will skip this",
                    "r2_prefix": "db/daily",
                    "filename_prefix": "django",
                }
            )
    except RuntimeError as exc:
        sources.append(
            {
                "name": "Django DB (tower.sqlite3)",
                "icon": "🗄️",
                "status": "warning",
                "detail": f"DJANGO_DATA not set — {exc}",
                "r2_prefix": "db/daily",
                "filename_prefix": "django",
            }
        )

    # --- Bot config DB (bot-config.sqlite3) ---
    bot_config_dir = get_bot_config_data()
    if bot_config_dir is None:
        sources.append(
            {
                "name": "Bot Config DB (bot-config.sqlite3)",
                "icon": "🤖",
                "status": "warning",
                "detail": "DISCORD_BOT_CONFIG env var is not set — backup service will skip this database",
                "r2_prefix": "db/daily",
                "filename_prefix": "bot-config",
            }
        )
    else:
        bot_db_path = bot_config_dir / "bot-config.sqlite3"
        if bot_db_path.exists():
            size = bot_db_path.stat().st_size
            sources.append(
                {
                    "name": "Bot Config DB (bot-config.sqlite3)",
                    "icon": "🤖",
                    "status": "ok",
                    "detail": f"Found at `{bot_db_path}` ({_fmt_bytes(size)})",
                    "r2_prefix": "db/daily",
                    "filename_prefix": "bot-config",
                }
            )
        else:
            sources.append(
                {
                    "name": "Bot Config DB (bot-config.sqlite3)",
                    "icon": "🤖",
                    "status": "error",
                    "detail": f"DISCORD_BOT_CONFIG is set to `{bot_config_dir}` but `bot-config.sqlite3` was not found there — backup service will skip",
                    "r2_prefix": "db/daily",
                    "filename_prefix": "bot-config",
                }
            )

    # --- CSV / tar archives ---
    csv_data_val = os.getenv("CSV_DATA")
    if not csv_data_val:
        sources.append(
            {
                "name": "Tournament CSV tars",
                "icon": "📦",
                "status": "warning",
                "detail": "CSV_DATA env var is not set — tar backup will fail on startup",
                "r2_prefix": "tar",
                "filename_prefix": None,
            }
        )
    else:
        csv_path = Path(csv_data_val)
        if csv_path.exists():
            sources.append(
                {
                    "name": "Tournament CSV tars",
                    "icon": "📦",
                    "status": "ok",
                    "detail": f"CSV_DATA points to `{csv_path}`",
                    "r2_prefix": "tar",
                    "filename_prefix": None,
                }
            )
        else:
            sources.append(
                {
                    "name": "Tournament CSV tars",
                    "icon": "📦",
                    "status": "error",
                    "detail": f"CSV_DATA is set to `{csv_data_val}` but the directory does not exist",
                    "r2_prefix": "tar",
                    "filename_prefix": None,
                }
            )

    return sources


def _render_backup_coverage(all_r2_stats: dict[str, dict]) -> None:
    """Render the Backup Coverage section."""
    sources = _check_backup_coverage()

    status_icon = {"ok": "✅", "warning": "⚠️", "error": "❌"}

    for src in sources:
        icon = status_icon[src["status"]]
        st.markdown(f"**{icon} {src['icon']} {src['name']}**")
        st.caption(src["detail"])

        # If R2 credentials available, show last backup time from the fetched stats
        prefix = src["r2_prefix"]
        fp = src.get("filename_prefix")
        if prefix in all_r2_stats and not all_r2_stats[prefix].get("error"):
            objects = all_r2_stats[prefix]["objects"]
            # Filter to objects matching this source's filename prefix
            if fp:
                matching = [o for o in objects if o["Key"].split("/")[-1].startswith(fp + "_")]
            else:
                matching = objects
            if matching:
                last_obj = matching[0]  # already sorted newest-first
                st.caption(f"↳ Last R2 backup: {fmt_dt(last_obj['LastModified'], fmt='%Y-%m-%d %H:%M UTC')} ({_time_ago(last_obj['LastModified'])})")
            elif src["status"] == "ok":
                st.caption("↳ No backups found yet in R2 for this source")

        if src is not sources[-1]:
            st.markdown("")


def backup_status_page() -> None:
    st.title("☁️ Backup Status")

    col_refresh, col_updated = st.columns([1, 3])
    with col_refresh:
        if st.button("🔄 Refresh"):
            _fetch_prefix_stats.clear()
            st.rerun()
    with col_updated:
        st.caption(f"Data cached for 5 minutes · Last render: {fmt_dt(datetime.now(timezone.utc), fmt='%H:%M:%S %Z')}")

    if not _credentials_available():
        st.warning(
            "R2 credentials not configured — bucket stats unavailable. Set R2_ACCOUNT_ID, R2_BUCKET_NAME, R2_ACCESS_KEY_ID, and R2_SECRET_ACCESS_KEY."
        )
        all_stats: dict[str, dict] = {}
    else:
        # Summary metric row
        st.markdown("---")
        cols = st.columns(len(_PREFIXES))
        all_stats = {}

        for i, (prefix, meta) in enumerate(_PREFIXES.items()):
            stats = _fetch_prefix_stats(prefix)
            all_stats[prefix] = stats
            with cols[i]:
                if stats["error"]:
                    st.metric(f"{meta['icon']} {meta['label']}", "Error")
                    st.caption(stats["error"][:80])
                else:
                    last_obj = stats["objects"][0] if stats["objects"] else None
                    last_str = _time_ago(last_obj["LastModified"]) if last_obj else "never"
                    st.metric(
                        f"{meta['icon']} {meta['label']}",
                        f"{stats['count']} files",
                        _fmt_bytes(stats["total_size"]),
                    )
                    st.caption(f"Last: {last_str}")

        # Detailed per-prefix tables
        st.markdown("---")
        for prefix, meta in _PREFIXES.items():
            stats = all_stats[prefix]
            default_expanded = prefix in ("db/daily", "db/weekly")

            with st.expander(f"{meta['icon']} {meta['label']} — {meta['description']}", expanded=default_expanded):
                if stats["error"]:
                    st.error(f"Error: {stats['error']}")
                    continue

                if not stats["objects"]:
                    st.info("No backups found in this prefix.")
                    continue

                is_tar = prefix.startswith("tar")
                rows = []
                for obj in stats["objects"][:25]:
                    last_mod: datetime = obj["LastModified"]
                    parts = obj["Key"].split("/")
                    row: dict = {}
                    if is_tar:
                        row["League"] = parts[-2] if len(parts) >= 2 else ""
                    row["Filename"] = parts[-1]
                    row["Size"] = _fmt_bytes(obj["Size"])
                    row["Uploaded"] = fmt_dt(last_mod, fmt="%Y-%m-%d %H:%M")
                    row["Age"] = _time_ago(last_mod)
                    rows.append(row)

                st.dataframe(rows, use_container_width=True, hide_index=True)

                if stats["count"] > 25:
                    st.caption(f"Showing 25 of {stats['count']} objects · Total: {_fmt_bytes(stats['total_size'])}")
                else:
                    st.caption(f"Total: {_fmt_bytes(stats['total_size'])}")

    # Backup coverage section — shows each source with status and last-backup time
    st.markdown("---")
    st.subheader("🔍 Backup Coverage")
    _render_backup_coverage(all_stats)

    # Activity log section — always shown, no R2 credentials needed
    st.markdown("---")
    st.subheader("📋 Activity Log")
    _render_activity_log()


def _render_activity_log() -> None:
    """Render stats and recent events from the backup JSONL log."""
    try:
        from thetower.backend.backup.backup_log import read_events
    except ImportError:
        st.info("Backup log module not available.")
        return

    events = read_events(last_n=500)
    if not events:
        st.info("No backup activity logged yet.")
        return

    # Stats from events
    tar_uploads = [e for e in events if e.get("type") == "tar_upload"]
    db_uploads = [e for e in events if e.get("type") == "db_upload"]
    errors = [e for e in events if e.get("type") in ("tar_error", "db_error")]

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Tars Uploaded", len(tar_uploads))
    with col2:
        total_tar_bytes = sum(e.get("size", 0) for e in tar_uploads)
        st.metric("Data Uploaded", _fmt_bytes(total_tar_bytes))
    with col3:
        st.metric("DB Backups", len(db_uploads))
    with col4:
        st.metric("Errors", len(errors), delta=None if not errors else f"{len(errors)} recent", delta_color="inverse")

    last_summary = next((e for e in events if e.get("type") == "run_summary"), None)
    if last_summary:
        ts_raw = last_summary.get("ts", "")
        run_type = last_summary.get("run", "?")
        try:
            ts_dt = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
            ts_str = fmt_dt(ts_dt, fmt="%Y-%m-%d %H:%M:%S %Z")
        except (ValueError, AttributeError):
            ts_str = ts_raw[:19].replace("T", " ")
        st.caption(f"Last run: {run_type} · {ts_str}")

    # Recent events table
    with st.expander("Recent Events", expanded=True):
        type_filter = st.selectbox(
            "Filter by type", ["all", "tar_upload", "db_upload", "tar_error", "db_error", "tar_delete_skipped", "run_summary"], key="log_filter"
        )
        filtered = events if type_filter == "all" else [e for e in events if e.get("type") == type_filter]

        rows = []
        for e in filtered[:100]:
            ts_raw = e.get("ts", "")
            try:
                ts_dt = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
                ts = fmt_dt(ts_dt, fmt="%Y-%m-%d %H:%M:%S")
            except (ValueError, AttributeError):
                ts = ts_raw[:19].replace("T", " ")
            etype = e.get("type", "")
            if etype == "tar_upload":
                detail = f"{e.get('league')}/{e.get('file')} ({_fmt_bytes(e.get('size', 0))})"
            elif etype == "db_upload":
                detail = f"{e.get('key')} ({_fmt_bytes(e.get('size', 0))})"
            elif etype in ("tar_error", "db_error"):
                detail = f"{e.get('league', '')}{e.get('key', '')} — {e.get('error', '')}"
            elif etype == "tar_delete_skipped":
                detail = f"{e.get('league')}/{e.get('file')} — {e.get('reason', '')}"
            elif etype == "run_summary":
                detail = " · ".join(f"{k}={v}" for k, v in e.items() if k not in ("type", "ts", "run"))
            else:
                detail = str(e)
            rows.append({"Time": ts, "Type": etype, "Detail": detail})

        if rows:
            st.dataframe(rows, use_container_width=True, hide_index=True)
        else:
            st.info("No events match filter.")


backup_status_page()
