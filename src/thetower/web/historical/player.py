import datetime
import os
from html import escape
from pathlib import Path
from urllib.parse import urlencode

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from thetower.backend.sus.models import PlayerId
from thetower.backend.tourney_results.constants import (
    Graph,
    all_relics,
    how_many_results_public_site,
    leagues,
)
from thetower.backend.tourney_results.data import (
    get_details,
    get_id_lookup,
    get_patches,
    is_shun,
    is_support_flagged,
    is_sus,
    is_under_review,
)
from thetower.backend.tourney_results.formatting import BASE_URL, color_position
from thetower.backend.tourney_results.models import BattleCondition
from thetower.backend.tourney_results.models import PatchNew as Patch
from thetower.backend.tourney_results.models import TourneyRow
from thetower.backend.tourney_results.tourney_utils import check_all_live_entry
from thetower.web.historical.search import compute_search
from thetower.web.util import escape_df_html, get_options

id_mapping = get_id_lookup()
hidden_features = os.environ.get("HIDDEN_FEATURES")


def compute_player_lookup():
    print("player")
    st.markdown("# Individual Player Stats")
    options = get_options(links=False)
    hidden_features = os.environ.get("HIDDEN_FEATURES")

    def search_for_new():
        st.query_params.clear()
        st.session_state.options.current_player = None
        st.session_state.options.current_player_id = None
        if "player_id" in st.session_state:
            st.session_state.pop("player_id")

    css_path = Path(__file__).parent.parent / "static" / "styles" / "style.css"
    with open(css_path, "r") as infile:
        table_styling = f"<style>{infile.read()}</style>"

    st.write(table_styling, unsafe_allow_html=True)

    if player_id := st.session_state.get("player_id"):
        options.current_player = player_id

    if options.current_player is not None:
        st.button("Search for another player?", on_click=search_for_new, key="player_search_for_new")

    if options.current_player is None:
        compute_search(player=True, comparison=False)
        exit()

    info_tab, league_graph_tab, patch_tab = st.tabs(["Overview", "Performance Graph", "Patch best"])

    player_ids = PlayerId.objects.filter(id=options.current_player).select_related("game_instance__player")
    print(f"{player_ids=} {options.current_player=}")

    hidden_query = {} if hidden_features else {"result__public": True, "position__lte": how_many_results_public_site}

    if player_ids:
        player_id = player_ids[0]
        print(f"{player_ids=} {player_id=}")
        # Get all PlayerIds for THIS specific game instance only
        if player_id.game_instance:
            game_instance_ids = PlayerId.objects.filter(game_instance=player_id.game_instance).values_list("id", flat=True)
        else:
            game_instance_ids = [player_id.id]
        rows = TourneyRow.objects.filter(
            player_id__in=game_instance_ids,
            **hidden_query,
        )
    else:
        print(f"{player_id=} {options.current_player=}")
        player_id = options.current_player
        rows = TourneyRow.objects.filter(
            player_id=player_id,
            **hidden_query,
        )

    if not rows:
        st.error(f"No results found for the player {player_id}.")
        return

    if (is_sus(player_id) or is_support_flagged(player_id)) and not hidden_features:
        st.error(f"No results found for the player {player_id}.")
        return

    player_df = get_details(rows)

    if player_df.empty:
        st.error(f"No results found for the player {player_id}.")
        return

    # Add player info to Performance Graph and Patch best tabs
    league_graph_tab.markdown(f"**Player:** {escape(player_df.iloc[0].real_name)} (ID: {player_df.iloc[0].id})")
    patch_tab.markdown(f"**Player:** {escape(player_df.iloc[0].real_name)} (ID: {player_df.iloc[0].id})")

    player_df = player_df.sort_values("date", ascending=False)
    user = player_df["real_name"][0]

    # Apply HTML escaping before styling or displaying
    player_df = escape_df_html(player_df, ["real_name", "tourney_name"])

    draw_info_tab(info_tab, user, player_id, player_df, hidden_features)

    player_df = player_df.reset_index(drop=True)
    player_df.index = player_df.index + 1
    player_df["battle"] = [" / ".join([bc.shortcut for bc in bcs]) for bcs in player_df.bcs]

    def dataframe_styler(player_df):
        df_copy = player_df.copy()
        # Convert patch objects to strings
        df_copy["patch"] = df_copy["patch"].apply(str)
        return (
            df_copy[["name", "wave", "#", "date", "patch", "battle", "league"]]
            .style.apply(
                lambda row: [
                    None,
                    f"color: {player_df[player_df['date'] == row.date].wave_role_color.iloc[0]}",
                    None,
                    None,
                    None,
                    None,
                    None,
                ],
                axis=1,
            )
            .map(color_position, subset=["#"])
        )

    player_df = player_df.rename({"tourney_name": "name", "position": "#"}, axis=1)
    # Allow limiting the full results to a patch and filtering by battle conditions (match graph_tab /comparison)
    patches_options = sorted([patch for patch in get_patches() if patch.version_minor], key=lambda patch: patch.start_date, reverse=True)
    raw_graph_options = [options.default_graph.value] + [
        value for value in list(Graph.__members__.keys()) + patches_options if value != options.default_graph.value
    ]

    raw_patch = info_tab.selectbox("Limit results to a patch?", raw_graph_options, key="player_raw_patch")

    all_battle_conditions = sorted(BattleCondition.objects.all(), key=lambda bc: bc.shortcut)
    raw_filter_bcs = info_tab.multiselect(
        "Filter full results by battle conditions?",
        all_battle_conditions,
        format_func=lambda bc: f"{bc.name} ({bc.shortcut})",
        key="player_raw_filter_bcs",
    )

    # Start from the full player_df and apply patch filter then BC filter
    raw_filtered_df = player_df

    # Apply patch filtering similar to handle_colors_dependant_on_patch
    if isinstance(raw_patch, Patch):
        raw_filtered_df = raw_filtered_df[raw_filtered_df.patch == raw_patch]
    elif raw_patch == Graph.last_8.value:
        raw_filtered_df = raw_filtered_df[raw_filtered_df.date.isin(sorted(player_df.date.unique())[-8:])]
    elif raw_patch == Graph.last_16.value:
        # Get the last 16 tournament dates from this player's own data
        raw_filtered_df = raw_filtered_df[raw_filtered_df.date.isin(sorted(player_df.date.unique())[-16:])]
    elif raw_patch == Graph.last_32.value:
        raw_filtered_df = raw_filtered_df[raw_filtered_df.date.isin(sorted(player_df.date.unique())[-32:])]

    if raw_filter_bcs:
        sbcs = set(raw_filter_bcs)
        filtered_player_df = raw_filtered_df[raw_filtered_df.bcs.map(lambda table_bcs: sbcs & set(table_bcs) == sbcs)].copy()
    else:
        filtered_player_df = raw_filtered_df

    filtered_player_df = filtered_player_df.reset_index(drop=True)
    filtered_player_df.index = filtered_player_df.index + 1

    info_tab.dataframe(dataframe_styler(filtered_player_df), width="stretch", height=610)

    # Performance Graph Tab
    col1, col2 = league_graph_tab.columns(2)
    league_patch = col1.selectbox("Limit results to a patch?", raw_graph_options, key="player_league_patch")
    league_select = col2.multiselect(
        "Select leagues to display (leave empty for all)",
        leagues,
        key="player_league_select",
    )

    all_battle_conditions = sorted(BattleCondition.objects.all(), key=lambda bc: bc.shortcut)
    league_filter_bcs = league_graph_tab.multiselect(
        "Filter by battle conditions?",
        all_battle_conditions,
        format_func=lambda bc: f"{bc.name} ({bc.shortcut})",
        key="player_league_filter_bcs",
    )

    col3, col4 = league_graph_tab.columns(2)
    league_graph_position = col3.checkbox("Graph position instead of wave", key="league_graph_position")
    show_patch_lines = col4.checkbox("Show patch start lines", key="show_patch_lines")

    rolling_average = league_graph_tab.slider(
        "Use rolling average for results from how many tourneys?", min_value=1, max_value=10, value=5, key="league_rolling_average"
    )

    league_filtered_df = player_df

    if isinstance(league_patch, Patch):
        league_filtered_df = league_filtered_df[league_filtered_df.patch == league_patch]
    elif league_patch == Graph.last_8.value:
        league_filtered_df = league_filtered_df[league_filtered_df.date.isin(sorted(player_df.date.unique())[-8:])]
    elif league_patch == Graph.last_16.value:
        league_filtered_df = league_filtered_df[league_filtered_df.date.isin(sorted(player_df.date.unique())[-16:])]
    elif league_patch == Graph.last_32.value:
        league_filtered_df = league_filtered_df[league_filtered_df.date.isin(sorted(player_df.date.unique())[-32:])]

    if league_filter_bcs:
        sbcs = set(league_filter_bcs)
        league_filtered_df = league_filtered_df[league_filtered_df.bcs.map(lambda table_bcs: sbcs & set(table_bcs) == sbcs)].copy()

    selected_leagues = league_select if league_select else leagues

    fig = go.Figure()
    colors = ["#CD7F32", "#C0C0C0", "#FFD700", "#E5E4E2", "#B9F2FF", "#FF6347", "#8A2BE2"]  # Colors for leagues: Bronze to Grandmaster

    y_col = "#" if league_graph_position else "wave"

    for i, league in enumerate(leagues):
        if league in selected_leagues:
            league_data = league_filtered_df[league_filtered_df.league == league].sort_values("date")
            if not league_data.empty:
                league_data = league_data.copy()
                league_data["average"] = league_data[y_col].rolling(rolling_average, min_periods=1, center=True).mean()
                fig.add_trace(
                    go.Scatter(
                        x=league_data.date,
                        y=league_data[y_col],
                        mode="lines+markers",
                        name=f"{league} - actual",
                        line=dict(color=colors[i] if i < len(colors) else None),
                    )
                )
                fig.add_trace(
                    go.Scatter(
                        x=league_data.date,
                        y=league_data["average"],
                        mode="lines",
                        name=f"{league} - {rolling_average} avg",
                        line=dict(color=colors[i] if i < len(colors) else None, dash="dot"),
                    )
                )

    y_title = "Position" if league_graph_position else "Wave"
    fig.update_layout(title=f"{y_title} Progression by League", xaxis_title="Date", yaxis_title=y_title, hovermode="x unified")

    if league_graph_position:
        fig.update_yaxes(autorange="reversed")  # Position 1 is best

    if show_patch_lines:
        handle_start_date_loop(fig, league_graph_position, league_filtered_df)

    league_graph_tab.plotly_chart(fig)

    write_for_each_patch(patch_tab, player_df)

    player_id = player_df.iloc[0].id


