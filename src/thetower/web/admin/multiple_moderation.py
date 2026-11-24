import pandas as pd
import streamlit as st
from django.db.models import Count

from thetower.backend.sus.models import ModerationRecord
from thetower.backend.tourney_results.data import get_player_id_lookup
from thetower.backend.tourney_results.formatting import make_player_url


@st.cache_data(ttl=300)  # Cache for 5 minutes
def get_multiple_moderation_raw_data():
    """Get all people with more than 1 active moderation record - DATA ONLY."""

    # Find tower_ids with more than 1 active moderation record
    multiple_moderation_ids = (
        ModerationRecord.objects.filter(resolved_at__isnull=True)  # Only active records
        .values("tower_id")
        .annotate(moderation_count=Count("id"))
        .filter(moderation_count__gt=1)
        .order_by("-moderation_count", "tower_id")
    )

    multiple_ids_list = list(multiple_moderation_ids)

    if not multiple_ids_list:
        return None

    # Get detailed information for these players
    tower_ids_with_multiple = [item["tower_id"] for item in multiple_ids_list]

    # Get all active moderation records for these players
    detailed_records = (
        ModerationRecord.objects.filter(tower_id__in=tower_ids_with_multiple, resolved_at__isnull=True)
        .select_related("game_instance__player")
        .order_by("tower_id", "moderation_type", "created_at")
    )

    # Process the data
    player_data = {}
    for record in detailed_records:
        tower_id = record.tower_id
        if tower_id not in player_data:
            if record.game_instance:
                player_name = f"{record.game_instance.player.name} ({record.game_instance.name})"
            else:
                player_name = "Unverified Player"

            player_data[tower_id] = {"tower_id": tower_id, "player_name": player_name, "moderation_records": []}

        player_data[tower_id]["moderation_records"].append(
            {
                "type": record.get_moderation_type_display(),
                "type_code": record.moderation_type,
                "created_at": record.created_at,
                "created_by": record.created_by_display,
                "source": record.get_source_display(),
                "reason": record.reason or "No reason provided",
                "started_at": record.started_at,
            }
        )

    # Get player name lookup for unverified players
    player_lookup = get_player_id_lookup()

    # Convert to display format
    display_data = []
    for tower_id, data in player_data.items():
        # Use known_player name if available, otherwise lookup
        if data["player_name"] == "Unverified Player":
            real_name = player_lookup.get(tower_id, "Unknown Player")
        else:
            real_name = data["player_name"]

        moderation_records = data["moderation_records"]
        moderation_count = len(moderation_records)

        # Create summary of moderation types
        type_counts = {}
        earliest_date = None
        latest_date = None
        moderation_details = []

        for record in moderation_records:
            mod_type = record["type"]
            if mod_type not in type_counts:
                type_counts[mod_type] = 0
            type_counts[mod_type] += 1

            # Track dates
            created_date = record["created_at"]
            if earliest_date is None or created_date < earliest_date:
                earliest_date = created_date
            if latest_date is None or created_date > latest_date:
                latest_date = created_date

            # Create detailed string for this record
            reason_part = f", Reason: {record['reason']}" if record["reason"] != "No reason provided" else ""
            moderation_details.append(
                f"{record['type']} (Created: {created_date.strftime('%Y-%m-%d %H:%M')}, "
                f"By: {record['created_by']}, Source: {record['source']}"
                f"{reason_part})"
            )

        # Create type summary string
        type_summary = ", ".join([f"{count}x {mod_type}" for mod_type, count in type_counts.items()])

        display_data.append(
            {
                "tower_id": tower_id,
                "player_name": real_name,
                "moderation_count": moderation_count,
                "type_summary": type_summary,
                "type_counts": type_counts,
                "earliest_date": earliest_date,
                "latest_date": latest_date,
                "moderation_details": moderation_details,
                "full_records": moderation_records,
            }
        )

    return display_data


