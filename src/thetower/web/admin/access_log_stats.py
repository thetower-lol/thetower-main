"""
Access log statistics for the Tower admin site.

Aggregates parsed log data into counts by page, IP, hour, and day.
Supports filtering by date range, IP, and path before aggregation.
"""

import logging
from datetime import datetime, timedelta, timezone

import pandas as pd
import plotly.express as px
import streamlit as st

from thetower.web.admin._access_log_common import (
    all_paths_for_dates,
    catalog_files,
    catalog_render_files,
    get_log_dir,
    parse_files,
    parse_render_files,
)
from thetower.web.util import get_user_tz

logger = logging.getLogger(__name__)

st.title("📊 Web Access Log Statistics")

# ---------------------------------------------------------------------------
# Load catalog
# ---------------------------------------------------------------------------
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
min_date, max_date = min(available_dates), max(available_dates)

_now_utc = datetime.now(timezone.utc)

# ---------------------------------------------------------------------------
# Quick presets
# ---------------------------------------------------------------------------
_PRESETS = [
    ("1h", timedelta(hours=1)),
    ("6h", timedelta(hours=6)),
    ("12h", timedelta(hours=12)),
    ("24h", timedelta(hours=24)),
    ("3d", timedelta(days=3)),
    ("7d", timedelta(days=7)),
]

if "stats_preset" not in st.session_state:
    st.session_state.stats_preset = None

preset_cols = st.columns(len(_PRESETS) + 1)
for _i, (_label, _) in enumerate(_PRESETS):
    if preset_cols[_i].button(f"Last {_label}", width="stretch"):
        st.session_state.stats_preset = _label
if preset_cols[-1].button("Clear", width="stretch"):
    st.session_state.stats_preset = None

_cutoff: datetime | None = None

if st.session_state.stats_preset:
    _delta = next(d for lbl, d in _PRESETS if lbl == st.session_state.stats_preset)
    _cutoff = _now_utc - _delta
    selected_dates = [d for d in available_dates if _cutoff.date() <= d <= max_date]
    start_date, end_date = _cutoff.date(), max_date
    start_hour, end_hour = 0, 23
    st.caption(f"⏱️ Showing last **{st.session_state.stats_preset}** — click **Clear** to switch to manual range")
    if not selected_dates:
        st.info("No log files in the selected range.")
        st.stop()
else:
    # ---------------------------------------------------------------------------
    # Date range selector
    # ---------------------------------------------------------------------------
    with st.expander("Date Range (UTC)", expanded=True):
        col1, col2 = st.columns(2)
        start_date = col1.date_input("From", value=max(min_date, max_date - timedelta(days=6)), min_value=min_date, max_value=max_date)
        end_date = col2.date_input("To", value=max_date, min_value=min_date, max_value=max_date)

        if end_date < start_date:
            st.warning("End date is before start date.")
            st.stop()

        col_mode, col_h1, col_h2 = st.columns([1, 1, 1])
        hour_mode = col_mode.radio("Hours", ["Full day", "Hour range"], horizontal=True)
        if hour_mode == "Hour range":
            start_hour = col_h1.selectbox("From hour (UTC)", list(range(24)), index=0, format_func=lambda h: f"{h:02d}:00")
            end_hour = col_h2.selectbox("To hour (UTC)", list(range(24)), index=23, format_func=lambda h: f"{h:02d}:59")
            if end_hour < start_hour:
                st.warning("End hour is before start hour — showing full day instead.")
                start_hour, end_hour = 0, 23
        else:
            start_hour, end_hour = 0, 23

    selected_dates = [d for d in available_dates if start_date <= d <= end_date]

    if not selected_dates:
        st.info("No log files in the selected date range.")
        st.stop()

# ---------------------------------------------------------------------------
# Load + parse all files in range
# ---------------------------------------------------------------------------
paths = all_paths_for_dates(catalog, selected_dates)

with st.spinner("Loading log data…"):
    rows = parse_files(paths)

if not rows:
    st.info("No log entries in the selected date range.")
    st.stop()

df = pd.DataFrame(rows)
for col in ["dt", "site", "ip", "path", "qs", "ctx", "render_id"]:
    if col not in df.columns:
        df[col] = "-"
df["ip"] = df["ip"].str.strip()
df["path"] = df["path"].str.strip()
df["render_id"] = df["render_id"].str.strip()