def filter_lower_leagues(df):
    leagues_in = set(df.league)

    for league in leagues:
        if league in leagues_in:
            break

    df = df[df.league == league]
    return df


def draw_info_tab(info_tab, user, player_id, player_df, hidden_features):
    # Generate URLs
    player_url = f"https://{BASE_URL}/player?" + urlencode({"player": player_id}, doseq=True)
    bracket_url = f"https://{BASE_URL}/livebracketview?" + urlencode({"player_id": player_id}, doseq=True)
    comparison_url = f"https://{BASE_URL}/comparison?bracket_player={player_id}"
    placement_url = f"https://{BASE_URL}/liveplacement?player_id={player_id}"
    quantile_url = f"https://{BASE_URL}/livequantile?player_id={player_id}"

    # Continue with the rest of the info tab content
    handle_sus_or_banned_ids(info_tab, player_id)

    # Escape real_name when used directly in HTML
    real_name = escape(player_df.iloc[0].real_name)

    if hidden_features:
        sus_button_style = "display: inline-block; padding: 8px 16px; background-color: #FFA500; color: white; text-align: center; text-decoration: none; border-radius: 4px; font-weight: 500;"
        info_tab.markdown(
            f"<div style='text-align: right; margin-bottom: 1rem;'><a href='https://admin.thetower.lol/admin/sus/moderationrecord/add/?tower_id={player_df.iloc[0].id}&moderation_type=sus' target='_blank' style='{sus_button_style}'>🔗 sus me</a></div>",
            unsafe_allow_html=True,
        )

    # avatar = player_df.iloc[0].avatar
    relic = player_df.iloc[0].relic

    # if avatar in [35, 36, 39, 42, 44, 45, 46]:
    #     extension = "webp"
    # else:
    #     extension = "png"

    # avatar_string = f"<img src='./app/static/Tower_Skins/{avatar}.{extension}' width=100>" if avatar > 0 else ""
    avatar_string = ""

    # Check if the relic exists in all_relics dictionary to avoid KeyError
    if relic in all_relics:
        # title = f"title='{all_relics[relic][0]}, {all_relics[relic][2]} {all_relics[relic][3]}'"
        # relic_url = f"<img src='./app/static/Tower_Relics/{all_relics[relic][1]}' width=100, {title}>" if relic >= 0 else ""
        relic_url = ""
    else:
        # Handle missing relic gracefully
        relic_url = ""

    tourney_join = "✅" if check_all_live_entry(player_df.iloc[0].id) else "⛔"

    # Get creator code from the player's KnownPlayer via GameInstance
    creator_code = ""
    try:
        # Look up the player by their player ID
        player_id_value = player_df.iloc[0].id

        player_ids = PlayerId.objects.filter(id=player_id_value).select_related("game_instance__player")
        if player_ids.exists():
            player_id_obj = player_ids.first()
            known_player = None

            # Get KnownPlayer via GameInstance
            if player_id_obj.game_instance:
                known_player = player_id_obj.game_instance.player

            if known_player and known_player.creator_code:
                creator_code = f"<div style='font-size: 15px'>Creator code: <span style='color:#cd4b3d; font-weight:bold;'>{known_player.creator_code}</span> <a href='https://store.techtreegames.com/thetower/' target='_blank' style='text-decoration: none;'>🏪</a></div>"
    except Exception:
        # Silently fail if there's any issue looking up the creator code
        pass

    info_tab.write(
        f"<table class='top'><tr><td>{avatar_string}</td><td><div style='font-size: 30px'><span style='vertical-align: middle;'>{real_name}</span></div><div style='font-size: 15px'>ID: {player_df.iloc[0].id} <a href='{player_url}' style='text-decoration: none;'>🔗</a></div><div style='font-size: 15px'>Joined the recent tourney {tourney_join}</div>{creator_code}</td><td>{relic_url}</td></tr></table>",
        unsafe_allow_html=True,
    )

    # Show live links only if player joined the recent tourney
    if check_all_live_entry(player_df.iloc[0].id):
        live_col1, live_col2, live_col3, live_col4 = info_tab.columns(4)

        button_style = "display: inline-block; padding: 8px 16px; background-color: #FF4B4B; color: white; text-align: center; text-decoration: none; border-radius: 4px; font-weight: 500;"
        center_style = "text-align: center; margin-bottom: 1rem;"

        live_col1.markdown(
            f'<div style="{center_style}"><a href="{bracket_url}" style="{button_style}">Bracket View</a></div>', unsafe_allow_html=True
        )
        live_col2.markdown(
            f'<div style="{center_style}"><a href="{comparison_url}" style="{button_style}">Bracket Comparison</a></div>', unsafe_allow_html=True
        )
        live_col3.markdown(
            f'<div style="{center_style}"><a href="{placement_url}" style="{button_style}">Placement Analysis</a></div>', unsafe_allow_html=True
        )
        live_col4.markdown(
            f'<div style="{center_style}"><a href="{quantile_url}" style="{button_style}">Quantile Analysis</a></div>', unsafe_allow_html=True
        )


