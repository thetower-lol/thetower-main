"""
Access log viewer for the Tower admin site.

Reads from DJANGO_DATA/web_access.log and its rotated hourly backups.
"""

import logging
from datetime import datetime, timedelta, timezone

import pandas as pd
import streamlit as st

from thetower.web.admin._access_log_common import all_paths_for_dates, catalog_files, get_log_dir, parse_files
from thetower.web.util import fmt_dt

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
st.title("🌐 Web Access Log Viewer")

try:
    log_dir = get_log_dir()
except Exception as e:
    st.error(f"Cannot locate log directory: {e}")
    st.stop()

catalog = catalog_files(log_dir)

if not catalog:
    st.info("No access log files found yet.")
    st.stop()

available_dates = list(catalog.keys())  # newest first

_now_utc = datetime.now(timezone.utc)

# --- Quick presets ---
_PRESETS = [
    ("1h", timedelta(hours=1)),
    ("6h", timedelta(hours=6)),
    ("12h", timedelta(hours=12)),
    ("24h", timedelta(hours=24)),
    ("3d", timedelta(days=3)),
    ("7d", timedelta(days=7)),
]

if "viewer_preset" not in st.session_state:
    st.session_state.viewer_preset = None

preset_cols = st.columns(len(_PRESETS) + 1)
for _i, (_label, _) in enumerate(_PRESETS):
    if preset_cols[_i].button(f"Last {_label}", width="stretch"):
        st.session_state.viewer_preset = _label
if preset_cols[-1].button("Clear", width="stretch"):
    st.session_state.viewer_preset = None

_cutoff: datetime | None = None

if st.session_state.viewer_preset:
    _delta = next(d for lbl, d in _PRESETS if lbl == st.session_state.viewer_preset)
    _cutoff = _now_utc - _delta
    _preset_dates = [d for d in available_dates if _cutoff.date() <= d]
    st.caption(f"⏱️ Showing last **{st.session_state.viewer_preset}** — click **Clear** to switch to manual range")
    selected_paths = all_paths_for_dates(catalog, _preset_dates)
else:
    # --- Time range controls ---
    with st.expander("Time Range", expanded=True):
        col_date, col_mode = st.columns([2, 1])

        selected_date = col_date.selectbox(
            "Date (UTC)",
            available_dates,
            format_func=lambda d: d.isoformat(),
            index=0,
        )

        view_mode = col_mode.radio("Show", ["Full day", "Hour range"], horizontal=True)

        hours_for_date = [h for h, _ in catalog[selected_date]]
        min_h, max_h = min(hours_for_date), max(hours_for_date)

        if view_mode == "Hour range":
            col_h1, col_h2 = st.columns(2)
            start_hour = col_h1.selectbox("From hour (UTC)", list(range(24)), index=min_h, format_func=lambda h: f"{h:02d}:00")
            end_hour = col_h2.selectbox("To hour (UTC)", list(range(24)), index=max_h, format_func=lambda h: f"{h:02d}:59")
            if end_hour < start_hour:
                st.warning("End hour is before start hour — showing full day instead.")
                start_hour, end_hour = 0, 23
        else:
            start_hour, end_hour = 0, 23

    selected_paths = [path for h, path in catalog[selected_date] if start_hour <= h <= end_hour]

# --- Text filters ---
with st.expander("Filters", expanded=True):
    col1, col2, col3, col4 = st.columns(4)
    ip_filter = col1.text_input("IP contains")
    path_filter = col2.text_input("Path contains")
    qs_filter = col3.text_input("Query string contains")
    ctx_filter = col4.text_input("Context contains")

# --- Load files for selected date + hour range ---
# selected_paths already set above
rows = parse_files(selected_paths)

# Apply exact timestamp cutoff when a preset is active
if _cutoff is not None:
    rows = [r for r in rows if datetime.strptime(r["dt"], "%Y-%m-%d %H:%M:%S UTC").replace(tzinfo=timezone.utc) >= _cutoff]

# --- Apply text filters ---
filtered = rows
if ip_filter:
    filtered = [r for r in filtered if ip_filter.lower() in r["ip"].lower()]
if path_filter:
    filtered = [r for r in filtered if path_filter.lower() in r["path"].lower()]
if qs_filter:
    filtered = [r for r in filtered if qs_filter.lower() in r["qs"].lower()]
if ctx_filter:
    filtered = [r for r in filtered if ctx_filter.lower() in r["ctx"].lower()]

# --- Summary ---
if st.session_state.viewer_preset:
    st.caption(f"Showing {len(filtered):,} of {len(rows):,} entries")
else:
    hour_label = f"{start_hour:02d}:00–{end_hour:02d}:59" if view_mode == "Hour range" else "all day"
    st.caption(f"Showing {len(filtered):,} of {len(rows):,} entries — " f"{selected_date.isoformat()} {hour_label} ({len(selected_paths)} file(s))")

# --- Display ---
if filtered:
    df = pd.DataFrame(filtered, columns=["dt", "site", "ip", "path", "qs", "ctx"])
    # Convert UTC timestamps to user's local timezone for display
    df["dt"] = pd.to_datetime(df["dt"], format="%Y-%m-%d %H:%M:%S UTC", utc=True).apply(lambda ts: fmt_dt(ts, fmt="%Y-%m-%d %H:%M:%S %Z"))
    df.columns = pd.Index(["Datetime", "Site", "IP", "Path", "Query String", "Context"])
    st.dataframe(df, width="stretch", hide_index=True)
else:
    st.info("No entries match the current filters.")
