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
        For two-stage mod review (Scenario B): action to perform with the verified ID.

        - ``"replace"`` — Delete old Tower ID, create new one as primary (user lost access to old ID)
        - ``"new_instance_keep_old"`` — Create new GameInstance with new ID as primary, keep old instances active (game changed ID, preserving history)
        - ``"merge_ids"`` — Add new ID to existing primary instance as primary, demote old ID(s) to alternates (consolidate multiple IDs)
        - ``"add_alt"`` — Add new ID to existing primary instance as non-primary alternate (keep old ID as primary; same logic as merge_ids with promote_new_id=False). No UI button currently — reserved for a future mod tool.
        - ``"new_instance_retire_old"`` — Create new GameInstance and set old instances to inactive (fresh start, preserve history)
        - ``None`` — Only valid for new users (no LinkedAccount yet). For existing players, always pass an explicit action_type.

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
            # Tower ID exists but no active platform link — check if cross-platform conflict or mod-approved relink
            if action_type == "relink":
                # Mod-approved relink: create a new platform link → existing KnownPlayer.
                # The player's game data is preserved; only the Discord connection is re-established.
                player = existing_player
                primary_instance = player.game_instances.filter(primary=True, active=True).first() or player.game_instances.first()
                LinkedAccount.objects.create(
                    player=player,
                    platform=platform,
                    account_id=account_id,
                    display_name=author_name,
                    verified=True,
                    verified_at=timezone.now(),
                    role_source_instance=primary_instance,
                )
                logger.info("Relink: created %s link account_id=%s → player_pk=%d", platform, account_id, player.pk)
                # Auto-link any orphaned ModerationRecords to the primary instance
                if primary_instance:
                    linked_count = ModerationRecord.objects.filter(tower_id=player_id, game_instance__isnull=True).update(
                        game_instance=primary_instance
                    )
                    if linked_count:
                        logger.info("Relink: auto-linked %d moderation record(s) for %s", linked_count, player_id)
                primary_pid = PlayerId.objects.filter(game_instance=primary_instance, primary=True).first() if primary_instance else None
                return {
                    "player_pk": player.pk,
                    "player_name": player.name,
                    "platform": platform,
                    "account_id": account_id,
                    "created": True,
                    "primary_tower_id": primary_pid.id if primary_pid else player_id,
                }
            # Not a relink — check for cross-platform conflict
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
            # Replace: Add new ID to the existing primary GameInstance, mark it primary, delete old ID.
            existing_primary = player.game_instances.filter(primary=True).first() or player.game_instances.first()
            if existing_primary is None:
                # Fallback: no instance found; create one (shouldn't happen for a verified user)
                existing_primary = GameInstance.objects.create(player=player, name="Instance 1", primary=True)

            # Delete the old PlayerId record(s)
            if old_player_id:
                old_pids = PlayerId.objects.filter(id__iexact=old_player_id)
                deleted_count = old_pids.delete()[0]
                logger.info("Replaced old Tower ID %s (deleted %d PlayerId record(s))", old_player_id, deleted_count)

            # Demote all remaining IDs on this instance to non-primary, then add new ID as primary
            existing_primary.player_ids.update(primary=False)
            PlayerId.objects.create(id=player_id, game_instance=existing_primary, primary=True)
            primary_instance = existing_primary
            if update_role_source:
                linked_account.role_source_instance = primary_instance
                linked_account.save()
            logger.info("Replaced ID on existing instance %s: new primary ID %s", existing_primary.name, player_id)

        elif action_type == "new_instance_keep_old":
            # New instance, keep old: Create new GameInstance with new ID as primary, keep old instances active
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

        elif action_type in ("merge_ids", "add_alt"):
            # Shared logic: add new ID to the existing primary game instance.
            # merge_ids: promote new ID to primary, demote all existing IDs (UI button: Merge IDs).
            # add_alt:   keep existing primary unchanged, add new ID as non-primary
            #            (no UI button currently — reserved for a future mod tool that adds an
            #            old/alternate ID directly to a player's existing game instance).
            promote_new_id = action_type == "merge_ids"
            existing_primary = player.game_instances.filter(primary=True).first()
            if not existing_primary:
                logger.warning("%s: No primary instance found for player %d, creating one", action_type, player.pk)
                existing_primary = player.game_instances.first()
                if existing_primary:
                    existing_primary.primary = True
                    existing_primary.save()

            if existing_primary:
                if promote_new_id:
                    existing_primary.player_ids.update(primary=False)
                PlayerId.objects.create(id=player_id, game_instance=existing_primary, primary=promote_new_id)
                primary_instance = existing_primary
                if promote_new_id:
                    logger.info("Merged IDs: new primary ID %s in instance %s, old IDs demoted", player_id, existing_primary.name)
                else:
                    logger.info("Added alt ID %s to instance %s (non-primary)", player_id, existing_primary.name)
            else:
                # Shouldn't happen, but handle gracefully: create new instance
                primary_instance = GameInstance.objects.create(player=player, name="Instance 1", primary=True)
                PlayerId.objects.create(id=player_id, game_instance=primary_instance, primary=True)

        elif action_type == "new_instance_retire_old":
            # Create new instance and retire old: Like merge but sets old instances to inactive
            # Retire all existing instances
            player.game_instances.update(primary=False, active=False)
            # Create new primary instance
            existing_names = player.game_instances.values_list("name", flat=True)
            max_num = max(
                (int(m.group(1)) for name in existing_names for m in [re.match(r"^Instance (\d+)$", name)] if m),
                default=0,
            )
            primary_instance = GameInstance.objects.create(
                player=player,
                name=f"Instance {max_num + 1}",
                primary=True,
                active=True,
            )
            PlayerId.objects.create(id=player_id, game_instance=primary_instance, primary=True)
            if update_role_source:
                linked_account.role_source_instance = primary_instance
                linked_account.save()
            logger.info("Created new instance %s and retired old instances for player %d", primary_instance.name, player.pk)

        else:
            # Unknown action_type for an existing player — this should not happen.
            logger.error(
                "create_or_update_player: unexpected action_type=%r for existing player %d (platform=%s account=%s)",
                action_type,
                player.pk,
                platform,
                account_id,
            )
            raise ValueError(f"Unknown action_type for existing player: {action_type!r}")
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