def write_for_each_patch(patch_tab, player_df):
    wave_data = []
    position_data = []

    for patch, patch_df in player_df.groupby("patch"):
        max_wave = patch_df.wave.max()
        max_wave_data = patch_df[patch_df.wave == max_wave].iloc[0]

        max_pos = patch_df["#"].min()
        max_pos_data = patch_df[patch_df["#"] == max_pos].iloc[0]

        # Convert patch to string using its __str__ method
        patch_str = str(patch)

        wave_data.append(
            {
                "patch": patch_str,
                "max_wave": max_wave,
                "tourney_name": max_wave_data["name"],
                "date": max_wave_data.date,
                "battle_conditions": ", ".join(max_wave_data.bcs.values_list("shortcut", flat=True)),
            }
        )

        position_data.append(
            {
                "patch": patch_str,
                "max_position": max_pos,
                "tourney_name": max_pos_data["name"],
                "date": max_pos_data.date,
                "battle_conditions": ", ".join(max_pos_data.bcs.values_list("shortcut", flat=True)),
            }
        )

    wave_data = sorted(wave_data, key=lambda x: x["date"], reverse=True)
    position_data = sorted(position_data, key=lambda x: x["date"], reverse=True)

    wave_df = pd.DataFrame(wave_data).reset_index(drop=True)
    position_df = pd.DataFrame(position_data).reset_index(drop=True)

    # Set index to start from 1 instead of 0
    wave_df.index = wave_df.index + 1
    position_df.index = position_df.index + 1

    wave_tbdf = wave_df[["patch", "max_wave", "tourney_name", "date", "battle_conditions"]].style.apply(
        lambda row: [
            None,
            None,
            None,
            None,
            None,
        ],
        axis=1,
    )

    position_tbdf = position_df[["patch", "max_position", "tourney_name", "date", "battle_conditions"]].style.apply(
        lambda row: [
            None,
            None,
            None,
            None,
            None,
        ],
        axis=1,
    )

    patch_tab.markdown("**Best wave per patch**")
    patch_tab.dataframe(wave_tbdf)

    patch_tab.markdown("**Best position per patch**")
    patch_tab.dataframe(position_tbdf)


