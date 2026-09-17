"""Automatic bans for the developers' test bracket.

The game places developer test accounts in a bracket named ``DEVDEVDEVDEV``. Those rows arrive
with every live leaderboard fetch and would otherwise show up in live views and, once imported,
in the results. When the ``automation.ban_dev_bracket`` toggle in ``visibility.json`` is on,
``ban_dev_bracket_players`` hard-bans each such player id that is not already banned. The rows
themselves stay in the data; a hard ban hides the player everywhere public and keeps them out of
the standings.
"""

import logging

import pandas as pd

from thetower.backend.tourney_results import visibility

logger = logging.getLogger(__name__)

DEV_BRACKET = "DEVDEVDEVDEV"
BAN_REASON = "dev"


def dev_bracket_player_ids(df: pd.DataFrame) -> list[str]:
    """Player ids in the dev bracket of a parsed leaderboard frame (``player_id`` and ``bracket`` columns)."""
    if df.empty or "bracket" not in df.columns:
        return []
    mask = df["bracket"].astype(str).str.strip().str.upper() == DEV_BRACKET
    return sorted(df.loc[mask, "player_id"].astype(str).str.upper().unique())


def ban_dev_bracket_players(df: pd.DataFrame, league: str) -> list[str]:
    """Hard-ban every dev-bracket player in ``df`` that has no active ban. Returns the ids banned.

    Does nothing unless the ``automation.ban_dev_bracket`` toggle is on. Needs Django set up.
    """
    if not visibility.auto_ban_dev_bracket():
        return []
    player_ids = dev_bracket_player_ids(df)
    if not player_ids:
        return []

    from thetower.backend.sus.models import ModerationRecord

    already_banned = set(
        ModerationRecord.objects.filter(
            tower_id__in=player_ids, moderation_type=ModerationRecord.ModerationType.BAN, resolved_at__isnull=True
        ).values_list("tower_id", flat=True)
    )
    banned: list[str] = []
    for player_id in player_ids:
        if player_id in already_banned:
            continue
        record = ModerationRecord.objects.create(
            tower_id=player_id,
            moderation_type=ModerationRecord.ModerationType.BAN,
            source=ModerationRecord.ModerationSource.AUTOMATED,
            game_instance=ModerationRecord._auto_link_game_instance(player_id),
            reason=BAN_REASON,
            needs_zendesk_ticket=False,
        )
        record._queue_recalculation()
        banned.append(player_id)
        logger.info(f"Banned {player_id} found in the {DEV_BRACKET} bracket of {league} (record {record.id})")
    return banned
