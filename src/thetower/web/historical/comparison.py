import datetime
import os
from pathlib import Path
from statistics import median, stdev
from urllib.parse import urlencode

import pandas as pd
import plotly.express as px
import streamlit as st

from thetower.backend.sus.models import PlayerId
from thetower.backend.tourney_results.constants import (
    Graph,
    champ,
    how_many_results_public_site,
    leagues,
)
from thetower.backend.tourney_results.data import get_details, get_patches, get_sus_ids
from thetower.backend.tourney_results.formatting import BASE_URL, make_player_url
from thetower.backend.tourney_results.models import BattleCondition
from thetower.backend.tourney_results.models import PatchNew as Patch
from thetower.backend.tourney_results.models import TourneyResult, TourneyRow
from thetower.backend.tourney_results.shun_config import include_shun_enabled_for
from thetower.backend.tourney_results.tourney_utils import get_latest_live_df
from thetower.web.historical.search import compute_search
from thetower.web.util import escape_df_html, get_league_selection

sus_ids = get_sus_ids()
hidden_features = os.environ.get("HIDDEN_FEATURES")


def compute_comparison(player_id=None, canvas=st):
    st.markdown("# Player Comparison")
    # Check if there's a bracket_player query param to load a full bracket for comparison
    bracket_player_id = st.query_params.get("bracket_player")

    if bracket_player_id:
        # Only process if this is a new/different bracket player ID — avoids overwriting
        # league selection on every re-render (e.g. when user changes the league selector)
        if st.session_state.get("_last_bracket_player") != bracket_player_id:
            bracket_players, bracket_league = get_bracket_players(bracket_player_id)
            if bracket_players:
                # Set these players as the comparison targets
                st.session_state.options.compare_players = bracket_players
                st.session_state.display_comparison = True

                # Store a note that we're viewing a bracket comparison
                st.session_state.bracket_comparison = True
                st.session_state.bracket_player_id = bracket_player_id

                # Override the league selection default to match the player's actual league
                if bracket_league:
                    st.session_state.selected_league = bracket_league
                    # Remove the radio widget's key so it re-initializes from the index
                    # parameter on the next render (setting it directly is unreliable when
                    # the key already exists from a prior session/page visit)
                    st.session_state.pop("league_selector", None)

            # Mark this bracket_player_id as processed so we don't overwrite state on re-renders
            st.session_state["_last_bracket_player"] = bracket_player_id

    with st.sidebar:
        show_legend = st.checkbox("Show legend", key="show_legend", value=True)
        is_mobile = st.session_state.get("mobile_view", False)
        st.checkbox("Mobile view", value=is_mobile, key="mobile_view")

    css_path = Path(__file__).parent.parent / "static" / "styles" / "style.css"
    with open(css_path, "r") as infile:
        table_styling = f"<style>{infile.read()}</style>"

    canvas.write(table_styling, unsafe_allow_html=True)

    def display_comparison():
        st.session_state.display_comparison = True
        st.session_state.options.compare_players = st.session_state.get("comparison", [])
        st.session_state.counter = st.session_state.counter + 1 if st.session_state.get("counter") else 1

    def remove_from_comparison(player):
        st.session_state.comparison.remove(player)
        st.session_state.counter = st.session_state.counter + 1 if st.session_state.get("counter") else 1

    def search_for_new():
        st.query_params.clear()
        st.session_state.pop("display_comparison", None)
        st.session_state.pop("_last_bracket_player", None)
        st.session_state.options.compare_players = []
        st.session_state.options.current_player = None
        st.session_state.options.current_player_id = None
        st.session_state.counter = st.session_state.counter + 1 if st.session_state.get("counter") else 1

    if (currently := st.session_state.get("comparison", [])) and st.session_state.get("display_comparison") is not True:
        canvas.write("Currently added:")

        for player in currently:
            addee_col, pop_col = canvas.columns([1, 1])

            addee_col.write(f"{st.session_state.addee_map[player]} ({player})")
            pop_col.button("Remove", on_click=remove_from_comparison, args=(player,), key=f"{player}remove")

        canvas.button("Show comparison", on_click=display_comparison, key="show_comparison_top")

    if not st.session_state.options.compare_players:
        st.session_state.options.compare_players = st.query_params.get_all("compare")

        if st.session_state.options.compare_players:
            st.session_state.display_comparison = True

    if (not st.session_state.options.compare_players) or (st.session_state.get("display_comparison") is None):
        compute_search(player=False, comparison=True)
        exit()
    else:
        users = st.session_state.options.compare_players or st.session_state.comparison

    if not player_id:
        # Show a message if we're in bracket comparison mode
        if st.session_state.get("bracket_comparison"):
            canvas.info(f"Showing comparison for all players in the same live bracket as player ID: {st.session_state.bracket_player_id}")

        search_for_new = canvas.button("Search for another player?", on_click=search_for_new, key="comparison_search_for_new")

        canvas.code(f"https://{BASE_URL}/comparison?" + urlencode({"compare": users}, doseq=True))

    player_ids = PlayerId.objects.filter(id__in=users).select_related("game_instance__player")
    player_ids = player_ids.exclude(id__in=sus_ids)
    # Get all game instances from the player_ids
    game_instances = [pid.game_instance for pid in player_ids if pid.game_instance]
    # Get all known players from these game instances
    known_players = {gi.player for gi in game_instances if gi.player}
    # Get all PlayerIds across all game instances for these players
    all_player_ids = set(PlayerId.objects.filter(game_instance__player__in=known_players).values_list("id", flat=True)) | set(users)

    hidden_query = {} if hidden_features else {"result__public": True, "position__lt": how_many_results_public_site}
    rows = TourneyRow.objects.filter(player_id__in=all_player_ids, **hidden_query)

    player_df = get_details(rows)

    patches_options = sorted([patch for patch in get_patches() if patch.version_minor], key=lambda patch: patch.start_date, reverse=True)
    graph_options = [st.session_state.options.default_graph.value] + [
        value for value in list(Graph.__members__.keys()) + patches_options if value != st.session_state.options.default_graph.value
    ]

    if player_id:
        patch = graph_options[0]
        filter_bcs = None
    else:
        patch_col, bc_col = canvas.columns([1, 1])
        patch = patch_col.selectbox("Limit results to a patch? (see side bar to change default)", graph_options)
        # Get all available battle conditions from the database, not just those in current results
        all_battle_conditions = sorted(BattleCondition.objects.all(), key=lambda bc: bc.shortcut)
        filter_bcs = bc_col.multiselect("Filter by battle conditions?", all_battle_conditions, format_func=lambda bc: f"{bc.name} ({bc.shortcut})")

    datas = [(sdf, player_id) for player_id, sdf in player_df.groupby("id") if len(sdf) >= 2]
    datas = filter_plot_datas(datas, patch, filter_bcs)
    datas = filter_league(datas)

    if not datas:
        return

    datas = sorted([(data, user) for data, user in datas if not data.empty], key=lambda datum: max(datum[0].wave), reverse=True)

    summary = pd.DataFrame(
        [
            [
                data.real_name.mode().iloc[0],
                max(data.wave),
                int(round(median(data.wave), 0)),
                len(data),
                int(round(stdev(data.wave), 0)),
                min(data.wave),
                user,
            ]
            for data, user in datas
            if len(data) >= 2
        ],
        columns=["Name", "total PB", "Median", "No. tourneys", "Stdev", "Lowest score", "Search term"],
    )
    # Use a 1-based numeric index for display (instead of starting at 0)
    summary.index = range(1, len(summary) + 1)

    # Escape any user-provided text in DataFrames
    summary = escape_df_html(summary, ["Name"])
    # Ensure how_many_slider is always defined to avoid UnboundLocalError later
    how_many_slider = None

    if player_id:
        how_many_slider = canvas.slider(
            "Narrow results to only your direct competitors?",
            0,
            len(users),
            value=[0, len(users)],
        )
        summary = summary.iloc[how_many_slider[0] : how_many_slider[1] + 1]

        narrowed_ids = summary["Search term"]
        # Renumber the index for the sliced subset to start at 1
        summary.index = range(1, len(summary) + 1)

    for data, _ in datas:
        data["real_name"] = data["real_name"].mode().iloc[0]

    try:
        pd_datas = pd.concat([data for data, _ in datas])
    except ValueError:
        canvas.warning("No data available for selected options.")
        return

    pd_datas["bcs"] = pd_datas.bcs.map(lambda bc_qs: " / ".join([bc.shortcut for bc in bc_qs]))

    if player_id:
        pd_datas = pd_datas[pd_datas.id.isin(narrowed_ids)]

    last_5_tourneys = sorted(pd_datas.date.unique())[-5:][::-1]
    last_5_bcs = [pd_datas[pd_datas.date == date].bcs.iloc[0] for date in last_5_tourneys]

    if player_id:
        last_5_bcs = ["" for _ in last_5_bcs]

    last_results = pd.DataFrame(
        [
            [
                data.real_name.unique()[0],
                user,
            ]
            + [wave_serie.iloc[0] if not (wave_serie := data[data.date == date].wave).empty else 0 for date in last_5_tourneys]
            for data, user in datas
        ],
        columns=["Name", "id", *[f"{date.month}/{date.day}: {bc}" for date, bc in zip(last_5_tourneys, last_5_bcs)]],
    )

    if player_id:
        last_results = last_results[last_results.id.isin(narrowed_ids)]

    last_results = last_results[["Name", *[f"{date.month}/{date.day}: {bc}" for date, bc in zip(last_5_tourneys, last_5_bcs)], "id"]]
    last_results.index = last_results.index + 1
    last_results = last_results.style

    pd_datas = pd_datas.drop_duplicates()

    # Build the main plot (wave over time)
    wave_fig = px.line(pd_datas, x="date", y="wave", color="real_name", markers=True, custom_data=["bcs", "position"])
    wave_fig.update_layout(showlegend=show_legend)
    wave_fig.update_yaxes(title_text=None)
    wave_fig.update_layout(margin=dict(l=20))
    wave_fig.update_traces(hovertemplate="%{y}<br>Postion: %{customdata[1]}")
    wave_fig.update_layout(hovermode="x unified")

    min_ = min(pd_datas.wave)
    max_ = max(pd_datas.wave)

    enrich_plot(wave_fig, max_, min_, pd_datas)

    # Placement plot (lower is better)
    placement_datas = pd_datas[pd_datas.position >= 1].copy()  # exclude sus
    placement_fig = px.line(placement_datas, x="date", y="position", color="real_name", markers=True)
    placement_fig.update_layout(showlegend=show_legend)
    placement_fig.update_yaxes(title_text=None)
    placement_fig.update_layout(margin=dict(l=20))
    placement_fig.update_yaxes(range=[max(pd_datas.position), min(pd_datas.position)])

    # Respect mobile view: render sequentially when mobile, else show tabs
    is_mobile = st.session_state.get("mobile_view", False)

    if is_mobile:
        canvas.plotly_chart(wave_fig, width="stretch")

        if st.session_state.options.links_toggle:
            to_be_displayed = summary.style.format(make_player_url, subset=["Search term"]).to_html(escape=False)
            canvas.write(to_be_displayed, unsafe_allow_html=True)
        else:
            canvas.dataframe(summary, width="stretch", hide_index=True)

        canvas.plotly_chart(placement_fig, width="stretch")

        if st.session_state.options.links_toggle:
            to_be_displayed = last_results.format(make_player_url, subset=["id"]).to_html(escape=False)
            canvas.write(to_be_displayed, unsafe_allow_html=True)
        else:
            canvas.dataframe(last_results, width="stretch", hide_index=True)
    else:
        # Present each chart and table in its own tab
        tab1, tab2, tab3, tab4 = canvas.tabs(["Waves over time", "Placement over Time", "Summary", "Last 5"])

        with tab1:
            canvas.plotly_chart(wave_fig, width="stretch")

        with tab2:
            canvas.plotly_chart(placement_fig, width="stretch")

        with tab3:
            if st.session_state.options.links_toggle:
                to_be_displayed = summary.style.format(make_player_url, subset=["Search term"]).to_html(escape=False)
                canvas.write(to_be_displayed, unsafe_allow_html=True)
            else:
                canvas.dataframe(summary, width="stretch", hide_index=True)

        with tab4:
            if st.session_state.options.links_toggle:
                to_be_displayed = last_results.format(make_player_url, subset=["id"]).to_html(escape=False)
                canvas.write(to_be_displayed, unsafe_allow_html=True)
            else:
                canvas.dataframe(last_results, width="stretch", hide_index=True)

    if not player_id:
        with canvas.expander("Debug data..."):
            data = {real_name: list(df.id.unique()) for real_name, df in pd_datas.groupby("real_name")}
            canvas.write("Player ids used:")
            canvas.json(data)


