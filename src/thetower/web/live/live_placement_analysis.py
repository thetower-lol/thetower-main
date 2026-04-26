import datetime
import logging
from time import perf_counter

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from thetower.backend.tourney_results.league_rules import get_league_rules
from thetower.web.live.data_ops import (
    analyze_wave_placement,
    format_time_ago,
    get_placement_analysis_data,
    process_display_names,
    require_tournament_data,
)
from thetower.web.live.ui_components import render_data_status, setup_common_ui
from thetower.web.util import add_player_id, fmt_dt, get_user_tz


@require_tournament_data
def live_placement_analysis():
    st.markdown("# Live Placement Analysis")
    logging.info("Starting live placement analysis")
    t2_start = perf_counter()

    # Use common UI setup, hide league selector for auto-detect
    options, league, is_mobile = setup_common_ui(show_league_selector=False)

    # Show data refresh and shun status upfront so users see it even if cache isn't ready
    refresh_timestamp = render_data_status(league, "live_placement_cache")

    # Get placement analysis data (plus tourney start date)
    df, latest_time, bracket_creation_times, tourney_start_date = get_placement_analysis_data(league)

    # Process display names to handle duplicates
    df = process_display_names(df)

    # Show tourney start date so users know which tourney the cache is for
    try:
        st.caption(f"Tourney start date: {tourney_start_date}")
    except Exception:
        st.write(f"Tourney start date: {tourney_start_date}")

    # (Optional) If we didn't have a refresh timestamp earlier, show fallback based on latest_time
    try:
        if not refresh_timestamp:
            ts = latest_time
            if ts is None:
                refresh_text = "Unknown"
                ts_display = "Unknown"
            else:
                # Make timezone explicit: show UTC timestamp
                if ts.tzinfo is None:
                    ts_utc = ts.replace(tzinfo=datetime.timezone.utc)
                else:
                    ts_utc = ts.astimezone(datetime.timezone.utc)

                refresh_text = format_time_ago(ts_utc)
                ts_display = fmt_dt(ts_utc)

            st.caption(f"📊 Data last refreshed: {refresh_text} ({ts_display})")
    except Exception:
        # Don't break the page for display issues
        pass

    # Check for query parameters
    query_player_id = st.query_params.get("player_id")
    query_player_name = st.query_params.get("player")

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

    # Initialize selected_player from query params or session state
    initial_player = None
    if query_player_id:
        # Find player by player_id (case-insensitive by normalizing to uppercase)
        qp_upper = query_player_id.strip().upper()
        matching_players = df[df["player_id"] == qp_upper]
        if not matching_players.empty:
            initial_player = matching_players.iloc[0]["display_name"]
    elif query_player_name:
        # Find player by name (check both real_name and display_name)
        matching_players = df[
            (df["real_name"].str.lower() == query_player_name.lower()) | (df["display_name"].str.lower() == query_player_name.lower())
        ]
        if not matching_players.empty:
            initial_player = matching_players.iloc[0]["display_name"]

    # Show "Search for another player" button if we have a player selected or from query params
    if selected_id_from_session or initial_player:
        st.button("Search for another player?", on_click=search_for_new, key=f"search_new_{league}")

    # Player selection via text inputs (no dropdown required)
    st.markdown("### Enter Player")

    # Only show search inputs if no player is selected
    if not (selected_id_from_session or initial_player):
        if is_mobile:
            # In mobile view, stack inputs vertically
            name_col = st.container()
            id_col = st.container()
        else:
            # In desktop view, use side-by-side columns
            name_col, id_col = st.columns([2, 1])

        with name_col:
            selected_player = st.text_input(
                "Enter player name",
                value=search_term or "",
                key="player_name_input",
            )

        with id_col:
            player_id_input = st.text_input("Or enter Player ID", value="", key=f"player_id_input_{league}")
    else:
        selected_player = ""
        player_id_input = ""

    # Handle player_id input with partial match and cross-league search
    if player_id_input and not selected_player:
        # Normalize to uppercase to align with stored player IDs
        pid_search = player_id_input.strip().upper()

        # Search across all leagues for partial player ID matches
        from thetower.backend.tourney_results.constants import leagues as ALL_LEAGUES

        all_matches = []  # Store (player_name, player_id, league) tuples

        for lg in ALL_LEAGUES:
            try:
                df_tmp, _, _, _ = get_placement_analysis_data(lg)
                df_tmp = process_display_names(df_tmp)
                # Partial match on player_id
                match_df = df_tmp[df_tmp["player_id"].str.contains(pid_search, na=False, regex=False)]
                # Add unique players from this league
                for _, row in match_df.drop_duplicates(subset=["player_id"]).iterrows():
                    all_matches.append((row["real_name"], row["player_id"], lg))
            except Exception:
                continue

        if not all_matches:
            st.error(f"No player IDs found matching '{pid_search}' in any active tournament.")
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
            selected_player_name = all_matches[0][0]
            target_league = all_matches[0][2]
            if target_league != league:
                # Reload data for the correct league
                df, latest_time, bracket_creation_times, tourney_start_date = get_placement_analysis_data(target_league)
                df = process_display_names(df)
                league = target_league
            # Set selected_player to continue with analysis
            match_df = df[df["real_name"] == selected_player_name]
            if not match_df.empty:
                selected_player = match_df.iloc[0]["display_name"]
            else:
                st.error("Error loading player data.")
                return

    # Check if a player ID was selected from multiple matches
    if selected_id_from_session:
        # Search across all leagues to find which league this player is in
        from thetower.backend.tourney_results.constants import leagues as ALL_LEAGUES

        found_player = None
        found_league = None

        for lg in ALL_LEAGUES:
            try:
                df_tmp, _, _, _ = get_placement_analysis_data(lg)
                df_tmp = process_display_names(df_tmp)
                match_df = df_tmp[df_tmp["player_id"] == selected_id_from_session]
                if not match_df.empty:
                    found_player = match_df.iloc[0]["display_name"]
                    found_league = lg
                    break
            except Exception:
                continue

        if found_player and found_league:
            if found_league != league:
                # Reload data for the correct league
                df, latest_time, bracket_creation_times, tourney_start_date = get_placement_analysis_data(found_league)
                df = process_display_names(df)
                league = found_league
            selected_player = found_player
            # Skip name-based search since we already have the exact player
        else:
            st.error(f"Player ID {selected_id_from_session} not found in any active tournament.")
            return
    elif initial_player:
        # Use query param player - skip name-based search since we already have the exact player
        selected_player = initial_player
    elif not selected_player or not selected_player.strip():
        st.info("Enter a player name or Player ID to analyze placement")
        return

    # Only do name-based search if player wasn't found via session state or query params
    if not (selected_id_from_session or initial_player):
        # Store search term for later
        st.session_state.player_search_term = selected_player

        # Try matching by entered name across all leagues (supports partial matches)
        name_lower = selected_player.strip().lower()

        # Search across all leagues
        from thetower.backend.tourney_results.constants import leagues as ALL_LEAGUES

        all_matches = []  # Store (player_name, player_id, league) tuples

        for lg in ALL_LEAGUES:
            try:
                df_tmp, _, _, _ = get_placement_analysis_data(lg)
                df_tmp = process_display_names(df_tmp)
                match_df = df_tmp[
                    (df_tmp["real_name"].str.lower().str.contains(name_lower, na=False, regex=False))
                    | (df_tmp["display_name"].str.lower().str.contains(name_lower, na=False, regex=False))
                ]
                # Add unique players from this league
                for _, row in match_df.drop_duplicates(subset=["real_name"]).iterrows():
                    all_matches.append((row["real_name"], row["player_id"], lg))
            except Exception:
                continue

        if not all_matches:
            st.error("Player not found by name in any active league's tournament data.")
            return
        elif len(all_matches) > 1:
            st.warning("Multiple players match. Please select one:")

            # Display each match with name, ID, league, and button
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
            selected_player_name = all_matches[0][0]
            target_league = all_matches[0][2]
            if target_league != league:
                # Reload data for the correct league
                df, latest_time, bracket_creation_times, tourney_start_date = get_placement_analysis_data(target_league)
                df = process_display_names(df)
                league = target_league
            # Get display name
            match_df = df[df["real_name"] == selected_player_name]
            if not match_df.empty:
                selected_player = match_df.iloc[0]["display_name"]
            else:
                st.error("Error loading player data.")
                return

    # Get the player's highest wave
    wave_to_analyze = df[df.display_name == selected_player].wave.max()
    st.write(f"Analyzing placement for {selected_player}'s highest wave: {wave_to_analyze}")

    # Analyze placements
    results = analyze_wave_placement(df, wave_to_analyze, latest_time)

    # analyze_wave_placement treats wave_to_analyze as a hypothetical NEW entrant (+1 to rank).
    # For the player's own bracket they are already present, so the formula overcounts by 1.
    # Correct that entry so Best Case / Worst Case bounds and the marker are all consistent.
    player_bracket_id = df[df["display_name"] == selected_player]["bracket"].iloc[0]
    for r in results:
        if r["Bracket"] == player_bracket_id:
            parts = r["Would Place"].split("/")
            corrected_rank = max(1, int(parts[0]) - 1)
            r["Would Place"] = f"{corrected_rank}/{parts[1]}"
            break

    # Process results for display
    results_df = pd.DataFrame(results)
    results_df["Creation Time"] = results_df["Bracket"].map(bracket_creation_times)
    # Add numeric position column for sorting
    results_df["Position"] = results_df["Would Place"].str.split("/").str[0].astype(int)
    # Sort by creation time initially
    results_df = results_df.sort_values("Creation Time")

    # Group by checkpoints (30-minute intervals) and calculate averages and quantiles
    results_df["Checkpoint"] = results_df["Creation Time"].dt.floor("30min")

    def q25(x):
        return x.quantile(0.25)

    def q75(x):
        return x.quantile(0.75)

    checkpoint_df = (
        results_df.groupby("Checkpoint")
        .agg(
            {
                "Position": ["mean", q25, q75, "min", "max"],
                "Top Wave": "mean",
                "Median Wave": "mean",
                "Players Above": "mean",
            }
        )
        .round(1)
        .reset_index()
    )

    # Flatten multi-level column names
    checkpoint_df.columns = ["_".join(col).strip("_") if col[1] else col[0] for col in checkpoint_df.columns.values]

    # Rename columns for display
    checkpoint_df = checkpoint_df.rename(
        columns={
            "Position_mean": "Avg Placement",
            "Position_q25": "Q25 Placement",
            "Position_q75": "Q75 Placement",
            "Position_min": "Best Case",
            "Position_max": "Worst Case",
            "Top Wave_mean": "Avg Top Wave",
            "Median Wave_mean": "Avg Median Wave",
            "Players Above_mean": "Avg Players Above",
        }
    )

    # Convert checkpoint timestamps to user's local timezone for display
    checkpoint_df["Checkpoint"] = pd.to_datetime(checkpoint_df["Checkpoint"]).apply(
        lambda ts: fmt_dt(ts.replace(tzinfo=datetime.timezone.utc) if ts.tzinfo is None else ts, fmt="%Y-%m-%d %H:%M:%S")
    )

    st.write(f"Analysis for wave {wave_to_analyze} (averaged by 30-minute checkpoints):")
    # Display condensed dataframe
    st.dataframe(
        checkpoint_df,
        hide_index=True,
        column_config={
            "Avg Placement": st.column_config.NumberColumn("Avg Placement", help="Average placement position across brackets in this checkpoint"),
            "Q25 Placement": st.column_config.NumberColumn(
                "Q25 Placement", help="25th percentile placement — 25% of brackets would place at this position or better"
            ),
            "Q75 Placement": st.column_config.NumberColumn(
                "Q75 Placement", help="75th percentile placement — 75% of brackets would place at this position or better"
            ),
            "Best Case": st.column_config.NumberColumn("Best Case", help="Best (lowest) placement position in this checkpoint"),
            "Worst Case": st.column_config.NumberColumn("Worst Case", help="Worst (highest) placement position in this checkpoint"),
        },
    )

    # Player's position comes from the already-corrected results entry for their bracket.
    player_bracket = df[df["display_name"] == selected_player]["bracket"].iloc[0]
    player_creation_time = bracket_creation_times[player_bracket]
    # Convert player's creation time to user's local timezone to match the plot x-axis
    _pct = player_creation_time if player_creation_time.tzinfo is not None else player_creation_time.replace(tzinfo=datetime.timezone.utc)
    player_creation_time_local = _pct.astimezone(get_user_tz()).replace(tzinfo=None)
    player_result = next((r for r in results if r["Bracket"] == player_bracket), None)
    if player_result:
        player_position = int(player_result["Would Place"].split("/")[0])
    else:
        # Fallback: compute directly using the tie rule (above + tied, tied includes the player)
        bracket_at_latest = df[(df["bracket"] == player_bracket) & (df["datetime"] == latest_time)]
        player_wave = bracket_at_latest[bracket_at_latest["display_name"] == selected_player]["wave"].iloc[0]
        above = int((bracket_at_latest["wave"] > player_wave).sum())
        tied = int((bracket_at_latest["wave"] == player_wave).sum())
        player_position = above + tied

    # Create plot data using checkpoint averages
    plot_df = checkpoint_df.copy()
    plot_df["Creation Time"] = pd.to_datetime(checkpoint_df["Checkpoint"])

    # Create placement timeline plot
    fig = px.scatter(
        plot_df,
        x="Creation Time",
        y="Avg Placement",
        title=f"Average Placement Timeline for {wave_to_analyze} waves",
        labels={"Creation Time": "Checkpoint Time", "Avg Placement": "Average Placement Position"},
        trendline="lowess",
        trendline_options=dict(frac=0.2),
    )

    # Update the legend names for the scatter plot and trendline
    fig.data[0].name = "Average Placement"
    fig.data[0].showlegend = True
    if len(fig.data) > 1:  # Trendline trace exists
        fig.data[1].name = "Lowess Trendline"
        fig.data[1].showlegend = True

    # Add Q25 line (better bracket scenarios — lower position number)
    fig.add_scatter(
        x=plot_df["Creation Time"],
        y=plot_df["Q25 Placement"],
        mode="lines",
        line=dict(color="green", width=2, dash="dash"),
        name="Q25 (25th Pct)",
        showlegend=True,
    )

    # Add Q75 line (weaker bracket scenarios — higher position number)
    fig.add_scatter(
        x=plot_df["Creation Time"],
        y=plot_df["Q75 Placement"],
        mode="lines",
        line=dict(color="red", width=2, dash="dash"),
        name="Q75 (75th Pct)",
        showlegend=True,
    )

    # Add min/max lines (dotted, lighter) to show absolute range
    fig.add_scatter(
        x=plot_df["Creation Time"],
        y=plot_df["Best Case"],
        mode="lines",
        line=dict(color="green", width=1, dash="dot"),
        name="Best Case (Min)",
        showlegend=True,
    )

    fig.add_scatter(
        x=plot_df["Creation Time"],
        y=plot_df["Worst Case"],
        mode="lines",
        line=dict(color="red", width=1, dash="dot"),
        name="Worst Case (Max)",
        showlegend=True,
    )

    # Add player's actual position marker
    fig.add_scatter(
        x=[player_creation_time_local],
        y=[player_position],
        mode="markers",
        marker=dict(symbol="x", size=15, color="purple"),
        name="Actual Position",
        showlegend=True,
    )

    # Update plot layout
    fig.update_layout(yaxis_title="Position", height=400, margin=dict(l=20, r=20, t=40, b=20), legend=dict(orientation="h" if is_mobile else "v"))
    fig.update_yaxes(autorange="reversed")

    st.plotly_chart(fig, width="stretch")

    # --- Placement Histogram ---
    st.markdown(f"### Placement Distribution Across All Brackets in {league}")
    st.caption(
        "How many brackets would give you each placement for this wave. Your actual placement is shown in a stronger shade of its zone colour."
    )

    _rules = get_league_rules(league)
    PROMOTE_CUTOFF: int | None = _rules.promote_cutoff
    RELEGATE_CUTOFF: int | None = _rules.relegate_cutoff
    REWARD_BRACKET_BOUNDARIES: list[float] = list(_rules.reward_boundaries)

    pos_counts = results_df["Position"].value_counts().sort_index().reset_index()
    pos_counts.columns = ["Position", "Brackets"]

    total_brackets = len(results_df)
    promote_n = int((results_df["Position"] <= PROMOTE_CUTOFF).sum()) if PROMOTE_CUTOFF is not None else 0
    relegate_n = int((results_df["Position"] >= RELEGATE_CUTOFF).sum()) if RELEGATE_CUTOFF is not None else 0
    safe_n = total_brackets - promote_n - relegate_n

    promote_pct = promote_n / total_brackets * 100 if total_brackets else 0
    relegate_pct = relegate_n / total_brackets * 100 if total_brackets else 0
    safe_pct = safe_n / total_brackets * 100 if total_brackets else 0

    pos_counts["Pct"] = pos_counts["Brackets"] / total_brackets * 100 if total_brackets else 0

    # X-range: show 3 positions on either side of the actual data (handles oversized brackets with 31/32 people)
    data_min = int(pos_counts["Position"].min())
    data_max = int(pos_counts["Position"].max())
    x_range_min = max(0.4, data_min - 3)
    x_range_max = data_max + 3

    # Zone-tinted bar colours; player's position gets a strong version of their zone colour.
    # All colours are medium-saturation so they read on both light and dark Streamlit themes:
    #   Promotion  — blue  (#93c5fd pale / #2563eb strong)
    #   Relegation — orange (#fdba74 pale / #c2410c strong)
    #   Safe       — grey  (#9ca3af pale / #d97706 amber highlight — distinct hue, visible on both bg colours)
    def _bar_color(p: int) -> str:
        in_promote = PROMOTE_CUTOFF is not None and p <= PROMOTE_CUTOFF
        in_relegate = RELEGATE_CUTOFF is not None and p >= RELEGATE_CUTOFF
        if p == player_position:
            if in_promote:
                return "#2563eb"  # strong blue
            elif in_relegate:
                return "#c2410c"  # strong orange
            return "#d97706"  # amber — distinct from blue/orange, visible on light & dark
        elif in_promote:
            return "#93c5fd"  # medium blue
        elif in_relegate:
            return "#fdba74"  # medium orange
        return "#9ca3af"  # medium grey

    bar_colors = [_bar_color(p) for p in pos_counts["Position"]]

    fig_hist = go.Figure()
    fig_hist.add_trace(
        go.Bar(
            x=pos_counts["Position"],
            y=pos_counts["Pct"],
            marker_color=bar_colors,
            hovertemplate="Position %{x}: %{y:.1f}% of brackets<extra></extra>",
        )
    )

    # Promotion zone boundary (blue) — only for leagues that have promotion
    if PROMOTE_CUTOFF is not None:
        fig_hist.add_vline(x=PROMOTE_CUTOFF + 0.5, line_dash="solid", line_color="rgba(59,130,246,0.8)", line_width=2)
    # Demotion zone boundary (orange) only for leagues with demotion (Platinum/Champion/Legend)
    if RELEGATE_CUTOFF is not None:
        fig_hist.add_vline(x=RELEGATE_CUTOFF - 0.5, line_dash="solid", line_color="rgba(234,88,12,0.8)", line_width=2)

    # Lighter dotted lines for reward tier boundaries
    for rb in REWARD_BRACKET_BOUNDARIES:
        fig_hist.add_vline(x=rb, line_dash="dot", line_color="rgba(160,160,160,0.5)", line_width=1)

    # Zone labels with percentages inside the chart near the top
    safe_left_bound = PROMOTE_CUTOFF + 1 if PROMOTE_CUTOFF is not None else x_range_min
    safe_right_bound = RELEGATE_CUTOFF - 1 if RELEGATE_CUTOFF is not None else x_range_max
    if PROMOTE_CUTOFF is not None:
        fig_hist.add_annotation(
            x=(1 + PROMOTE_CUTOFF) / 2,
            y=0.97,
            yref="paper",
            text=f"Promote<br>{promote_pct:.0f}%",
            showarrow=False,
            font=dict(size=11),
            xanchor="center",
            yanchor="top",
        )
    fig_hist.add_annotation(
        x=(safe_left_bound + safe_right_bound) / 2,
        y=0.97,
        yref="paper",
        text=f"Safe<br>{safe_pct:.0f}%",
        showarrow=False,
        font=dict(size=11),
        xanchor="center",
        yanchor="top",
    )
    if RELEGATE_CUTOFF is not None:
        relegate_center = (RELEGATE_CUTOFF + x_range_max) / 2
        fig_hist.add_annotation(
            x=relegate_center,
            y=0.97,
            yref="paper",
            text=f"Relegate<br>{relegate_pct:.0f}%",
            showarrow=False,
            font=dict(size=11),
            xanchor="center",
            yanchor="top",
        )

    fig_hist.update_layout(
        title=f"Placement distribution for wave {wave_to_analyze} ({league})",
        xaxis_title="Placement Position",
        yaxis_title="% of Brackets",
        yaxis=dict(ticksuffix="%"),
        height=350,
        margin=dict(l=20, r=20, t=60, b=20),
        showlegend=False,
        xaxis=dict(dtick=1, range=[x_range_min, x_range_max]),
    )
    st.plotly_chart(fig_hist, width="stretch")

    # Log execution time
    t2_stop = perf_counter()
    logging.info(f"Full live_placement_analysis for {league} took {t2_stop - t2_start}")


live_placement_analysis()