# ---------------------------------------------------------------------------
# Account management service functions
# Used by both Discord bot UI and web UI — no direct ORM writes in the UI layer.
# ---------------------------------------------------------------------------


def set_primary_game_instance(player_pk: int, instance_id: int) -> dict[str, Any]:
    """Set a game instance as the player's primary instance.

    Returns dict with 'status' ('ok' or 'error') and optional 'message'.
    """
    from thetower.backend.sus.models import GameInstance, KnownPlayer

    try:
        player = KnownPlayer.objects.get(id=player_pk)
        instance = GameInstance.objects.filter(id=instance_id, player=player).first()
        if not instance:
            return {"status": "error", "message": "Game instance not found for this player"}
        GameInstance.objects.filter(player=player).update(primary=False)
        instance.primary = True
        instance.save(update_fields=["primary"])
        logger.info("Set primary game instance: player=%d instance=%d", player_pk, instance_id)
        return {"status": "ok"}
    except KnownPlayer.DoesNotExist:
        return {"status": "error", "message": "Player not found"}
    except Exception as exc:
        logger.exception("set_primary_game_instance failed: player=%d instance=%d", player_pk, instance_id)
        return {"status": "error", "message": str(exc)}


def toggle_game_instance_active(instance_id: int, active: bool) -> dict[str, Any]:
    """Activate or deactivate a game instance.

    Returns dict with 'status' ('ok' or 'error') and optional 'message'.
    """
    from thetower.backend.sus.models import GameInstance

    try:
        updated = GameInstance.objects.filter(id=instance_id).update(active=active)
        if not updated:
            return {"status": "error", "message": "Game instance not found"}
        logger.info("Toggled game instance active: instance=%d active=%s", instance_id, active)
        return {"status": "ok", "active": active}
    except Exception as exc:
        logger.exception("toggle_game_instance_active failed: instance=%d", instance_id)
        return {"status": "error", "message": str(exc)}


