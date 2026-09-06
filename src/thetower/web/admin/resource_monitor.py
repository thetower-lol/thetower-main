"""Resource Monitor admin page.

Displays per-service RSS memory, CPU usage, and open file descriptors over
time, sampled every 30 minutes by the placement cache generator into the
rotating resources.log (timestamp,service,pid,rss_kb,cpu_seconds,num_fds,
num_sockets). CPU% is derived from deltas of the cumulative CPU seconds
between consecutive samples of the same pid, so it reads as average core
usage per interval (100 = one full core). Rows converted from the retired
memory.log carry -1 for cpu/fd values and are excluded from those charts.
"""

import logging
from glob import glob
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

from thetower.backend.env_config import get_csv_data

logger = logging.getLogger(__name__)

_SERVICE_LABELS: dict[str, str] = {
    "streamlit_public": "Public Site",
    "streamlit_hidden": "Hidden Site",
    "discord_bot": "Discord Bot",
    "web_platform": "Web Platform",
    "bot_ui": "Bot UI",
    "backup": "Backup Service",
    "placement_cache": "Placement Cache",
    "recalc_worker": "Recalc Worker",
    "zendesk_queue": "Zendesk Queue",
    "import_live": "Import Live",
    "import_results": "Import Results",
    "get_live_results": "Get Live Results",
    "get_results": "Get Results",
}

_RESOURCE_COLUMNS = ["timestamp", "service", "pid", "rss_kb", "cpu_seconds", "num_fds", "num_sockets"]


@st.cache_data(ttl=120)
def _load_resource_data() -> pd.DataFrame:
    """Load and parse all resources log files (current + rotated). Cached 2 minutes."""
    csv_data = Path(get_csv_data())
    log_files = sorted(glob(str(csv_data / "resources.log*")))

    if not log_files:
        return pd.DataFrame(columns=_RESOURCE_COLUMNS + ["rss_mb", "service_label"])

    dfs = []
    for path in log_files:
        try:
            df = pd.read_csv(
                path,
                header=None,
                names=_RESOURCE_COLUMNS,
                on_bad_lines="skip",
            )
            dfs.append(df)
        except Exception:
            logger.exception(f"Failed to read resources log {path}")

    if not dfs:
        return pd.DataFrame(columns=_RESOURCE_COLUMNS + ["rss_mb", "service_label"])

    combined = pd.concat(dfs, ignore_index=True)
    combined["timestamp"] = pd.to_datetime(combined["timestamp"], utc=True, errors="coerce")
    combined = combined.dropna(subset=["timestamp"])
    combined["rss_mb"] = combined["rss_kb"] / 1024
    combined["service_label"] = combined["service"].map(lambda s: _SERVICE_LABELS.get(s, s))
    return combined.sort_values("timestamp")


def _derive_cpu_pct(rdf: pd.DataFrame) -> pd.DataFrame:
    """Average CPU% per sampling interval, from cumulative CPU seconds.

    Deltas between consecutive samples of the same service divided by wall
    time give average core usage over the interval (100 = one full core).
    Deltas across a pid change (service restart) or counter reset are dropped.
    """
    rdf = rdf[rdf["cpu_seconds"] >= 0].sort_values("timestamp")
    parts = []
    for _, group in rdf.groupby("service"):
        group = group.copy()
        dt = group["timestamp"].diff().dt.total_seconds()
        dcpu = group["cpu_seconds"].diff()
        valid = group["pid"].eq(group["pid"].shift()) & (dcpu >= 0) & (dt > 0)
        group["cpu_pct"] = (dcpu / dt * 100).where(valid)
        parts.append(group.dropna(subset=["cpu_pct"]))
    if not parts:
        return pd.DataFrame(columns=list(rdf.columns) + ["cpu_pct"])
    return pd.concat(parts, ignore_index=True)


def _finish_figure(fig, cutoff: pd.Timestamp, latest: pd.Timestamp) -> None:
    """Shared layout, with the x-axis pinned to the selected window.

    Left to autorange, each chart spans only its own series. CPU and FD samples
    exist only from the sampler upgrade onward while converted memory.log rows
    reach further back, so widening the window visibly moved just the memory
    chart. Endpoints are passed tz-naive UTC to match how Plotly serializes the
    timestamp column.
    """
    pad = (latest - cutoff) * 0.02
    fig.update_xaxes(range=[(cutoff - pad).tz_convert(None), (latest + pad).tz_convert(None)])
    fig.update_layout(legend_title_text="Service", hovermode="x unified", yaxis={"rangemode": "tozero"})


def _series_start_note(label: str, series_df: pd.DataFrame, cutoff: pd.Timestamp) -> None:
    """Caption when a series begins after the window start, so the empty left part reads as 'no data yet'."""
    first = series_df["timestamp"].min()
    if pd.notna(first) and first > cutoff:
        st.caption(f"{label} sampling starts {first:%Y-%m-%d %H:%M} UTC; the window before that has no {label} data.")