def get_bracket_players(player_id: str) -> tuple[list[str], str | None]:
    """
    Get all players in the same bracket as the provided player ID.

    Args:
        player_id: The player ID to find bracket members for

    Returns:
        Tuple of (list of player IDs in the same bracket, league name), or ([], None) if not found
    """
    try:
        # Get live data for all available leagues
        for league in leagues:
            include_shun = include_shun_enabled_for("comparison")
            df = get_latest_live_df(league, include_shun)

            # Find if player is in this dataframe
            player_df = df[df.player_id == player_id]

            if not player_df.empty:
                # Get the bracket ID for this player
                bracket_id = player_df.bracket.iloc[0]

                # Get all players in the same bracket
                bracket_df = df[df.bracket == bracket_id]

                # Return unique player IDs in this bracket along with the league
                return sorted(bracket_df.player_id.unique()), league

        # Player not found in any bracket
        return [], None

    except Exception as e:
        st.error(f"Error finding bracket players: {str(e)}")
        return [], None


def filter_plot_datas(datas, patch, filter_bcs):
    filtered_datas = []

    for sdf, name in datas:
        patch_df = get_patch_df(sdf, sdf, patch)

        if filter_bcs:
            sbcs = set(filter_bcs)
            patch_df = patch_df[patch_df.bcs.map(lambda table_bcs: sbcs & set(table_bcs) == sbcs)]

        tbdf = patch_df.reset_index(drop=True)
        tbdf.index = tbdf.index + 1

        if len(tbdf) >= 2:
            filtered_datas.append((tbdf, name))

    return filtered_datas


