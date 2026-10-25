"""Player verification and account management services.

Shared backend logic for creating/updating player records and linking social accounts.
Used by both thetower-bot (Discord verification) and thetower-web (web verification).
"""

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# A Tower player ID: 12-16 characters, all hex except that the first may also be G (case-insensitive)
TOWER_ID_RE = re.compile(r"[0-9A-FG][0-9A-F]{11,15}", re.IGNORECASE)


def save_with_reason(account, reason: str) -> None:
    """Save a LinkedAccount noting how the change was made: the reason lands on its history row and on the
    account events the save emits (see sus/events.py), where the bot's link log reads it."""
    account._change_reason = reason[:100]
    account.save()


def is_valid_tower_id(text: str) -> bool:
    """Return True if text is a valid Tower player ID (12–16 hex characters, the first may also be G; case-insensitive)."""
    return bool(TOWER_ID_RE.fullmatch(text.strip()))


def create_or_update_player(
    platform: str,
    account_id: str,
    author_name: str,
    player_id: str,
    update_role_source: bool = True,
    action_type: str | None = None,
    old_player_id: str | None = None,
    reason: str = "verification",
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
    reason:
        How a new LinkedAccount came about, for its history and link events (see ``save_with_reason``).

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
                save_with_reason(
                    LinkedAccount(
                        player=player,
                        platform=platform,
                        account_id=account_id,
                        display_name=author_name,
                        verified=True,
                        verified_at=timezone.now(),
                        role_source_instance=primary_instance,
                    ),
                    f"{reason}: mod-approved relink",
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
                (int(m.group(1)) for name in existing_names for m in [re.match(r"^Instance (\d+)\b", name)] if m),
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
                (int(m.group(1)) for name in existing_names for m in [re.match(r"^Instance (\d+)\b", name)] if m),
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
        save_with_reason(
            LinkedAccount(
                player=player,
                platform=platform,
                account_id=account_id,
                display_name=author_name,
                verified=True,
                verified_at=timezone.now(),
                role_source_instance=primary_instance,
            ),
            f"{reason}: new player",
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
    how: str = "website",
    primary_display_name: str | None = None,
) -> bool:
    """Link a secondary social account to the player already linked via the primary platform.

    Called after OAuth when a user connects a second provider while already verified. The new account starts
    with the role source instance of the logged-in primary account it was linked through; when that has none,
    the player's primary game instance, else any active one. *how* (e.g. "website: Link Reddit") starts the
    reason recorded for the link, which goes on "logged in as <platform>:<account id> (<name>)". The name is
    *primary_display_name* (the name the platform gave at this login), else the one stored when the logged-in
    account was linked, so the record still says who it was if that account is later deleted.
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

    role_source_instance_id = primary_link.role_source_instance_id
    if not role_source_instance_id:
        instance = player.game_instances.filter(active=True).order_by("-primary", "id").first()
        role_source_instance_id = instance.id if instance else None

    reason = f"{how}, logged in as {primary_platform}:{primary_account_id}"
    # Parentheses would end the name early for readers of the reason (the bot's link log parses it), and the name is
    # shortened rather than losing its closing parenthesis to the reason's 100-character limit
    logged_in_name = (primary_display_name or primary_link.display_name or "").replace("(", "").replace(")", "").strip()
    logged_in_name = logged_in_name[: max(0, 100 - len(reason) - 3)]
    if logged_in_name:
        reason += f" ({logged_in_name})"

    save_with_reason(
        LinkedAccount(
            player=player,
            platform=secondary_platform,
            account_id=secondary_account_id,
            display_name=secondary_display_name,
            verified=True,
            verified_at=timezone.now(),
            role_source_instance_id=role_source_instance_id,
        ),
        reason,
    )
    logger.info(
        "Auto-linked %s %s to player pk=%d via %s (role source instance %s)",
        secondary_platform,
        secondary_account_id,
        player.pk,
        primary_platform,
        role_source_instance_id,
    )
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


def set_account_public(player, platform: str, account_id: str, public: bool, how: str) -> bool:
    """Show or hide one of the player's active linked accounts on their public profiles (see sus.social).

    *how* (e.g. "website", "bot") starts the reason recorded on the account's history. Returns True if the account
    belongs to the player and the setting changed.
    """
    from thetower.backend.sus.models import LinkedAccount

    account = LinkedAccount.objects.filter(player=player, platform=platform, account_id=str(account_id), active=True).first()
    if account is None or account.public == public:
        return False
    account.public = public
    save_with_reason(account, f"{how}: {'shown on' if public else 'hidden from'} profiles")
    return True


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
        instance = GameInstance.objects.filter(id=instance_id).first()
        if instance is None:
            return {"status": "error", "message": "Game instance not found"}
        # Names identify instances to the player and mods, so keep them unique per player
        if GameInstance.objects.filter(player_id=instance.player_id, name__iexact=new_name).exclude(id=instance_id).exists():
            return {"status": "error", "message": "Another of your game instances already has that name"}
        GameInstance.objects.filter(id=instance_id).update(name=new_name)
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
    from thetower.backend.events import emit
    from thetower.backend.sus.models import GameInstance, LinkedAccount

    try:
        instance = GameInstance.objects.filter(id=source_instance_id).first()
        if not instance:
            return {"status": "error", "message": "Game instance not found"}
        accounts = LinkedAccount.objects.filter(id__in=target_account_ids, player_id=player_pk)
        changed = list(accounts.exclude(role_source_instance=instance).values_list("platform", "account_id"))
        updated = accounts.update(role_source_instance=instance)
        # A bulk update fires no signals, so emit the events here
        for platform, account_id in changed:
            emit("account.role_source_changed", known_player=player_pk, platform=platform, account_id=account_id, instance=instance.pk)
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

        new_linked_account = LinkedAccount.objects.filter(
            platform=link_row["target_platform"], account_id=str(link_row["target_account_id"])
        ).first() or LinkedAccount(platform=link_row["target_platform"], account_id=str(link_row["target_account_id"]))
        new_linked_account.player = player
        new_linked_account.display_name = target_display_name or link_row.get("target_name", "")
        new_linked_account.verified = True
        new_linked_account.active = True
        save_with_reason(
            new_linked_account,
            f"bot: {link_row['link_type']} request from {link_row['initiator_platform']}:{link_row['initiator_account_id']} accepted",
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


def toggle_linked_account_active(linked_account_id: int, active: bool, reason: str | None = None) -> dict[str, Any]:
    """Activate or retire a linked Discord account.

    Args:
        linked_account_id: LinkedAccount primary key
        active: True to activate, False to retire
        reason: Who made the change and how (e.g. "bot: disabled by discord:123"), for the history row and link log

    Returns dict with 'status' ('ok' or 'error') and 'account_id', 'player_pk' on success.
    """
    from thetower.backend.sus.models import LinkedAccount

    try:
        account = LinkedAccount.objects.select_related("player").get(id=linked_account_id)
        account.active = active
        if reason:
            account._change_reason = reason[:100]
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
# Player merge: a person's duplicate KnownPlayer joins their original one
# ---------------------------------------------------------------------------


class _MergeAborted(Exception):
    """Raised inside the merge transaction to roll it back with an error for the caller."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def find_merge_target(platform: str, account_id: str, tower_id: str) -> int | None:
    """The KnownPlayer this account's player should merge into, or None.

    That is the player holding *tower_id* when it is not the account's own player and has no active link on any
    platform: typically its owner lost the Discord account they verified with and was verified afresh under a new
    one. Only a moderator can decide the two are the same person (see merge_players).
    """
    from thetower.backend.sus.models import LinkedAccount, PlayerId

    link = LinkedAccount.objects.filter(platform=platform, account_id=str(account_id), active=True).first()
    owner_pk = PlayerId.objects.filter(id=tower_id.strip().upper()).values_list("game_instance__player_id", flat=True).first()
    if link is None or owner_pk is None or owner_pk == link.player_id:
        return None
    if LinkedAccount.objects.filter(player_id=owner_pk, active=True).exists():
        return None
    return owner_pk


def _next_instance_number(player) -> int:
    names = player.game_instances.values_list("name", flat=True)
    return max((int(m.group(1)) for name in names if name for m in [re.match(r"^Instance (\d+)\b", name)] if m), default=0) + 1


def _merge_snapshot(player) -> dict[str, Any]:
    """Everything a merge moves off *player*, as JSON-ready data, so the merge can be audited or undone by hand."""
    from django.forms.models import model_to_dict

    from thetower.backend.tourney_results.models import NameDayWinner

    snapshot = {
        "player": model_to_dict(player),
        "instances": [
            {**model_to_dict(instance), "tower_ids": [model_to_dict(pid) for pid in instance.player_ids.all()]}
            for instance in player.game_instances.all()
        ],
        "accounts": [model_to_dict(account) for account in player.linked_accounts.all()],
        "name_day_wins": list(NameDayWinner.objects.filter(winner=player).values_list("id", flat=True)),
    }
    return json.loads(json.dumps(snapshot, default=str))


def merge_players(
    absorbed_pk: int, target_pk: int, tower_id: str, retire_moved_instances: bool, reason: str, owner_override: bool = False
) -> dict[str, Any]:
    """Merge a person's duplicate KnownPlayer into their original one, then delete the duplicate.

    A moderator has ruled that both are the same person, so one social account still means one person. The
    duplicate's game instances (with their Tower IDs and moderation records) and linked accounts move to the
    original; its name-day wins follow. Moved instances are renamed when their name is taken, and retired when
    *retire_moved_instances* (the duplicate's IDs were a typo or are being replaced). Moved accounts whose role
    source was retired get the instance holding *tower_id*. The original keeps its own name, creator code and
    Django user, taking the duplicate's only where it has none; a duplicate carrying either of the last two needs
    *owner_override*.

    Nothing is deleted but the emptied duplicate, and only once nothing points at it. Every save carries *reason*
    into its history row; the snapshot returned describes what the duplicate held.

    Returns {"status": "ok", "snapshot", "moved_instances", "moved_accounts"} or {"status": "error", "code",
    "message"}; on error nothing was changed.
    """
    from django.db import transaction
    from django.db.models import Q

    from thetower.backend.sus.models import KnownPlayer, PlayerId
    from thetower.backend.tourney_results.models import NameDayWinner

    reason = reason[:100]
    try:
        with transaction.atomic():
            absorbed = KnownPlayer.objects.filter(pk=absorbed_pk).first()
            target = KnownPlayer.objects.filter(pk=target_pk).first()
            if absorbed is None or target is None or absorbed.pk == target.pk:
                raise _MergeAborted("not_found", "Both players must exist and differ")
            target_pid = PlayerId.objects.filter(id=tower_id.strip().upper(), game_instance__player=target).select_related("game_instance").first()
            if target_pid is None:
                raise _MergeAborted("tower_id_moved", f"{tower_id} no longer belongs to player #{target.pk}")
            for field, label in (("django_user_id", "website login"), ("creator_code", "creator code")):
                theirs, ours = getattr(absorbed, field), getattr(target, field)
                if theirs and ours and theirs != ours:
                    raise _MergeAborted("conflict", f"Both players have a {label}; merge them in the Django admin")
                if theirs and not owner_override:
                    raise _MergeAborted("needs_owner", f"Player #{absorbed.pk} has a {label}; only the bot owner can merge it")

            snapshot = _merge_snapshot(absorbed)

            # Game instances, with their Tower IDs (the deprecated direct player link follows them)
            taken = {name.lower() for name in target.game_instances.values_list("name", flat=True) if name}
            moved_instances = []
            for instance in list(absorbed.game_instances.all()):
                instance.player = target
                instance.primary = False
                if retire_moved_instances:
                    instance.active = False
                if not instance.name or instance.name.lower() in taken:
                    instance.name = f"Instance {_next_instance_number(target)}"
                taken.add(instance.name.lower())
                instance._change_reason = reason
                instance.save()
                moved_instances.append(instance.pk)
            note = f"Moved from player #{absorbed.pk} to #{target.pk} ({reason})"
            for pid in PlayerId.objects.filter(Q(player=absorbed) | Q(game_instance_id__in=moved_instances)):
                if pid.player_id == absorbed.pk:
                    pid.player = target
                pid.notes = f"{pid.notes}\n{note}" if pid.notes else note
                pid._change_reason = reason
                pid.save()

            # Linked accounts: the new one the person verified with, and any retired ones
            retired = set(moved_instances) if retire_moved_instances else set()
            moved_accounts = []
            for account in list(absorbed.linked_accounts.all()):
                account.player = target
                if account.role_source_instance_id is None or account.role_source_instance_id in retired:
                    account.role_source_instance = target_pid.game_instance
                # An active account takes "primary" from a retired one; otherwise the original's primary stays
                target_primary = target.linked_accounts.filter(platform=account.platform, primary=True)
                if account.primary and (target_primary.filter(active=True).exists() or (not account.active and target_primary.exists())):
                    account.primary = False
                save_with_reason(account, reason)
                moved_accounts.append(f"{account.platform}:{account.account_id}")

            NameDayWinner.objects.filter(winner=absorbed).update(winner=target)

            # The original keeps its own details and fills gaps from the duplicate (a Django user can't be on both)
            fill = {
                field: getattr(absorbed, field)
                for field in ("name", "creator_code", "django_user")
                if getattr(absorbed, field) and not getattr(target, field)
            }
            if absorbed.django_user_id:
                absorbed.django_user = None
                absorbed._change_reason = reason
                absorbed.save()
            if fill:
                for field, value in fill.items():
                    setattr(target, field, value)
                target._change_reason = reason
                target.save()

            # Anything still pointing at the duplicate (history tables aside) would be cascade-deleted with it
            for relation in KnownPlayer._meta.related_objects:
                model = relation.related_model
                if hasattr(model, "instance_type"):  # simple_history row: keeps the old id on purpose
                    continue
                if model._base_manager.filter(**{relation.field.name: absorbed}).exists():
                    raise _MergeAborted("still_referenced", f"{model.__name__} rows still point at player #{absorbed.pk}")

            absorbed._change_reason = reason
            absorbed.delete()
    except _MergeAborted as exc:
        logger.warning("Player merge #%s -> #%s refused: %s", absorbed_pk, target_pk, exc.message)
        return {"status": "error", "code": exc.code, "message": exc.message}

    logger.info(
        "Merged player #%d into #%d: instances=%s accounts=%s retired=%s (%s)",
        absorbed_pk,
        target_pk,
        moved_instances,
        moved_accounts,
        retire_moved_instances,
        reason,
    )
    return {"status": "ok", "snapshot": snapshot, "moved_instances": moved_instances, "moved_accounts": moved_accounts}


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

    ban_ids = ModerationRecord.get_verification_ban_ids()
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


def player_ban_state(player, account_wide: bool, ban_ids: set[str] | None = None) -> dict[str, Any]:
    """Classify a player's game instances by ban status and decide whether the account is blocked.

    A game instance is one game save; its Tower IDs are the IDs that save has had, so an active ban
    on any one of them bans the whole instance. Which ID is primary does not matter, and inactive
    (retired) instances count too. Instances with no Tower IDs are neither clean nor banned. Bans flagged
    ignore_for_verification are left out.

    ``blocked`` answers "should this account be denied the verified role and verification?":

    - ``account_wide=False``: blocked only when the player has a banned instance and no clean one.
    - ``account_wide=True``: blocked when any instance is banned.

    Args:
        player: The ``KnownPlayer`` to classify.
        account_wide: The bot's ``ban_is_account_wide`` setting.
        ban_ids: Ban Tower IDs that count for verification (ModerationRecord.get_verification_ban_ids()), when the
            caller already has them (avoids a query per player in loops).

    Returns:
        dict with:
            'clean_instances': list[GameInstance] — instances with Tower IDs and no active ban.
            'banned_instances': list[GameInstance] — instances with at least one actively banned Tower ID.
            'banned_ids': list[str] — the actively banned Tower IDs across all instances.
            'blocked': bool — see above.
    """
    from thetower.backend.sus.models import ModerationRecord

    if ban_ids is None:
        ban_ids = ModerationRecord.get_verification_ban_ids()

    clean_instances = []
    banned_instances = []
    banned_ids: list[str] = []
    for instance in player.game_instances.all():
        instance_ids = [pid.id for pid in instance.player_ids.all()]
        if not instance_ids:
            continue
        instance_banned_ids = [pid for pid in instance_ids if pid in ban_ids]
        if instance_banned_ids:
            banned_instances.append(instance)
            banned_ids.extend(instance_banned_ids)
        else:
            clean_instances.append(instance)

    if account_wide:
        blocked = bool(banned_instances)
    else:
        blocked = bool(banned_instances) and not clean_instances

    return {
        "clean_instances": clean_instances,
        "banned_instances": banned_instances,
        "banned_ids": banned_ids,
        "blocked": blocked,
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
