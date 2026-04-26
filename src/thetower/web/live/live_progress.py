import datetime
import logging
from time import perf_counter

import pandas as pd
import plotly.express as px
import streamlit as st

from thetower.backend.tourney_results.constants import champ
from thetower.backend.tourney_results.data import get_tourneys
from thetower.backend.tourney_results.models import TourneyResult
from thetower.backend.tourney_results.shun_config import include_shun_enabled_for
from thetower.web.live.data_ops import (
    get_processed_data,
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

    # Get processed data
    include_shun = include_shun_enabled_for("live_progress")
    df, tdf, ldf, first_moment, last_moment = get_processed_data(league, include_shun)

    # Process display names for better visualization
    tdf = process_display_names(tdf)

    # Convert datetime column to user's local timezone for display
    user_tz = get_user_tz()
    _dt = pd.to_datetime(tdf["datetime"])
    if _dt.dt.tz is None:
        _dt = _dt.dt.tz_localize("UTC")
    tdf = tdf.copy()
    tdf["datetime"] = _dt.dt.tz_convert(user_tz).dt.tz_localize(None)

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
    )
    st.plotly_chart(fig, width="stretch")

    # Get reference data for fill-up calculation
    qs = TourneyResult.objects.filter(league=league, public=True).order_by("-date")
    if not qs:
        qs = TourneyResult.objects.filter(league=champ, public=True).order_by("-date")
    tourney = qs[0]
    pdf = get_tourneys([tourney])

    # Fill up progress calculation
    fill_ups = []
    for dt, sdf in df.groupby("datetime"):
        joined_ids = set(sdf.player_id.unique())
        # Convert datetime to user's local timezone
        dt_aware = dt if dt.tzinfo is not None else dt.replace(tzinfo=datetime.timezone.utc)
        dt_local = dt_aware.astimezone(user_tz).replace(tzinfo=None)
        fillup = sum([player_id in joined_ids for player_id in pdf.id])
        fill_ups.append((dt_local, fillup))

    # Create fill up progress plot
    fill_ups = pd.DataFrame(sorted(fill_ups), columns=["time", "fillup"])
    fig = px.line(fill_ups, x="time", y="fillup", title="Fill up progress", markers=True, line_shape="linear")
    fig.update_traces(mode="lines+markers", fill="tozeroy")
    fig.update_layout(
        xaxis_title="Time",
        yaxis_title="Fill up [players]",
        hovermode="closest",
        height=400,
        margin=dict(l=20, r=20, t=40, b=20),
    )
    st.plotly_chart(fig, width="stretch")

    # Log execution time
    t2_stop = perf_counter()
    logging.info(f"Full live_progress for {league} took {t2_stop - t2_start}")


live_progress()
