# Standard library imports
import os
import zoneinfo
from importlib.metadata import version
from pathlib import Path

# Third-party imports
import django
import streamlit as st
from streamlit_js_eval import streamlit_js_eval

from thetower.backend.tourney_results.constants import Graph, Options
from thetower.web.maintenance import get_maintenance_state
from thetower.web.popups import show_site_popups
from thetower.web.request_logger import log_render_complete, log_request, start_render
from thetower.web.util import makeitrain

# Django setup
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "thetower.backend.towerdb.settings")
django.setup()

# Local imports

hidden_features = os.environ.get("HIDDEN_FEATURES")

_maintenance = get_maintenance_state()
_maintenance_enabled = _maintenance["enabled"] and not hidden_features


def _page(path: str, **kwargs) -> st.Page:
    """Return st.Page pointing to message.py when maintenance mode is active on the public site."""
    return st.Page("message.py" if _maintenance_enabled else path, **kwargs)


if hidden_features:
    page_title = "Admin: The Tower tourney results"
else:
    page_title = "The Tower tourney results"

st.set_page_config(
    page_title=page_title,
    layout="centered",
    initial_sidebar_state="auto",
    menu_items={
        "Get Help": "https://discord.com/channels/850137217828388904/1361353523601867034",
    },
)

options = Options(links_toggle=True, default_graph=Graph.last_16.value, average_foreground=True)

if st.session_state.get("options") is None:
    st.session_state.options = options

home_info_pages = [
    _page("historical/overview.py", title="Overview", icon=":material/home:", url_path="overview"),
    _page("discord_bot.py", title="Discord Bot", icon=":material/smart_toy:", url_path="discordbot"),
    _page("historical/creator_codes.py", title="Creator Codes", icon=":material/storefront:", url_path="creatorcodes"),
    _page("historical/about.py", title="About", icon=":material/info:", url_path="about"),
]

current_tournament_pages = [
    _page("live/bcs.py", title="Battle Conditions", icon=":material/tune:", url_path="bcs"),
    _page("live/live_progress.py", title="Live Progress", icon=":material/timer:", url_path="liveprogress"),
    _page("live/live_results.py", title="Live Results", icon=":material/list_alt:", url_path="liveresults"),
    _page("live/live_bracket.py", title="Live Bracket View", icon=":material/grid_view:", url_path="livebracketview"),
    _page("live/peer_watch.py", title="Peer Watch", icon=":material/group:", url_path="peerwatch"),
]

live_analytics_pages = [
    _page("live/live_bracket_analysis.py", title="Live Bracket Analysis", icon=":material/analytics:", url_path="livebracketanalysis"),
    _page("live/live_placement_analysis.py", title="Live Placement Analysis", icon=":material/trending_up:", url_path="liveplacement"),
    _page("live/live_quantile_analysis.py", title="Live Quantile Analysis", icon=":material/bar_chart:", url_path="livequantile"),
]

player_statistics_pages = [
    _page("historical/player.py", title="Individual Player Stats", icon=":material/person:", url_path="player"),
    _page("historical/comparison.py", title="Player Comparison", icon=":material/compare_arrows:", url_path="comparison"),
    _page("historical/namechangers.py", title="Namechangers", icon=":material/badge:", url_path="namechangers"),
]

historical_data_pages = [
    _page("historical/results.py", title="League Standings", icon=":material/leaderboard:", url_path="results"),
    _page("historical/counts.py", title="Wave Cutoffs", icon=":material/waves:", url_path="counts"),
    _page("historical/winners.py", title="Winners", icon=":material/emoji_events:", url_path="winners"),
    _page("historical/median_history.py", title="Median History", icon=":material/show_chart:", url_path="medianhistory"),
    _page("historical/static_placement.py", title="Static Global Placement", icon=":material/public:", url_path="staticplacement"),
    _page("historical/regression_analysis.py", title="Tournament Trends", icon=":material/insights:", url_path="tournamenttrends"),
    _page("historical/bc_filter.py", title="Battle Condition Filter", icon=":material/filter_alt:", url_path="bcfilter"),
]

