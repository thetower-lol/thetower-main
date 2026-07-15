"""Memory Monitor admin page.

Displays RSS memory usage over time for all tower services, sampled every 30
minutes by the placement cache generator. Data is read from the rotating
memory.log file written by generate_placement_cache.py.
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


@st.cache_data(ttl=120)
def _load_memory_data() -> pd.DataFrame:
    """Load and parse all memory log files (current + rotated). Cached 2 minutes."""
    csv_data = Path(get_csv_data())
    log_files = sorted(glob(str(csv_data / "memory.log*")))

    if not log_files:
        return pd.DataFrame(columns=["timestamp", "service", "pid", "rss_mb"])

    dfs = []
    for path in log_files:
        try:
            df = pd.read_csv(
                path,
                header=None,
                names=["timestamp", "service", "pid", "rss_kb"],
                on_bad_lines="skip",
            )
            dfs.append(df)
        except Exception:
            logger.exception(f"Failed to read memory log {path}")

    if not dfs:
        return pd.DataFrame(columns=["timestamp", "service", "pid", "rss_mb"])

    combined = pd.concat(dfs, ignore_index=True)
    combined["timestamp"] = pd.to_datetime(combined["timestamp"], utc=True, errors="coerce")
    combined = combined.dropna(subset=["timestamp"])
    combined["rss_mb"] = combined["rss_kb"] / 1024
    combined["service_label"] = combined["service"].map(lambda s: _SERVICE_LABELS.get(s, s))
    return combined.sort_values("timestamp")


def render_memory_monitor() -> None:
    st.header("Memory Monitor")
    st.caption("RSS memory usage per service, sampled every 30 minutes by the placement cache generator.")

    df = _load_memory_data()

    if df.empty:
        st.info("No memory log data yet. Data will appear after the next placement cache run (at :01 or :31 past the hour).")
        return

    earliest = df["timestamp"].min()
    latest = df["timestamp"].max()
    span_days = max(1, (latest - earliest).days + 1)

    col1, col2 = st.columns([2, 1])
    lookback_days = col1.slider(
        "Show last N days",
        min_value=1,
        max_value=min(14, span_days),
        value=min(2, span_days),
    )
    cutoff = latest - pd.Timedelta(days=lookback_days)
    filtered = df[df["timestamp"] >= cutoff]

    selected_services = col2.multiselect(
        "Services",
        options=sorted(filtered["service_label"].unique()),
        default=sorted(filtered["service_label"].unique()),
    )
    filtered = filtered[filtered["service_label"].isin(selected_services)]

    if filtered.empty:
        st.warning("No data for the selected filters.")
        return

    fig = px.line(
        filtered,
        x="timestamp",
        y="rss_mb",
        color="service_label",
        labels={"timestamp": "Time (UTC)", "rss_mb": "RSS (MB)", "service_label": "Service"},
        title=f"Memory Usage — Last {lookback_days} Day{'s' if lookback_days > 1 else ''}",
    )
    fig.update_layout(
        legend_title_text="Service",
        hovermode="x unified",
        yaxis={"rangemode": "tozero"},
    )
    st.plotly_chart(fig, use_container_width=True)

    # Summary table: latest reading per service
    st.subheader("Latest Readings")
    latest_per_service = (
        filtered.sort_values("timestamp")
        .groupby("service_label")
        .last()
        .reset_index()[["service_label", "rss_mb", "timestamp"]]
        .rename(columns={"service_label": "Service", "rss_mb": "RSS (MB)", "timestamp": "Sampled At"})
        .sort_values("RSS (MB)", ascending=False)
    )
    latest_per_service["RSS (MB)"] = latest_per_service["RSS (MB)"].round(1)
    st.dataframe(latest_per_service, hide_index=True, use_container_width=True)


render_memory_monitor()
