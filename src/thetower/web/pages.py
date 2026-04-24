# Standard library imports
import os
import time
import zoneinfo
from importlib.metadata import version
from pathlib import Path

# Third-party imports
import django
import streamlit as st
from streamlit_js_eval import streamlit_js_eval

from thetower.backend.tourney_results.constants import Graph, Options
from thetower.web.maintenance import get_maintenance_state
from thetower.web.request_logger import log_render_complete, log_request
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
    _page("historical/overview.py", title="Overview", icon="🏠", url_path="overview"),
    _page("discord_bot.py", title="Discord Bot", icon="🆕", url_path="discordbot"),
    _page("historical/about.py", title="About", icon="👴", url_path="about"),
]

current_tournament_pages = [
    _page("live/bcs.py", title="Battle Conditions", icon="🔮", url_path="bcs"),
    _page("live/live_progress.py", title="Live Progress", icon="⏱️", url_path="liveprogress"),
    _page("live/live_results.py", title="Live Results", icon="📋", url_path="liveresults"),
    _page("live/live_bracket.py", title="Live Bracket View", icon="🔠", url_path="livebracketview"),
]

live_analytics_pages = [
    _page("live/live_bracket_analysis.py", title="Live Bracket Analysis", icon="📉", url_path="livebracketanalysis"),
    _page("live/live_placement_analysis.py", title="Live Placement Analysis", icon="📈", url_path="liveplacement"),
    _page("live/live_quantile_analysis.py", title="Live Quantile Analysis", icon="📊", url_path="livequantile"),
]

player_statistics_pages = [
    _page("historical/player.py", title="Individual Player Stats", icon="⛹️", url_path="player"),
    _page("historical/comparison.py", title="Player Comparison", icon="🔃", url_path="comparison"),
    _page("historical/namechangers.py", title="Namechangers", icon="💩", url_path="namechangers"),
]

historical_data_pages = [
    _page("historical/results.py", title="League Standings", icon="🐳", url_path="results"),
    _page("historical/counts.py", title="Wave Cutoffs", icon="🐈", url_path="counts"),
    _page("historical/winners.py", title="Winners", icon="🔥", url_path="winners"),
    _page("historical/median_history.py", title="Median History", icon="📉", url_path="medianhistory"),
    _page("historical/static_placement.py", title="Static Global Placement", icon="🌐", url_path="staticplacement"),
    _page("historical/regression_analysis.py", title="Tournament Trends", icon="📈", url_path="tournamenttrends"),
    _page("historical/bc_filter.py", title="Battle Condition Filter", icon="🔎", url_path="bcfilter"),
]

archive_pages = [
    _page("historical/deprecated/top_scores.py", title="Top Scores", icon="🤑", url_path="top"),
    _page("historical/deprecated/breakdown.py", title="Breakdown", icon="🪁", url_path="breakdown"),
    _page("historical/deprecated/various.py", title="Relics and Avatars", icon="👽", url_path="relics"),
    _page("historical/deprecated/fallen_defenders.py", title="Fallen Defenders", icon="🪦", url_path="fallen"),
]

# Hidden admin pages (only available when HIDDEN_FEATURES env var is set)
admin_system_health_pages = []
admin_moderation_pages = []

if hidden_features:
    admin_system_health_pages = [
        st.Page("admin/service_status.py", title="Service Status", icon="🔧", url_path="services"),
        st.Page("admin/codebase_status.py", title="Codebase Status", icon="📦", url_path="codebase"),
        st.Page("admin/migrations.py", title="Migrations", icon="🔄", url_path="migrations"),
        st.Page("admin/site_settings.py", title="Site Settings", icon="⚙️", url_path="sitesettings"),
        st.Page("admin/access_log.py", title="Access Log Viewer", icon="🌐", url_path="accesslog"),
        st.Page("admin/access_log_stats.py", title="Access Log Stats", icon="📊", url_path="accesslogstats"),
        st.Page("admin/backup_status.py", title="Backup Status", icon="☁️", url_path="backupstatus"),
    ]

    admin_moderation_pages = [
        st.Page("admin/shun_admin.py", title="Shun List Management", icon="🛑", url_path="shunadmin"),
        st.Page("admin/sus_moderation.py", title="Sus Moderation", icon="🚫", url_path="susmoderation"),
        st.Page("admin/multiple_moderation.py", title="Multiple Moderation", icon="⚠️", url_path="multiplemoderation"),
        st.Page("admin/league_progression.py", title="League Progression", icon="📈", url_path="leagueprogression"),
        st.Page("admin/duplicate_tournaments.py", title="Duplicate Tournaments", icon="🔍", url_path="duplicates"),
        st.Page("admin/bc_mismatch.py", title="BC Mismatch Analysis", icon="⚖️", url_path="bcmismatch"),
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
if pg.title != "Overview":
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
    _tz_label = "Timezone ✏️" if st.session_state.tz_is_user_set else "Timezone"
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
            st.button("🔁", help="Reset to browser-detected timezone", on_click=_reset_tz, type="tertiary")
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

st.html(
    """
<style>
    .stMainBlockContainer {
        max-width:60rem;
    }
    /* Collapse the invisible iframes produced by streamlit_js_eval */
    iframe[title="streamlit_js_eval.streamlit_js_eval"] {
        display: none;
    }
</style>
"""
)

_path, _render_id = log_request()
_render_start = time.perf_counter()
pg.run()
_elapsed_ms = int((time.perf_counter() - _render_start) * 1000)
log_render_complete(_render_id, _elapsed_ms)
st.sidebar.markdown(
    """<div style="text-align:center; margin-bottom:0.5em; font-size:0.9em; padding:0.5em; background-color:rgba(30,144,255,0.1); border-radius:0.5em; border:1px solid rgba(30,144,255,0.3);">
        <b>🐟 Support the fishy!</b><br>
        Use code <span style="color:#cd4b3d; font-weight:bold;">thedisasterfish</span> in the <a href="https://store.techtreegames.com/thetower/">TechTree Store</a>
    </div>""",
    unsafe_allow_html=True,
)
try:
    _pkg_version = version("thetower")
    _prefix = "🔧 " if hidden_features else ""
    _version_line = f"{_prefix}Version: {_pkg_version} &nbsp;·&nbsp; ⚡ {_elapsed_ms} ms"
except Exception:
    _version_line = f"⚡ {_elapsed_ms} ms"
st.sidebar.markdown(
    f'<div style="text-align:center; font-size:0.75em; color:#888888; margin-bottom:0.5em;">{_version_line}</div>',
    unsafe_allow_html=True,
)