def enrich_plot(fig, max_, min_, pd_datas):
    for index, (start, version_minor, version_patch, interim) in enumerate(
        Patch.objects.all().values_list("start_date", "version_minor", "version_patch", "interim")
    ):
        name = f"0.{version_minor}.{version_patch}"
        interim = "interim" if interim else ""

        if start < pd_datas.date.min() - datetime.timedelta(days=2) or start > pd_datas.date.max() + datetime.timedelta(days=3):
            continue

        fig.add_vline(x=start, line_width=3, line_dash="dash", line_color="#888", opacity=0.4)
        fig.add_annotation(
            x=start,
            y=pd_datas.wave.max() - 300 * (index % 2 + 1),
            text=f"Patch {name}{interim} start",
            showarrow=True,
            arrowhead=1,
        )


def get_patch_df(df, player_df, patch):
    if isinstance(patch, Patch):
        patch_df = player_df[player_df.patch == patch]
    elif patch in (Graph.last_8.value, Graph.last_16.value, Graph.last_32.value):
        last_n = int(patch.split("_")[1])
        hidden_query = {} if hidden_features else dict(public=True)
        qs = set(TourneyResult.objects.filter(league=champ, **hidden_query).order_by("-date").values_list("date", flat=True)[:last_n])
        patch_df = player_df[player_df.date.isin(qs)]
    else:
        patch_df = player_df
    return patch_df


def filter_league(datas):
    # Get current patch from first dataset if available
    patch = None
    if datas and len(datas) > 0 and not datas[0][0].empty and "patch" in datas[0][0].columns:
        try:
            patch = datas[0][0]["patch"].iloc[0]
        except Exception as e:
            st.write(f"🔍 Debug: Error getting patch: {str(e)}")

    # Use patch-aware league selection (default is pre-set to bracket league when bracket_player param is used)
    league = get_league_selection(patch=patch)
    filtered_datas = [(sdf[sdf.league == league], name) for sdf, name in datas]

    # Log if no data remains after filtering
    if not filtered_datas:
        st.warning(f"No data found for league: {league}")

    return filtered_datas


def filter_lower_leagues(rows):
    # only leave top league results -- otherwise results are not comparable?
    leagues_in = rows.values_list("result__league", flat=True).distinct()

    for league in leagues:
        if league in leagues_in:
            break

    rows = rows.filter(result__league=league)
    return rows


compute_comparison()
