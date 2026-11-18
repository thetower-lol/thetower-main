import logging
from pathlib import Path
from time import perf_counter

import pandas as pd
import plotly.express as px
import streamlit as st

from thetower.backend.tourney_results.constants import leagues as ALL_LEAGUES
from thetower.backend.tourney_results.formatting import format_wave, make_player_url
from thetower.backend.tourney_results.shun_config import include_shun_enabled_for
from thetower.backend.tourney_results.sus_config import include_sus_enabled_for
from thetower.web.historical.proximal_utils import get_proximal_players
from thetower.web.live.data_ops import (
    get_latest_bracket_filtered_df,
    get_peer_live_data,
    latest_snapshot_key,
    process_display_names,
    require_tournament_data,
    tie_positions,
)
from thetower.web.live.player_search import find_live_players, no_match_message, show_player_choices
from thetower.web.live.ui_components import render_data_status
from thetower.web.util import clear_other_search_boxes

logger = logging.getLogger(__name__)


@require_tournament_data
def peer_watch():
    st.markdown("# Peer Watch")
    t_start = perf_counter()

    # --- Sidebar: peer count slider ---
    with st.sidebar:
        peer_n = st.slider("Peers above/below", min_value=1, max_value=25, value=10, key="peer_watch_n")

    # --- Session state helpers ---
    def clear_player():
        st.query_params.clear()
        for key in ("pw_player_id", "pw_focal_name", "pw_league", "pw_date", "pw_player_search_term"):
            st.session_state.pop(key, None)
        if "player_id" in st.session_state:
            st.session_state.pop("player_id")

    # Resolve player_id: query param → session "player_id" (disambiguation select) → search inputs
    resolved_id: str | None = None

    # From query param ?player_id=...
    qp_id = st.query_params.get("player_id")
    if qp_id:
        resolved_id = qp_id.strip().upper()
        st.session_state["pw_player_id"] = resolved_id

    # From disambiguation button click
    selected_from_session = st.session_state.get("player_id")
    if selected_from_session and not resolved_id:
        resolved_id = selected_from_session
        st.session_state["pw_player_id"] = resolved_id
        st.session_state.pop("player_id")

    # From previously resolved state
    if not resolved_id:
        resolved_id = st.session_state.get("pw_player_id")

    # Show search inputs or "search again" button
    if resolved_id:
        st.button("Search for another player?", on_click=clear_player, key="pw_search_new")
    else:
        # Only one box holds text at a time: submitting one clears the other
        box_keys = ("pw_name_input", "pw_id_input")
        st.session_state.setdefault("pw_name_input", st.session_state.get("pw_player_search_term", ""))

        name_col, id_col = st.columns(2)
        name_input = name_col.text_input(
            "Search by Player Name", key="pw_name_input", on_change=clear_other_search_boxes, args=("pw_name_input", box_keys)
        )
        id_input = id_col.text_input("Or by Player ID", key="pw_id_input", on_change=clear_other_search_boxes, args=("pw_id_input", box_keys))

        name_term, id_term = name_input.strip(), id_input.strip()
        if name_term or id_term:
            search_by, term = ("name", name_term) if name_term else ("id", id_term)
            if search_by == "name":
                st.session_state["pw_player_search_term"] = term
            include_shun = include_shun_enabled_for("peer_watch")
            include_sus = include_sus_enabled_for("peer_watch")
            # Latest snapshot, bracket-filtered so partial brackets stay hidden while entry is open (anti-snipe)
            matches = find_live_players(term, search_by, lambda lg: get_latest_bracket_filtered_df(lg, include_shun, include_sus))
            if len(matches) > 1:
                show_player_choices(matches, "pw")
                return
            if matches:
                resolved_id = matches[0].player_id
            elif search_by == "id":
                # Not in the current tourney: peers come from the last completed one, so look the ID up there
                resolved_id = id_term.upper()
            else:
                st.error(no_match_message(term, search_by))
                return
            st.session_state["pw_player_id"] = resolved_id

    if not resolved_id:
        st.info("Enter a player name or ID to find their peer group.")
        return

    # --- Get proximal peers from last completed tournament ---
    peer_ids, focal_name, focal_league, focal_date = get_proximal_players(resolved_id, peer_n)

    if not peer_ids:
        if focal_name:
            # Found, but placed past the public results cap: no peer group on the public site
            st.info(
                f"Peer Watch is only available to players who placed within the published results. **{focal_name}** did not in {focal_league} on {focal_date}."
            )
        else:
            st.error(f"No historical tournament data found for player ID: `{resolved_id}`")
        st.session_state.pop("pw_player_id", None)
        return

    st.caption(f"Showing {peer_n} peers above/below **{focal_name}** — {focal_league} · {focal_date} (last completed tournament)")

    # --- Load live data for the peer group only (expanded from the delta archives) ---
    peer_live_df = get_peer_live_data(tuple(peer_ids), include_shun_enabled_for("peer_watch"), include_sus_enabled_for("peer_watch"))

    # Distinguish "no tourney data at all" from "peers not participating"
    if peer_live_df.empty and all(latest_snapshot_key(lg) == "no-data" for lg in ALL_LEAGUES):
        st.error("No live tournament data available.")
        return

    render_data_status(focal_league, "peer_watch")

    if peer_live_df.empty:
        st.warning(f"None of the {len(peer_ids)} peers are currently participating in the live tournament.")
        return

    found_ids = set(peer_live_df["player_id"].unique())
    missing_ids = [pid for pid in peer_ids if pid not in found_ids]

    st.info(
        f"**{len(found_ids)}** of {len(peer_ids)} peers found in live tournament"
        + (f" · {len(missing_ids)} not participating" if missing_ids else "")
    )

    # --- Line chart ---
    peer_live_df["datetime"] = pd.to_datetime(peer_live_df["datetime"])
    peer_live_df = process_display_names(peer_live_df)

    fig = px.line(
        peer_live_df,
        x="datetime",
        y="wave",
        color="display_name",
        title=f"Peer Watch — {focal_name}'s peer group",
        markers=True,
        line_shape="linear",
    )
    fig.update_traces(mode="lines+markers", hovertemplate="%{y}")
    fig.update_layout(xaxis_title="Time", yaxis_title="Wave", legend_title="Player", hovermode="closest")
    st.plotly_chart(fig, width="stretch")

    # --- Current standings table ---
    last_moment = peer_live_df["datetime"].max()
    latest_df = peer_live_df[peer_live_df["datetime"] == last_moment].copy()
    latest_df = latest_df.sort_values("wave", ascending=False).reset_index(drop=True)
    latest_df["#"] = tie_positions(latest_df["wave"])
    latest_df = process_display_names(latest_df)

    display_cols = ["#"] + [c for c in ["player_id", "display_name", "wave", "league", "datetime"] if c in latest_df.columns]
    display_df = latest_df.loc[:, display_cols]

    css_path = Path(__file__).parent.parent / "static" / "styles" / "style.css"
    # The index is hidden because the "#" column carries the placement now
    styled = display_df.style.format(make_player_url, subset=["player_id"]).format(format_wave, subset=["wave"]).hide(axis="index")
    st.write(styled.to_html(escape=False), unsafe_allow_html=True)
    with open(css_path, "r") as infile:
        st.write(f"<style>{infile.read()}</style>", unsafe_allow_html=True)

    logger.info(f"peer_watch for {resolved_id} (n={peer_n}) took {perf_counter() - t_start:.3f}s")


peer_watch()
