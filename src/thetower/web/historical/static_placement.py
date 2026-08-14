import statistics

import pandas as pd
import streamlit as st

from thetower.backend.tourney_results.constants import leagues
from thetower.backend.tourney_results.models import TourneyResult, TourneyRow
from thetower.backend.tourney_results.tourney_utils import get_tourney_state

_DEFAULT_LEAGUES = ["Legend", "Champion", "Platinum", "Gold"]
_PLACES = list(range(1, 31))


def _compute_cohort_stats(result: TourneyResult) -> tuple[list[tuple[float, float] | None], int, float, float]:
    """
    For each of the 30 bracket places compute the median and mean wave across the cohort
    of players who would occupy that place in a perfectly-distributed bracket assignment.

    Methodology (as per community analysis):
      - Sort all players globally by wave descending.
      - n_brackets = total_players // 30  (remainder players are ignored).
      - The place-k cohort is waves[(k-1)*n_brackets : k*n_brackets].
      - Report median and mean of that cohort.

    Returns:
        stats_per_place: list of (median, mean) tuples, one per place (None if empty)
        n_brackets: number of hypothetical brackets
        global_median: median of all player waves
        global_mean: mean of all player waves
    """
    waves = list(TourneyRow.objects.filter(result=result, position__gt=0).values_list("wave", flat=True).order_by("-wave"))
    if not waves:
        return [None] * 30, 0, 0.0, 0.0

    n_brackets = len(waves) // 30
    if n_brackets == 0:
        return [None] * 30, 0, 0.0, 0.0

    stats_per_place: list[tuple[float, float] | None] = []
    for place in _PLACES:
        start = (place - 1) * n_brackets
        end = place * n_brackets
        cohort = waves[start:end]
        if not cohort:
            stats_per_place.append(None)
        else:
            stats_per_place.append((round(statistics.median(cohort), 1), round(statistics.mean(cohort), 1)))

    global_med = statistics.median(waves)
    global_mean = statistics.mean(waves)
    return stats_per_place, n_brackets, global_med, global_mean


def compute_static_placement():
    st.header("Static Global Placement")
    st.caption(
        "What-if analysis: players are sorted globally by wave and distributed one-per-bracket into "
        "hypothetical brackets. Each row shows the median (or mean) wave for the cohort of players "
        "who would hold that bracket place — bracket luck removed."
    )

    # Tournament selector
    col1, col2, col3 = st.columns([2, 1, 1])

    # Build list of available tournaments
    all_results = TourneyResult.objects.filter(public=True).order_by("-date").values("date").distinct()
    available_dates = sorted({r["date"] for r in all_results}, reverse=True)

    if not available_dates:
        st.error("No historical tournament data available.")
        return

    # Hide the most recent tournament if it is still running
    if get_tourney_state().is_active and len(available_dates) > 1:
        available_dates = available_dates[1:]

    date_options = [str(d) for d in available_dates]
    selected_date_str = col1.selectbox("Tournament date", date_options, index=0)
    selected_date = available_dates[date_options.index(selected_date_str)]

    selected_leagues = col2.multiselect(
        "Leagues",
        leagues,
        default=_DEFAULT_LEAGUES,
        help="Select leagues to display",
    )

    stat_choice = col3.radio("Statistic", ["Median", "Mean", "Both"], horizontal=False)

    if not selected_leagues:
        st.warning("Please select at least one league.")
        return

    # Fetch results for selected date
    results_by_league: dict[str, TourneyResult | None] = {}
    for league in selected_leagues:
        qs = TourneyResult.objects.filter(league=league, date=selected_date, public=True)
        results_by_league[league] = qs.first()

    # Show summary stats per league
    summary_cols = st.columns(len(selected_leagues))
    cohort_stats: dict[str, list[tuple[float, float] | None]] = {}

    for i, league in enumerate(selected_leagues):
        result = results_by_league[league]
        if result is None:
            with summary_cols[i]:
                st.metric(league, "No data")
            cohort_stats[league] = [None] * 30
            continue

        stats_per_place, _n_brackets, global_med, global_mean = _compute_cohort_stats(result)
        cohort_stats[league] = stats_per_place

        bcs = result.conditions.all()
        bc_str = " / ".join(bc.shortcut for bc in bcs) if bcs else "–"
        with summary_cols[i]:
            # Never show the bracket count here — it reveals total participation (brackets x 30)
            st.metric(
                league,
                f"Median: {global_med:.0f}",
                help=f"BCs: {bc_str} · Mean: {global_mean:.0f}",
            )

    # Build placement table
    table: dict[str, list] = {"Place": _PLACES}
    for league in selected_leagues:
        if stat_choice == "Both":
            med_col = f"{league} (median)"
            mean_col = f"{league} (mean)"
            table[med_col] = [(v[0] if v is not None else None) for v in cohort_stats[league]]
            table[mean_col] = [(v[1] if v is not None else None) for v in cohort_stats[league]]
        elif stat_choice == "Median":
            table[league] = [(v[0] if v is not None else None) for v in cohort_stats[league]]
        else:
            table[league] = [(v[1] if v is not None else None) for v in cohort_stats[league]]

    df = pd.DataFrame(table)

    def _highlight_median(row: pd.Series) -> list[str]:
        return ["font-weight: bold; background-color: rgba(100,100,200,0.15)" if row["Place"] == 15 else "" for _ in row]

    styled = df.style.apply(_highlight_median, axis=1)

    data_cols = [c for c in df.columns if c != "Place"]
    col_config: dict = {"Place": st.column_config.NumberColumn("Place", format="%d")}
    for col in data_cols:
        col_config[col] = st.column_config.NumberColumn(col, format="%.0f")

    row_height = (len(_PLACES) + 1) * 35 + 10
    st.dataframe(styled, hide_index=True, width="stretch", height=row_height, column_config=col_config)

    with st.expander("How to read this table"):
        st.markdown(
            """
**Methodology** — players in the league are sorted by wave (highest first) and divided into
groups of *n* (where *n* = total players ÷ 30). The first group of *n* players would all be
1st-place finishers in a perfect draw; the next *n* would be 2nd-place finishers, and so on.

**Median / Mean** — the central wave for the cohort of players who would hold that bracket place.
This removes bracket luck: whatever bracket you land in, your cohort average is stable.

**Place 15 (bold)** — the middle bracket position; half the hypothetical brackets have a median
above this wave, half below.

**Why it differs from your actual result** — bracket assignment is random. You might land in a
bracket where your wave earns you 10th instead of 15th — or 20th. The static table shows what
a fair draw would expect.
"""
        )


compute_static_placement()
