"""Player verification and account management services.

Shared backend logic for creating/updating player records and linking social accounts.
Used by both thetower-bot (Discord verification) and thetower-web (web verification).
"""

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

_HEX_DIGITS = frozenset("0123456789abcdef")
_TOWER_ID_MIN_LEN = 13
_TOWER_ID_MAX_LEN = 16


def is_valid_tower_id(text: str) -> bool:
    """Return True if text is a valid Tower player ID (13–16 hex characters, case-insensitive)."""
    stripped = text.strip().lower()
    return _TOWER_ID_MIN_LEN <= len(stripped) <= _TOWER_ID_MAX_LEN and all(c in _HEX_DIGITS for c in stripped)


def create_or_update_player(
    platform: str,
    account_id: str,
    author_name: str,
    player_id: str,
    update_role_source: bool = True,
    action_type: str | None = None,
    old_player_id: str | None = None,
) -> dict[str, Any]:
    """Create or update a player record for a verified social account + Tower ID pair.

    Parameters
    ----------
    platform:
        A ``LinkedAccount.Platform`` value (e.g. ``"discord"``, ``"reddit"``).
    account_id:
        The platform-specific account ID of the authenticating user.
    author_name:
        Display name to use when creating a new ``KnownPlayer``.
    player_id:
        The Tower game ID submitted by the user (will be uppercased).
    update_role_source:
        If True, update the LinkedAccount's role_source_instance to the newly created/updated instance.
    action_type:
        For two-stage mod review (Scenario B): "replace", "merge", or "add_alt".
        If None (default), behaves like legacy/Scenario A (auto-creates new primary instance).
    old_player_id:
        For two-stage mod review (Scenario B): The previously verified Tower ID being replaced/updated.
        Required when action_type is not None.

    Returns
    -------
    dict
        On error: ``{"error": <code>, ...}`` where code is one of:

        - ``"already_linked"`` — Tower ID is claimed by a different active account on this platform.
        - ``"no_platform_link"`` — Tower ID exists but has no active link for this platform.
        - ``"verify_on_web"`` — Tower ID exists but linked to different platform (bot should redirect to web).

        On success: player info dict with no ``"error"`` key.
    """
    from django.utils import timezone

    from thetower.backend.sus.models import GameInstance, KnownPlayer, LinkedAccount, ModerationRecord, PlayerId

    account_id = str(account_id)
    player_id = player_id.strip().upper()

    # Check if this Tower ID is already registered.
    existing_pid = PlayerId.objects.filter(id=player_id).select_related("game_instance__player").first()
    if existing_pid and existing_pid.game_instance:
        existing_player = existing_pid.game_instance.player
        existing_link = LinkedAccount.objects.filter(player=existing_player, platform=platform, active=True).first()
        if existing_link and existing_link.account_id == account_id:
            # Same account resubmitting the same Tower ID — idempotent success.
            logger.info("Tower ID %s already linked to this account (%s %s), skipping duplicate creation", player_id, platform, account_id)
            return {
                "player_pk": existing_player.pk,
                "player_name": existing_player.name,
                "platform": platform,
                "account_id": account_id,
                "created": False,
                "primary_tower_id": player_id,
            }
        if existing_link and existing_link.account_id != account_id:
            return {
                "error": "already_linked",
                "existing_account_id": existing_link.account_id,
                "existing_player_name": existing_player.name,
            }
        if not existing_link:
            # Tower ID exists but no platform link — check if this is cross-platform conflict
            other_platform_link = LinkedAccount.objects.filter(player=existing_player, active=True).first()
            if other_platform_link and other_platform_link.platform != platform:
                # e.g., verified on Reddit, now trying via Discord
                return {"error": "verify_on_web", "existing_platform": other_platform_link.platform}
            return {"error": "no_platform_link", "existing_player_pk": existing_player.pk}

    # Find the existing KnownPlayer by platform account, if any.
    linked_account = LinkedAccount.objects.filter(platform=platform, account_id=account_id, active=True).select_related("player").first()

    if linked_account:
        # Existing player verifying with a new Tower ID — handle based on action_type.
        player = linked_account.player
        created = False

        if action_type == "replace":
            # Replace: Delete old Tower ID(s), create new one as primary
            if old_player_id:
                old_pids = PlayerId.objects.filter(id__iexact=old_player_id)
                deleted_count = old_pids.delete()[0]
                logger.info("Replaced old Tower ID %s (deleted %d PlayerId record(s))", old_player_id, deleted_count)

            # Mark all instances as non-primary, then create new primary
            player.game_instances.update(primary=False)
            existing_names = player.game_instances.values_list("name", flat=True)
            max_num = max(
                (int(m.group(1)) for name in existing_names for m in [re.match(r"^Instance (\d+)$", name)] if m),
                default=0,
            )
            primary_instance = GameInstance.objects.create(
                player=player,
                name=f"Instance {max_num + 1}",
                primary=True,
            )
            PlayerId.objects.create(id=player_id, game_instance=primary_instance, primary=True)
            if update_role_source:
                linked_account.role_source_instance = primary_instance
                linked_account.save()

        elif action_type == "merge":
            # Merge: Keep both old and new, set new as primary
            # Mark all instances as non-primary, then create new primary
            player.game_instances.update(primary=False)
            existing_names = player.game_instances.values_list("name", flat=True)
            max_num = max(
                (int(m.group(1)) for name in existing_names for m in [re.match(r"^Instance (\d+)$", name)] if m),
                default=0,
            )
            primary_instance = GameInstance.objects.create(
                player=player,
                name=f"Instance {max_num + 1}",
                primary=True,
            )
            PlayerId.objects.create(id=player_id, game_instance=primary_instance, primary=True)
            if update_role_source:
                linked_account.role_source_instance = primary_instance
                linked_account.save()

        elif action_type == "add_alt":
            # Add Alt: Keep existing primary unchanged, add new ID as alternate (non-primary)
            existing_primary = player.game_instances.filter(primary=True).first()
            if not existing_primary:
                # No primary instance — fall back to creating one
                logger.warning("add_alt: No primary instance found for player %d, creating one", player.pk)
                existing_primary = player.game_instances.first()
                if existing_primary:
                    existing_primary.primary = True
                    existing_primary.save()

            if existing_primary:
                # Add new PlayerId to the existing primary instance as non-primary
                PlayerId.objects.create(id=player_id, game_instance=existing_primary, primary=False)
                primary_instance = existing_primary
            else:
                # Shouldn't happen, but handle gracefully: create new instance
                primary_instance = GameInstance.objects.create(player=player, name="Instance 1", primary=True)
                PlayerId.objects.create(id=player_id, game_instance=primary_instance, primary=True)

        else:
            # Default/legacy behavior (Scenario A or NULL action_type): Create new primary instance
            player.game_instances.update(primary=False)
            existing_names = player.game_instances.values_list("name", flat=True)
            max_num = max(
                (int(m.group(1)) for name in existing_names for m in [re.match(r"^Instance (\d+)$", name)] if m),
                default=0,
            )
            primary_instance = GameInstance.objects.create(
                player=player,
                name=f"Instance {max_num + 1}",
                primary=True,
            )
            PlayerId.objects.create(id=player_id, game_instance=primary_instance, primary=True)
            if update_role_source:
                linked_account.role_source_instance = primary_instance
                linked_account.save()
    else:
        # Brand-new player.
        player = KnownPlayer.objects.create(name=author_name)
        created = True
        primary_instance = GameInstance.objects.create(player=player, name="Instance 1", primary=True)
        LinkedAccount.objects.create(
            player=player,
            platform=platform,
            account_id=account_id,
            display_name=author_name,
            verified=True,
            verified_at=timezone.now(),
            role_source_instance=primary_instance,
        )
        PlayerId.objects.create(id=player_id, game_instance=primary_instance, primary=True)

    # Auto-link any orphaned ModerationRecords that reference this Tower ID.
    linked_count = ModerationRecord.objects.filter(tower_id=player_id, game_instance__isnull=True).update(game_instance=primary_instance)
    if linked_count > 0:
        logger.info("Auto-linked %d moderation record(s) to new GameInstance for Tower ID %s", linked_count, player_id)

    return {
        "player_pk": player.pk,
        "player_name": player.name,
        "platform": platform,
        "account_id": account_id,
        "created": created,
        "primary_tower_id": player_id,
    }


