"""
Results Limits admin page.

Configures the per-league public-site results row caps stored in `results_limits.json`
(DJANGO_DATA directory). The caps bound how many rows the public results pages will show,
so they must stay below real participation to avoid leaking total participation numbers.

Also provides a participation viewer: pick a tournament date and see per-league row counts
so caps can be chosen with real numbers in front of you.
"""

import pandas as pd
import streamlit as st
from django.db.models import Count

from thetower.backend.tourney_results.constants import how_many_results_public_site, leagues
from thetower.backend.tourney_results.models import TourneyResult, TourneyRow
from thetower.backend.tourney_results.results_config import (
    RESULTS_LIMITS_FILENAME,
    read_results_limits_from_disk,
    results_limits_invalidate,
    write_results_limits_to_disk,
)


def _render_limits_section() -> None:
    st.subheader("🔢 Per-League Public Results Caps")
    st.markdown(
        f"Public results pages never show rows past a league's cap. Leave a box **blank** to remove the league "
        f"from the config — it then falls back to the global constant (`{how_many_results_public_site:,}`) and keeps "
        f"tracking it if the constant ever changes. "
        f"Caps must stay **below** real participation, otherwise the end of the results list reveals the total. "
        f"Saved to `{RESULTS_LIMITS_FILENAME}` in the DJANGO_DATA directory; pages pick changes up within ~5 minutes."
    )

    on_disk = read_results_limits_from_disk()
    configured = on_disk.get("leagues", {})

    new_limits: dict[str, int] = {}
    cols = st.columns(3)
    for i, league in enumerate(leagues):
        with cols[i % 3]:
            configured_value = configured.get(league)
            value = st.number_input(
                league,
                min_value=100,
                max_value=100_000,
                value=int(configured_value) if configured_value is not None else None,
                step=100,
                placeholder=f"{how_many_results_public_site:,} (default)",
                key=f"results_limit_{league}",
            )
            if value is not None:
                new_limits[league] = int(value)

    if st.button("💾 Save Limits", type="primary"):
        try:
            write_results_limits_to_disk(new_limits)
            results_limits_invalidate()
            st.success(f"Saved to {RESULTS_LIMITS_FILENAME} and cache invalidated.")
        except Exception as exc:
            st.error(f"Failed to save limits: {exc}")


def _render_participation_section() -> None:
    st.subheader("👥 Participation by Tournament")
    st.markdown("Row counts straight from the database — use these to sanity-check that every cap sits below real participation.")

    available_dates = list(TourneyResult.objects.order_by("-date").values_list("date", flat=True).distinct())
    if not available_dates:
        st.warning("No tournament results in the database.")
        return

    selected_date = st.selectbox("Tournament date", available_dates, format_func=str)

    counts = (
        TourneyRow.objects.filter(result__date=selected_date, position__gt=0)
        .values("result__league")
        .annotate(participants=Count("id"))
    )
    participants_by_league = {row["result__league"]: row["participants"] for row in counts}
    public_by_league = dict(TourneyResult.objects.filter(date=selected_date).values_list("league", "public"))

    configured = read_results_limits_from_disk().get("leagues", {})

    rows = []
    for league in leagues:
        participants = participants_by_league.get(league)
        if participants is None:
            continue
        cap = int(configured.get(league, how_many_results_public_site))
        rows.append(
            {
                "League": league,
                "Participants": participants,
                "≈ Brackets": participants // 30,
                "Public": "✓" if public_by_league.get(league) else "✗",
                "Current cap": cap,
                "Cap hides total?": "✅" if cap < participants else "⚠️ leaks",
            }
        )

    if not rows:
        st.warning("No rows found for this date.")
        return

    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")


def main() -> None:
    st.title("🔢 Results Limits")

    st.divider()
    _render_limits_section()

    st.divider()
    _render_participation_section()


main()
