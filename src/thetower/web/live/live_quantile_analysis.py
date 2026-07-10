import logging
from time import perf_counter

import pandas as pd
import plotly.express as px
import streamlit as st

from thetower.backend.tourney_results.shun_config import include_shun_enabled_for
from thetower.web.live.data_ops import (
    get_processed_data,
    get_quantile_analysis_data,
    require_tournament_data,
)
from thetower.web.live.ui_components import render_data_status, setup_common_ui


@require_tournament_data
def quantile_analysis():
    st.markdown("# Live Quantile Analysis")
    logging.info("Starting live quantile analysis")
    t2_start = perf_counter()

    # Use common UI setup
    options, league, is_mobile = setup_common_ui()

    render_data_status(league, "live_placement_cache")

    # Get quantile data from cache
    quantile_df, tourney_start_date, latest_time = get_quantile_analysis_data(league)

    # Check for player ID and get player data if available
    player_data = None
    player_name = None
    if options.current_player_id:
        try:
            include_shun = include_shun_enabled_for("live_placement_cache")
            df, _, _, _, _ = get_processed_data(league, include_shun)
            player_df = df[df.player_id == options.current_player_id].copy()
            if not player_df.empty:
                player_data = player_df
                player_name = player_df["real_name"].iloc[0]

                st.caption(f"📊 Showing data for player: {player_name} (ID: {options.current_player_id})")
        except Exception as e:
            logging.warning(f"Failed to get player data for {options.current_player_id}: {e}")

    # Show tourney start date
    st.caption(f"Tournament start date: {tourney_start_date}")

    # Introduction and explanation
    st.markdown("""
    This analysis shows the **distribution of waves required** to achieve specific placements
    across all brackets in the current tournament. Each curve represents a different placement
    position (1st, 4th, 10th, etc.).

    ### How to Read This Chart:
    - **X-axis (Quantile)**: Percentage of brackets (5% = easiest brackets, 95% = hardest brackets)
    - **Y-axis (Waves)**: Wave count required to achieve that placement
    - **Each line**: Represents a specific placement position

    **Example**: If the 75% quantile for 10th place shows 2500 waves, it means:
    - 75% of brackets required **2500 or fewer waves** to reach 10th place
    - 25% of brackets required **more than 2500 waves** to reach 10th place
    """)

    # Display the quantile data
    if quantile_df.empty:
        st.warning("No quantile data available for this tournament yet.")
        return

    # Sort so quantile curves draw left-to-right (0% → 5% → ... → 95% → 100%)
    quantile_df = quantile_df.sort_values(["rank", "quantile"]).reset_index(drop=True)

    # Create the main quantile curves chart
    fig = px.line(
        quantile_df,
        x="quantile",
        y="waves",
        color="rank",
        title="Wave Requirements by Placement (Quantile Curves)",
        labels={"quantile": "Quantile", "waves": "Waves Required", "rank": "Placement"},
        line_shape="spline",
        markers=True,
    )

    # Format x-axis as percentage
    fig.update_xaxes(tickformat=".0%", range=[0, 1])

    # Customize layout
    # Add a bit of headroom so unified hover labels and top markers aren't clipped
    try:
        min_waves = float(quantile_df["waves"].min())
        max_waves = float(quantile_df["waves"].max())
        y_min = max(0.0, min_waves * 0.98)
        y_max = max_waves * 1.05
        fig.update_yaxes(range=[y_min, y_max])
    except Exception:
        # Fall back to autorange if anything goes wrong
        pass

    fig.update_layout(
        hovermode="x unified",
        legend=dict(title=dict(text="Placement"), orientation="h", y=-0.2, x=0.5, xanchor="center"),
        height=720,
        margin=dict(l=20, r=20, t=80, b=40),
    )

    # Add hover template for better tooltips
    fig.update_traces(
        mode="lines+markers",
        marker=dict(size=6),
        hovertemplate="<b>Rank %{fullData.name}</b><br>" + "Quantile: %{x:.0%}<br>" + "Waves: %{y:.0f}<br>" + "<extra></extra>",
    )

    # Add player's current wave as a horizontal reference line
    if player_data is not None:
        current_wave = player_data["wave"].max()
        fig.add_hline(
            y=current_wave,
            line_dash="dash",
            line_color="red",
            annotation_text=f"{player_name}'s Current Wave: {current_wave}",
            annotation_position="top right",
        )

    st.plotly_chart(fig, width="stretch")

    # Display summary statistics table
    st.markdown("### Summary Statistics by Placement")

    # Create summary table with key quantiles
    def _wave_at_q(rank_data: pd.DataFrame, q: float):
        """Return wave count at quantile q, or None if not in data (e.g. old cache)."""
        matches = rank_data[rank_data["quantile"] == q]["waves"]
        return int(matches.iloc[0]) if not matches.empty else None

    summary_data = []
    for rank in quantile_df["rank"].unique():
        rank_data = quantile_df[quantile_df["rank"] == rank]
        row = {
            "Placement": f"Top {int(rank)}",
            "Min": _wave_at_q(rank_data, 0.0),
            "5th %ile": _wave_at_q(rank_data, 0.05),
            "25th %ile": _wave_at_q(rank_data, 0.25),
            "Median (50th)": _wave_at_q(rank_data, 0.50),
            "75th %ile": _wave_at_q(rank_data, 0.75),
            "95th %ile": _wave_at_q(rank_data, 0.95),
            "Max": _wave_at_q(rank_data, 1.0),
        }
        summary_data.append(row)

    summary_df = pd.DataFrame(summary_data)

    st.dataframe(
        summary_df,
        hide_index=True,
        column_config={
            "Placement": st.column_config.TextColumn("Placement", help="Placement position in bracket"),
            "Min": st.column_config.NumberColumn("Min", help="Minimum waves across all brackets"),
            "5th %ile": st.column_config.NumberColumn("5th %ile", help="Easiest 5% of brackets"),
            "25th %ile": st.column_config.NumberColumn("25th %ile", help="Lower quartile"),
            "Median (50th)": st.column_config.NumberColumn("Median (50th)", help="Middle value across all brackets"),
            "75th %ile": st.column_config.NumberColumn("75th %ile", help="Upper quartile"),
            "95th %ile": st.column_config.NumberColumn("95th %ile", help="Hardest 5% of brackets"),
            "Max": st.column_config.NumberColumn("Max", help="Maximum waves across all brackets"),
        },
    )

    # Add interpretation guidance
    with st.expander("📖 How to Use This Information"):
        st.markdown("""
        ### Strategic Planning:

        **Conservative Strategy** (75th-90th percentile):
        - Use these values if you want to be confident of securing a placement
        - Example: To guarantee top 10, aim for the 75th percentile wave count

        **Target Strategy** (50th percentile / Median):
        - The typical wave count needed across all brackets
        - Good baseline for planning your tournament run

        **Aggressive Strategy** (25th-10th percentile):
        - Minimum waves that might secure a placement
        - Risky - depends on getting a favorable bracket

        ### Understanding Variability:

        **Wide spread** (big difference between 25th and 75th percentile):
        - High variability between brackets
        - Bracket luck plays a larger role

        **Narrow spread** (small difference between 25th and 75th percentile):
        - Consistent requirements across brackets
        - Performance matters more than bracket luck
        """)

    # Additional insights section
    st.markdown("### Bracket Variability Analysis")

    # Calculate interquartile range (IQR) for each placement
    variability_data = []
    for rank in quantile_df["rank"].unique():
        rank_data = quantile_df[quantile_df["rank"] == rank]
        q25 = rank_data[rank_data["quantile"] == 0.25]["waves"].iloc[0]
        q75 = rank_data[rank_data["quantile"] == 0.75]["waves"].iloc[0]
        iqr = q75 - q25
        median = rank_data[rank_data["quantile"] == 0.50]["waves"].iloc[0]
        variability_pct = (iqr / median * 100) if median > 0 else 0

        variability_data.append({"Placement": f"Top {int(rank)}", "IQR (waves)": int(iqr), "IQR % of Median": f"{variability_pct:.1f}%"})

    variability_df = pd.DataFrame(variability_data)

    col1, col2 = st.columns(2)

    with col1:
        st.dataframe(
            variability_df,
            hide_index=True,
            column_config={
                "Placement": st.column_config.TextColumn("Placement"),
                "IQR (waves)": st.column_config.NumberColumn("IQR (waves)", help="Interquartile Range: difference between 75th and 25th percentile"),
                "IQR % of Median": st.column_config.TextColumn("IQR % of Median", help="IQR as percentage of median - higher = more variability"),
            },
        )

    with col2:
        st.markdown("""
        **Interquartile Range (IQR)** measures the spread of the middle 50% of brackets.

        - **Lower IQR**: More consistent across brackets
        - **Higher IQR**: More bracket-to-bracket variation
        - **IQR % of Median**: Normalizes variability for comparison
        """)

    # Log execution time
    t2_stop = perf_counter()
    logging.info(f"Full live_quantile_analysis for {league} took {t2_stop - t2_start}")


quantile_analysis()