def auto_link_secondary_provider(
    primary_platform: str,
    primary_account_id: str,
    secondary_platform: str,
    secondary_account_id: str,
    secondary_display_name: str,
) -> bool:
    """Link a secondary social account to the player already linked via the primary platform.

    Called after OAuth when a user connects a second provider while already verified.
    Safe to call even if the link already exists — returns False in that case.

    Returns True if a new LinkedAccount was created, False if not needed.
    """
    from django.utils import timezone

    from thetower.backend.sus.models import LinkedAccount

    primary_link = (
        LinkedAccount.objects.filter(platform=primary_platform, account_id=primary_account_id, active=True).select_related("player").first()
    )
    if not primary_link:
        return False

    player = primary_link.player
    existing = LinkedAccount.objects.filter(platform=secondary_platform, account_id=secondary_account_id, active=True).first()
    if existing:
        return False  # Already linked somewhere, leave it alone

    LinkedAccount.objects.create(
        player=player,
        platform=secondary_platform,
        account_id=secondary_account_id,
        display_name=secondary_display_name,
        verified=True,
        verified_at=timezone.now(),
    )
    logger.info("Auto-linked %s %s to player pk=%d via %s", secondary_platform, secondary_account_id, player.pk, primary_platform)
    return True


def get_linked_tower_ids(platform: str, account_id: str) -> list[str]:
    """Return all Tower player IDs associated with the given social account.

    Returns an empty list if the account is not yet linked to any Tower ID.
    """
    from thetower.backend.sus.models import LinkedAccount

    link = LinkedAccount.objects.filter(platform=platform, account_id=str(account_id), active=True).select_related("player").first()
    if not link:
        return []

    return list(
        link.player.game_instances.prefetch_related("player_ids")
        .values_list("player_ids__id", flat=True)
        .filter(player_ids__id__isnull=False)
        .distinct()
    )
