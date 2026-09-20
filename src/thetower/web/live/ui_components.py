import datetime
import os

import streamlit as st

from thetower.backend.tourney_results.constants import leagues, top_league
from thetower.backend.tourney_results.shun_config import include_shun_enabled_for
from thetower.backend.tourney_results.sus_config import include_sus_enabled_for
from thetower.backend.tourney_results.tourney_utils import check_live_entry
from thetower.web.live.data_ops import live_banned_ids
from thetower.web.util import fmt_dt, get_league_selection, get_options


def get_league_for_player(player_id: str) -> str:
    """Find which league a player is participating in.

    ``fast=True`` reads only the membership columns of the latest snapshot and
    skips the full player-name lookup — sufficient for a participation check,
    and this loop runs up to once per league on every player-linked render.
    """
    for league in leagues:
        if check_live_entry(league, player_id, fast=True, excluded_ids=live_banned_ids()):
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
        league = get_league_for_player(player_id) or top_league
        st.session_state.selected_league = league
    else:
        # Either show the selector or use existing/default league without rendering
        if show_league_selector:
            league = get_league_selection(options)
        else:
            league = st.session_state.get("selected_league", top_league)

    with st.sidebar:
        is_mobile = st.session_state.get("mobile_view", False)
        st.checkbox("Mobile view", value=is_mobile, key="mobile_view")

    return options, league, is_mobile


def render_data_status(
    league: str, page_key: str, cache_snapshot_time: datetime.datetime | None = None, cache_label: str | None = None
) -> datetime.datetime | None:
    """Render the data timestamp, cache-lag and shun-inclusion captions.

    Shared by all live-data pages. ``cache_snapshot_time`` is the snapshot the page's cache was
    built from. Without ``cache_label`` the page is served entirely from that cache, so that is the
    time shown as the data time -- what users are looking at, not what the newest snapshot on disk
    would give them. With a label (e.g. "Bracket creation times") only that part comes from the
    cache, and it gets its own line under the live data time.

    The generator finishes about a minute after each snapshot lands, so a cache one snapshot behind
    is routine and stays quiet. Further behind gets a heads-up next to the data, not an error in
    place of it. The admin page also sees the newest snapshot on disk beside the cache's.

    Returns the timestamp shown so callers that need it for fallback logic can reuse it.
    """
    from thetower.web.live.data_ops import format_time_ago, get_data_refresh_timestamp, snapshots_behind

    disk_timestamp = get_data_refresh_timestamp(league)
    shown = cache_snapshot_time if cache_snapshot_time is not None and cache_label is None else disk_timestamp
    if shown:
        st.caption(f"📊 Data last refreshed: {format_time_ago(shown)} ({fmt_dt(shown)})")
    else:
        st.caption("📊 Data refresh time: Unknown")

    behind = snapshots_behind(league, cache_snapshot_time) if cache_snapshot_time is not None else 0
    if cache_label and cache_snapshot_time is not None:
        st.caption(f"📊 {cache_label} from snapshot: {fmt_dt(cache_snapshot_time)}")
    if behind > 1:
        st.caption(f"⏳ {cache_label or 'Data'} {behind} checkpoints behind live data — catching up.")

    if os.environ.get("HIDDEN_FEATURES"):
        try:
            if cache_snapshot_time is not None:
                st.caption(
                    f"🔍 Latest snapshot on disk: {fmt_dt(disk_timestamp) or 'none'}. "
                    f"Showing cache built from snapshot: {fmt_dt(cache_snapshot_time)} ({behind} behind)"
                )
            include_shun = include_shun_enabled_for(page_key)
            include_sus = include_sus_enabled_for(page_key)
            st.caption(f"🔍Including shunned players: {'Yes' if include_shun else 'No'}")
            st.caption(f"🔍Including sus players: {'Yes' if include_sus else 'No'}")
        except Exception:
            pass

    return shown
