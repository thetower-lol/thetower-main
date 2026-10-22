"""Domain events: named notices that something happened, for any code that wants to react.

Writers call ``emit("moderation.created", record=..., kind="ban", ...)``. Delivery happens only once the current
transaction commits (a rolled-back change emits nothing):

1. to every in-process subscriber (``subscribe``), in the committing thread;
2. to the Discord bot: directly when this process *is* the bot (``set_bot_dispatcher``), otherwise over the bot's
   socket from one background sender thread that batches whatever is queued.

Socket delivery is best effort: events are dropped, with a warning, when the bot is down or restarting. Payloads
carry ids only; listeners read current state from the database when they act.

Event names come from ``CATALOGUE``. In the bot they arrive as ``tower_<name with dots as underscores>``, e.g. a
cog method ``on_tower_moderation_created(self, payload)``.
"""

import asyncio
import logging
import queue
import threading
from collections.abc import Callable
from typing import Any

from django.db import transaction

logger = logging.getLogger(__name__)

CATALOGUE = frozenset(
    {
        # record, kind, tower_id, known_player, source
        "moderation.created",
        # record, kind, tower_id, known_player
        "moderation.resolved",
        "moderation.ignored",
        "moderation.unignored",
        "moderation.deleted",
        # known_player, platform, account_id
        "account.linked",
        "account.unlinked",
        # known_player, platform, account_id, instance
        "account.role_source_changed",
        # known_player, account_id
        "account.reddit_flair_changed",
        # tower_id, instance, known_player (moved also: previous_instance, previous_known_player)
        "tower_id.added",
        "tower_id.moved",
        "tower_id.removed",
        # instance, known_player
        "instance.created",
        "instance.retired",
        "instance.reactivated",
        "instance.deleted",
        # stem, actor: a verification submission changed; its Discord messages need refreshing
        "verification.updated",
    }
)

Subscriber = Callable[[str, dict[str, Any]], None]

_subscribers: list[Subscriber] = []
_bot_dispatcher: Subscriber | None = None
_outbox: "queue.Queue[dict[str, Any]]" = queue.Queue()
_sender: threading.Thread | None = None
_sender_lock = threading.Lock()

# Most events a single socket request carries; a burst is sent in several requests
SOCKET_BATCH = 100


def subscribe(callback: Subscriber) -> None:
    """Call *callback(name, payload)* for every event this process emits, after its transaction commits."""
    if callback not in _subscribers:
        _subscribers.append(callback)


def unsubscribe(callback: Subscriber) -> None:
    if callback in _subscribers:
        _subscribers.remove(callback)


def set_bot_dispatcher(callback: Subscriber | None) -> None:
    """In the bot process: hand events to *callback* instead of sending them to the bot's own socket."""
    global _bot_dispatcher
    _bot_dispatcher = callback


def emit(name: str, **payload: Any) -> None:
    """Announce *name* with *payload* (ids, kinds, platforms) once the current transaction commits."""
    if name not in CATALOGUE:
        raise ValueError(f"Unknown event {name!r}; add it to thetower.backend.events.CATALOGUE")
    transaction.on_commit(lambda: _deliver(name, payload))


def _deliver(name: str, payload: dict[str, Any]) -> None:
    for callback in list(_subscribers):
        try:
            callback(name, payload)
        except Exception:
            logger.warning("Event subscriber %r failed on %s", callback, name, exc_info=True)
    if _bot_dispatcher is not None:
        try:
            _bot_dispatcher(name, payload)
        except Exception:
            logger.warning("Bot event dispatcher failed on %s", name, exc_info=True)
        return
    _outbox.put({"name": name, "payload": payload})
    _ensure_sender()


def _ensure_sender() -> None:
    global _sender
    with _sender_lock:
        if _sender is None or not _sender.is_alive():
            _sender = threading.Thread(target=_send_loop, name="tower-events", daemon=True)
            _sender.start()


def _send_loop() -> None:
    while True:
        batch = [_outbox.get()]
        while len(batch) < SOCKET_BATCH:
            try:
                batch.append(_outbox.get_nowait())
            except queue.Empty:
                break
        _send(batch)


def _send(batch: list[dict[str, Any]]) -> None:
    from thetower.backend.sus.bot_socket_client import DISCORD_BOT_SOCKET_TOKEN, _send_socket_request

    request: dict[str, Any] = {"action": "events", "events": batch}
    if DISCORD_BOT_SOCKET_TOKEN:
        request["token"] = DISCORD_BOT_SOCKET_TOKEN
    names = sorted({event["name"] for event in batch})
    try:
        response = asyncio.run(_send_socket_request(request, timeout=3.0))
        if not response or response.get("status") != "ok":
            logger.warning("Bot did not take %d event(s) %s: %s", len(batch), names, response)
    except Exception as exc:
        logger.warning("Could not send %d event(s) %s to the bot: %s", len(batch), names, exc)
