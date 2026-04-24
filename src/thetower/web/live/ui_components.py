import os

import streamlit as st

from thetower.backend.tourney_results.constants import leagues
from thetower.backend.tourney_results.shun_config import include_shun_enabled_for
from thetower.backend.tourney_results.tourney_utils import check_live_entry
from thetower.web.util import fmt_dt, get_league_selection, get_options


def get_league_for_player(player_id: str) -> str:
    """Find which league a player is participating in."""
    for league in leagues:
        if check_live_entry(league, player_id):
            return league
    return None


def setup_common_ui(show_league_selector: bool = True):
    """Setup common UI elements across live views

    Args:
        show_league_selector: Whether to render the league selector. Defaults to True.
    """
    options = get_options(links=False)

    # Check if we have a player_id in query params
    if player_id := options.current_player_id:
        # Get league directly without showing selector
        league = get_league_for_player(player_id) or "Legend"
        st.session_state.selected_league = league
    else:
        # Either show the selector or use existing/default league without rendering
        if show_league_selector:
            league = get_league_selection(options)
        else:
            league = st.session_state.get("selected_league", "Legend")

    with st.sidebar:
        is_mobile = st.session_state.get("mobile_view", False)
        st.checkbox("Mobile view", value=is_mobile, key="mobile_view")

    return options, league, is_mobile


def render_data_status(league: str, page_key: str):
    """Render the data-refresh timestamp and shun-inclusion captions.

    Shared by all live-data pages.  Returns the ``refresh_timestamp`` so callers
    that need it for fallback logic (e.g. live_placement_analysis) can reuse it.
    """
    from thetower.web.live.data_ops import format_time_ago, get_data_refresh_timestamp

    refresh_timestamp = get_data_refresh_timestamp(league)
    if refresh_timestamp:
        time_ago = format_time_ago(refresh_timestamp)
        st.caption(f"📊 Data last refreshed: {time_ago} ({fmt_dt(refresh_timestamp)})")
    else:
        st.caption("📊 Data refresh time: Unknown")

    if os.environ.get("HIDDEN_FEATURES"):
        try:
            include_shun = include_shun_enabled_for(page_key)
            st.caption(f"🔍Including shunned players: {'Yes' if include_shun else 'No'}")
        except Exception:
            pass

    return refresh_timestamp
