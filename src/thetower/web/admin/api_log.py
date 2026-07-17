"""
API request log viewer for the Tower admin site.

Reads from DJANGO_DATA/api_requests.log, the file written by
thetower.backend.sus.api_views.log_api_request.
"""

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import streamlit as st
from django.conf import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------

st.title("🔑 API Request Log Viewer")


@st.cache_data(ttl=30)
def _load_log() -> pd.DataFrame:
    """Read and parse the TSV api_requests.log. Returns empty DataFrame on missing file."""
    log_path = Path(str(settings.DATA_DIR)) / "api_requests.log"
    if not log_path.exists():
        return pd.DataFrame(columns=["timestamp", "user", "player_id", "action", "status", "note"])

    lines = log_path.read_text(encoding="utf-8").splitlines()
    rows = []
    for line in lines:
        parts = line.rstrip("\n").split("\t")
        # Pad to 6 fields in case note is missing
        while len(parts) < 6:
            parts.append("")
        rows.append(parts[:6])

    df = pd.DataFrame(rows, columns=["timestamp", "user", "player_id", "action", "status", "note"])

    # Parse timestamps; unparseable rows get NaT
    df["timestamp"] = pd.to_datetime(df["timestamp"], format="%Y-%m-%dT%H:%M:%SZ", utc=True, errors="coerce")

    # Newest first
    df = df.sort_values("timestamp", ascending=False).reset_index(drop=True)
    return df


df = _load_log()

if df.empty:
    st.info("No API request log found yet (`$DJANGO_DATA/api_requests.log`).")
    st.stop()

# ---------------------------------------------------------------------------
# Summary metrics
# ---------------------------------------------------------------------------

total = len(df)
successes = (df["status"] == "SUCCESS").sum()
failures = (df["status"] == "FAIL").sum()

col1, col2, col3 = st.columns(3)
col1.metric("Total Requests", total)
col2.metric("Successes", successes)
col3.metric("Failures", failures)

st.divider()

# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------

filter_cols = st.columns([2, 2, 2, 2])

# Date range preset
_now = datetime.now(timezone.utc)
_PRESETS = [
    ("1h", timedelta(hours=1)),
    ("6h", timedelta(hours=6)),
    ("24h", timedelta(hours=24)),
    ("7d", timedelta(days=7)),
    ("30d", timedelta(days=30)),
    ("All", None),
]
preset_label = filter_cols[0].selectbox("Time window", [p[0] for p in _PRESETS], index=5)
cutoff_delta = next(d for lbl, d in _PRESETS if lbl == preset_label)
cutoff = (_now - cutoff_delta) if cutoff_delta else None

# Status filter
status_opts = ["All", "SUCCESS", "FAIL"]
status_filter = filter_cols[1].selectbox("Status", status_opts)

# Action filter
action_values = sorted(df["action"].dropna().unique().tolist())
action_opts = ["All"] + action_values
action_filter = filter_cols[2].selectbox("Action", action_opts)

# User filter
user_values = sorted(df["user"].dropna().unique().tolist())
user_opts = ["All"] + user_values
user_filter = filter_cols[3].selectbox("API Key User", user_opts)

# Apply filters
mask = pd.Series([True] * len(df))
if cutoff:
    mask &= df["timestamp"] >= cutoff
if status_filter != "All":
    mask &= df["status"] == status_filter
if action_filter != "All":
    mask &= df["action"] == action_filter
if user_filter != "All":
    mask &= df["user"] == user_filter

view = df[mask].copy()

st.caption(f"Showing {len(view):,} of {total:,} log entries")

if view.empty:
    st.info("No entries match the current filters.")
    st.stop()

# ---------------------------------------------------------------------------
# Display table
# ---------------------------------------------------------------------------

display = view.copy()
display["timestamp"] = display["timestamp"].dt.strftime("%Y-%m-%d %H:%M:%S UTC")

st.dataframe(
    display,
    use_container_width=True,
    hide_index=True,
    column_config={
        "timestamp": st.column_config.TextColumn("Timestamp", width="medium"),
        "user": st.column_config.TextColumn("API Key User", width="small"),
        "player_id": st.column_config.TextColumn("Player ID", width="medium"),
        "action": st.column_config.TextColumn("Action", width="small"),
        "status": st.column_config.TextColumn("Status", width="small"),
        "note": st.column_config.TextColumn("Note", width="large"),
    },
)

# ---------------------------------------------------------------------------
# Per-action breakdown
# ---------------------------------------------------------------------------

with st.expander("Breakdown by action"):
    breakdown = view.groupby(["action", "status"]).size().reset_index(name="count").sort_values(["action", "status"])
    st.dataframe(breakdown, use_container_width=True, hide_index=True)
