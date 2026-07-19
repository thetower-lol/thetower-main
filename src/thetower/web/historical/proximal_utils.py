import streamlit as st

from thetower.backend.tourney_results.models import TourneyRow


def get_proximal_players(player_id: str, n: int) -> tuple[list[str], str | None, str | None, str | None]:
    """
    Get the n players above and below the given player in their most recent completed tournament.

    Returns:
        Tuple of (player_id_list, focal_player_name, league, date_str), or ([], None, None, None) if not found.
    """
    try:
        focal_row = TourneyRow.objects.filter(player_id=player_id).select_related("result").order_by("-result__date").first()
        if not focal_row:
            return [], None, None, None

        tr = focal_row.result
        pos = focal_row.position

        neighbor_ids = list(
            TourneyRow.objects.filter(
                result=tr,
                position__gte=max(1, pos - n),
                position__lte=pos + n,
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

        focal_name = focal_row.nickname or player_id
        date_str = tr.date.strftime("%Y-%m-%d") if tr.date else None
        return player_ids, focal_name, tr.league, date_str

    except Exception as e:
        st.error(f"Error finding proximal players: {e}")
        return [], None, None, None