def handle_start_date_loop(fig, graph_position_instead, tbdf):
    for index, (start, version_minor, version_patch, interim) in enumerate(
        Patch.objects.all().values_list("start_date", "version_minor", "version_patch", "interim")
    ):
        name = f"0.{version_minor}.{version_patch}"
        interim = " interim" if interim else ""

        if start < tbdf.date.min() - datetime.timedelta(days=2) or start > tbdf.date.max() + datetime.timedelta(days=3):
            continue

        fig.add_vline(x=start, line_width=3, line_dash="dash", line_color="#888", opacity=0.4)
        fig.add_annotation(
            x=start,
            y=(tbdf["#"].min() + 10 * (index % 5)) if graph_position_instead else (tbdf.wave.max() - 150 * (index % 5 + 1)),
            text=f"Patch {name}{interim} start",
            showarrow=True,
            arrowhead=1,
        )


def handle_sus_or_banned_ids(info_tab, player_id):
    if hidden_features:
        if is_support_flagged(player_id):
            info_tab.warning("This player is currently (soft/hard) banned.")
        elif is_sus(player_id):
            info_tab.warning("This player is currently sussed.")
        elif is_shun(player_id):
            info_tab.warning("This player is currently shunned.")
        elif is_under_review(player_id):
            info_tab.warning("This player is under review.")


compute_player_lookup()
