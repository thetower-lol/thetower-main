import os

import streamlit as st

from thetower.backend.tourney_results.models import TourneyRow
from thetower.backend.tourney_results.results_config import get_results_limit

hidden_features = os.environ.get("HIDDEN_FEATURES")


def get_proximal_players(player_id: str, n: int) -> tuple[list[str], str | None, str | None, str | None]:
    """
    Get the n players above and below the given player in their most recent completed tournament.

    On the public site only published tournaments count and the neighbours stop at the league's public
    results cap: neighbours past it would let a viewer step down the field 2n at a time and count it.
    A player who placed past the cap has no peer group there.

    Returns:
        Tuple of (player_id_list, focal_player_name, league, date_str). ([], None, None, None) if the player
        was not found; an empty list with the name, league and date set if they placed past the public cap.
    """
    try:
        focal_rows = TourneyRow.objects.filter(player_id=player_id)
        if not hidden_features:
            focal_rows = focal_rows.filter(result__public=True)
        focal_row = focal_rows.select_related("result").order_by("-result__date").first()
        if not focal_row:
            return [], None, None, None

        tr = focal_row.result
        pos = focal_row.position
        focal_name = focal_row.nickname or player_id
        date_str = tr.date.strftime("%Y-%m-%d") if tr.date else None

        last_position = pos + n
        if not hidden_features:
            cap = get_results_limit(tr.league)
            if pos > cap:
                return [], focal_name, tr.league, date_str
            last_position = min(last_position, cap)

        neighbor_ids = list(
            TourneyRow.objects.filter(
                result=tr,
                position__gte=max(1, pos - n),
                position__lte=last_position,
            )
            .order_by("position")
            .values_list("player_id", flat=True)
        )

        # Deduplicate while preserving position order
        seen: set[str] = set()
        player_ids: list[str] = []
        for pid in neighbor_ids:
            if pid not in seen:
                seen.add(pid)
                player_ids.append(pid)

        return player_ids, focal_name, tr.league, date_str

    except Exception as e:
        st.error(f"Error finding proximal players: {e}")
        return [], None, None, None