archive_pages = [
    _page("historical/deprecated/top_scores.py", title="Top Scores", icon=":material/star:", url_path="top"),
    _page("historical/deprecated/breakdown.py", title="Breakdown", icon=":material/pie_chart:", url_path="breakdown"),
    _page("historical/deprecated/various.py", title="Relics and Avatars", icon=":material/diamond:", url_path="relics"),
    _page("historical/deprecated/fallen_defenders.py", title="Fallen Defenders", icon=":material/person_off:", url_path="fallen"),
]

# Hidden admin pages (only available when HIDDEN_FEATURES env var is set)
admin_system_health_pages = []
admin_moderation_pages = []

admin_analytics_pages: list[st.Page] = []

if hidden_features:
    admin_analytics_pages = [
        st.Page("historical/league_stats.py", title="League Statistics", icon=":material/query_stats:", url_path="leaguestats"),
    ]

    admin_system_health_pages = [
        st.Page("admin/service_status.py", title="Service Status", icon=":material/dns:", url_path="services"),
        st.Page("admin/codebase_status.py", title="Codebase Status", icon=":material/inventory_2:", url_path="codebase"),
        st.Page("admin/migrations.py", title="Migrations", icon=":material/sync:", url_path="migrations"),
        st.Page("admin/site_settings.py", title="Site Settings", icon=":material/settings:", url_path="sitesettings"),
        st.Page("admin/site_popups.py", title="Site Popups", icon=":material/campaign:", url_path="sitepopups"),
        st.Page("admin/results_limits.py", title="Results Limits", icon=":material/pin:", url_path="resultslimits"),
        st.Page("admin/access_log.py", title="Access Log Viewer", icon=":material/list:", url_path="accesslog"),
        st.Page("admin/access_log_stats.py", title="Access Log Stats", icon=":material/monitoring:", url_path="accesslogstats"),
        st.Page("admin/api_log.py", title="API Request Log", icon=":material/key:", url_path="apilog"),
        st.Page("admin/backup_status.py", title="Backup Status", icon=":material/cloud_done:", url_path="backupstatus"),
        st.Page("admin/resource_monitor.py", title="Resource Monitor", icon=":material/memory:", url_path="resourcemonitor"),
    ]

    admin_moderation_pages = [
        st.Page("admin/visibility_admin.py", title="Moderation Visibility Config", icon=":material/visibility_off:", url_path="visibilityadmin"),
        st.Page("admin/sus_moderation.py", title="Sus Moderation", icon=":material/gavel:", url_path="susmoderation"),
        st.Page("admin/active_bans.py", title="Active Bans", icon=":material/block:", url_path="activebans"),
        st.Page("admin/multiple_moderation.py", title="Multiple Moderation", icon=":material/warning:", url_path="multiplemoderation"),
        st.Page("admin/league_progression.py", title="League Progression", icon=":material/stairs:", url_path="leagueprogression"),
        st.Page("admin/duplicate_tournaments.py", title="Duplicate Tournaments", icon=":material/content_copy:", url_path="duplicates"),
        st.Page("admin/bc_mismatch.py", title="BC Mismatch Analysis", icon=":material/balance:", url_path="bcmismatch"),
    ]


page_dict = {}
page_dict["Home & Info"] = home_info_pages
page_dict["Current Tournament"] = current_tournament_pages
page_dict["Live Analytics"] = live_analytics_pages
page_dict["Player Statistics"] = player_statistics_pages
page_dict["Historical Data"] = historical_data_pages
page_dict["Archive"] = archive_pages

# Add admin pages only for hidden features
if hidden_features:
    if admin_analytics_pages:
        page_dict["Admin - Analytics"] = admin_analytics_pages
    if admin_system_health_pages:
        page_dict["Admin - System Health"] = admin_system_health_pages
    if admin_moderation_pages:
        page_dict["Admin - Moderation"] = admin_moderation_pages

