import pandas as pd
import streamlit as st
from django.db.models import Count

from thetower.backend.tourney_results.data import get_player_id_lookup
from thetower.backend.tourney_results.formatting import make_player_url
from thetower.backend.tourney_results.models import TourneyRow


@st.cache_data(ttl=300)  # Cache for 5 minutes
def get_duplicate_tournaments():
    """Find players with multiple tournament entries on the same date."""

    # Apply styling

    st.markdown("# Duplicate Tournament Entries")
    st.markdown("Players with multiple records for the same tournament date")

    # Step 1: Use SQL aggregation to find player-date combinations with duplicates
    with st.spinner("🔍 Analyzing tournament data for duplicates..."):
        duplicate_combinations = (
            TourneyRow.objects.values("player_id", "result__date")
            .annotate(count=Count("id"))
            .filter(count__gt=1)
            .order_by("-result__date", "player_id")
        )

        # Convert to list to get the actual data
        duplicate_list = list(duplicate_combinations)

    if not duplicate_list:
        st.success("🎉 No duplicate tournament entries found!")
        return

    st.success(f"Found **{len(duplicate_list)}** player-date combinations with duplicates")

    # Step 2: Get detailed information for duplicates efficiently
    with st.spinner("📊 Getting detailed duplicate information..."):
        # Use a more efficient approach: get player_ids and dates separately
        duplicate_player_ids = [item["player_id"] for item in duplicate_list]
        duplicate_dates = [item["result__date"] for item in duplicate_list]

        # Get all rows for players who have duplicates, then filter in pandas
        detailed_rows = (
            TourneyRow.objects.select_related("result")
            .filter(player_id__in=duplicate_player_ids, result__date__in=duplicate_dates)
            .values("player_id", "nickname", "wave", "position", "result__date", "result__league")
            .order_by("-result__date", "player_id", "position")
        )

        # Convert to DataFrame
        df = pd.DataFrame(detailed_rows)

        if df.empty:
            st.warning("No detailed data found for duplicates.")
            return

        # Filter to only actual duplicates (since the above query might get some non-duplicates)
        duplicate_combinations_set = {(item["player_id"], item["result__date"]) for item in duplicate_list}

        # Filter DataFrame to only include actual duplicate combinations
        df["combination"] = list(zip(df["player_id"], df["result__date"]))
        df = df[df["combination"].isin(duplicate_combinations_set)]
        df = df.drop("combination", axis=1)
    df = df.rename(columns={"nickname": "tourney_name", "result__date": "date", "result__league": "league"})

    # Get player name lookup
    player_lookup = get_player_id_lookup()

    # Process duplicate data for display
    duplicate_data = []

    # Group by player_id and date to organize the duplicate details
    for (player_id, date), group in df.groupby(["player_id", "date"]):
        real_name = player_lookup.get(player_id, "Unknown Player")
        duplicate_entries = []

        # Get all the duplicate tournament details for this player-date combination
        for _, row in group.iterrows():
            duplicate_entries.append(
                {"date": date, "league": row["league"], "tourney_name": row["tourney_name"], "wave": row["wave"], "position": row["position"]}
            )

        duplicate_data.append(
            {
                "player_id": player_id,
                "real_name": real_name,
                "duplicate_count": len(group),
                "duplicate_entries": duplicate_entries,
                "latest_date": date,  # For sorting
            }
        )

    # Convert to DataFrame for display
    display_data = []
    for entry in duplicate_data:
        # Create a string representation of duplicate dates and details
        duplicate_details = []
        for dup in entry["duplicate_entries"]:
            duplicate_details.append(f"{dup['date']} ({dup['league']}) - {dup['tourney_name']} - Wave {dup['wave']} - Pos {dup['position']}")

        display_data.append(
            {
                "player_id": entry["player_id"],
                "real_name": entry["real_name"],
                "duplicate_count": entry["duplicate_count"],
                "latest_date": entry["latest_date"],
                "duplicate_details": "; ".join(duplicate_details),
            }
        )

    result_df = pd.DataFrame(display_data)
    result_df = result_df.sort_values(["latest_date", "duplicate_count"], ascending=[False, False]).reset_index(drop=True)

    # Summary statistics
    unique_players = len(result_df)
    total_duplicates = result_df["duplicate_count"].sum()

    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Players with Duplicates", unique_players)
    with col2:
        st.metric("Total Duplicate Entries", total_duplicates)
    with col3:
        st.metric("Player-Date Combinations", len(duplicate_list))

    # Format the player_id column to be clickable
    styled_df = result_df.copy()
    styled_df["clickable_player_id"] = [make_player_url(player_id, id=player_id) for player_id in styled_df["player_id"]]

    # Display the table - rename the column for display
    display_cols = ["clickable_player_id", "real_name", "duplicate_count", "latest_date", "duplicate_details"]
    display_df = styled_df[display_cols].rename(columns={"clickable_player_id": "player_id"})

    st.write(
        display_df.style.format(
            {"latest_date": lambda x: x.strftime("%Y-%m-%d") if pd.notnull(x) else "", "duplicate_details": lambda x: x}  # Keep details as-is
        ).to_html(escape=False, index=False),
        unsafe_allow_html=True,
    )

    # Optional: Show detailed breakdown in an expander
    with st.expander("📋 Detailed Breakdown"):
        for entry in duplicate_data[:20]:  # Limit to first 20 for performance
            st.subheader(f"{entry['real_name']} ({entry['player_id']})")
            for dup in entry["duplicate_entries"]:
                st.write(f"- **{dup['date']}** in **{dup['league']}**: {dup['tourney_name']} - Wave {dup['wave']} - Position {dup['position']}")
            st.write("---")

        if len(duplicate_data) > 20:
            st.info(f"Showing first 20 players. Total: {len(duplicate_data)} players with duplicates.")


get_duplicate_tournaments()
