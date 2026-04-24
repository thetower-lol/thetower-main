import logging
import os
from pathlib import Path
from time import perf_counter

import pandas as pd
import plotly.express as px
import streamlit as st

from thetower.backend.tourney_results.data import get_player_id_lookup
from thetower.backend.tourney_results.formatting import BASE_URL, make_player_url
from thetower.backend.tourney_results.shun_config import include_shun_enabled_for
from thetower.web.live.data_ops import (
    format_time_ago,
    get_bracket_data,
    get_data_refresh_timestamp,
    get_live_data,
    initialize_bracket_state,
    process_bracket_selection,
    process_display_names,
    require_tournament_data,
)
from thetower.web.live.ui_components import setup_common_ui
from thetower.web.util import add_player_id, fmt_dt


@require_tournament_data
def live_bracket():
    st.markdown("# Live Bracket")
    logging.info("Starting live bracket")
    t2_start = perf_counter()

    # Use common UI setup, hide league selector for auto-detect
    options, league, is_mobile = setup_common_ui(show_league_selector=False)

    # Get data refresh timestamp
    refresh_timestamp = get_data_refresh_timestamp(league)
    if refresh_timestamp:
        time_ago = format_time_ago(refresh_timestamp)
        st.caption(f"📊 Data last refreshed: {time_ago} ({fmt_dt(refresh_timestamp)})")
        # Indicate whether shunned players are included for this page (only on hidden site)
        hidden_features = os.environ.get("HIDDEN_FEATURES")
        if hidden_features:
            try:
                include_shun = include_shun_enabled_for("live_bracket")
                st.caption(f"🔍 Including shunned players: {'Yes' if include_shun else 'No'}")
            except Exception:
                # Don't break the page if the shun config can't be read
                pass
    else:
        st.caption("📊 Data refresh time: Unknown")

    # Get live data and process brackets
    try:
        include_shun = include_shun_enabled_for("live_bracket")
        df = get_live_data(league, include_shun)
        bracket_order, fullish_brackets = get_bracket_data(df)
        df_filtered = df[df.bracket.isin(fullish_brackets)].copy()  # no sniping

    except (IndexError, ValueError):
        if options.current_player_id:
            # Get player's known name
            lookup = get_player_id_lookup()
            known_name = lookup.get(options.current_player_id, options.current_player_id)
            st.error(f"{known_name} ({options.current_player_id}) hasn't participated in this tournament.")
        else:
            st.error("No tournament data available.")
        return

    # Check if requested player exists and handle anti-snipe protection
    if options.current_player_id:
        player_in_filtered_data = options.current_player_id in df_filtered.player_id.values

        if not player_in_filtered_data:
            # Player either doesn't exist or is in a partial bracket during entry period
            # Don't disclose which to prevent sniping
            lookup = get_player_id_lookup()
            known_name = lookup.get(options.current_player_id, options.current_player_id)
            st.error(f"{known_name} ({options.current_player_id}) hasn't participated in this tournament.")
            return

    # Now use filtered data for display
    df = df_filtered
    bracket_order, fullish_brackets = get_bracket_data(df)

    # Function to clear selection and search again
    def search_for_new():
        st.query_params.clear()
        st.session_state.options.current_player = None
        st.session_state.options.current_player_id = None
        if "player_id" in st.session_state:
            st.session_state.pop("player_id")
        if "player_search_term" in st.session_state:
            st.session_state.pop("player_search_term")

    # Check if a player was selected from multiple matches
    selected_id_from_session = st.session_state.get("player_id")
    search_term = st.session_state.get("player_search_term")

    # Show "Search for another player" button if we have a player selected or from query params
    if selected_id_from_session or options.current_player or options.current_player_id:
        st.button("Search for another player?", on_click=search_for_new, key=f"search_new_{league}")

    # Only show search inputs if no player is selected
    if not (selected_id_from_session or options.current_player or options.current_player_id):
        name_col, id_col = st.columns(2)
        selected_real_name_input = name_col.text_input("Search by Player Name", value=search_term or "", key=f"player_name_input_{league}")
        selected_player_id_input = id_col.text_input("Or by Player ID", value="", key=f"player_id_input_{league}")
        # Optional bracket ID search
        selected_bracket_input = st.text_input("Or search by Bracket ID", value="", key=f"bracket_id_input_{league}")
    else:
        selected_real_name_input = ""
        selected_player_id_input = ""
        selected_bracket_input = ""

    # Initialize bracket navigation
    bracket_idx = initialize_bracket_state(bracket_order, league)
    selected_real_name = None
    selected_player_id = None
    selected_bracket = None

    # Handle selection methods via text inputs
    if selected_id_from_session:
        # Player ID selected from multi-match list - use it for cross-league search
        selected_player_id = selected_id_from_session
    elif options.current_player:
        selected_real_name = options.current_player
    elif options.current_player_id:
        selected_player_id = options.current_player_id
    else:
        # Process inputs with cross-league search
        if selected_player_id_input.strip():
            pid_search = selected_player_id_input.strip().upper()

            # Search across all leagues for partial player ID matches
            from thetower.backend.tourney_results.constants import leagues as ALL_LEAGUES

            all_matches = []  # Store (player_name, player_id, league) tuples

            for lg in ALL_LEAGUES:
                try:
                    include_shun = include_shun_enabled_for("live_bracket")
                    df_tmp = get_live_data(lg, include_shun)
                    order_tmp, full_tmp = get_bracket_data(df_tmp)
                    df_tmp = df_tmp[df_tmp.bracket.isin(full_tmp)].copy()
                    if not df_tmp.empty:
                        # Partial match on player_id
                        match_df = df_tmp[df_tmp["player_id"].str.contains(pid_search, na=False, regex=False)]
                        # Add unique players from this league
                        for _, row in match_df.drop_duplicates(subset=["player_id"]).iterrows():
                            all_matches.append((row["real_name"], row["player_id"], lg))
                except Exception:
                    continue

            if not all_matches:
                st.error(f"No player IDs found matching '{pid_search}' in any active league.")
                return
            elif len(all_matches) > 1:
                # Show multiple matches sorted by player ID
                all_matches.sort(key=lambda x: x[1])
                st.warning("Multiple player IDs match. Please select one:")
                for player_name, player_id, player_league in all_matches:
                    name_col, id_col, league_col, button_col = st.columns([3, 1, 1, 1])
                    name_col.write(player_name)
                    id_col.write(player_id)
                    league_col.write(player_league)
                    if button_col.button("Select", key=f"select_id_{player_id}_{player_league}", on_click=add_player_id, args=(player_id,)):
                        pass
                return
            else:
                # Single match found
                selected_real_name = all_matches[0][0]
                league = all_matches[0][2]
                # Reload data for the correct league
                include_shun = include_shun_enabled_for("live_bracket")
                df = get_live_data(league, include_shun)
                bracket_order, fullish_brackets = get_bracket_data(df)
                df = df[df.bracket.isin(fullish_brackets)].copy()
        elif selected_real_name_input.strip():
            # Search across all leagues for partial matches
            from thetower.backend.tourney_results.constants import leagues as ALL_LEAGUES

            search_name = selected_real_name_input.strip()
            name_lower = search_name.lower()
            all_matches = []  # Store (player_name, player_id, league) tuples

            for lg in ALL_LEAGUES:
                try:
                    include_shun = include_shun_enabled_for("live_bracket")
                    df_tmp = get_live_data(lg, include_shun)
                    order_tmp, full_tmp = get_bracket_data(df_tmp)
                    df_tmp = df_tmp[df_tmp.bracket.isin(full_tmp)].copy()
                    if not df_tmp.empty:
                        # Partial match on real_name and name
                        match_df = df_tmp[(df_tmp["real_name"].str.lower().str.contains(name_lower, na=False, regex=False))]
                        if "name" in df_tmp.columns:
                            match_df_alt = df_tmp[(df_tmp["name"].str.lower().str.contains(name_lower, na=False, regex=False))]
                            match_df = pd.concat([match_df, match_df_alt]).drop_duplicates()

                        # Add unique players from this league
                        for _, row in match_df.drop_duplicates(subset=["real_name"]).iterrows():
                            all_matches.append((row["real_name"], row["player_id"], lg))
                except Exception:
                    continue

            if not all_matches:
                st.error(f"No players found matching '{search_name}' in any active league.")
                return
            elif len(all_matches) > 1:
                # Show multiple matches sorted by name
                all_matches.sort(key=lambda x: x[0].lower())
                st.warning("Multiple players match. Please select one:")
                for player_name, player_id, player_league in all_matches:
                    name_col, id_col, league_col, button_col = st.columns([3, 1, 1, 1])
                    name_col.write(player_name)
                    id_col.write(player_id)
                    league_col.write(player_league)
                    if button_col.button("Select", key=f"select_{player_name}_{player_league}", on_click=add_player_id, args=(player_id,)):
                        pass
                return
            else:
                # Single match found
                selected_real_name = all_matches[0][0]
                league = all_matches[0][2]
                # Reload data for the correct league
                include_shun = include_shun_enabled_for("live_bracket")
                df = get_live_data(league, include_shun)
                bracket_order, fullish_brackets = get_bracket_data(df)
                df = df[df.bracket.isin(fullish_brackets)].copy()
            # Store search term for later
            st.session_state.player_search_term = search_name
        elif selected_bracket_input.strip():
            selected_bracket = selected_bracket_input.strip()

    if not any([selected_real_name, selected_player_id, selected_bracket]):
        return

    try:
        # Process bracket selection using data_ops utility
        bracket_id, tdf, selected_real_name, bracket_idx = process_bracket_selection(
            df, selected_real_name, selected_player_id, selected_bracket, bracket_order
        )
        # Update session state for selected bracket index but do not render nav controls
        st.session_state[f"current_bracket_idx_{league}"] = bracket_idx
        # Clear the session state selection flag
        if "player_id" in st.session_state:
            st.session_state.pop("player_id")
    except ValueError as e:
        error_msg = str(e)
        if "MULTIPLE_MATCHES:" in error_msg:
            # Extract matches and show selection with buttons
            # Handle both "MULTIPLE_MATCHES:..." and "Selection not found: MULTIPLE_MATCHES:..." formats
            matches_part = error_msg.split("MULTIPLE_MATCHES:", 1)[1]
            matches = matches_part.split(", ")
            st.warning("Multiple players match. Please select one:")

            # Display each match with name, ID, league, and button (like /player)
            for player_name in matches:
                # Get player ID for this name
                player_data = df[df.real_name == player_name]
                if not player_data.empty:
                    player_id = player_data.iloc[0].player_id
                    name_col, id_col, league_col, button_col = st.columns([3, 1, 1, 1])
                    name_col.write(player_name)
                    id_col.write(player_id)
                    league_col.write(league)
                    if button_col.button("Select", key=f"select_{player_name}_{league}", on_click=add_player_id, args=(player_id,)):
                        pass
            return
        elif selected_player_id:
            # Get player's known name
            lookup = get_player_id_lookup()
            known_name = lookup.get(selected_player_id, selected_player_id)
            st.error(f"{known_name} (#{selected_player_id}) hasn't participated in this tournament.")
            return
        else:
            st.error(error_msg)
            return

    if not any([selected_real_name, selected_player_id, selected_bracket]):
        return

    try:
        # Process bracket selection using data_ops utility
        bracket_id, tdf, selected_real_name, bracket_idx = process_bracket_selection(
            df, selected_real_name, selected_player_id, selected_bracket, bracket_order
        )
        # Update session state for selected bracket index but do not render nav controls
        st.session_state[f"current_bracket_idx_{league}"] = bracket_idx
    except ValueError as e:
        error_msg = str(e)
        if "MULTIPLE_MATCHES:" in error_msg:
            # Extract matches and show selection with buttons
            # Handle both "MULTIPLE_MATCHES:..." and "Selection not found: MULTIPLE_MATCHES:..." formats
            matches_part = error_msg.split("MULTIPLE_MATCHES:", 1)[1]
            matches = matches_part.split(", ")
            st.warning(f"Multiple players match '{selected_real_name_input}'. Please select one:")

            # Display each match with name, ID, league, and button (like /player)
            for player_name in matches:
                # Get player ID for this name
                player_data = df[df.real_name == player_name]
                if not player_data.empty:
                    player_id = player_data.iloc[0].player_id
                    name_col, id_col, league_col, button_col = st.columns([3, 1, 1, 1])
                    name_col.write(player_name)
                    id_col.write(player_id)
                    league_col.write(league)
                    if button_col.button("Select", key=f"select_{player_name}_{league}", on_click=add_player_id, args=(player_id,)):
                        pass
            return
        elif selected_player_id:
            # Get player's known name
            lookup = get_player_id_lookup()
            known_name = lookup.get(selected_player_id, selected_player_id)
            st.error(f"{known_name} (#{selected_player_id}) hasn't participated in this tournament.")
            return
        else:
            st.error(error_msg)
            return

    # Create a copy of the DataFrame to avoid SettingWithCopyWarning
    tdf = tdf.copy()

    # Display bracket information
    player_ids = sorted(tdf.player_id.unique())
    # Use loc for datetime conversion
    tdf.loc[:, "datetime"] = pd.to_datetime(tdf["datetime"])
    bracket_start_time = tdf["datetime"].min()
    st.info(f"Bracket started at approx.: {fmt_dt(bracket_start_time)}")

    # Process display names and create visualization
    tdf = process_display_names(tdf)
    fig = px.line(tdf, x="datetime", y="wave", color="display_name", title="Live bracket score", markers=True, line_shape="linear")
    fig.update_traces(mode="lines+markers")
    fig.update_layout(xaxis_title="Time", yaxis_title="Wave", legend_title="real_name", hovermode="closest")
    fig.update_traces(hovertemplate="%{y}")
    st.plotly_chart(fig, width="stretch")

    # Process and display latest data
    last_moment = tdf.datetime.max()
    # Create a copy and use loc for setting index
    ldf = tdf[tdf.datetime == last_moment].copy()
    ldf.loc[:, "datetime"] = pd.to_datetime(ldf["datetime"])
    ldf = ldf.reset_index(drop=True)
    ldf.index = pd.RangeIndex(start=1, stop=len(ldf) + 1)
    ldf = process_display_names(ldf)

    # Use loc for safer column selection
    display_df = ldf.loc[:, ["player_id", "name", "real_name", "wave", "datetime"]]

    # Create table HTML
    st.write(display_df.style.format(make_player_url, subset=["player_id"]).to_html(escape=False), unsafe_allow_html=True)

    css_path = Path(__file__).parent.parent / "static" / "styles" / "style.css"
    with open(css_path, "r") as infile:
        table_styling = f"<style>{infile.read()}</style>"
    st.write(table_styling, unsafe_allow_html=True)

    # Display comparison links
    comparison_container = st.container()
    with comparison_container:
        if player_ids:
            # Use selected player ID if available, otherwise find ID for selected name, otherwise first player
            if selected_player_id:
                comparison_player_id = selected_player_id
            elif selected_real_name:
                # Find player ID for the selected name
                selected_player_data = tdf[tdf.real_name == selected_real_name]
                comparison_player_id = selected_player_data.player_id.iloc[0] if not selected_player_data.empty else player_ids[0]
            else:
                # Bracket navigation - use first player
                comparison_player_id = player_ids[0]
            bracket_url = f"https://{BASE_URL}/comparison?bracket_player={comparison_player_id}"
            st.write(f'<a href="{bracket_url}">See comparison</a>', unsafe_allow_html=True)

    # Log execution time
    t2_stop = perf_counter()
    logging.info(f"Full live_bracket for {league} took {t2_stop - t2_start}")


live_bracket()