def rename_game_instance(instance_id: int, new_name: str) -> dict[str, Any]:
    """Rename a game instance.

    Returns dict with 'status' ('ok' or 'error') and optional 'message'.
    """
    from thetower.backend.sus.models import GameInstance

    new_name = new_name.strip()
    if not new_name:
        return {"status": "error", "message": "Name cannot be empty"}
    if len(new_name) > 50:
        return {"status": "error", "message": "Name too long (max 50 characters)"}

    try:
        updated = GameInstance.objects.filter(id=instance_id).update(name=new_name)
        if not updated:
            return {"status": "error", "message": "Game instance not found"}
        logger.info("Renamed game instance: instance=%d name=%r", instance_id, new_name)
        return {"status": "ok", "name": new_name}
    except Exception as exc:
        logger.exception("rename_game_instance failed: instance=%d", instance_id)
        return {"status": "error", "message": str(exc)}


def set_role_source(linked_account_id: int, instance_id: int | None) -> dict[str, Any]:
    """Set which game instance provides tournament roles for a linked Discord account.

    Args:
        linked_account_id: LinkedAccount primary key
        instance_id: GameInstance primary key, or None to clear the role source

    Returns dict with 'status' ('ok' or 'error') and optional 'message'.
    """
    from thetower.backend.sus.models import GameInstance, LinkedAccount

    try:
        account = LinkedAccount.objects.get(id=linked_account_id)
        if instance_id is not None:
            instance = GameInstance.objects.filter(id=instance_id).first()
            if not instance:
                return {"status": "error", "message": "Game instance not found"}
            account.role_source_instance = instance
        else:
            account.role_source_instance = None
        account.save(update_fields=["role_source_instance"])
        logger.info("Set role source: linked_account=%d instance=%s", linked_account_id, instance_id)
        return {"status": "ok"}
    except LinkedAccount.DoesNotExist:
        return {"status": "error", "message": "Linked account not found"}
    except Exception as exc:
        logger.exception("set_role_source failed: linked_account=%d", linked_account_id)
        return {"status": "error", "message": str(exc)}


def copy_role_source_to_accounts(player_pk: int, source_instance_id: int, target_account_ids: list[int]) -> dict[str, Any]:
    """Copy a role source assignment from one game instance to multiple linked accounts.

    Args:
        player_pk: KnownPlayer primary key
        source_instance_id: GameInstance pk to set as role source
        target_account_ids: List of LinkedAccount pks to update

    Returns dict with 'status' ('ok' or 'error'), 'updated' count.
    """
    from thetower.backend.sus.models import GameInstance, LinkedAccount

    try:
        instance = GameInstance.objects.filter(id=source_instance_id).first()
        if not instance:
            return {"status": "error", "message": "Game instance not found"}
        updated = LinkedAccount.objects.filter(id__in=target_account_ids, player_id=player_pk).update(role_source_instance=instance)
        logger.info(
            "Copied role source: player=%d instance=%d updated_accounts=%d",
            player_pk,
            source_instance_id,
            updated,
        )
        return {"status": "ok", "updated": updated}
    except Exception as exc:
        logger.exception("copy_role_source_to_accounts failed: player=%d", player_pk)
        return {"status": "error", "message": str(exc)}


