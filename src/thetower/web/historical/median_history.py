import statistics
from collections import defaultdict

import pandas as pd
import streamlit as st

from thetower.backend.tourney_results.constants import leagues
from thetower.backend.tourney_results.models import TourneyResult, TourneyRow


@st.cache_data(ttl=300, show_spinner="Loading tournament data...")
def _load_median_data(
    num_tourneys: int,
    selected_leagues: tuple[str, ...],
) -> tuple[dict[str, dict[str, int]], dict[int, list[int]], dict[str, str], list[str]]:
    """Load and cache tournament wave data for median/mean history.

    Returns:
        league_to_date_id: {league: {date_str: result_id}}
        waves_by_result:   {result_id: [wave, ...]}
        date_to_bc:        {date_str: bc_shortcut_str}
        date_strs:         sorted list of date strings, descending
    """
    league_to_date_id: dict[str, dict[str, int]] = {}
    all_result_ids: list[int] = []
    all_dates: set[str] = set()

    for league in selected_leagues:
        results = list(TourneyResult.objects.filter(league=league, public=True).order_by("-date").values("id", "date")[:num_tourneys])
        league_to_date_id[league] = {str(r["date"]): r["id"] for r in results}
        for r in results:
            all_result_ids.append(r["id"])
            all_dates.add(str(r["date"]))

    # Fetch all waves in one query
    waves_by_result: dict[int, list[int]] = defaultdict(list)
    for row in TourneyRow.objects.filter(result__in=all_result_ids, position__gt=0).values("result_id", "wave"):
        waves_by_result[row["result_id"]].append(row["wave"])

    # Fetch BCs with prefetch_related to avoid N+1 queries
    result_bcs: dict[int, str] = {}
    for r in TourneyResult.objects.filter(id__in=all_result_ids).prefetch_related("conditions"):
        bcs = r.conditions.all()
        result_bcs[r.id] = " / ".join(bc.shortcut for bc in bcs) if bcs else ""

    # Build per-date BC tooltip (first non-empty value across leagues wins)
    date_to_bc: dict[str, str] = {}
    for league in selected_leagues:
        for date_str, result_id in league_to_date_id[league].items():
            if date_str not in date_to_bc or not date_to_bc[date_str]:
                date_to_bc[date_str] = result_bcs.get(result_id, "")

    date_strs = sorted(all_dates, reverse=True)
    return league_to_date_id, dict(waves_by_result), date_to_bc, date_strs


def compute_median_history():
    st.header("Median Wave History")
    st.caption(
        "Median (and mean) wave across all brackets in a league for each tournament. "
        "Useful for tracking overall difficulty and wave inflation over time."
    )

    col1, col2, col3 = st.columns([2, 2, 2])

    num_tourneys = col1.slider(
        "Number of recent tournaments",
        min_value=5,
        max_value=30,
        value=12,
    )
    stat_choice = col2.radio("Statistic", ["Median", "Mean", "Both"], horizontal=True)
    selected_leagues = st.multiselect(
        "Leagues",
        leagues,
        default=["Legend", "Champion", "Platinum", "Gold"],
        help="Select leagues to display",
    )

    if not selected_leagues:
        st.warning("Please select at least one league.")
        return

    league_to_date_id, waves_by_result, date_to_bc, date_strs = _load_median_data(num_tourneys, tuple(selected_leagues))

    # Build rows: one per league (two if both median + mean)
    table_rows = []
    for league in selected_leagues:
        date_to_result_id = league_to_date_id.get(league, {})

        if stat_choice == "Both":
            row_med: dict[str, object] = {"League": f"{league} (median)"}
            row_mean: dict[str, object] = {"League": f"{league} (mean)"}
            for d in date_strs:
                result_id = date_to_result_id.get(d)
                if result_id is None:
                    row_med[d] = None
                    row_mean[d] = None
                else:
                    waves = waves_by_result.get(result_id, [])
                    if not waves:
                        row_med[d] = None
                        row_mean[d] = None
                    else:
                        row_med[d] = round(statistics.median(waves), 1)
                        row_mean[d] = round(statistics.mean(waves), 0)
            table_rows.append(row_med)
            table_rows.append(row_mean)
        else:
            row: dict[str, object] = {"League": league}
            for d in date_strs:
                result_id = date_to_result_id.get(d)
                if result_id is None:
                    row[d] = None
                else:
                    waves = waves_by_result.get(result_id, [])
                    if not waves:
                        row[d] = None
                    elif stat_choice == "Median":
                        row[d] = round(statistics.median(waves), 1)
                    else:
                        row[d] = round(statistics.mean(waves), 0)
            table_rows.append(row)

    # Build flipped table: dates as rows, leagues (or "League (stat)") as columns.
    # BCs vary per league, so only show the BCs column when exactly one league is selected;
    # with multiple leagues the column would be ambiguous.
    show_bcs = len(selected_leagues) == 1

    league_cols = [row["League"] for row in table_rows]
    flipped_rows = []
    for d in date_strs:
        flipped_row: dict[str, object] = {"Date": d}
        if show_bcs:
            bc = date_to_bc.get(d, "")
            flipped_row["BCs"] = bc if bc else "–"
        for row in table_rows:
            flipped_row[row["League"]] = row.get(d)
        flipped_rows.append(flipped_row)

    df = pd.DataFrame(flipped_rows)

    col_config: dict = {
        "Date": st.column_config.TextColumn("Date", width="small"),
    }
    if show_bcs:
        col_config["BCs"] = st.column_config.TextColumn("BCs", width="small")
    else:
        st.caption("ℹ️ Battle conditions differ per league and are hidden when multiple leagues are shown.")
    for col in league_cols:
        col_config[col] = st.column_config.NumberColumn(col, format="%.1f")

    row_height = (len(flipped_rows) + 1) * 35 + 10

    st.dataframe(df, hide_index=True, width="stretch", height=row_height, column_config=col_config)


compute_median_history()
