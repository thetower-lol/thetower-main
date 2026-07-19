import logging
from pathlib import Path
from time import perf_counter

import pandas as pd
import plotly.express as px
import streamlit as st

from thetower.backend.tourney_results.constants import leagues as ALL_LEAGUES
from thetower.backend.tourney_results.formatting import make_player_url
from thetower.backend.tourney_results.shun_config import include_shun_enabled_for
from thetower.backend.tourney_results.sus_config import include_sus_enabled_for
from thetower.web.historical.comparison import get_proximal_players
from thetower.web.live.data_ops import (
    format_time_ago,
    get_bracket_data,
    get_data_refresh_timestamp,
    get_live_data,
    process_display_names,
    require_tournament_data,
)
from thetower.web.util import add_player_id, fmt_dt

logger = logging.getLogger(__name__)


def _search_live_data_for_player(name: str = "", player_id: str = "") -> tuple[str | None, str | None]:
    """Search all leagues' live data for a player by name or ID.

    Returns:
        (player_id, league) if a unique match is found, (None, None) otherwise.
        Writes st.warning/st.error for disambiguation or not-found cases.
    """
    include_shun = include_shun_enabled_for("peer_watch")
    include_sus = include_sus_enabled_for("peer_watch")
    matches: list[tuple[str, str, str]] = []  # (real_name, player_id, league)

    for lg in ALL_LEAGUES:
        try:
            df_tmp = get_live_data(lg, include_shun, include_sus)
            if df_tmp.empty:
                continue
            if player_id:
                pid_upper = player_id.strip().upper()
                match_df = df_tmp[df_tmp["player_id"].str.contains(pid_upper, na=False, regex=False)]
            else:
                name_lower = name.strip().lower()
                match_df = df_tmp[df_tmp["real_name"].str.lower().str.contains(name_lower, na=False, regex=False)]
            for _, row in match_df.drop_duplicates(subset=["player_id"]).iterrows():
                matches.append((row["real_name"], row["player_id"], lg))
        except Exception:
            continue

    if not matches:
        return None, None

    if len(matches) == 1:
        return matches[0][1], matches[0][2]

    # Multiple matches — show disambiguation list
    matches.sort(key=lambda x: x[0].lower())
    st.warning("Multiple players match. Please select one:")
    for m_name, m_id, m_league in matches:
        c1, c2, c3, c4 = st.columns([3, 2, 1, 1])
        c1.write(m_name)
        c2.write(m_id)
        c3.write(m_league)
        if c4.button("Select", key=f"pw_select_{m_id}_{m_league}", on_click=add_player_id, args=(m_id,)):
            pass
    return None, None


def _load_combined_live_data() -> pd.DataFrame:
    """Load current live tournament data for all leagues (bracket-filtered) and combine."""
    include_shun = include_shun_enabled_for("peer_watch")
    include_sus = include_sus_enabled_for("peer_watch")
    frames = []
    for lg in ALL_LEAGUES:
        try:
            df_tmp = get_live_data(lg, include_shun, include_sus)
            if df_tmp.empty:
                continue
            # Apply same bracket filter as live_bracket.py to restrict to current tournament
            _, fullish_brackets = get_bracket_data(df_tmp)
            df_tmp = df_tmp[df_tmp.bracket.isin(fullish_brackets)].copy()
            if not df_tmp.empty:
                df_tmp["league"] = lg
                frames.append(df_tmp)
        except Exception:
            continue
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


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
        name_col, id_col = st.columns(2)
        name_input = name_col.text_input("Search by Player Name", value=st.session_state.get("pw_player_search_term", ""), key="pw_name_input")
        id_input = id_col.text_input("Or by Player ID", value="", key="pw_id_input")

        if id_input.strip():
            found_id, _ = _search_live_data_for_player(player_id=id_input.strip())
            if found_id:
                resolved_id = found_id
                st.session_state["pw_player_id"] = resolved_id
            else:
                # Try treating the input directly as a player ID (for historical lookup)
                resolved_id = id_input.strip().upper()
                st.session_state["pw_player_id"] = resolved_id
        elif name_input.strip():
            st.session_state["pw_player_search_term"] = name_input.strip()
            found_id, _ = _search_live_data_for_player(name=name_input.strip())
            if found_id:
                resolved_id = found_id
                st.session_state["pw_player_id"] = resolved_id
            elif "pw_player_id" not in st.session_state:
                # No match found, stop here
                st.info("Enter a player name or ID to find their peer group.")
                return

    if not resolved_id:
        st.info("Enter a player name or ID to find their peer group.")
        return

    # --- Get proximal peers from last completed tournament ---
    peer_ids, focal_name, focal_league, focal_date = get_proximal_players(resolved_id, peer_n)

    if not peer_ids:
        st.error(f"No historical tournament data found for player ID: `{resolved_id}`")
        st.session_state.pop("pw_player_id", None)
        return

    st.caption(f"Showing {peer_n} peers above/below **{focal_name}** — {focal_league} · {focal_date} (last completed tournament)")

    # --- Load combined live data and filter to peer group ---
    all_live_df = _load_combined_live_data()

    if all_live_df.empty:
        st.error("No live tournament data available.")
        return

    peer_live_df = all_live_df[all_live_df["player_id"].isin(peer_ids)].copy()

    found_ids = set(peer_live_df["player_id"].unique())
    missing_ids = [pid for pid in peer_ids if pid not in found_ids]

    # Show data freshness
    refresh_timestamp = get_data_refresh_timestamp(focal_league)
    if refresh_timestamp:
        st.caption(f"📊 Data last refreshed: {format_time_ago(refresh_timestamp)} ({fmt_dt(refresh_timestamp)})")

    if peer_live_df.empty:
        st.warning(f"None of the {len(peer_ids)} peers are currently participating in the live tournament.")
        return

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
    latest_df.index = pd.RangeIndex(start=1, stop=len(latest_df) + 1)
    latest_df = process_display_names(latest_df)

    display_cols = [c for c in ["player_id", "display_name", "wave", "league", "datetime"] if c in latest_df.columns]
    display_df = latest_df.loc[:, display_cols]

    css_path = Path(__file__).parent.parent / "static" / "styles" / "style.css"
    st.write(display_df.style.format(make_player_url, subset=["player_id"]).to_html(escape=False), unsafe_allow_html=True)
    with open(css_path, "r") as infile:
        st.write(f"<style>{infile.read()}</style>", unsafe_allow_html=True)

    logger.info(f"peer_watch for {resolved_id} (n={peer_n}) took {perf_counter() - t_start:.3f}s")


peer_watch()