def accept_account_link(link_stem: str, target_display_name: str = "") -> dict[str, Any]:
    """Accept an account link request: create the Django LinkedAccount and resolve the DB record.

    Args:
        link_stem: The account_links table stem for the pending request
        target_display_name: Display name for the new LinkedAccount

    Returns dict with 'status' ('ok' or 'error'), plus on success:
        'player_name', 'linked_account_id', 'primary_player_id'
    """
    from thetower.backend.sus import verification
    from thetower.backend.sus.models import KnownPlayer, LinkedAccount, PlayerId

    try:
        link_row = verification.get_account_link(link_stem)
        if not link_row or link_row.get("status") != "pending":
            return {"status": "error", "message": "Link request no longer valid or not found"}

        player = KnownPlayer.objects.get(id=link_row["player_pk"])

        new_linked_account, _ = LinkedAccount.objects.update_or_create(
            platform=link_row["target_platform"],
            account_id=str(link_row["target_account_id"]),
            defaults={
                "player": player,
                "display_name": target_display_name or link_row.get("target_name", ""),
                "verified": True,
                "active": True,
            },
        )

        verification.resolve_account_link(link_stem, "completed", resolved_by=f"{link_row['target_platform']}:{link_row['target_account_id']}")

        # Determine if role source should be copied from an existing account
        should_ask_role_source = False
        source_instance_id = None
        source_player_id = None
        if not new_linked_account.role_source_instance_id:
            other_accounts = (
                LinkedAccount.objects.filter(player=player, platform=link_row["target_platform"])
                .exclude(id=new_linked_account.id)
                .select_related("role_source_instance")
            )
            for other in other_accounts:
                if other.role_source_instance_id:
                    should_ask_role_source = True
                    source_instance_id = other.role_source_instance_id
                    primary_pid = PlayerId.objects.filter(game_instance_id=other.role_source_instance_id, primary=True).first()
                    source_player_id = primary_pid.id if primary_pid else None
                    break

        # Primary player ID for event dispatching
        primary_player_id = None
        primary_instance = player.game_instances.filter(primary=True).first()
        if primary_instance:
            primary_pid = PlayerId.objects.filter(game_instance=primary_instance, primary=True).first()
            primary_player_id = primary_pid.id if primary_pid else None

        logger.info(
            "Account link accepted: stem=%s player=%d linked_account=%d",
            link_stem,
            player.pk,
            new_linked_account.pk,
        )
        return {
            "status": "ok",
            "player_name": player.name,
            "player_pk": player.pk,
            "linked_account_id": new_linked_account.id,
            "should_ask_role_source": should_ask_role_source,
            "source_instance_id": source_instance_id,
            "source_player_id": source_player_id,
            "primary_player_id": primary_player_id,
            "target_account_id": link_row["target_account_id"],
        }
    except KnownPlayer.DoesNotExist:
        return {"status": "error", "message": "Player not found"}
    except Exception as exc:
        logger.exception("accept_account_link failed: stem=%s", link_stem)
        return {"status": "error", "message": str(exc)}


def remove_account_link(linked_account_id: int) -> dict[str, Any]:
    """Permanently remove a linked Discord account.

    Args:
        linked_account_id: LinkedAccount primary key to delete

    Returns dict with 'status' ('ok' or 'error'), plus on success:
        'account_id', 'player_pk'
    """
    from thetower.backend.sus.models import LinkedAccount

    try:
        account = LinkedAccount.objects.select_related("player").get(id=linked_account_id)
        account_id = account.account_id
        player_pk = account.player_id
        account.delete()
        logger.info(
            "Removed account link: linked_account=%d account_id=%s player=%d",
            linked_account_id,
            account_id,
            player_pk,
        )
        return {"status": "ok", "account_id": account_id, "player_pk": player_pk}
    except LinkedAccount.DoesNotExist:
        return {"status": "error", "message": "Linked account not found"}
    except Exception as exc:
        logger.exception("remove_account_link failed: linked_account=%d", linked_account_id)
        return {"status": "error", "message": str(exc)}


def toggle_linked_account_active(linked_account_id: int, active: bool) -> dict[str, Any]:
    """Activate or retire a linked Discord account.

    Args:
        linked_account_id: LinkedAccount primary key
        active: True to activate, False to retire

    Returns dict with 'status' ('ok' or 'error') and 'account_id', 'player_pk' on success.
    """
    from thetower.backend.sus.models import LinkedAccount

    try:
        account = LinkedAccount.objects.select_related("player").get(id=linked_account_id)
        account.active = active
        account.save(update_fields=["active"])
        logger.info(
            "Toggled linked account active: linked_account=%d account_id=%s active=%s",
            linked_account_id,
            account.account_id,
            active,
        )
        return {"status": "ok", "account_id": account.account_id, "player_pk": account.player_id}
    except LinkedAccount.DoesNotExist:
        return {"status": "error", "message": "Linked account not found"}
    except Exception as exc:
        logger.exception("toggle_linked_account_active failed: linked_account=%d", linked_account_id)
        return {"status": "error", "message": str(exc)}


