"""League statistics page — hidden site only.

Shows highest max wave, average wave, and median wave per league,
filtered by user-selected patch versions and leagues.

Sections:
  1. Trend chart — per-tournament time series with patch boundary markers.
  2. Per-patch summary — aggregate stats broken down by patch.
  3. Overall summary — aggregate across all selected patches.
"""

import statistics
from collections import defaultdict

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from django.db.models import Q

from thetower.backend.tourney_results.constants import leagues
from thetower.backend.tourney_results.data import get_patches
from thetower.backend.tourney_results.models import TourneyResult, TourneyRow

# Colours used consistently across charts
_STAT_COLORS = {
    "Max Wave": "#e74c3c",
    "Avg Wave": "#3498db",
    "Median Wave": "#2ecc71",
}

# One colour per league (cycling for overflow)
_LEAGUE_COLORS = ["#9b59b6", "#e67e22", "#1abc9c", "#f39c12", "#2980b9", "#c0392b"]


def _compute_stats(waves: list[int]) -> tuple[int | None, float | None, float | None]:
    """Return (max, mean, median) for a list of waves, or (None, None, None) if empty."""
    if not waves:
        return None, None, None
    return max(waves), round(statistics.mean(waves), 1), round(statistics.median(waves), 1)


def compute_league_stats() -> None:
    st.header("League Statistics")
    st.caption("Aggregate wave statistics per league across selected patches. " "Includes all players from every tournament in the selected range.")

    # ---- Filters ----------------------------------------------------------------
    all_patches = sorted(
        [p for p in get_patches() if p.version_minor],
        key=lambda p: p.start_date,
        reverse=True,
    )

    filter_col1, filter_col2 = st.columns(2)

    default_patches = all_patches[:5] if len(all_patches) >= 5 else all_patches
    selected_patches = filter_col1.multiselect(
        "Patch versions",
        options=all_patches,
        default=default_patches,
        format_func=str,
        help="Select one or more patch versions to include.",
    )

    selected_leagues = filter_col2.multiselect(
        "Leagues",
        options=leagues,
        default=list(leagues),
        help="Select one or more leagues to display.",
    )

    if not selected_patches:
        st.warning("Please select at least one patch version.")
        return

    if not selected_leagues:
        st.warning("Please select at least one league.")
        return

    # Sort patches chronologically for boundary markers
    sorted_patches = sorted(selected_patches, key=lambda p: p.start_date)

    # ---- Data fetch -------------------------------------------------------------
    patch_q = Q()
    for patch in selected_patches:
        patch_q |= Q(date__gte=patch.start_date, date__lte=patch.end_date)

    results_qs = list(TourneyResult.objects.filter(patch_q, league__in=selected_leagues).order_by("date").values("id", "league", "date"))

    if not results_qs:
        st.info("No tournament data found for the selected filters.")
        return

    result_ids = [r["id"] for r in results_qs]

    rows = TourneyRow.objects.filter(
        result__in=result_ids,
        position__gt=0,
    ).values("result_id", "wave")

    waves_by_result: dict[int, list[int]] = defaultdict(list)
    for row in rows:
        waves_by_result[row["result_id"]].append(row["wave"])

    # ---- Section 1: Trend chart -------------------------------------------------
    st.markdown("### Trend Over Time")
    st.caption("One point per tournament. Dashed vertical lines mark patch boundaries.")

    # Build per-tournament stats, grouped by league
    trend_by_league: dict[str, list[dict]] = defaultdict(list)
    for result in results_qs:
        rid = result["id"]
        waves = waves_by_result.get(rid, [])
        mx, avg, med = _compute_stats(waves)
        if mx is None:
            continue
        trend_by_league[result["league"]].append({"date": result["date"], "Max Wave": mx, "Avg Wave": avg, "Median Wave": med})

    trend_fig = go.Figure()

    for league_idx, league in enumerate(selected_leagues):
        league_color = _LEAGUE_COLORS[league_idx % len(_LEAGUE_COLORS)]
        points = sorted(trend_by_league.get(league, []), key=lambda p: p["date"])
        if not points:
            continue
        dates = [p["date"] for p in points]

        # Show all 3 stats, distinguished by line dash style
        dash_styles = {"Max Wave": "solid", "Avg Wave": "dot", "Median Wave": "dash"}
        for stat, dash in dash_styles.items():
            y_vals = [p[stat] for p in points]
            trace_name = f"{league} — {stat}" if len(selected_leagues) > 1 else stat
            trend_fig.add_trace(
                go.Scatter(
                    x=dates,
                    y=y_vals,
                    mode="lines+markers",
                    name=trace_name,
                    line=dict(color=league_color, dash=dash, width=2),
                    marker=dict(size=5),
                    legendgroup=f"{league}_{stat}",
                    showlegend=True,
                )
            )

    # Patch boundary vertical lines
    for patch in sorted_patches:
        trend_fig.add_shape(
            type="line",
            x0=patch.start_date,
            x1=patch.start_date,
            y0=0,
            y1=1,
            yref="paper",
            line=dict(dash="dash", color="rgba(255,255,255,0.4)", width=1),
        )
        trend_fig.add_annotation(
            x=patch.start_date,
            y=1,
            yref="paper",
            text=str(patch),
            showarrow=False,
            font=dict(size=10, color="rgba(200,200,200,0.8)"),
            xanchor="left",
            yanchor="top",
        )

    trend_fig.update_layout(
        xaxis_title="Tournament Date",
        yaxis_title="Wave",
        height=480,
        hovermode="x unified",
        margin=dict(l=20, r=20, t=30, b=20),
        legend=dict(orientation="h", y=-0.25, x=0.5, xanchor="center"),
    )
    st.plotly_chart(trend_fig, width="stretch")

    # ---- Section 2: Per-patch summary -------------------------------------------
    st.markdown("### Per-Patch Summary")

    # Collect waves grouped by (patch label, league)
    waves_by_patch_league: dict[tuple[str, str], list[int]] = defaultdict(list)
    for result in results_qs:
        rid = result["id"]
        waves = waves_by_result.get(rid, [])
        if not waves:
            continue
        rdate = result["date"]
        patch_label = next(
            (str(p) for p in sorted_patches if p.start_date <= rdate <= p.end_date),
            "Unknown",
        )
        waves_by_patch_league[(patch_label, result["league"])].extend(waves)

    patch_stat_rows = []
    for patch in sorted_patches:
        plabel = str(patch)
        for league in selected_leagues:
            waves = waves_by_patch_league.get((plabel, league), [])
            mx, avg, med = _compute_stats(waves)
            patch_stat_rows.append(
                {
                    "Patch": plabel,
                    "League": league,
                    "Max Wave": mx,
                    "Avg Wave": avg,
                    "Median Wave": med,
                    "Entries": len(waves),
                }
            )

    patch_df = pd.DataFrame(patch_stat_rows)

    # Grouped bar chart: x=patch, bars grouped by league, one subplot per stat
    patch_fig = go.Figure()
    for league_idx, league in enumerate(selected_leagues):
        league_color = _LEAGUE_COLORS[league_idx % len(_LEAGUE_COLORS)]
        sub = patch_df[patch_df["League"] == league]
        patch_labels = [str(p) for p in sorted_patches]
        median_vals = [sub[sub["Patch"] == pl]["Median Wave"].values[0] if pl in sub["Patch"].values else None for pl in patch_labels]
        avg_vals = [sub[sub["Patch"] == pl]["Avg Wave"].values[0] if pl in sub["Patch"].values else None for pl in patch_labels]
        max_vals = [sub[sub["Patch"] == pl]["Max Wave"].values[0] if pl in sub["Patch"].values else None for pl in patch_labels]

        patch_fig.add_trace(
            go.Bar(
                name=f"{league} — Median",
                x=patch_labels,
                y=median_vals,
                marker_color=league_color,
                opacity=1.0,
                legendgroup=league,
            )
        )
        patch_fig.add_trace(
            go.Bar(
                name=f"{league} — Avg",
                x=patch_labels,
                y=avg_vals,
                marker_color=league_color,
                opacity=0.65,
                legendgroup=league,
                showlegend=True,
            )
        )
        patch_fig.add_trace(
            go.Bar(
                name=f"{league} — Max",
                x=patch_labels,
                y=max_vals,
                marker_color=league_color,
                opacity=0.35,
                legendgroup=league,
                showlegend=True,
            )
        )

    patch_fig.update_layout(
        barmode="group",
        xaxis_title="Patch",
        yaxis_title="Wave",
        height=420,
        margin=dict(l=20, r=20, t=30, b=20),
        legend=dict(orientation="h", y=-0.3, x=0.5, xanchor="center"),
    )
    st.plotly_chart(patch_fig, width="stretch")

    patch_col_config = {
        "Patch": st.column_config.TextColumn("Patch", width="medium"),
        "League": st.column_config.TextColumn("League", width="medium"),
        "Max Wave": st.column_config.NumberColumn("Max Wave", format="%d"),
        "Avg Wave": st.column_config.NumberColumn("Avg Wave", format="%.1f"),
        "Median Wave": st.column_config.NumberColumn("Median Wave", format="%.1f"),
        "Entries": st.column_config.NumberColumn("Entries", help="Total player-tournament entries for this patch+league"),
    }
    patch_row_height = (len(patch_df) + 1) * 35 + 10
    st.dataframe(patch_df, hide_index=True, width="stretch", height=patch_row_height, column_config=patch_col_config)

    # ---- Section 3: Overall summary (across all selections) ---------------------
    st.markdown("### Overall Summary")
    st.caption("Aggregated across all selected patches and tournaments.")

    waves_by_league: dict[str, list[int]] = defaultdict(list)
    for result in results_qs:
        waves = waves_by_result.get(result["id"], [])
        if waves:
            waves_by_league[result["league"]].extend(waves)

    overall_rows = []
    for league in selected_leagues:
        waves = waves_by_league.get(league, [])
        mx, avg, med = _compute_stats(waves)
        overall_rows.append(
            {
                "League": league,
                "Max Wave": mx,
                "Avg Wave": avg,
                "Median Wave": med,
                "Entries": len(waves),
            }
        )

    overall_df = pd.DataFrame(overall_rows)

    overall_fig = go.Figure()
    valid = overall_df[overall_df["Max Wave"].notna()]
    if not valid.empty:
        for stat, color in _STAT_COLORS.items():
            overall_fig.add_trace(
                go.Bar(
                    name=stat,
                    x=valid["League"],
                    y=valid[stat],
                    marker_color=color,
                )
            )
    overall_fig.update_layout(
        barmode="group",
        xaxis_title="League",
        yaxis_title="Wave",
        height=400,
        margin=dict(l=20, r=20, t=30, b=20),
        legend=dict(orientation="h", y=-0.2, x=0.5, xanchor="center"),
    )
    st.plotly_chart(overall_fig, width="stretch")

    overall_col_config = {
        "League": st.column_config.TextColumn("League", width="medium"),
        "Max Wave": st.column_config.NumberColumn("Max Wave", format="%d"),
        "Avg Wave": st.column_config.NumberColumn("Avg Wave", format="%.1f"),
        "Median Wave": st.column_config.NumberColumn("Median Wave", format="%.1f"),
        "Entries": st.column_config.NumberColumn("Entries", help="Total player-tournament entries included"),
    }
    overall_row_height = (len(overall_df) + 1) * 35 + 10
    st.dataframe(overall_df, hide_index=True, width="stretch", height=overall_row_height, column_config=overall_col_config)


compute_league_stats()