pg = st.navigation(page_dict)

# Get absolute paths for logo images
current_dir = Path(__file__).parent
logo_path = current_dir / "static" / "images" / "TT.png"
icon_path = current_dir / "static" / "images" / "TTIcon.png"

# Only show logo if not on the overview page
# Check if we're navigating to the overview page
# Use getattr to guard against Streamlit 1.54+ not initialising _title outside a run context
if getattr(pg, "title", None) != "Overview":
    st.logo(str(logo_path), size="large", icon_image=str(icon_path))

# Only show toggle and make it rain if there are active rain periods
from thetower.backend.tourney_results.models import RainPeriod

active_period = RainPeriod.get_active_period()

if active_period:
    with st.sidebar:
        if "rain" not in st.session_state:
            st.session_state.rain = True
        rainenabled = st.toggle("Make it rain?", key="rain")

    if rainenabled:
        makeitrain()

show_site_popups()

# Read from localStorage and detect browser timezone (both return None on first render)
_stored_tz = streamlit_js_eval(
    js_expressions="localStorage.getItem('thetower_user_timezone')",
    key="tz_localstorage_read",
)
_browser_tz = streamlit_js_eval(
    js_expressions="Intl.DateTimeFormat().resolvedOptions().timeZone",
    key="browser_tz_detect",
)
_stored_24h = streamlit_js_eval(
    js_expressions="localStorage.getItem('thetower_time_24h')",
    key="time24h_localstorage_read",
)
_all_timezones = sorted(zoneinfo.available_timezones())


def _on_tz_change() -> None:
    """Mark timezone as user-picked so it gets saved to localStorage."""
    st.session_state.tz_user_picked = True
    st.session_state.tz_is_user_set = True


def _reset_tz() -> None:
    """Reset to browser-detected timezone and clear localStorage preference."""
    st.session_state.user_timezone = st.session_state.tz_sniffed
    st.session_state.tz_is_user_set = False
    st.session_state.tz_user_picked = False
    st.session_state.tz_clear_count = st.session_state.get("tz_clear_count", 0) + 1


def _on_time_format_change() -> None:
    """Mark time format as user-picked so it gets saved to localStorage."""
    st.session_state.time_24h_changed = True


# Initialise session state defaults
if "user_timezone" not in st.session_state:
    st.session_state.user_timezone = "UTC"
if "tz_user_picked" not in st.session_state:
    st.session_state.tz_user_picked = False
if "tz_is_user_set" not in st.session_state:
    st.session_state.tz_is_user_set = False
if "tz_sniffed" not in st.session_state:
    st.session_state.tz_sniffed = None
# Restore 24h preference from localStorage once JS resolves (never overwrite after that)
if not st.session_state.get("time_24h_initialized") and _stored_24h is not None:
    st.session_state.time_24h = _stored_24h != "false"
    st.session_state.time_24h_initialized = True
elif "time_24h" not in st.session_state:
    st.session_state.time_24h = True  # default until JS resolves

# Capture sniffed timezone the first time JS responds
if _browser_tz in _all_timezones and not st.session_state.tz_sniffed:
    st.session_state.tz_sniffed = _browser_tz

# On first JS response: prefer stored preference, fall back to browser-detected timezone
if not st.session_state.get("tz_initialized"):
    if _stored_tz and _stored_tz in _all_timezones:
        st.session_state.user_timezone = _stored_tz
        st.session_state.tz_is_user_set = True
        st.session_state.tz_initialized = True
    elif _browser_tz in _all_timezones:
        st.session_state.user_timezone = _browser_tz
        st.session_state.tz_initialized = True

