from pathlib import Path

import pandas as pd
import streamlit as st
from django.db.models import Max

from thetower.backend.sus.models import ModerationRecord
from thetower.backend.tourney_results.data import get_player_id_lookup
from thetower.backend.tourney_results.formatting import make_player_url
from thetower.backend.tourney_results.models import TourneyRow
from thetower.web.util import fmt_dt


@st.cache_data(ttl=300)  # Cache for 5 minutes
def get_sus_moderation_raw_data():
    """Get all people with active sus moderation and their last tournament participation - DATA ONLY."""

    # Get all active sus moderation records
    sus_records = (
        ModerationRecord.objects.filter(moderation_type="sus", resolved_at__isnull=True)  # Active records only
        .select_related("game_instance__player")
        .order_by("-created_at")
    )

    if not sus_records.exists():
        return None

    sus_data = []
    tower_ids = []

    for record in sus_records:
        if record.game_instance:
            player_name = f"{record.game_instance.player.name} ({record.game_instance.name})"
        else:
            player_name = "Unverified Player"

        sus_data.append(
            {
                "tower_id": record.tower_id,
                "player_name": player_name,
                "created_at": record.created_at,
                "created_by": record.created_by_display,
                "reason": record.reason or "No reason provided",
                "started_at": record.started_at,
            }
        )

        tower_ids.append(record.tower_id)

    # Get last tournament participation for each tower_id
    # Get the most recent tournament result for each player
    last_tournaments = (
        TourneyRow.objects.filter(player_id__in=tower_ids, result__public=True).values("player_id").annotate(last_tournament_date=Max("result__date"))
    )

    # Convert to dict for easy lookup
    last_tournament_lookup = {item["player_id"]: item["last_tournament_date"] for item in last_tournaments}

    # Get player name lookup for any unverified players
    player_lookup = get_player_id_lookup()

    # Combine the data
    combined_data = []
    for sus_record in sus_data:
        tower_id = sus_record["tower_id"]

        # Use known_player name if available, otherwise lookup
        if sus_record["player_name"] == "Unverified Player":
            real_name = player_lookup.get(tower_id, "Unknown Player")
        else:
            real_name = sus_record["player_name"]

        last_tournament = last_tournament_lookup.get(tower_id)

        combined_data.append(
            {
                "tower_id": tower_id,
                "player_name": real_name,
                "last_tournament_date": last_tournament,
                "days_since_last_tournament": (pd.Timestamp.now().date() - last_tournament).days if last_tournament else None,
                "sus_created_at": sus_record["created_at"],
                "sus_created_by": sus_record["created_by"],
                "sus_reason": sus_record["reason"],
                "sus_started_at": sus_record["started_at"],
            }
        )

    return combined_data


