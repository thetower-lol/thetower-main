"""Battle Conditions Mismatch Analysis - Admin Page

Compare stored battle conditions against predicted values for all tournaments.
Shows mismatches between database values and calculated predictions.
"""

# Django setup
import os

import django
import pandas as pd
import streamlit as st

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "thetower.backend.towerdb.settings")
django.setup()

from thetower.backend.tourney_results.models import TourneyResult

# Try to import thetower_bcs with graceful fallback
try:
    from thetower_bcs import TournamentPredictor, predict_future_tournament

    TOWERBCS_AVAILABLE = True
except ImportError:
    TOWERBCS_AVAILABLE = False
    predict_future_tournament = None
    TournamentPredictor = None


st.markdown("# Battle Conditions Mismatch Analysis")


if not TOWERBCS_AVAILABLE:
    st.error("⚠️ thetower-bcs package not available")
    st.markdown(
        """
    The `thetower-bcs` package is not installed. To use battle conditions prediction, install it with: `pip install -e /path/to/thetower-bcs`
    """
    )
    st.stop()


# Function to get tourney_id for a given date
# Replace placeholder with actual implementation using TournamentPredictor
def get_tourney_id_for_date(date):
    # date: datetime.date
    # Convert to datetime for predictor
    from datetime import datetime

    input_dt = datetime(date.year, date.month, date.day)
    tourney_id, _, _, _ = TournamentPredictor.get_tournament_info(input_dt)
    return tourney_id


# Minimum date for BC prediction (thetower_bcs predictor starts on 2024-10-19)
MIN_PREDICTION_DATE = pd.Timestamp("2024-10-19").date()


# Get all tournaments
tournaments = TourneyResult.objects.filter(public=True, date__gte=MIN_PREDICTION_DATE).order_by("date", "league")

mismatches = []
total_checked = 0

with st.spinner("Analyzing battle conditions..."):
    for tournament in tournaments:
        total_checked += 1
        # Skip copper league since it has no battle conditions
        if tournament.league.lower() == "copper":
            continue

        # Get stored conditions by full name
        stored_bcs = set(tournament.conditions.values_list("name", flat=True))

        # Predict conditions
        try:
            if tournament.date < MIN_PREDICTION_DATE:
                continue  # Skip tournaments before the prediction cutoff
            tourney_id = get_tourney_id_for_date(tournament.date)
            predicted_bcs = set(predict_future_tournament(tourney_id, tournament.league))
        except Exception as e:
            st.warning(f"Failed to predict BCs for {tournament}: {e}")
            continue

        # Check for mismatch
        if stored_bcs != predicted_bcs:
            mismatch_info = {
                "tournament_id": tournament.id,
                "date": tournament.date,
                "league": tournament.league,
                "stored_bcs": ", ".join(sorted(stored_bcs)),
                "predicted_bcs": ", ".join(sorted(predicted_bcs)),
                "missing_in_db": ", ".join(sorted(predicted_bcs - stored_bcs)),
                "extra_in_db": ", ".join(sorted(stored_bcs - predicted_bcs)),
            }
            mismatches.append(mismatch_info)

st.markdown("## Analysis Complete")
st.markdown(f"Checked {total_checked} tournaments")
st.markdown(f"Found {len(mismatches)} mismatches")

if mismatches:
    st.markdown("## Mismatches Found")

    # Convert to DataFrame for better display
    df = pd.DataFrame(mismatches)

    # Display summary stats
    st.markdown("### Summary Statistics")
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Total Mismatches", len(mismatches))
    with col2:
        st.metric("Tournaments Affected", df["tournament_id"].nunique())
    with col3:
        leagues_affected = df["league"].nunique()
        st.metric("Leagues Affected", leagues_affected)

    # Display detailed table
    st.markdown("### Detailed Mismatch Table")
    st.dataframe(
        df[["tournament_id", "date", "league", "stored_bcs", "predicted_bcs", "missing_in_db", "extra_in_db"]],
        width="stretch",
        column_config={
            "tournament_id": st.column_config.NumberColumn("Tournament ID", width="small"),
            "date": st.column_config.DateColumn("Date", width="medium"),
            "league": st.column_config.TextColumn("League", width="medium"),
            "stored_bcs": st.column_config.TextColumn("Stored BCs", width="large"),
            "predicted_bcs": st.column_config.TextColumn("Predicted BCs", width="large"),
            "missing_in_db": st.column_config.TextColumn("Missing in DB", width="large"),
            "extra_in_db": st.column_config.TextColumn("Extra in DB", width="large"),
        },
    )

    # Group by league for additional insights
    st.markdown("### Mismatches by League")
    league_summary = df.groupby("league").size().reset_index(name="mismatch_count")
    st.bar_chart(league_summary.set_index("league"))

else:
    st.success("✅ No battle condition mismatches found!")

    st.markdown("---")
    st.markdown(
        f"*Note: Only tournaments from {MIN_PREDICTION_DATE} onwards are analyzed. Tournament IDs are derived using the thetower_bcs predictor scheduling logic.*"
    )