# Load render timing data for the same date range
render_catalog = catalog_render_files(log_dir)
render_paths = all_paths_for_dates(render_catalog, selected_dates)
with st.spinner("Loading render timing data…"):
    render_rows = parse_render_files(render_paths)
df_render = pd.DataFrame(render_rows) if render_rows else pd.DataFrame(columns=["render_id", "dt", "elapsed_ms"])

# Parse timestamps and convert to user's local timezone for display groupings
df["timestamp"] = pd.to_datetime(df["dt"], format="%Y-%m-%d %H:%M:%S UTC", utc=True, errors="coerce")
df = df.dropna(subset=["timestamp"])
_user_tz = get_user_tz()
df["local_ts"] = df["timestamp"].dt.tz_convert(_user_tz)
df["date"] = df["local_ts"].dt.date
df["hour"] = df["local_ts"].dt.floor("h")

# Apply quick preset cutoff or hour-of-day filter
if _cutoff is not None:
    df = df[df["timestamp"] >= _cutoff]
    if not df_render.empty:
        df_render["timestamp"] = pd.to_datetime(df_render["dt"], format="%Y-%m-%d %H:%M:%S UTC", utc=True, errors="coerce")
        df_render = df_render[df_render["timestamp"] >= _cutoff].drop(columns=["timestamp"])
elif start_hour != 0 or end_hour != 23:
    df = df[df["timestamp"].dt.hour.between(start_hour, end_hour)]
    if not df_render.empty:
        df_render["timestamp"] = pd.to_datetime(df_render["dt"], format="%Y-%m-%d %H:%M:%S UTC", utc=True, errors="coerce")
        df_render = df_render[df_render["timestamp"].dt.hour.between(start_hour, end_hour)].drop(columns=["timestamp"])

# ---------------------------------------------------------------------------
# Pre-filter controls
# ---------------------------------------------------------------------------
with st.expander("Filters", expanded=True):
    col_site, col_ip, col_path = st.columns(3)
    site_options = sorted(df["site"].unique().tolist())
    site_filter = col_site.multiselect("Site", site_options, default=site_options)
    ip_filter = col_ip.text_input("IP contains")
    path_filter = col_path.text_input("Path contains")

if site_filter:
    df = df[df["site"].isin(site_filter)]

if ip_filter:
    df = df[df["ip"].str.contains(ip_filter, case=False, na=False)]
if path_filter:
    df = df[df["path"].str.contains(path_filter, case=False, na=False)]

if df.empty:
    st.info("No entries match the current filters.")
    st.stop()

total = len(df)
hour_range_label = f" {start_hour:02d}:00–{end_hour:02d}:59 UTC" if (start_hour != 0 or end_hour != 23) else ""
st.caption(f"**{total:,} requests** across {len(selected_dates)} day(s) ({start_date.isoformat()} → {end_date.isoformat()}){hour_range_label}")

st.divider()

# ---------------------------------------------------------------------------
# Tabs
# ---------------------------------------------------------------------------
tab_time, tab_pages, tab_ips, tab_render = st.tabs(["📅 Over Time", "📄 By Page", "🌐 By IP", "⚡ Render Time"])

# ── Over Time ───────────────────────────────────────────────────────────────
with tab_time:
    granularity = st.radio("Granularity", ["Hourly", "Daily"], horizontal=True)

    if granularity == "Hourly":
        counts = df.groupby("hour").size().reset_index(name="Requests")
        counts.rename(columns={"hour": "Time"}, inplace=True)
        fig = px.bar(counts, x="Time", y="Requests", title="Requests per Hour")
    else:
        counts = df.groupby("date").size().reset_index(name="Requests")
        counts.rename(columns={"date": "Date"}, inplace=True)
        fig = px.bar(counts, x="Date", y="Requests", title="Requests per Day")

    st.plotly_chart(fig, width="stretch")

# ── By Page ─────────────────────────────────────────────────────────────────
with tab_pages:
    col_n, col_sort = st.columns([1, 1])
    top_n = col_n.slider("Show top N pages", min_value=5, max_value=50, value=20, step=5)
    sort_by = col_sort.radio("Sort by", ["Count", "Path"], horizontal=True)

    page_counts = df.groupby("path").size().reset_index(name="Requests")

    if sort_by == "Count":
        page_counts = page_counts.sort_values("Requests", ascending=False)
    else:
        page_counts = page_counts.sort_values("path")

    top_pages = page_counts.head(top_n)
    fig_pages = px.bar(
        top_pages.sort_values("Requests"),
        x="Requests",
        y="path",
        orientation="h",
        title=f"Top {top_n} Pages by Request Count",
        labels={"path": "Page"},
    )
    fig_pages.update_layout(height=max(300, top_n * 22))
    st.plotly_chart(fig_pages, width="stretch")

    st.dataframe(page_counts, width="stretch", hide_index=True)

