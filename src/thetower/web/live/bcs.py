"""Display tournament battle conditions in Streamlit interface."""

import datetime
import logging
from time import perf_counter

import pandas as pd
import streamlit as st

from thetower.backend.tourney_results.constants import leagues
from thetower.web.countdown import render_countdown

# Try to import thetower_bcs with graceful fallback
try:
    from thetower_bcs import TournamentPredictor, predict_future_tournament

    TOWERBCS_AVAILABLE = True
except ImportError:
    TOWERBCS_AVAILABLE = False
    predict_future_tournament = None
    TournamentPredictor = None

logging.info("Starting battle conditions analysis")
t2_start = perf_counter()

# Check if thetower_bcs is available
if not TOWERBCS_AVAILABLE:
    st.markdown("# Battle Conditions")
    st.error("⚠️ Battle Conditions module not available")
    st.stop()

tourney_id, tourney_date, days_until, _ = TournamentPredictor.get_tournament_info()

# BCs are revealed this many days before the tournament
BC_DAYS_EARLY = 1


def _predict(league: str) -> list[str]:
    """Battle conditions for a league, or a one-line notice when the predictor does not model it yet."""
    try:
        return predict_future_tournament(tourney_id, league)
    except ValueError:
        logging.warning(f"Battle conditions unavailable for {league}: the predictor does not know this league yet")
        return [f"{league} battle conditions are unavailable"]


st.markdown("# Battle Conditions")
if days_until > BC_DAYS_EARLY:
    bc_dt = datetime.datetime.combine(tourney_date, datetime.time.min, tzinfo=datetime.timezone.utc) - datetime.timedelta(days=BC_DAYS_EARLY)
    tourney_date_str = tourney_date.strftime("%A, %B %d, %Y")
    phases = [
        {"label": "Battle conditions revealed in", "sub": tourney_date_str, "t": int(bc_dt.timestamp() * 1000)},
    ]
    render_countdown(phases)
else:
    st.markdown(f"## Tournament {'is today!' if days_until == 0 else f'is on {tourney_date}'}")

    st.dataframe(
        pd.DataFrame.from_dict({league: _predict(league) for league in leagues}, orient="index").transpose().fillna(""),
        width="stretch",
    )

# Log execution time at the end of the file
t2_stop = perf_counter()
logging.info(f"Full battle conditions analysis took {t2_stop - t2_start}")
