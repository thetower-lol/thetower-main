"""Tournament trend analysis via linear regression.

For each key placement position (determined by league promotion/relegation rules),
this page charts the wave required at that position across all tournaments in a
selected patch, fits a linear regression, and shows a summary table comparing the
latest tournament's actual vs predicted values.
"""

import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from thetower.backend.env_config import get_csv_data
from thetower.backend.tourney_results.archive_utils import STRING_COLUMN_DTYPES
from thetower.backend.tourney_results.constants import leagues
from thetower.backend.tourney_results.league_rules import get_league_rules
from thetower.backend.tourney_results.models import PatchNew as Patch


def _fetch_patch_data(league: str, patch: Patch, key_places: list[int]) -> list[dict] | None:
    """Fetch per-tournament bracket-level wave thresholds at key places.

    Reads the historical league CSVs (one file per completed tournament, named
    YYYY-MM-DD.csv.gz) which carry the full final leaderboard including the
    'bracket' column.  For each tournament in the selected patch finds the
    median wave at each key place across all full brackets.

    Returns a list of dicts with keys: date, tournament_index, <place>: wave, ...
    Returns None if fewer than 2 data points are available.
    """
    hist_path = Path(get_csv_data()) / league
    if not hist_path.exists():
        return None

    files = sorted([f for f in hist_path.glob("*.csv.gz") if f.stat().st_size > 0])
    if not files:
        return None

    rules = get_league_rules(league, patch)
    # Tolerate brackets that are missing a few players (e.g. late-join or early-leave)
    min_bracket_size = max(rules.bracket_size - 5, rules.bracket_size // 2)

    data_points: list[dict] = []
    for f in files:
        try:
            tourney_date = datetime.date.fromisoformat(f.stem.replace(".csv", ""))
        except ValueError:
            continue
        if tourney_date < patch.start_date or tourney_date > patch.end_date:
            continue

        df = pd.read_csv(f, dtype=STRING_COLUMN_DTYPES)
        if df.empty or "bracket" not in df.columns:
            continue

        # For each full bracket find the wave at each key place (0-indexed)
        place_waves: dict[int, list[int]] = {p: [] for p in key_places}
        for _bracket, bdf in df.groupby("bracket"):
            bdf_sorted = bdf.sort_values("wave", ascending=False).reset_index(drop=True)
            if len(bdf_sorted) < min_bracket_size:
                continue
            for place in key_places:
                idx = place - 1
                if idx < len(bdf_sorted):
                    place_waves[place].append(int(bdf_sorted.iloc[idx]["wave"]))

        if not all(place_waves[p] for p in key_places):
            continue

        point: dict = {"date": tourney_date}
        for place in key_places:
            point[place] = int(np.median(place_waves[place]))
        data_points.append(point)

    if len(data_points) < 2:
        return None

    data_points.sort(key=lambda x: x["date"])
    for i, pt in enumerate(data_points):
        pt["tournament_index"] = i

    return data_points


def _linregress(x: np.ndarray, y: np.ndarray) -> tuple[float, float]:
    """Return (slope, intercept) from a least-squares linear fit."""
    coeffs = np.polyfit(x, y, 1)
    return float(coeffs[0]), float(coeffs[1])


# Distinct colours for up to 4 key places
_PLACE_COLORS = [
    "#4C9BE8",  # blue
    "#E87C4C",  # orange
    "#4CE87C",  # green
    "#E84C9B",  # pink
    "#B04CE8",  # purple
    "#E8D44C",  # yellow
    "#4CE8D4",  # teal
    "#E84C4C",  # red
    "#8CE84C",  # lime
    "#4C6CE8",  # indigo
]


def compute_regression_analysis():
    st.header("Tournament Trends (Linear Regression)")
    st.caption(
        "Linear regression of wave requirements at key placement positions over a patch. "
        "The slope is how many waves the cutoff rises per tournament."
    )

    col1, col2, col3 = st.columns([2, 2, 2])
    league = col1.selectbox("League", leagues, index=0)
    patches = list(Patch.objects.order_by("-start_date"))
    if not patches:
        st.error("No patch data available.")
        return
    selected_patch = col2.selectbox("Patch", patches, index=0)
    mode = col3.radio("Cutoffs", ["Progression", "Rewards", "Custom"], horizontal=True)

    rules = get_league_rules(league)

    _CUSTOM_PLACES = [1, 4, 15, 24]

    if mode == "Rewards":
        key_places = [t.max_rank for t in rules.reward_tiers]

        def place_label(place: int) -> str:
            tier = rules.rewards_for_place(place)
            if tier is None:
                return f"#{place}"
            if rules.has_keys:
                return f"#{place} ({tier.gems}💎 {tier.stones}🪨 {tier.keys}🔑)"
            return f"#{place} ({tier.gems}💎 {tier.stones}🪨)"

    elif mode == "Custom":
        all_places = list(range(1, 31))
        key_places = sorted(st.multiselect("Places to chart", all_places, default=_CUSTOM_PLACES, key="custom_places"))
        if not key_places:
            st.warning("Select at least one place.")
            return
        place_label = rules.place_label
    else:
        key_places = rules.key_places()
        place_label = rules.place_label

    with st.spinner("Loading tournament data…"):
        data_points = _fetch_patch_data(league, selected_patch, key_places)

    if data_points is None:
        st.warning(f"Not enough tournament data for {league} · {selected_patch} (need at least 2 tournaments).")
        return

    n = len(data_points)
    dates = [pt["date"] for pt in data_points]
    x_all = np.arange(n, dtype=float)

    # Fit regression per key place
    place_color = {place: _PLACE_COLORS[i % len(_PLACE_COLORS)] for i, place in enumerate(key_places)}
    regressions: dict[int, dict] = {}
    for place in key_places:
        y = np.array([pt[place] for pt in data_points], dtype=float)
        slope, intercept = _linregress(x_all, y)
        predicted_latest = slope * (n - 1) + intercept
        actual_latest = float(data_points[-1][place])
        prev_slope: float | None = None
        if n > 2:
            ps, _ = _linregress(x_all[:-1], y[:-1])
            prev_slope = ps
        regressions[place] = {
            "slope": slope,
            "intercept": intercept,
            "predicted_latest": predicted_latest,
            "actual_latest": actual_latest,
            "difference": actual_latest - predicted_latest,
            "prev_slope": prev_slope,
            "y_values": y,
        }

    # ------------------------------------------------------------------
    # Chart: scatter (actual) + regression line per key place
    # ------------------------------------------------------------------
    fig = go.Figure()
    for place in key_places:
        reg = regressions[place]
        color = place_color[place]
        y_line = [reg["intercept"] + reg["slope"] * xi for xi in x_all]

        fig.add_trace(
            go.Scatter(
                x=dates,
                y=reg["y_values"].tolist(),
                mode="markers",
                name=f"{place_label(place)}",
                marker=dict(color=color, size=9),
                legendgroup=str(place),
            )
        )
        fig.add_trace(
            go.Scatter(
                x=dates,
                y=y_line,
                mode="lines",
                name=f"{place_label(place)} trend",
                line=dict(color=color, dash="dash", width=2),
                legendgroup=str(place),
                showlegend=True,
            )
        )

    fig.update_layout(
        title=f"{league} — Wave Trends by Place ({selected_patch})",
        xaxis_title="Tournament Date",
        yaxis_title="Wave",
        hovermode="x unified",
        height=460,
        margin=dict(l=20, r=20, t=50, b=20),
        legend=dict(orientation="h", y=-0.25, x=0.5, xanchor="center"),
    )
    st.plotly_chart(fig, width="stretch")

    # ------------------------------------------------------------------
    # Summary table
    # ------------------------------------------------------------------
    st.markdown("### Regression Summary — Latest Tournament")

    has_prev_slope = any(regressions[p]["prev_slope"] is not None for p in key_places)
    table_rows = []
    for place in key_places:
        reg = regressions[place]
        row: dict = {
            "Place": place_label(place),
            "Actual": int(round(reg["actual_latest"])),
            "Predicted": int(round(reg["predicted_latest"])),
            "Difference": int(round(reg["difference"])),
            "Slope": round(reg["slope"], 2),
        }
        if has_prev_slope:
            row["Prev Slope"] = round(reg["prev_slope"], 2) if reg["prev_slope"] is not None else None
        table_rows.append(row)

    summary_df = pd.DataFrame(table_rows)

    def _colour_diff(val: object) -> str:
        if isinstance(val, int):
            if val > 0:
                return "color: green"
            if val < 0:
                return "color: red"
        return ""

    styled = summary_df.style.map(_colour_diff, subset=["Difference"])

    col_config: dict = {
        "Place": st.column_config.TextColumn("Place"),
        "Actual": st.column_config.NumberColumn("Actual", help="Wave at this place in the latest tournament"),
        "Predicted": st.column_config.NumberColumn("Predicted", help="Regression line value for this tournament"),
        "Difference": st.column_config.NumberColumn("Difference", help="Actual minus Predicted — green = harder than trend, red = easier"),
        "Slope": st.column_config.NumberColumn("Slope (waves/tourney)", help="Waves the trend rises per tournament"),
    }
    if has_prev_slope:
        col_config["Prev Slope"] = st.column_config.NumberColumn(
            "Prev Slope", help="Slope computed without the latest tournament — compare to Slope to see if trend is accelerating"
        )

    st.dataframe(styled, hide_index=True, width="stretch", column_config=col_config)

    with st.expander("How to read this"):
        st.markdown(
            f"""
**Slope** — how many waves the cutoff rises per tournament on average across this patch.
A slope of 8 means the trend line climbs 8 waves each tournament.

**Predicted** — where the regression line sits for the most recent tournament.

**Difference** — Actual minus Predicted.
- Positive (green): the latest tournament was *harder* than the trend expected.
- Negative (red): the latest tournament was *easier* than the trend expected.

**Prev Slope** — slope recomputed without the latest tournament.
If Slope > Prev Slope the trend is accelerating; if less, it is decelerating.

**Key places shown for {league}:**
{chr(10).join(f"- {place_label(p)}" for p in key_places)}
"""
        )


compute_regression_analysis()