def render_sus_moderation_page():
    """Render the sus moderation page with UI components."""

    # Apply styling
    css_path = Path(__file__).parent.parent / "static" / "styles" / "style.css"
    with open(css_path, "r") as infile:
        table_styling = f"<style>{infile.read()}</style>"
    st.write(table_styling, unsafe_allow_html=True)

    st.markdown("# Active Sus Moderation Records")
    st.markdown("All players currently marked as suspicious with their last tournament participation")

    with st.spinner("🔍 Getting active sus moderation records..."):
        combined_data = get_sus_moderation_raw_data()

        if combined_data is None:
            st.success("🎉 No active sus moderation records found!")
            return

        st.success(f"Found **{len(combined_data)}** active sus moderation records")

    with st.spinner("📊 Processing tournament participation data..."):
        # Convert to DataFrame
        df = pd.DataFrame(combined_data)
        df = df.sort_values(["last_tournament_date", "sus_created_at"], ascending=[False, False], na_position="last").reset_index(drop=True)

    # Summary statistics
    total_sus = len(df)
    never_participated = len(df[df["last_tournament_date"].isna()])
    participated = total_sus - never_participated

    if participated > 0:
        avg_days_since = df[df["days_since_last_tournament"].notna()]["days_since_last_tournament"].mean()
        recent_participants = len(df[df["days_since_last_tournament"] <= 30])
    else:
        avg_days_since = 0
        recent_participants = 0

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Total Sus Records", total_sus)
    with col2:
        st.metric("Never Participated", never_participated)
    with col3:
        st.metric("Recent Participants (30d)", recent_participants)
    with col4:
        if participated > 0:
            st.metric("Avg Days Since Last", f"{avg_days_since:.0f}")
        else:
            st.metric("Avg Days Since Last", "N/A")

    # Format the display DataFrame
    display_df = df.copy()

    # Make player_id clickable
    display_df["clickable_player_id"] = [make_player_url(player_id, id=player_id) for player_id in display_df["tower_id"]]

    # Format dates for display
    display_df["formatted_last_tournament"] = display_df["last_tournament_date"].apply(lambda x: x.strftime("%Y-%m-%d") if pd.notnull(x) else "Never")

    display_df["formatted_sus_created"] = display_df["sus_created_at"].apply(lambda x: fmt_dt(x, fmt="%Y-%m-%d %H:%M") if pd.notnull(x) else "")

    display_df["days_since_display"] = display_df["days_since_last_tournament"].apply(lambda x: f"{x} days" if pd.notnull(x) else "Never")

    # Select columns for display
    display_cols = [
        "clickable_player_id",
        "player_name",
        "formatted_last_tournament",
        "days_since_display",
        "formatted_sus_created",
        "sus_created_by",
    ]

    # Display the sortable datatable
    st.subheader("📊 Active Sus Moderation Records")

    # Prepare data for st.dataframe (without HTML links for sorting)
    sortable_df = display_df.copy()
    sortable_df = sortable_df[display_cols].rename(
        columns={
            "clickable_player_id": "Tower ID",
            "player_name": "Player Name",
            "formatted_last_tournament": "Last Tournament",
            "days_since_display": "Days Since",
            "formatted_sus_created": "Sus Created",
            "sus_created_by": "Created By",
            "sus_reason": "Reason",
        }
    )

    # Replace clickable links with just the Tower ID for sorting
    sortable_df["Tower ID"] = display_df["tower_id"]

    st.dataframe(
        sortable_df,
        width="stretch",
        height=600,
        column_config={
            "Tower ID": st.column_config.TextColumn("Tower ID", help="Click to copy Tower ID", max_chars=16),
            "Player Name": st.column_config.TextColumn("Player Name", help="Player's display name"),
            "Last Tournament": st.column_config.TextColumn("Last Tournament", help="Date of last tournament participation"),
            "Days Since": st.column_config.TextColumn("Days Since", help="Days since last tournament"),
            "Sus Created": st.column_config.TextColumn("Sus Created", help="When the sus record was created"),
            "Created By": st.column_config.TextColumn("Created By", help="Who created the sus record"),
            "Reason": st.column_config.TextColumn("Reason", help="Reason for sus moderation"),
        },
    )  # Additional filters and analysis
    with st.expander("🔍 Filter and Analysis Options"):
        col1, col2 = st.columns(2)

        with col1:
            st.subheader("Filter by Days Since Last Tournament")
            days_filter = st.selectbox(
                "Show players with last tournament:",
                ["All", "Never participated", "Within 7 days", "Within 30 days", "Within 90 days", "Over 90 days ago"],
            )

            if days_filter != "All":
                filtered_df = df.copy()
                if days_filter == "Never participated":
                    filtered_df = filtered_df[filtered_df["last_tournament_date"].isna()]
                elif days_filter == "Within 7 days":
                    filtered_df = filtered_df[filtered_df["days_since_last_tournament"] <= 7]
                elif days_filter == "Within 30 days":
                    filtered_df = filtered_df[filtered_df["days_since_last_tournament"] <= 30]
                elif days_filter == "Within 90 days":
                    filtered_df = filtered_df[filtered_df["days_since_last_tournament"] <= 90]
                elif days_filter == "Over 90 days ago":
                    filtered_df = filtered_df[filtered_df["days_since_last_tournament"] > 90]

                st.write(f"**Filtered Results: {len(filtered_df)} records**")

                if not filtered_df.empty:
                    # Show filtered results with sortable datatable
                    filtered_display = filtered_df.copy()
                    filtered_display["formatted_last_tournament"] = filtered_display["last_tournament_date"].apply(
                        lambda x: x.strftime("%Y-%m-%d") if pd.notnull(x) else "Never"
                    )
                    filtered_display["days_since_display"] = filtered_display["days_since_last_tournament"].apply(
                        lambda x: f"{x} days" if pd.notnull(x) else "Never"
                    )

                    filtered_final = filtered_display[["tower_id", "player_name", "formatted_last_tournament", "days_since_display"]].rename(
                        columns={
                            "tower_id": "Tower ID",
                            "player_name": "Player Name",
                            "formatted_last_tournament": "Last Tournament",
                            "days_since_display": "Days Since",
                        }
                    )

                    st.dataframe(
                        filtered_final,
                        width="stretch",
                        height=400,
                        column_config={
                            "Tower ID": st.column_config.TextColumn("Tower ID", max_chars=16),
                            "Player Name": st.column_config.TextColumn("Player Name"),
                            "Last Tournament": st.column_config.TextColumn("Last Tournament"),
                            "Days Since": st.column_config.TextColumn("Days Since"),
                        },
                    )


render_sus_moderation_page()
