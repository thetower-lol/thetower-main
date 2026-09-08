import os

import streamlit as st

from thetower.backend.tourney_results.constants import leagues
from thetower.backend.tourney_results.data import get_all_banned_ids
from thetower.backend.tourney_results.models import BattleCondition, TourneyResult, TourneyRow


def compute_bc_filter():
    st.markdown("# Battle Condition Filter")
    st.write("Find historical tournaments based on which battle conditions were and/or were not active.")

    league = st.selectbox("League:", leagues)

    hidden_features = os.environ.get("HIDDEN_FEATURES")
    public = {"public": True} if not hidden_features else {}

    all_bcs = list(BattleCondition.objects.order_by("name"))

    if not all_bcs:
        st.info("No battle conditions found in the database.")
        return

    bc_names = [bc.name for bc in all_bcs]
    bc_by_name = {bc.name: bc for bc in all_bcs}

    # Before rendering the exclude widget, clean any values that are now also in include
    # (guards against conflicts when the user adds a BC to include that was already in exclude)
    if "bc_must_exclude" in st.session_state:
        current_include = st.session_state.get("bc_must_include") or []
        cleaned = [v for v in st.session_state["bc_must_exclude"] if v not in current_include]
        if cleaned != list(st.session_state["bc_must_exclude"]):
            st.session_state["bc_must_exclude"] = cleaned

    col1, col2 = st.columns(2)

    with col1:
        included = st.multiselect(
            "Must include all of these battle conditions:",
            bc_names,
            key="bc_must_include",
        )

    # Conditions already selected for include are unavailable for exclude
    exclude_opts = [bc for bc in bc_names if bc not in included]

    with col2:
        excluded = st.multiselect(
            "Must not include any of these battle conditions:",
            exclude_opts,
            key="bc_must_exclude",
        )

    # Build filtered queryset
    qs = TourneyResult.objects.filter(league=league, **public).order_by("-date")

    # Chain a .filter(conditions=bc) per required condition — each is an inner join (AND semantics)
    for bc_name in included:
        qs = qs.filter(conditions=bc_by_name[bc_name])

    # Chain .exclude(conditions=bc) per forbidden condition
    for bc_name in excluded:
        qs = qs.exclude(conditions=bc_by_name[bc_name])

    results = list(qs.prefetch_related("conditions"))

    # Status message
    if not included and not excluded:
        st.info(f"No filters applied — showing all {len(results)} {league} tournament(s). Select battle conditions above to filter.")
    elif not results:
        st.warning("No tournaments found matching the selected criteria.")
        return
    else:
        st.success(f"Found {len(results)} {league} tournament(s) matching the selected criteria.")

    if not results:
        return

    position_cols = [1, 10, 25, 50, 100, 200]
    result_ids = [r.id for r in results]

    # Fetch all rows up to the highest target position so ties (duplicate positions) are handled.
    # A tie at e.g. pos 49 means no row exists at exactly pos 50 — we want the wave at the last
    # position <= the target instead of querying for exact position matches.
    all_rows = (
        TourneyRow.objects.filter(result_id__in=result_ids, position__lte=max(position_cols), position__gt=0)
        .exclude(player_id__in=get_all_banned_ids())
        .values("result_id", "position", "wave")
        .order_by("result_id", "position")
    )

    # Build result_id -> sorted list of (position, wave)
    from collections import defaultdict

    result_rows: dict[int, list[tuple[int, int]]] = defaultdict(list)
    for row in all_rows:
        result_rows[row["result_id"]].append((row["position"], row["wave"]))

    # For each target position find the wave at the highest position <= target
    wave_lookup: dict[int, dict[int, int | str]] = {}
    for result_id, sorted_rows in result_rows.items():
        waves: dict[int, int | str] = {}
        for target_pos in position_cols:
            val: int | str = ""
            for pos, wave in sorted_rows:
                if pos <= target_pos:
                    val = wave
                else:
                    break
            waves[target_pos] = val
        wave_lookup[result_id] = waves

    rows = []
    for result in results:
        bcs = list(result.conditions.all())
        bc_str = ", ".join(bc.name for bc in sorted(bcs, key=lambda b: b.name)) if bcs else "None"
        pos_waves = wave_lookup.get(result.id, {})
        entry = {"Date": result.date, "Battle Conditions": bc_str}
        for pos in position_cols:
            entry[f"#{pos}"] = pos_waves.get(pos, "")
        rows.append(entry)

    pos_headers = "".join(f"<th style='text-align:right;white-space:nowrap;padding:4px 0 4px 12px'>#{pos}</th>" for pos in position_cols)
    rows_html = "".join(
        "<tr>"
        f"<td style='white-space:nowrap;padding:4px 12px 4px 0'>{row['Date']}</td>"
        f"<td style='padding:4px 12px'>{row['Battle Conditions']}</td>"
        + "".join(f"<td style='text-align:right;white-space:nowrap;padding:4px 0 4px 12px'>{row[f'#{pos}']}</td>" for pos in position_cols)
        + "</tr>"
        for row in rows
    )
    html = (
        "<table style='width:100%;border-collapse:collapse'>"
        "<thead><tr>"
        "<th style='text-align:left;white-space:nowrap;padding:4px 12px 4px 0'>Date</th>"
        "<th style='text-align:left;padding:4px 12px'>Battle Conditions</th>"
        f"{pos_headers}"
        "</tr></thead>"
        f"<tbody>{rows_html}</tbody></table>"
    )
    st.markdown(html, unsafe_allow_html=True)


compute_bc_filter()
