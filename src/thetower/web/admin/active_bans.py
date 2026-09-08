"""Active bans: every hard and soft ban currently in force, with the player's most recent public result.

Soft bans do not stop in-game play, so a soft-banned player can keep appearing in tournament results
while being hidden from the public site. This page is where that is visible at a glance.
"""

import pandas as pd
import streamlit as st
from django.db.models import Count, Max

from thetower.backend.sus.models import ModerationRecord, PlayerId
from thetower.backend.tourney_results.data import get_player_id_lookup
from thetower.backend.tourney_results.models import TourneyRow

BAN_TYPES = {ModerationRecord.ModerationType.BAN: "hard", ModerationRecord.ModerationType.SOFT_BAN: "soft"}


@st.cache_data(ttl=300)
def get_active_bans() -> pd.DataFrame:
    """One row per active ban record: who it covers and when any covered id last appeared in public results."""
    records = list(
        ModerationRecord.objects.filter(moderation_type__in=list(BAN_TYPES), resolved_at__isnull=True)
        .select_related("game_instance__player")
        .order_by("-created_at")
    )
    if not records:
        return pd.DataFrame()

    # A ban on a game instance covers every tower id in that instance; a standalone record covers its own id
    instance_ids = {record.game_instance_id for record in records if record.game_instance_id}
    ids_by_instance: dict[int, list[str]] = {}
    for instance_id, tower_id in PlayerId.objects.filter(game_instance_id__in=instance_ids).values_list("game_instance_id", "id"):
        ids_by_instance.setdefault(instance_id, []).append(tower_id)
    covered = {record.pk: sorted(set(ids_by_instance.get(record.game_instance_id, [])) | {record.tower_id}) for record in records}

    all_ids = {tower_id for ids in covered.values() for tower_id in ids}
    last_seen = {
        row["player_id"]: (row["last_date"], row["rows"])
        for row in TourneyRow.objects.filter(player_id__in=all_ids, position__gt=0, result__public=True)
        .values("player_id")
        .annotate(last_date=Max("result__date"), rows=Count("id"))
    }
    lookup = get_player_id_lookup()
    today = pd.Timestamp.now().date()

    rows = []
    for record in records:
        seen = [last_seen[tower_id] for tower_id in covered[record.pk] if tower_id in last_seen]
        last_date = max((date for date, _ in seen), default=None)
        if record.game_instance and record.game_instance.player:
            player = record.game_instance.player.name
        else:
            player = lookup.get(record.tower_id, "Unknown")
        rows.append(
            {
                "type": BAN_TYPES[record.moderation_type],
                "player": player,
                "tower_id": record.tower_id,
                "covered_ids": len(covered[record.pk]),
                "last_public_result": last_date,
                "days_since": (today - last_date).days if last_date else None,
                "public_rows": sum(count for _, count in seen),
                "source": record.source,
                "banned_since": (record.started_at or record.created_at).date(),
                "reason": record.reason or "",
            }
        )
    return pd.DataFrame(rows)


def render_active_bans() -> None:
    st.markdown("# Active Bans")
    st.caption(
        "Every hard and soft ban currently in force, with the most recent public tournament result across all the "
        "tower ids the ban covers. Banned players are hidden from the public site; soft bans do not stop in-game "
        "play, so this is where a soft-banned player who keeps competing shows up."
    )

    with st.spinner("Loading active bans..."):
        df = get_active_bans()
    if df.empty:
        st.success("No active bans.")
        return

    has_result = df["last_public_result"].notna()
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Hard bans", int((df["type"] == "hard").sum()))
    c2.metric("Soft bans", int((df["type"] == "soft").sum()))
    c3.metric("With public results", int(has_result.sum()))
    c4.metric("Played in last 30 days", int((df["days_since"] <= 30).sum()))

    f1, f2 = st.columns(2)
    ban_type = f1.selectbox("Ban type", ["all", "soft", "hard"])
    activity = f2.selectbox("Activity", ["all", "played in last 30 days", "played in last 90 days", "has public results", "never in public results"])

    view = df
    if ban_type != "all":
        view = view[view["type"] == ban_type]
    if activity == "played in last 30 days":
        view = view[view["days_since"] <= 30]
    elif activity == "played in last 90 days":
        view = view[view["days_since"] <= 90]
    elif activity == "has public results":
        view = view[view["last_public_result"].notna()]
    elif activity == "never in public results":
        view = view[view["last_public_result"].isna()]

    st.caption(f"Showing {len(view)} of {len(df)} active bans, most recently active first")
    st.dataframe(
        view.sort_values(["last_public_result", "banned_since"], ascending=False, na_position="last"),
        hide_index=True,
        width="stretch",
        column_config={
            "type": st.column_config.TextColumn("Type", width="small"),
            "player": st.column_config.TextColumn("Player"),
            "tower_id": st.column_config.TextColumn("Tower ID", width="medium"),
            "covered_ids": st.column_config.NumberColumn("IDs covered", width="small"),
            "last_public_result": st.column_config.DateColumn("Last public result"),
            "days_since": st.column_config.NumberColumn("Days since", width="small"),
            "public_rows": st.column_config.NumberColumn("Public rows", width="small"),
            "source": st.column_config.TextColumn("Source", width="small"),
            "banned_since": st.column_config.DateColumn("Banned since"),
            "reason": st.column_config.TextColumn("Reason", width="large"),
        },
    )


render_active_bans()
