import logging
from time import perf_counter

import pandas as pd
import plotly.express as px
import streamlit as st

from thetower.backend.tourney_results.shun_config import include_shun_enabled_for
from thetower.web.live.data_ops import (
    get_bracket_stats,
    get_cached_plot_data,
    get_processed_data,
    require_tournament_data,
)
from thetower.web.live.ui_components import render_data_status, setup_common_ui


@require_tournament_data
def bracket_analysis():
    st.markdown("# Live Bracket Analysis")
    logging.info("Starting live bracket analysis")
    t2_start = perf_counter()

    options, league, is_mobile = setup_common_ui()

    render_data_status(league, "live_bracket_analysis")

    # Get processed data
    include_shun = include_shun_enabled_for("live_bracket_analysis")
    df, _, ldf, _, _ = get_processed_data(league, include_shun)

    # Sidebar option to include single-player brackets
    show_singles = st.sidebar.checkbox("Show full fidelity", value=False)
    if not show_singles:
        bracket_sizes = ldf.groupby("bracket")["wave"].transform("count")
        ldf = ldf[bracket_sizes > 1]

    # Get bracket statistics
    bracket_stats = get_bracket_stats(ldf)
    st.write(f"Total closed brackets until now: {bracket_stats['total_brackets']}")

    # Calculate top positions for each bracket more efficiently
    def get_top_n(group, n):
        return group.nlargest(n).iloc[-1] if len(group) >= n else None

    group_by_bracket = ldf.groupby("bracket").wave
    stats_dict = {f"Top {n}": group_by_bracket.apply(lambda x: get_top_n(x, n)) for n in [1, 4, 10, 15]}

    # Create stats dataframe with proper column names
    stats_df = pd.DataFrame(stats_dict).reset_index()
    stats_df_melted = stats_df.melt(id_vars=["bracket"], var_name="Position", value_name="Waves")

    # Create histogram using cached plot data
    plot_data = get_cached_plot_data(stats_df_melted)
    fig1 = px.histogram(
        plot_data,
        x="Waves",
        color="Position",
        barmode="overlay",
        opacity=0.7,
        title="Distribution of Top Positions per Bracket",
        labels={"Waves": "Wave Reached", "count": "Number of Brackets", "Position": "Position"},
        height=300,
    )

    fig1.update_layout(margin=dict(l=20, r=20, t=40, b=20), legend=dict(yanchor="top", y=0.99, xanchor="right", x=0.99))
    st.plotly_chart(fig1, width="stretch")

    # Display bracket statistics in columns
    cols = st.columns(2 if not is_mobile else 1)

    with cols[0]:
        st.write(f"Highest total waves: {bracket_stats['highest_total']}")
        st.dataframe(ldf[ldf.bracket == bracket_stats["highest_total"]][["real_name", "wave", "datetime"]])

        st.write(f"Lowest total waves: {bracket_stats['lowest_total']}")
        st.dataframe(ldf[ldf.bracket == bracket_stats["lowest_total"]][["real_name", "wave", "datetime"]])

    with cols[1]:
        st.write(f"Highest median waves: {bracket_stats['highest_median']}")
        st.dataframe(ldf[ldf.bracket == bracket_stats["highest_median"]][["real_name", "wave", "datetime"]])

        st.write(f"Lowest median waves: {bracket_stats['lowest_median']}")
        st.dataframe(ldf[ldf.bracket == bracket_stats["lowest_median"]][["real_name", "wave", "datetime"]])

    # --- Salt Ranking ---
    st.markdown("---")
    st.markdown("## 🧂 Salt Ranking")
    st.caption("The brackets where the promotion/relegation cutoffs were hardest to reach this tournament.")

    salt_cols = st.columns(2 if not is_mobile else 1)

    with salt_cols[0]:
        if league == "Legend":
            st.info("Legend is the top tier — players cannot be promoted further.")
        elif bracket_stats["hardest_promotion"] is not None:
            bracket_id = bracket_stats["hardest_promotion"]
            wave = bracket_stats["hardest_promotion_wave"]
            st.write(f"**Hardest Promotion** — last-promoted wave: **{wave}**")
            st.caption(f"Bracket: {bracket_id}")
            st.dataframe(ldf[ldf.bracket == bracket_id][["real_name", "wave", "datetime"]])
        else:
            st.info("Not enough data for promotion bracket (need at least 4 players per bracket).")

    with salt_cols[1] if not is_mobile else salt_cols[0]:
        if league in ("Copper", "Silver", "Gold"):
            st.info(f"{league} is a protected tier — players cannot be demoted from it.")
        elif bracket_stats["hardest_relegation"] is not None:
            bracket_id = bracket_stats["hardest_relegation"]
            wave = bracket_stats["hardest_relegation_wave"]
            st.write(f"**Hardest Relegation** — first-demoted wave: **{wave}**")
            st.caption(f"Bracket: {bracket_id}")
            st.dataframe(ldf[ldf.bracket == bracket_id][["real_name", "wave", "datetime"]])
        else:
            st.info("Not enough data for relegation bracket (need at least 25 players per bracket).")

    # --- Spoon Ranking ---
    st.markdown("---")
    st.markdown("## 🥄 Spoon Ranking")
    st.caption("The brackets where the promotion/relegation cutoffs were easiest to reach this tournament.")

    spoon_cols = st.columns(2 if not is_mobile else 1)

    with spoon_cols[0]:
        if league == "Legend":
            st.info("Legend is the top tier — players cannot be promoted further.")
        elif bracket_stats["easiest_promotion"] is not None:
            bracket_id = bracket_stats["easiest_promotion"]
            wave = bracket_stats["easiest_promotion_wave"]
            st.write(f"**Easiest Promotion** — last-promoted wave: **{wave}**")
            st.caption(f"Bracket: {bracket_id}")
            st.dataframe(ldf[ldf.bracket == bracket_id][["real_name", "wave", "datetime"]])
        else:
            st.info("Not enough data for promotion bracket (need at least 4 players per bracket).")

    with spoon_cols[1] if not is_mobile else spoon_cols[0]:
        if league in ("Copper", "Silver", "Gold"):
            st.info(f"{league} is a protected tier — players cannot be demoted from it.")
        elif bracket_stats["easiest_relegation"] is not None:
            bracket_id = bracket_stats["easiest_relegation"]
            wave = bracket_stats["easiest_relegation_wave"]
            st.write(f"**Easiest Relegation** — first-demoted wave: **{wave}**")
            st.caption(f"Bracket: {bracket_id}")
            st.dataframe(ldf[ldf.bracket == bracket_id][["real_name", "wave", "datetime"]])
        else:
            st.info("Not enough data for relegation bracket (need at least 25 players per bracket).")

    # Log execution time
    t2_stop = perf_counter()
    logging.info(f"Full live_bracket_analysis for {league} took {t2_stop - t2_start}")


bracket_analysis()
