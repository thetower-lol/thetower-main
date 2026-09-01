import time
from urllib.parse import urlencode

import pandas as pd
import streamlit as st

from thetower.backend.tourney_results.constants import leagues, top_league
from thetower.backend.tourney_results.formatting import BASE_URL
from thetower.backend.tourney_results.models import TourneyRow


def main():
    st.title("League Progression Analysis — Admin")

    st.markdown("""
    This page shows player IDs that have no participation in the league preceding the selected league.
    Useful for identifying players who reached a league without participating in the previous tier.
    """)

    # League selector and controls in one row
    col1, col2, col3 = st.columns([2, 1, 1])
    with col1:
        selected_league = st.selectbox(
            "Select League to Analyze",
            options=leagues,
            index=leagues.index(top_league),
            help="Choose the league to analyze progression for",
            label_visibility="collapsed",
        )
    with col2:
        page_size = st.selectbox(
            "Results per page",
            options=[50, 100, 250, 500],
            index=2,  # Default to 250
            help="Number of players to show per page",
            label_visibility="collapsed",
        )
    with col3:
        page = st.number_input("Page", min_value=1, value=1, step=1, help="Page number to display", label_visibility="collapsed")

    # Define league hierarchy (from lowest to highest)
    league_hierarchy = leagues[::-1]

    try:
        # Find the league that precedes the selected one
        if selected_league not in league_hierarchy:
            st.error(f"Unknown league: {selected_league}")
            return

        league_index = league_hierarchy.index(selected_league)
        if league_index == 0:
            st.info("Copper is the lowest league - no preceding league to check.")
            return

        preceding_league = league_hierarchy[league_index - 1]

        st.subheader(f"Players in {selected_league} with No {preceding_league} Experience")

        # Create single status update placeholder
        status_box = st.empty()

        # Show initial loading message
        status_box.info("🔄 Starting league progression analysis...")

        # Get players in selected league but NOT in preceding league using more efficient query
        print(f"[{time.strftime('%H:%M:%S')}] Starting league progression analysis for {selected_league} vs {preceding_league}")
        with st.spinner(f"Analyzing {selected_league} vs {preceding_league} participation..."):
            start_time = time.time()
            print(f"[{time.strftime('%H:%M:%S')}] Entered spinner, starting SQL query")

            # Use raw SQL for maximum efficiency - find players in selected league but not in preceding
            from django.db import connection

            sql_start = time.time()
            print(f"[{time.strftime('%H:%M:%S')}] Starting raw SQL query execution")

            # Get banned player IDs to exclude
            banned_player_ids = set()
            try:
                from thetower.backend.sus.models import ModerationRecord

                banned_player_ids = ModerationRecord.get_active_moderation_ids("ban")
                print(f"[{time.strftime('%H:%M:%S')}] Found {len(banned_player_ids)} banned players to exclude")
            except Exception as e:
                print(f"[{time.strftime('%H:%M:%S')}] Error getting banned players: {e}")

            # Get sus player IDs to indicate
            sus_player_ids = set()
            try:
                sus_player_ids = ModerationRecord.get_active_moderation_ids("sus")
                print(f"[{time.strftime('%H:%M:%S')}] Found {len(sus_player_ids)} sus players to indicate")
            except Exception as e:
                print(f"[{time.strftime('%H:%M:%S')}] Error getting sus players: {e}")

            # Build exclusion clause for banned players
            banned_exclusion = ""
            if banned_player_ids:
                banned_ids_str = ",".join(f"'{pid}'" for pid in banned_player_ids)
                banned_exclusion = f"AND tr.player_id NOT IN ({banned_ids_str})"

            with connection.cursor() as cursor:
                # Calculate offset for pagination
                offset = (page - 1) * page_size

                # Get players who have participated in selected league but NOT in preceding league
                cursor.execute(
                    f"""
                    SELECT DISTINCT tr.player_id
                    FROM tourney_results_tourneyrow tr
                    JOIN tourney_results_tourneyresult res ON tr.result_id = res.id
                    WHERE res.league = %s
                    {banned_exclusion}
                    AND tr.player_id NOT IN (
                        SELECT DISTINCT tr2.player_id
                        FROM tourney_results_tourneyrow tr2
                        JOIN tourney_results_tourneyresult res2 ON tr2.result_id = res2.id
                        WHERE res2.league = %s
                    )
                    ORDER BY tr.player_id
                    LIMIT %s OFFSET %s
                """,
                    [selected_league, preceding_league, page_size, offset],
                )

                players_missing_preceding = [row[0] for row in cursor.fetchall()]
                print(f"[{time.strftime('%H:%M:%S')}] Got {len(players_missing_preceding)} player IDs from page {page} (limit {page_size})")

                # Get total count
                cursor.execute(
                    f"""
                    SELECT COUNT(DISTINCT tr.player_id)
                    FROM tourney_results_tourneyrow tr
                    JOIN tourney_results_tourneyresult res ON tr.result_id = res.id
                    WHERE res.league = %s
                    {banned_exclusion}
                    AND tr.player_id NOT IN (
                        SELECT DISTINCT tr2.player_id
                        FROM tourney_results_tourneyrow tr2
                        JOIN tourney_results_tourneyresult res2 ON tr2.result_id = res2.id
                        WHERE res2.league = %s
                    )
                """,
                    [selected_league, preceding_league],
                )

                total_missing = cursor.fetchone()[0]
                print(f"[{time.strftime('%H:%M:%S')}] Got total count: {total_missing}")

            sql_time = time.time() - sql_start
            print(f"[{time.strftime('%H:%M:%S')}] SQL queries completed in {sql_time:.2f} seconds")
            status_box.info(f"🔍 SQL Query completed in {sql_time:.2f} seconds - processing results...")

            if not players_missing_preceding and page > 1:
                st.info(f"No more results for page {page}. Try a lower page number.")
                return

            st.write(f"Found {total_missing} players in {selected_league} with no {preceding_league} experience")
            total_pages = (total_missing + page_size - 1) // page_size  # Ceiling division

            # Get detailed data for each player using simple, fast SQL queries
            detail_start = time.time()
            print(f"[{time.strftime('%H:%M:%S')}] Starting detail query for {len(players_missing_preceding)} players using simple SQL")

            player_ids_str = ",".join(f"'{pid}'" for pid in players_missing_preceding)

            # Get basic stats (latest date and tourney count) - this is fast
            stats_query = f"""
                SELECT
                    tr.player_id,
                    MAX(res.date) as latest_date,
                    COUNT(DISTINCT tr.result_id) as tourney_count
                FROM tourney_results_tourneyrow tr
                JOIN tourney_results_tourneyresult res ON tr.result_id = res.id
                WHERE tr.player_id IN ({player_ids_str})
                AND res.league = %s
                GROUP BY tr.player_id
                ORDER BY tr.player_id
            """

            with connection.cursor() as cursor:
                cursor.execute(stats_query, [selected_league])
                stats_results = cursor.fetchall()
                print(f"[{time.strftime('%H:%M:%S')}] Got {len(stats_results)} stats rows")

            # Get latest nicknames - simple approach
            nickname_query = f"""
                SELECT tr.player_id, tr.nickname
                FROM tourney_results_tourneyrow tr
                JOIN tourney_results_tourneyresult res ON tr.result_id = res.id
                WHERE tr.player_id IN ({player_ids_str})
                AND res.league = %s
                ORDER BY tr.player_id, res.date DESC
            """

            with connection.cursor() as cursor:
                cursor.execute(nickname_query, [selected_league])
                all_nickname_results = cursor.fetchall()

            # Process to get latest nickname per player
            nickname_dict = {}
            seen_players = set()
            for player_id, nickname in all_nickname_results:
                if player_id not in seen_players:
                    nickname_dict[player_id] = nickname
                    seen_players.add(player_id)

            print(f"[{time.strftime('%H:%M:%S')}] Processed {len(nickname_dict)} nickname entries")

            # Combine results
            detail_results = []
            for player_id, latest_date, tourney_count in stats_results:
                nickname = nickname_dict.get(player_id, "Unknown")
                detail_results.append((player_id, latest_date, nickname, tourney_count))

            print(f"[{time.strftime('%H:%M:%S')}] Combined {len(detail_results)} detail rows")

            # Convert to dictionary for easy lookup
            player_details = {}
            for row in detail_results:
                player_details[row[0]] = {"latest_nickname": row[2] or "Unknown", "latest_date": row[1], "tourney_count": row[3]}

            # Ensure all players have entries
            for pid in players_missing_preceding:
                if pid not in player_details:
                    player_details[pid] = {"latest_nickname": "Unknown", "latest_date": None, "tourney_count": 0}

            detail_time = time.time() - detail_start
            print(f"[{time.strftime('%H:%M:%S')}] Detail query completed in {detail_time:.2f} seconds")
            status_box.info(f"📊 Detail query completed in {detail_time:.2f} seconds - looking up player names...")

            # Get player names
            lookup_start = time.time()
            print(f"[{time.strftime('%H:%M:%S')}] Starting player lookup")
            from thetower.backend.tourney_results.data import get_player_id_lookup

            full_player_lookup = get_player_id_lookup()
            player_lookup = {pid: full_player_lookup.get(pid, f"Player {pid}") for pid in players_missing_preceding}
            lookup_time = time.time() - lookup_start
            print(f"[{time.strftime('%H:%M:%S')}] Player lookup completed in {lookup_time:.2f} seconds")
            status_box.info(f"👤 Player lookup completed in {lookup_time:.2f} seconds - processing data...")

            # Create display data
            process_start = time.time()
            print(f"[{time.strftime('%H:%M:%S')}] Starting data processing")
            display_data = []
            for player_id in players_missing_preceding:
                details = player_details.get(player_id, {})
                real_name = player_lookup.get(player_id, f"Player {player_id}")
                player_url = f"https://{BASE_URL}/player?" + urlencode({"player": player_id}, doseq=True)
                is_sus = player_id in sus_player_ids

                display_data.append(
                    {
                        "Player ID": player_id,  # Keep player ID for display
                        "Real Name": real_name,
                        "Tourney Nick": details.get("latest_nickname", "Unknown"),
                        "Latest Tourney": details.get("latest_date", "Unknown"),
                        "Total Tourneys": details.get("tourney_count", 0),
                        "Status": (
                            "🚨 SUS"
                            if is_sus
                            else f"<a href='https://admin.thetower.lol/admin/sus/moderationrecord/add/?tower_id={player_id}&moderation_type=sus' target='_blank'>🔗 sus me</a>"
                        ),
                        "Player Link": player_url,
                    }
                )

            # Display results as a table
            display_df = pd.DataFrame(display_data)

            # Format the date column
            if "Latest Tourney" in display_df.columns:
                display_df["Latest Tourney"] = pd.to_datetime(display_df["Latest Tourney"]).dt.strftime("%Y-%m-%d")

            # Create clickable player links
            def make_clickable(url, name):
                return f'<a href="{url}" target="_blank">{name}</a>'

            display_df["Player ID Link"] = display_df.apply(lambda row: make_clickable(row["Player Link"], row["Player ID"]), axis=1)

            # Reorder columns for display
            final_display_df = display_df[["Player ID Link", "Real Name", "Tourney Nick", "Latest Tourney", "Total Tourneys", "Status"]]

            process_time = time.time() - process_start
            print(f"[{time.strftime('%H:%M:%S')}] Data processing completed in {process_time:.2f} seconds")
            status_box.info(f"⚙️ Data processing completed in {process_time:.2f} seconds - rendering table...")

            total_time = time.time() - start_time
            print(f"[{time.strftime('%H:%M:%S')}] Total processing time: {total_time:.2f} seconds")
            status_box.info(f"⏱️ Total processing: {total_time:.2f} seconds - displaying results...")

            # Display as HTML table (DataFrame LinkColumn has API issues)
            print(f"[{time.strftime('%H:%M:%S')}] Rendering HTML table")
            st.write(final_display_df.to_html(escape=False, index=False), unsafe_allow_html=True)

            # Clear status updates once main data is displayed
            status_box.empty()

            # Summary statistics
            st.subheader("Summary")

            summary_start = time.time()
            print(f"[{time.strftime('%H:%M:%S')}] Starting summary statistics calculation")
            # Get total counts using database queries
            total_selected_players = TourneyRow.objects.filter(result__league=selected_league).values("player_id").distinct().count()

            total_preceding_players = TourneyRow.objects.filter(result__league=preceding_league).values("player_id").distinct().count()

            summary_time = time.time() - summary_start
            print(f"[{time.strftime('%H:%M:%S')}] Summary statistics completed in {summary_time:.2f} seconds")
            status_box.info(f"📈 Summary statistics completed in {summary_time:.2f} seconds - analysis complete!")

            col1, col2, col3 = st.columns(3)

            with col1:
                st.metric(f"Total {selected_league} Players", total_selected_players)

            with col2:
                st.metric(f"{preceding_league} Players", total_preceding_players)

            with col3:
                percentage = (total_missing / total_selected_players) * 100 if total_selected_players else 0
                st.metric("Missing Preceding Experience", f"{total_missing} ({percentage:.1f}%)")

            if total_missing > page_size:
                st.info(f"Use pagination controls above to view all {total_missing} players ({total_pages} pages total)")

            print(f"[{time.strftime('%H:%M:%S')}] League progression analysis completed successfully")

        # Clear final status
        status_box.empty()

    except Exception as e:
        print(f"[{time.strftime('%H:%M:%S')}] Error in league progression analysis: {str(e)}")
        st.error(f"Error analyzing league progression: {str(e)}")


if __name__ == "__main__":
    main()