def render_resource_monitor() -> None:
    st.header("Resource Monitor")
    st.caption(
        "Per-service RSS, CPU, and open-file usage, sampled every 30 minutes by the placement cache generator. "
        "For live FD counts against the process limit, see Service Status."
    )

    df = _load_resource_data()

    if df.empty:
        st.info("No resource log data yet. Data will appear after the next placement cache run (at :01 or :31 past the hour).")
        return

    earliest = df["timestamp"].min()
    latest = df["timestamp"].max()
    span_days = max(1, (latest - earliest).days + 1)

    col1, col2 = st.columns([2, 1])
    if span_days > 1:
        lookback_days = col1.slider(
            "Show last N days",
            min_value=1,
            max_value=min(14, span_days),
            value=min(2, span_days),
        )
    else:
        lookback_days = span_days
        col1.caption(f"Showing all {span_days} day(s) of data available.")
    cutoff = latest - pd.Timedelta(days=lookback_days)
    filtered = df[df["timestamp"] >= cutoff]
    window_label = f"Last {lookback_days} Day{'s' if lookback_days > 1 else ''}"

    selected_services = col2.multiselect(
        "Services",
        options=sorted(filtered["service_label"].unique()),
        default=sorted(filtered["service_label"].unique()),
    )
    filtered = filtered[filtered["service_label"].isin(selected_services)]

    if filtered.empty:
        st.warning("No data for the selected filters.")
        return

    # --- Memory ---
    st.subheader("Memory")
    fig = px.line(
        filtered,
        x="timestamp",
        y="rss_mb",
        color="service_label",
        labels={"timestamp": "Time (UTC)", "rss_mb": "RSS (MB)", "service_label": "Service"},
        title=f"Memory Usage — {window_label}",
    )
    _finish_figure(fig, cutoff, latest)
    st.plotly_chart(fig, use_container_width=True)

    # --- CPU ---
    st.subheader("CPU")
    cpu_df = _derive_cpu_pct(filtered)
    if cpu_df.empty:
        st.info("CPU data appears once the sampler has written two consecutive resources.log samples (deltas need pairs).")
    else:
        fig = px.line(
            cpu_df,
            x="timestamp",
            y="cpu_pct",
            color="service_label",
            labels={"timestamp": "Time (UTC)", "cpu_pct": "Avg CPU % (100 = one core)", "service_label": "Service"},
            title=f"Average CPU per Sampling Interval — {window_label}",
        )
        _finish_figure(fig, cutoff, latest)
        st.plotly_chart(fig, use_container_width=True)
        _series_start_note("CPU", cpu_df, cutoff)

    # --- File descriptors ---
    st.subheader("Open File Descriptors")
    fd_df = filtered[filtered["num_fds"] >= 0]
    if fd_df.empty:
        st.info("FD data appears after the first post-upgrade sampler run.")
    else:
        fig = px.line(
            fd_df,
            x="timestamp",
            y="num_fds",
            color="service_label",
            hover_data=["num_sockets"],
            labels={"timestamp": "Time (UTC)", "num_fds": "Open FDs", "num_sockets": "Sockets", "service_label": "Service"},
            title=f"Open File Descriptors — {window_label} (hover shows socket count)",
        )
        _finish_figure(fig, cutoff, latest)
        st.plotly_chart(fig, use_container_width=True)
        _series_start_note("FD", fd_df, cutoff)

    # --- Latest readings ---
    st.subheader("Latest Readings")
    table = (
        filtered.sort_values("timestamp")
        .groupby("service_label")
        .last()
        .reset_index()[["service_label", "rss_mb", "num_fds", "num_sockets", "timestamp"]]
        .rename(columns={"service_label": "Service", "rss_mb": "RSS (MB)", "num_fds": "FDs", "num_sockets": "Sockets", "timestamp": "Sampled At"})
    )
    table["RSS (MB)"] = table["RSS (MB)"].round(1)
    # Converted memory.log history carries -1 sentinels — show as blank, not negative
    table["FDs"] = table["FDs"].where(table["FDs"] >= 0)
    table["Sockets"] = table["Sockets"].where(table["Sockets"] >= 0)

    if not cpu_df.empty:
        clatest = (
            cpu_df.sort_values("timestamp")
            .groupby("service_label")["cpu_pct"]
            .last()
            .reset_index()
            .rename(columns={"service_label": "Service", "cpu_pct": "CPU %"})
        )
        clatest["CPU %"] = clatest["CPU %"].round(1)
        table = table.merge(clatest, on="Service", how="left")

    table = table.sort_values("RSS (MB)", ascending=False)
    st.dataframe(table, hide_index=True, use_container_width=True)


render_resource_monitor()
