import datetime
import html
import zoneinfo

import pandas as pd
import plotly.express as px
import streamlit as st
from streamlit_extras.let_it_rain import rain

from thetower.backend.tourney_results.constants import Graph, Options, leagues


def get_user_tz() -> zoneinfo.ZoneInfo:
    """Return the ZoneInfo for the user's selected timezone (falls back to UTC)."""
    tz_name = getattr(st.session_state, "user_timezone", "UTC") or "UTC"
    try:
        return zoneinfo.ZoneInfo(tz_name)
    except (zoneinfo.ZoneInfoNotFoundError, ValueError):
        return zoneinfo.ZoneInfo("UTC")


def _apply_time_format(fmt: str) -> str:
    """Rewrite 24-hour strftime tokens to 12-hour when the user prefers 12h."""
    if getattr(st.session_state, "time_24h", True):
        return fmt
    # Replace longest pattern first so %H:%M:%S doesn't partially match %H:%M
    for pattern, replacement in (
        ("%H:%M:%S", "%I:%M:%S %p"),
        ("%H:%M", "%I:%M %p"),
    ):
        if pattern in fmt:
            return fmt.replace(pattern, replacement)
    return fmt


def fmt_dt(dt: datetime.datetime, fmt: str = "%Y-%m-%d %H:%M:%S %Z") -> str:
    """Convert a UTC-aware datetime to the user's local timezone and format it.

    If *dt* is naive it is assumed to be UTC.
    The 24h/12h preference from session state is applied automatically.
    """
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt.astimezone(get_user_tz()).strftime(_apply_time_format(fmt))


def links_toggle():
    with st.sidebar:
        st.write("Toggles")
        links = st.checkbox("Links to users? (will make dataframe ugly)", value=False)

    return links


def escape_df_html(df: pd.DataFrame, escape_columns: list[str]) -> pd.DataFrame:
    """Escape HTML special characters in specified DataFrame columns.

    Args:
        df: DataFrame to process
        escape_columns: List of column names that need HTML escaping

    Returns:
        DataFrame with HTML-escaped values in specified columns
    """
    result = df.copy()
    for col in escape_columns:
        if col in result.columns:
            result[col] = result[col].apply(lambda x: html.escape(str(x)) if pd.notnull(x) else "")
    return result


def get_options(links=None):
    if links is not False:
        links = links_toggle()

    options = Options(links_toggle=links, default_graph=Graph.last_16.value, average_foreground=True)

    query = st.query_params

    if query:
        print(datetime.datetime.now(), query)

    player = query.get("player")
    player_id = query.get("player_id")
    compare_players = query.get_all("compare")
    league = query.get("league")
    print(f"{player=}, {compare_players=}, {league=}")

    options.current_player = player
    options.current_player_id = player_id
    options.compare_players = compare_players
    options.current_league = league

    if options.current_league:
        options.current_league = options.current_league.capitalize()

    return options


def get_league_filter(league=None):
    try:
        index = leagues.index(league)
    except ValueError:
        index = 0

    return index


def league_changed():
    """Callback for when league selection changes"""
    st.session_state.selected_league = st.session_state.league_selector


def get_league_selection(options=None, patch=None):
    """Get or set the league selection from session state

    Args:
        options: Options object containing settings
        patch: Optional patch object to enforce historical league selection

    Returns:
        str: Selected league name
    """
    if options is None:
        options = get_options(links=False)

    # Initialize league in session state if not present
    if "selected_league" not in st.session_state:
        league_index = get_league_filter(options.current_league)
        st.session_state.selected_league = leagues[league_index]

    with st.sidebar:
        # Use the session state value as the default
        league_index = leagues.index(st.session_state.selected_league)
        league = st.radio("League", leagues, league_index, key="league_selector", on_change=league_changed)

        # Force Champion league for historical patches if Legend is selected
        if (
            patch
            and league == "Legend"
            and hasattr(patch, "version_minor")
            and isinstance(patch.version_minor, (int, float))
            and patch.version_minor < 25
        ):
            league = "Champion"
            st.info("Using Champion league for historical patch (Legend not available)")
            st.session_state.selected_league = league

    return st.session_state.selected_league


def gantt(df):
    def get_borders(dates: list[datetime.date]) -> list[tuple[datetime.date, datetime.date]]:
        """Get start and finish of each interval. Assuming dates are sorted and tourneys are max 4 days apart."""

        borders = []

        start = dates[0]

        for date, next_date in zip(dates[1:], dates[2:]):
            if next_date - date > datetime.timedelta(days=4):
                end = date
                borders.append((start, end))
                start = next_date

        borders.append((start, dates[-1]))

        return borders

    gantt_data = []

    for i, row in df.iterrows():
        borders = get_borders(row.tourneys_attended)
        name = row.Player

        for start, end in borders:
            gantt_data.append(
                {
                    "Player": name,
                    "Start": start,
                    "Finish": end,
                    "Champion": name,
                }
            )

    gantt_df = pd.DataFrame(gantt_data)

    fig = px.timeline(gantt_df, x_start="Start", x_end="Finish", y="Player", color="Champion")
    fig.update_yaxes(autorange="reversed")
    return fig


def add_player_id(player_id):
    st.session_state.player_id = player_id


def add_to_comparison(player_id, nicknames):
    if "comparison" in st.session_state:
        st.session_state.comparison.add(player_id)
        st.session_state.addee_map[player_id] = nicknames
    else:
        st.session_state.comparison = {player_id}
        st.session_state.addee_map = {player_id: nicknames}

    print(f"{st.session_state.comparison=} {st.session_state.addee_map=}")
    st.session_state.counter = st.session_state.counter + 1 if st.session_state.get("counter") else 1


def deprecated():
    st.info(
        "This page is now deprecated and won't be updated past the end of Champ era. If you use or like this page, please let the site admins know on discord."
    )


def makeitrain(icon: str | None = None, after: datetime.date | None = None, before: datetime.date | None = None):
    """
    Make it rain with emojis based on active rain periods from the database.
    If specific parameters are provided, they override the database entries.
    """
    from django.utils import timezone

    today = timezone.now().date()

    if icon and after and before:
        # Use provided parameters
        if today >= after and today <= before:
            rain(
                emoji=icon,
                font_size=27,
                falling_speed=20,
                animation_length="infinite",
            )
        return

    # Use cached lookup for active period
    from thetower.backend.tourney_results.models import RainPeriod

    active_period = RainPeriod.get_active_period()

    if active_period:
        rain(
            emoji=active_period.emoji,
            font_size=27,
            falling_speed=20,
            animation_length="infinite",
        )
