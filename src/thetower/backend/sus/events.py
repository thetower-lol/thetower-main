"""Emit domain events (thetower.backend.events) for players, accounts, Tower IDs and moderation records.

Model signals cover every writer: bot, web, Django admin, API, workers. ``pre_save`` remembers the row's previous
values, ``post_save`` compares them with the new ones and emits what actually changed, so a save that touches
nothing relevant emits nothing. Bulk ``.update()`` calls bypass signals and emit themselves.
"""

from django.db.models.signals import post_delete, post_save, pre_save

from thetower.backend.events import emit

# Fields whose changes produce events, per model
_TRACKED = {
    "LinkedAccount": ("player_id", "active", "role_source_instance_id", "reddit_flair", "reddit_flair_role"),
    "PlayerId": ("game_instance_id", "player_id"),
    "GameInstance": ("player_id", "active"),
    "ModerationRecord": ("resolved_at", "ignore_for_verification"),
}


def _remember_previous(sender, instance, raw=False, **kwargs) -> None:
    """Keep the stored values of the tracked fields on the instance, for post_save to compare."""
    instance._event_previous = None
    # A new row with an explicit primary key (Tower IDs) finds nothing here and counts as created
    if raw or instance.pk is None:
        return
    instance._event_previous = sender.objects.filter(pk=instance.pk).values(*_TRACKED[sender.__name__]).first()


def _changed(instance, field: str) -> bool:
    previous = getattr(instance, "_event_previous", None)
    return previous is not None and previous[field] != getattr(instance, field)


def _instance_player(instance_id):
    """The KnownPlayer that owns a game instance (None when unknown or deleted)."""
    from thetower.backend.sus.models import GameInstance

    if not instance_id:
        return None
    return GameInstance.objects.filter(id=instance_id).values_list("player_id", flat=True).first()


def _tower_id_player(tower_id):
    from thetower.backend.sus.models import PlayerId

    row = PlayerId.objects.filter(id=tower_id).values_list("game_instance__player_id", "player_id").first()
    return (row[0] or row[1]) if row else None


# === LinkedAccount ===


def _account_payload(account) -> dict:
    return {"known_player": account.player_id, "platform": str(account.platform), "account_id": account.account_id}


def _account_saved(sender, instance, created=False, raw=False, **kwargs) -> None:
    from thetower.backend.sus.models import LinkedAccount

    if raw:
        return
    payload = _account_payload(instance)
    if created:
        if instance.active:
            emit("account.linked", **payload)
        return
    previous = getattr(instance, "_event_previous", None)
    if previous is None:
        return
    if _changed(instance, "player_id"):
        if previous["active"]:
            emit("account.unlinked", **{**payload, "known_player": previous["player_id"]})
        if instance.active:
            emit("account.linked", **payload)
    elif _changed(instance, "active"):
        emit("account.linked" if instance.active else "account.unlinked", **payload)
    if _changed(instance, "role_source_instance_id"):
        emit("account.role_source_changed", **payload, instance=instance.role_source_instance_id)
    if instance.platform == LinkedAccount.Platform.REDDIT and (_changed(instance, "reddit_flair") or _changed(instance, "reddit_flair_role")):
        emit("account.reddit_flair_changed", known_player=instance.player_id, account_id=instance.account_id)


def _account_deleted(sender, instance, **kwargs) -> None:
    if instance.active:
        emit("account.unlinked", **_account_payload(instance))


# === PlayerId (Tower IDs) ===


def _tower_id_saved(sender, instance, created=False, raw=False, **kwargs) -> None:
    if raw:
        return
    payload = {
        "tower_id": instance.id,
        "instance": instance.game_instance_id,
        "known_player": _instance_player(instance.game_instance_id) or instance.player_id,
    }
    if created:
        emit("tower_id.added", **payload)
    elif _changed(instance, "game_instance_id"):
        previous = instance._event_previous
        emit(
            "tower_id.moved",
            **payload,
            previous_instance=previous["game_instance_id"],
            previous_known_player=_instance_player(previous["game_instance_id"]) or previous["player_id"],
        )


def _tower_id_deleted(sender, instance, **kwargs) -> None:
    emit(
        "tower_id.removed",
        tower_id=instance.id,
        instance=instance.game_instance_id,
        known_player=_instance_player(instance.game_instance_id) or instance.player_id,
    )


# === GameInstance ===


def _game_instance_saved(sender, instance, created=False, raw=False, **kwargs) -> None:
    if raw:
        return
    payload = {"instance": instance.pk, "known_player": instance.player_id}
    if created:
        emit("instance.created", **payload)
    elif _changed(instance, "active"):
        emit("instance.reactivated" if instance.active else "instance.retired", **payload)


def _game_instance_deleted(sender, instance, **kwargs) -> None:
    emit("instance.deleted", instance=instance.pk, known_player=instance.player_id)


# === ModerationRecord ===


def _moderation_payload(record) -> dict:
    return {
        "record": record.pk,
        "kind": str(record.moderation_type),
        "tower_id": record.tower_id,
        "known_player": _instance_player(record.game_instance_id) or _tower_id_player(record.tower_id),
    }


def _moderation_saved(sender, instance, created=False, raw=False, **kwargs) -> None:
    if raw:
        return
    if created:
        emit("moderation.created", **_moderation_payload(instance), source=str(instance.source))
        return
    if _changed(instance, "resolved_at") and instance.resolved_at is not None:
        emit("moderation.resolved", **_moderation_payload(instance))
    if _changed(instance, "ignore_for_verification"):
        emit("moderation.ignored" if instance.ignore_for_verification else "moderation.unignored", **_moderation_payload(instance))


def _moderation_deleted(sender, instance, **kwargs) -> None:
    emit("moderation.deleted", **_moderation_payload(instance))


def connect_signals() -> None:
    """Hook the handlers to the models; called from the sus app's ready()."""
    from thetower.backend.sus.models import GameInstance, LinkedAccount, ModerationRecord, PlayerId

    for model, saved, deleted in (
        (LinkedAccount, _account_saved, _account_deleted),
        (PlayerId, _tower_id_saved, _tower_id_deleted),
        (GameInstance, _game_instance_saved, _game_instance_deleted),
        (ModerationRecord, _moderation_saved, _moderation_deleted),
    ):
        uid = f"tower_events_{model.__name__}"
        pre_save.connect(_remember_previous, sender=model, dispatch_uid=f"{uid}_pre_save")
        post_save.connect(saved, sender=model, dispatch_uid=f"{uid}_post_save")
        post_delete.connect(deleted, sender=model, dispatch_uid=f"{uid}_post_delete")