# ---------------------------------------------------------------------------
# Query helpers for UI layers — replaces direct ORM access in UI code.
# ---------------------------------------------------------------------------


def check_ban_status(platform: str, account_id: str, new_player_id: str) -> dict[str, Any]:
    """Check ban status for a platform account submitting a new Tower ID.

    Performs two checks in a single DB round-trip:
      1. Whether the submitted Tower ID itself is actively banned.
      2. Whether the submitting account has other Tower IDs that are banned
         (used for account-wide ban enforcement).

    Args:
        platform: Platform identifier (e.g. "discord").
        account_id: Platform account identifier.
        new_player_id: The Tower ID the user is trying to verify with.

    Returns:
        dict with:
            'new_id_banned': bool — whether new_player_id is actively banned.
            'previously_banned_ids': list[str] — other Tower IDs on this account that are banned.
    """
    from thetower.backend.sus.models import LinkedAccount, ModerationRecord

    ban_ids = ModerationRecord.get_active_moderation_ids("ban")
    new_id_banned = new_player_id in ban_ids

    previously_banned_ids: list[str] = []
    try:
        link = LinkedAccount.objects.filter(platform=platform, account_id=str(account_id), active=True).select_related("player").first()
        if link:
            all_ids = [
                pid.id
                for instance in link.player.game_instances.prefetch_related("player_ids").all()
                for pid in instance.player_ids.all()
                if pid.id != new_player_id
            ]
            previously_banned_ids = [pid for pid in all_ids if pid in ban_ids]
    except Exception:
        logger.exception("check_ban_status: failed to retrieve previously banned IDs for %s:%s", platform, account_id)

    return {
        "new_id_banned": new_id_banned,
        "previously_banned_ids": previously_banned_ids,
    }


def get_instance_primary_id(instance_id: int) -> str | None:
    """Return the primary Tower ID for a game instance, or None if not found.

    Args:
        instance_id: GameInstance primary key.

    Returns:
        The primary Tower ID string, or None if the instance or its primary ID does not exist.
    """
    from thetower.backend.sus.models import GameInstance

    instance = GameInstance.objects.filter(id=instance_id).first()
    if not instance:
        return None
    primary = instance.player_ids.filter(primary=True).first()
    return primary.id if primary else None


def get_player_linked_accounts(player_pk: int, platform: str | None = None) -> list[dict[str, Any]]:
    """Return linked accounts for a KnownPlayer, optionally filtered by platform.

    Each entry includes basic account fields plus the role source instance name and
    primary Tower ID (if a role source instance is set), matching the shape used by
    the Discord bot's account management UI.

    Args:
        player_pk: KnownPlayer primary key.
        platform: If given, filter to this platform only (e.g. "discord").
            If None, returns accounts for all platforms.

    Returns:
        List of dicts with keys: id, account_id, platform, display_name, verified,
        primary, active, verified_at, instance (dict with name/primary_id or None).
        Returns an empty list if the player is not found.
    """
    from thetower.backend.sus.models import KnownPlayer, LinkedAccount

    player = KnownPlayer.objects.filter(id=player_pk).first()
    if not player:
        return []

    qs = LinkedAccount.objects.filter(player=player).select_related("role_source_instance").order_by("-verified", "-primary", "account_id")
    if platform is not None:
        qs = qs.filter(platform=platform)

    accounts = []
    for acc in qs:
        instance_info = None
        if acc.role_source_instance:
            primary_pid = acc.role_source_instance.player_ids.filter(primary=True).first()
            instance_info = {
                "name": acc.role_source_instance.name,
                "primary_id": primary_pid.id if primary_pid else None,
            }
        accounts.append(
            {
                "id": acc.id,
                "account_id": acc.account_id,
                "platform": acc.platform,
                "display_name": acc.display_name,
                "verified": acc.verified,
                "primary": acc.primary,
                "active": acc.active,
                "verified_at": acc.verified_at,
                "instance": instance_info,
            }
        )
    return accounts