with st.sidebar:
    _tz_label = "Timezone (set by you)" if st.session_state.tz_is_user_set else "Timezone"
    _can_reset = st.session_state.tz_is_user_set and st.session_state.tz_sniffed
    if _can_reset:
        st.markdown(f"**{_tz_label}**")
        _tz_sel_col, _tz_reset_col = st.columns([5, 1])
        with _tz_sel_col:
            st.selectbox(
                _tz_label,
                options=_all_timezones,
                key="user_timezone",
                on_change=_on_tz_change,
                label_visibility="collapsed",
            )
        with _tz_reset_col:
            st.button(":material/restart_alt:", help="Reset to browser-detected timezone", on_click=_reset_tz, type="tertiary")
    else:
        st.selectbox(_tz_label, options=_all_timezones, key="user_timezone", on_change=_on_tz_change)
    st.toggle("24-hour clock", key="time_24h", on_change=_on_time_format_change)

# Persist manually-chosen timezone to localStorage (never persists the auto-sniffed value)
if st.session_state.tz_user_picked:
    _tz_to_save = st.session_state.user_timezone
    streamlit_js_eval(
        js_expressions=f"localStorage.setItem('thetower_user_timezone', '{_tz_to_save}'); true",
        key=f"tz_localstorage_write_{_tz_to_save}",
    )
    st.session_state.tz_user_picked = False

# Clear localStorage when user resets to browser timezone (key changes per reset to force re-execution)
if st.session_state.get("tz_clear_count", 0) > 0:
    streamlit_js_eval(
        js_expressions="localStorage.removeItem('thetower_user_timezone'); true",
        key=f"tz_localstorage_clear_{st.session_state.tz_clear_count}",
    )

# Persist 24h/12h preference to localStorage only when user changed the toggle
if st.session_state.get("time_24h_changed"):
    _time_24h_val = "true" if st.session_state.time_24h else "false"
    streamlit_js_eval(
        js_expressions=f"localStorage.setItem('thetower_time_24h', '{_time_24h_val}'); true",
        key=f"time24h_localstorage_write_{_time_24h_val}",
    )
    st.session_state.time_24h_changed = False

st.html("""
<style>
    .stMainBlockContainer {
        max-width:60rem;
    }
    /* Collapse the invisible iframes produced by streamlit_js_eval */
    iframe[title="streamlit_js_eval.streamlit_js_eval"] {
        display: none;
    }
</style>
""")
# Site stylesheet, once for every page (tables, podium cards, button links)
st.html(current_dir / "static" / "styles" / "style.css")

_path, _render_id = log_request()
_render_clock = start_render()
_render_status = "ok"
try:
    pg.run()
except BaseException as _exc:  # re-raised below; BaseException so Streamlit's control-flow exceptions are recorded too
    _render_status = type(_exc).__name__
    raise
finally:
    _render_stats = log_render_complete(_render_id, _path, _render_clock, _render_status)
# Unlabeled on purpose: wall ms | cpu ms | runs in flight, same order as the render log line.
_render_badge = f"{_render_stats.elapsed_ms} ms | {_render_stats.cpu_ms} | {_render_stats.inflight}"
st.sidebar.markdown(
    """<div class="tt-support">
        <div class="tt-support-title">🐟 Support the fishy</div>
        Use code <span class="tt-code">thedisasterfish</span>
        in the <a href="https://store.techtreegames.com/thetower/">TechTree Store</a>
    </div>""",
    unsafe_allow_html=True,
)
try:
    _pkg_version = version("thetower")
    _prefix = "Admin · " if hidden_features else ""
    # Timing on its own line, kept whole so it never wraps mid-figure
    _version_line = f'{_prefix}Version: {_pkg_version}<br><span style="white-space:nowrap;">{_render_badge}</span>'
except Exception:
    _version_line = _render_badge
st.sidebar.markdown(
    f'<div class="tt-meta" style="font-size:12px; margin-bottom:8px; font-variant-numeric:tabular-nums;">{_version_line}</div>',
    unsafe_allow_html=True,
)
