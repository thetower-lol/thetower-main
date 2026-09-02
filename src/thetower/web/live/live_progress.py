import datetime
import logging
from time import perf_counter

import pandas as pd
import plotly.express as px
import streamlit as st

from thetower.backend.tourney_results.shun_config import include_shun_enabled_for
from thetower.backend.tourney_results.sus_config import include_sus_enabled_for
from thetower.web.live.data_ops import (
    get_progress_data,
    get_reference_tourney_df,
    process_display_names,
    require_tournament_data,
)
from thetower.web.live.ui_components import render_data_status, setup_common_ui
from thetower.web.util import get_user_tz


@require_tournament_data
def live_progress():
    st.markdown("# Live Progress")
    logging.info("Starting live progress")
    t2_start = perf_counter()

    # Use common UI setup
    options, league, is_mobile = setup_common_ui()

    render_data_status(league, "live_progress")

    # Top-25 timeline + join times, expanded from the delta archive
    include_shun = include_shun_enabled_for("live_progress")
    include_sus = include_sus_enabled_for("live_progress")
    tdf, join_times, timestamps = get_progress_data(league, include_shun, include_sus)

    # Process display names for better visualization
    tdf = process_display_names(tdf)

    # Convert datetime column to user's local timezone for display
    user_tz = get_user_tz()
    _dt = pd.to_datetime(tdf["datetime"])
    if _dt.dt.tz is None:
        _dt = _dt.dt.tz_localize("UTC")
    tdf = tdf.copy()
    tdf["datetime"] = _dt.dt.tz_convert(user_tz).dt.tz_localize(None)

    time_fmt = "%H:%M" if getattr(st.session_state, "time_24h", True) else "%I:%M %p"

    # Create top 25 progress plot
    fig = px.line(tdf, x="datetime", y="wave", color="display_name", title="Top 25 Players: live score", markers=True, line_shape="linear")

    fig.update_traces(mode="lines+markers")
    fig.update_layout(
        xaxis_title="Time",
        yaxis_title="Wave",
        legend_title="Player Name",
        hovermode="closest",
        height=500,
        margin=dict(l=20, r=20, t=40, b=20),
        legend=dict(orientation="h" if is_mobile else "v"),
        xaxis_tickformat=time_fmt,
    )
    st.plotly_chart(fig, width="stretch")

    # Get reference data for fill-up calculation
    pdf = get_reference_tourney_df(league)

    if pdf.empty:
        st.info("No previous tournament results are available to compute fill-up progress yet.")
    else:
        reference_league = pdf["league"].iloc[0]
        if reference_league != league:
            st.caption(f"No previous {league} results — showing {reference_league} instead.")

        # Fill up progress calculation, as a percentage of last tournament's players
        # (absolute counts would leak total participation)
        reference_total = len(pdf.id)
        fill_ups = []
        for dt in timestamps:
            # Convert datetime to user's local timezone
            dt_aware = dt if dt.tzinfo is not None else dt.replace(tzinfo=datetime.timezone.utc)
            dt_local = dt_aware.astimezone(user_tz).replace(tzinfo=None)
            fillup = sum(1 for player_id in pdf.id if player_id in join_times and join_times[player_id] <= dt)
            fillup_pct = round(fillup / reference_total * 100, 1) if reference_total else 0.0
            fill_ups.append((dt_local, fillup_pct))

        # Create fill up progress plot
        fill_ups = pd.DataFrame(sorted(fill_ups), columns=["time", "fillup"])
        fig = px.line(fill_ups, x="time", y="fillup", title="Fill up progress", markers=True, line_shape="linear")
        fig.update_traces(mode="lines+markers", fill="tozeroy")
        fig.update_layout(
            xaxis_title="Time",
            yaxis_title="Fill up [% of last tournament's players]",
            hovermode="closest",
            height=400,
            margin=dict(l=20, r=20, t=40, b=20),
            xaxis_tickformat=time_fmt,
            yaxis_ticksuffix="%",
        )
        st.plotly_chart(fig, width="stretch")

    # Log execution time
    t2_stop = perf_counter()
    logging.info(f"Full live_progress for {league} took {t2_stop - t2_start}")


live_progress()