def render_multiple_moderation_page():
    """Render the multiple moderation page with UI components."""

    # Apply styling

    st.markdown("# Multiple Active Moderation Records")
    st.markdown("All players with more than one active moderation record")

    with st.spinner("🔍 Finding players with multiple active moderation records..."):
        display_data = get_multiple_moderation_raw_data()

        if display_data is None:
            st.success("🎉 No players found with multiple active moderation records!")
            return

        st.success(f"Found **{len(display_data)}** players with multiple active moderation records")

    with st.spinner("📊 Processing moderation data..."):
        # Convert to DataFrame and sort
        df = pd.DataFrame(display_data)
        df = df.sort_values(["moderation_count", "latest_date"], ascending=[False, False]).reset_index(drop=True)

    # Summary statistics
    total_players = len(df)
    total_records = df["moderation_count"].sum()
    max_records = df["moderation_count"].max() if not df.empty else 0
    avg_records = df["moderation_count"].mean() if not df.empty else 0

    # Analyze moderation type combinations
    type_combination_counts = {}
    for _, row in df.iterrows():
        # Create a sorted tuple of moderation types for this player
        types = sorted(row["type_counts"].keys())
        combo = ", ".join(types)
        if combo not in type_combination_counts:
            type_combination_counts[combo] = 0
        type_combination_counts[combo] += 1

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Players with Multiple Records", total_players)
    with col2:
        st.metric("Total Active Records", total_records)
    with col3:
        st.metric("Max Records per Player", max_records)
    with col4:
        st.metric("Avg Records per Player", f"{avg_records:.1f}")

    # Display main table
    if not df.empty:
        display_df = df.copy()

        # Make player_id clickable
        display_df["clickable_player_id"] = [make_player_url(player_id, id=player_id) for player_id in display_df["tower_id"]]

        # Format dates for display
        display_df["formatted_earliest"] = display_df["earliest_date"].apply(lambda x: x.strftime("%Y-%m-%d") if pd.notnull(x) else "")

        display_df["formatted_latest"] = display_df["latest_date"].apply(lambda x: x.strftime("%Y-%m-%d") if pd.notnull(x) else "")

        # Select columns for main display
        main_display_cols = ["clickable_player_id", "player_name", "moderation_count", "type_summary", "formatted_earliest", "formatted_latest"]

        # Display the sortable datatable
        st.subheader("📊 Multiple Moderation Records")

        # Prepare data for st.dataframe (without HTML links for sorting)
        sortable_df = display_df.copy()
        sortable_df = sortable_df[main_display_cols].rename(
            columns={
                "clickable_player_id": "Tower ID",
                "player_name": "Player Name",
                "moderation_count": "Active Records",
                "type_summary": "Moderation Types",
                "formatted_earliest": "Earliest Record",
                "formatted_latest": "Latest Record",
            }
        )

        # Replace clickable links with just the Tower ID for sorting
        sortable_df["Tower ID"] = display_df["tower_id"]

        st.dataframe(
            sortable_df,
            width="stretch",
            height=600,
            column_config={
                "Tower ID": st.column_config.TextColumn("Tower ID", help="Player's Tower ID", max_chars=16),
                "Player Name": st.column_config.TextColumn("Player Name", help="Player's display name"),
                "Active Records": st.column_config.NumberColumn("Active Records", help="Number of active moderation records", format="%d"),
                "Moderation Types": st.column_config.TextColumn("Moderation Types", help="Summary of active moderation types"),
                "Earliest Record": st.column_config.TextColumn("Earliest Record", help="Date of earliest moderation record"),
                "Latest Record": st.column_config.TextColumn("Latest Record", help="Date of most recent moderation record"),
            },
        )

        # Detailed breakdown in expander
        with st.expander("📋 Detailed Breakdown by Player"):
            for _, row in df.iterrows():
                st.subheader(f"{row['player_name']} ({row['tower_id']})")
                st.write(f"**Total Active Records:** {row['moderation_count']}")

                for i, detail in enumerate(row["moderation_details"], 1):
                    st.write(f"**{i}.** {detail}")

                st.write("---")

        # Analysis section
        with st.expander("📊 Analysis and Patterns"):
            col1, col2 = st.columns(2)

            with col1:
                st.subheader("Moderation Type Combinations")
                st.write("**Most common combinations of active moderation types:**")
                sorted_combos = sorted(type_combination_counts.items(), key=lambda x: x[1], reverse=True)
                for combo, count in sorted_combos:
                    st.write(f"- **{combo}**: {count} players")

            with col2:
                st.subheader("Records by Count")
                count_distribution = df["moderation_count"].value_counts().sort_index()
                st.write("**Distribution of number of records per player:**")
                for count, players in count_distribution.items():
                    st.write(f"- **{count} records**: {players} players")

        # Filter options
        with st.expander("🔍 Filter Options"):
            col1, col2 = st.columns(2)

            with col1:
                st.subheader("Filter by Moderation Type")
                all_types = set()
                for _, row in df.iterrows():
                    all_types.update(row["type_counts"].keys())

                selected_type = st.selectbox("Show only players with this moderation type:", ["All"] + sorted(list(all_types)))

                if selected_type != "All":
                    filtered_df = df[df["type_counts"].apply(lambda x: selected_type in x)]
                    st.write(f"**Filtered Results: {len(filtered_df)} players with {selected_type} records**")

                    if not filtered_df.empty:
                        filtered_final = filtered_df[["tower_id", "player_name", "moderation_count", "type_summary"]].rename(
                            columns={
                                "tower_id": "Tower ID",
                                "player_name": "Player Name",
                                "moderation_count": "Active Records",
                                "type_summary": "Moderation Types",
                            }
                        )

                        st.dataframe(
                            filtered_final,
                            width="stretch",
                            height=400,
                            column_config={
                                "Tower ID": st.column_config.TextColumn("Tower ID", max_chars=16),
                                "Player Name": st.column_config.TextColumn("Player Name"),
                                "Active Records": st.column_config.NumberColumn("Active Records", format="%d"),
                                "Moderation Types": st.column_config.TextColumn("Moderation Types"),
                            },
                        )

            with col2:
                st.subheader("Filter by Record Count")
                min_records = st.selectbox("Minimum number of active records:", sorted(df["moderation_count"].unique()))

                count_filtered_df = df[df["moderation_count"] >= min_records]
                st.write(f"**Players with {min_records}+ active records: {len(count_filtered_df)} players**")

                if not count_filtered_df.empty and min_records > 1:
                    count_final = count_filtered_df[["tower_id", "player_name", "moderation_count", "type_summary"]].rename(
                        columns={
                            "tower_id": "Tower ID",
                            "player_name": "Player Name",
                            "moderation_count": "Active Records",
                            "type_summary": "Moderation Types",
                        }
                    )

                    st.dataframe(
                        count_final,
                        width="stretch",
                        height=400,
                        column_config={
                            "Tower ID": st.column_config.TextColumn("Tower ID", max_chars=16),
                            "Player Name": st.column_config.TextColumn("Player Name"),
                            "Active Records": st.column_config.NumberColumn("Active Records", format="%d"),
                            "Moderation Types": st.column_config.TextColumn("Moderation Types"),
                        },
                    )

    else:
        st.warning("No data to display.")


render_multiple_moderation_page()
