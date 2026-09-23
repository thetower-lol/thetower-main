import logging
from time import perf_counter
from typing import Callable

import pandas as pd
import streamlit as st
from pandas.io.formats.style import Styler

from thetower.backend.tourney_results.formatting import format_wave
from thetower.backend.tourney_results.results_config import get_results_limit
from thetower.backend.tourney_results.shun_config import include_shun_enabled_for
from thetower.backend.tourney_results.sus_config import include_sus_enabled_for
from thetower.backend.tourney_results.tourney_utils import get_tourney_state
from thetower.web.live.data_ops import (
    get_live_standings,
    get_reference_tourney_df,
    require_tournament_data,
    tie_positions,
)
from thetower.web.live.ui_components import render_data_status, setup_common_ui


@require_tournament_data
def live_results():
    st.markdown("# Live Results")
    logging.info("Starting live results")
    t2_start = perf_counter()

    # Use common UI setup
    options, league, is_mobile = setup_common_ui()

    render_data_status(league, "live_results")

    # Latest + prior checkpoint standings, read directly from the two newest snapshots
    include_shun = include_shun_enabled_for("live_results")
    include_sus = include_sus_enabled_for("live_results")
    ldf, prior_snapshot_df = get_live_standings(league, include_shun, include_sus)

    # Get reference data for joined calculation
    pdf = get_reference_tourney_df(league)

    # Compute position deltas vs. the prior checkpoint snapshot
    prior_positions: dict[str, int] = {}
    prior_waves: dict[str, float] = {}
    prior_joined_ids: set[str] = set()
    if not prior_snapshot_df.empty:
        prior_df = prior_snapshot_df.sort_values("wave", ascending=False).reset_index(drop=True)
        prior_df["_pos"] = tie_positions(prior_df["wave"])
        prior_positions = dict(zip(prior_df["player_id"], prior_df["_pos"]))
        prior_waves = dict(zip(prior_df["player_id"], prior_df["wave"]))
        prior_joined_ids = set(prior_df["player_id"])

    def _rank_formatter(delta: int | None) -> Callable[[int], str]:
        if delta is None:
            return lambda pos: f"{pos} 🆕"
        if delta > 0:
            return lambda pos: f"{pos} ↑{delta}"
        return lambda pos: f"{pos} ↓{abs(delta)}"

    def _wave_formatter(delta: float) -> Callable[[float], str]:
        sign = "+" if delta > 0 else ""
        return lambda wave: f"{format_wave(wave)} ({sign}{format_wave(delta)})"

    def _format_deltas(styler: Styler, column: str, deltas: dict, make_formatter: Callable) -> Styler:
        # Deltas are rendered as display text only, so the underlying column stays numeric and sorts numerically.
        # One format call per distinct delta keeps this cheap on a 5000-row table.
        rows_by_delta: dict = {}
        for label, delta in deltas.items():
            rows_by_delta.setdefault(delta, []).append(label)
        for delta, rows in rows_by_delta.items():
            styler = styler.format(make_formatter(delta), subset=pd.IndexSlice[rows, [column]])
        return styler

    tourney_active = get_tourney_state().is_active

    ldf_display = ldf.copy()
    ldf_display.insert(0, "#", ldf_display.index)  # preserve rank as a column before resetting index
    ldf_display = ldf_display.reset_index(drop=True)

    cols = st.columns([3, 2] if not is_mobile else [1], gap="large")

    with cols[0]:
        st.write("Current result (ordered)")
        show_player_ids = st.toggle("Show player IDs", value=False, key=f"show_player_ids_{league}")
        display_cols = ["#", "name", "real_name", "wave"]
        if show_player_ids:
            display_cols = display_cols + ["player_id"]
        # TODO: ?results=full is an unpublished workaround for authorized users to see the complete
        # table. A guessable query param leaks total participation — replace with something more
        # secure (e.g. gate behind HIDDEN_FEATURES or real auth).
        results_full = st.query_params.get("results") == "full"
        row_limit = None if results_full else min(get_results_limit(league), 5000)
        limited_df = ldf_display[:row_limit]
        if tourney_active:
            active_only = st.toggle("Active players only", key=f"active_only_{league}", help="Players whose wave improved since the last checkpoint")
            if active_only:
                active_ids = {pid for pid, wave in zip(ldf["player_id"], ldf["wave"]) if pid not in prior_waves or wave > prior_waves[pid]}
                limited_df = limited_df[limited_df["player_id"].isin(active_ids)]
        display_df = limited_df[display_cols].style.format(format_wave, subset=["wave"])
        if tourney_active:
            rank_deltas: dict = {}  # row label -> places moved (positive = up), None = new since last checkpoint
            wave_deltas: dict = {}  # row label -> waves gained since last checkpoint
            for label, pos, wave, pid in zip(limited_df.index, limited_df["#"], limited_df["wave"], limited_df["player_id"]):
                if pid not in prior_positions:
                    rank_deltas[label] = None
                elif prior_positions[pid] != pos:
                    rank_deltas[label] = prior_positions[pid] - pos
                if pid in prior_waves and wave != prior_waves[pid]:
                    wave_deltas[label] = wave - prior_waves[pid]
            display_df = _format_deltas(display_df, "#", rank_deltas, _rank_formatter)
            display_df = _format_deltas(display_df, "wave", wave_deltas, _wave_formatter)

            def _delta_colors(column: pd.Series) -> list[str]:
                deltas = rank_deltas if column.name == "#" else wave_deltas
                colors = []
                for label in column.index:
                    delta = deltas.get(label)
                    colors.append("" if delta is None else "color: green" if delta > 0 else "color: red")
                return colors

            display_df = display_df.apply(_delta_colors, subset=["#", "wave"])
        st.dataframe(
            display_df,
            height=700,
            width="stretch",
            hide_index=True,
            column_config={
                "#": st.column_config.NumberColumn("#"),
                "name": st.column_config.TextColumn("name", width="small"),
                "real_name": st.column_config.TextColumn("real_name", width="small"),
            },
        )

    canvas = cols[0] if is_mobile else cols[1]

    if pdf.empty:
        canvas.info("No previous tournament results are available to compare against yet.")
    else:
        reference_league = pdf["league"].iloc[0]
        if reference_league != league:
            canvas.caption(f"No previous {league} results — showing {reference_league} instead.")

        joined_ids = set(ldf.player_id.unique())
        newly_joined_ids = joined_ids - prior_joined_ids

        def _join_status(player_id: str) -> str:
            if player_id in newly_joined_ids:
                return "🆕"
            if player_id in joined_ids:
                return "✓"
            return ""

        pdf["joined"] = [_join_status(pid) for pid in pdf.id]
        pdf = pdf.rename(columns={"wave": "wave_last"})

        pdf = pdf.sort_values("wave_last", ascending=False).reset_index(drop=True)
        pdf.index = tie_positions(pdf["wave_last"])

        topx = canvas.selectbox("top x", [1000, 500, 200, 100, 50, 25], key=f"topx_{league}")
        join_filter = canvas.selectbox("Filter", ["all players", "newly joined", "needing to get in"], key=f"join_filter_{league}")

        joined_sum = sum(1 for v in pdf["joined"][:topx] if v)
        joined_tot = len(pdf["joined"][:topx])
        not_joined_count = joined_tot - joined_sum

        top_x_df = pdf[:topx]

        if join_filter == "needing to get in":
            # Show count of players who need to join
            canvas.write(f"{not_joined_count} in the top {topx} need to join", unsafe_allow_html=True)
            # Filter to show only those who haven't joined from the top X
            display_df = top_x_df[top_x_df["joined"] == ""]
        elif join_filter == "newly joined":
            new_count = sum(1 for v in top_x_df["joined"] if v == "🆕")
            canvas.write(f"{new_count} in the top {topx} joined since the last refresh", unsafe_allow_html=True)
            # Filter to show only players who joined since the prior snapshot
            display_df = top_x_df[top_x_df["joined"] == "🆕"]
        else:
            # Show original message
            color = "green" if joined_sum / joined_tot >= 0.7 else "orange" if joined_sum / joined_tot >= 0.5 else "red"
            canvas.write(f"<font color='{color}'>{joined_sum}</font>/{topx} have already joined.", unsafe_allow_html=True)
            # Show all players in top X
            display_df = top_x_df

        final_df = display_df[["real_name", "wave_last", "joined"]].copy()
        final_df.insert(0, "#", final_df.index)  # the placement, before reset_index drops it
        final_df = final_df.reset_index(drop=True)
        canvas.dataframe(
            final_df[["#", "real_name", "wave_last", "joined"]],
            height=600,
            width="stretch",
            hide_index=True,
            column_config={"#": st.column_config.NumberColumn("#")},
        )

    # Log execution time
    t2_stop = perf_counter()
    logging.info(f"Full live_results for {league} took {t2_stop - t2_start}")


live_results()
