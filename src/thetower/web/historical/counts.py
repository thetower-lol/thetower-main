import os

import pandas as pd
import streamlit as st

from thetower.backend.tourney_results.data import date_to_patch
from thetower.backend.tourney_results.models import TourneyResult, TourneyRow
from thetower.backend.tourney_results.results_config import get_results_limit
from thetower.web.util import get_league_selection, get_options


def compute_counts():
    options = get_options(links=False)

    # Get latest tournament result to check patch
    latest_result = TourneyResult.objects.filter(public=True).order_by("-date").first()
    patch = date_to_patch(latest_result.date) if latest_result else None

    # Use patch-aware league selection
    league = get_league_selection(options, patch=patch)

    st.header(f"Wave cutoff required for top X in {league}")

    # Define cutoff ranges
    cutoff_ranges = {
        # "Top 200": {
        #     "counts": [1, 10, 25, 50, 100, 200],
        #     "limit": 300
        # },
        "Top 1000": {"counts": [1, 10, 25, 50, 100, 200, 400, 600, 800, 1000], "limit": 1100},
        "Top 2500": {"counts": [1, 100, 250, 500, 750, 1000, 1500, 2000, 2500], "limit": 2600},
        "Top 5000": {"counts": [1, 100, 500, 1000, 2000, 2500, 3000, 4000, 5000], "limit": 5100},
        "Top 10000": {"counts": [1, 100, 500, 1000, 2000, 5000, 7500, 10000], "limit": 10100},
        "Top 25000": {"counts": [1, 100, 500, 1000, 5000, 10000, 15000, 20000, 25000], "limit": 25100},
    }

    # Public site: only offer ranges fully inside the league's results cap, so zero cells
    # never reveal where participation ends
    if not os.environ.get("HIDDEN_FEATURES"):
        cap = get_results_limit(league)
        cutoff_ranges = {name: cfg for name, cfg in cutoff_ranges.items() if max(cfg["counts"]) <= cap}
        if not cutoff_ranges:
            counts = [c for c in [1, 10, 25, 50, 100, 200, 400, 600, 800, 1000] if c <= cap]
            cutoff_ranges = {f"Top {counts[-1]}": {"counts": counts, "limit": counts[-1] + 100}}

    # Create columns for controls
    range_col, bc_col, slid_col, transpose_col = st.columns([1, 1, 2, 2])

    selected_range = range_col.selectbox("Range", options=list(cutoff_ranges.keys()), help="Select the range of positions to display")

    # Add checkbox in transpose_col
    untranspose = transpose_col.checkbox("Show dates as rows", value=False, help="Switch between dates as columns or rows")

    # Only show BC selector when untransposed
    bc_display = "Hide"
    if untranspose:
        bc_display = bc_col.selectbox("Battle Conditions", ["Hide", "Short", "Full"], help="How to display battle conditions")

    counts_for = cutoff_ranges[selected_range]["counts"]
    limit = cutoff_ranges[selected_range]["limit"]

    champ_results = TourneyResult.objects.filter(league=league, public=True).order_by("-date")

    per_page = 10
    pages = len(champ_results) // per_page

    if pages == 1:
        per_page = 10
        pages = 2

    which_page = slid_col.slider("Select page", 1, pages, 1)

    champ_results = champ_results[(which_page - 1) * per_page : which_page * per_page]

    rows = TourneyRow.objects.filter(result__in=champ_results, position__lt=limit, position__gt=0).order_by("-wave").values("result_id", "wave")

    row_height = (per_page + 1) * 35 + 2

    results = []

    for tourney in champ_results:
        waves = [row["wave"] for row in rows if row["result_id"] == tourney.id]
        result = {"date": tourney.date}

        # Handle BC display based on selection
        if bc_display != "Hide":
            bcs = tourney.conditions.all()
            if bc_display == "Short":
                result["bcs"] = "/".join([bc.shortcut for bc in bcs])
            else:  # "Full"
                result["bcs"] = "/".join([bc.name for bc in bcs])

        result |= {f"Top {count_for}": waves[count_for - 1] if count_for <= len(waves) else 0 for count_for in counts_for}
        results.append(result)

    to_be_displayed = pd.DataFrame(results).sort_values("date", ascending=False).reset_index(drop=True)

    if untranspose:
        # Display without transposing
        st.dataframe(to_be_displayed, width="stretch", height=row_height, hide_index=True)
    else:
        # Existing transposed view
        date_col = to_be_displayed["date"]

        # Create visible headers and tooltips separately
        visible_headers = [str(date) for date in date_col]
        tooltips = []
        for idx, date in enumerate(date_col):
            bcs = champ_results[idx].conditions.all()
            bc_text = "\n\n".join([bc.name for bc in bcs]) if bcs else ""
            tooltip = f"{bc_text}" if bc_text else str(date)
            tooltips.append(tooltip)

        # Rest of transposed view logic
        if "bcs" in to_be_displayed.columns:
            bcs_col = to_be_displayed["bcs"]
            transposed = to_be_displayed.drop(["date", "bcs"], axis=1).T
            transposed.insert(0, "Battle Conditions", bcs_col)
        else:
            transposed = to_be_displayed.drop("date", axis=1).T

        # Set column names to just dates, with tooltips for hover
        transposed.columns = pd.Index(visible_headers, name=None)
        st.dataframe(
            transposed,
            width="stretch",
            height=row_height,
            hide_index=False,
            column_config={header: {"help": tooltip} for header, tooltip in zip(visible_headers, tooltips)},
        )


compute_counts()