# ── By IP ────────────────────────────────────────────────────────────────────
with tab_ips:
    col_n2, col_sort2 = st.columns([1, 1])
    top_n_ip = col_n2.slider("Show top N IPs", min_value=5, max_value=100, value=25, step=5)
    sort_by_ip = col_sort2.radio("Sort by", ["Count", "IP"], horizontal=True, key="ip_sort")

    ip_counts = df.groupby("ip").size().reset_index(name="Requests")

    if sort_by_ip == "Count":
        ip_counts = ip_counts.sort_values("Requests", ascending=False)
    else:
        ip_counts = ip_counts.sort_values("ip")

    top_ips = ip_counts.head(top_n_ip)
    fig_ips = px.bar(
        top_ips.sort_values("Requests"),
        x="Requests",
        y="ip",
        orientation="h",
        title=f"Top {top_n_ip} IPs by Request Count",
        labels={"ip": "IP"},
    )
    fig_ips.update_layout(height=max(300, top_n_ip * 22))
    st.plotly_chart(fig_ips, width="stretch")

    # Per-IP breakdown: click an IP to see which pages they hit
    st.subheader("Per-IP page breakdown")
    selected_ip = st.selectbox(
        "Select IP",
        options=ip_counts["ip"].tolist(),
        format_func=lambda x: f"{x}  ({ip_counts.loc[ip_counts['ip'] == x, 'Requests'].iloc[0]:,} reqs)",
    )
    if selected_ip:
        ip_df = df[df["ip"] == selected_ip].groupby("path").size().reset_index(name="Requests")
        ip_df = ip_df.sort_values("Requests", ascending=False)
        st.dataframe(ip_df, width="stretch", hide_index=True)

# ── Render Time ─────────────────────────────────────────────────────────────────────────────
with tab_render:
    if df_render.empty:
        st.info("No render timing data in the selected date range. Timing data is collected from version X onwards.")
    else:
        # Join render data to access log on render_id to get page paths
        df_access_ids = df[["render_id", "path"]].copy()
        df_access_ids = df_access_ids[df_access_ids["render_id"] != "-"]
        df_joined = df_render.merge(df_access_ids, on="render_id", how="left")
        df_joined["path"] = df_joined["path"].fillna("unknown")

        st.caption(f"**{len(df_render):,} render timing entries** in selected range")

        # — Avg render time by page —
        st.subheader("Average render time by page")
        col_n_r, col_sort_r = st.columns([1, 1])
        top_n_r = col_n_r.slider("Show top N pages", min_value=5, max_value=50, value=20, step=5, key="render_top_n")
        sort_r = col_sort_r.radio("Sort by", ["Avg ms (slowest first)", "Page"], horizontal=True, key="render_sort")

        page_stats = (
            df_joined.groupby("path")["elapsed_ms"]
            .agg(Renders="count", Avg="mean", p50="median", p95=lambda x: x.quantile(0.95), p99=lambda x: x.quantile(0.99))
            .reset_index()
            .rename(columns={"path": "Page"})
        )
        page_stats[["Avg", "p50", "p95", "p99"]] = page_stats[["Avg", "p50", "p95", "p99"]].round(0).astype(int)

        if sort_r == "Avg ms (slowest first)":
            top_pages_r = page_stats.sort_values("Avg", ascending=False).head(top_n_r)
        else:
            top_pages_r = page_stats.sort_values("Page").head(top_n_r)

        fig_render = px.bar(
            top_pages_r.sort_values("Avg"),
            x="Avg",
            y="Page",
            orientation="h",
            title=f"Top {top_n_r} Pages — Avg Render Time (ms)",
            labels={"Avg": "Avg ms"},
        )
        fig_render.update_layout(height=max(300, top_n_r * 22))
        st.plotly_chart(fig_render, width="stretch")

        # — Percentile table —
        st.subheader("Render time percentiles by page")
        st.dataframe(
            page_stats.sort_values("Avg", ascending=False).rename(columns={"Avg": "Avg ms", "p50": "p50 ms", "p95": "p95 ms", "p99": "p99 ms"}),
            width="stretch",
            hide_index=True,
        )
