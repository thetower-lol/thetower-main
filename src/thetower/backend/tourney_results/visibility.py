"""Moderation visibility: who is hidden where, in one file with two sections.

``<DJANGO_DATA>/visibility.json``::

    {
      "placement": {"exclude_sus": false, "exclude_shun": false},
      "public":    {"hide_sus": false,    "hide_shun": false}
    }

``placement`` decides whether sus and shunned players receive positions at import and on
recalculation. That is a property of the data, not of a viewer, so it is global; changing it
means repositioning every tournament. Hard-banned players never receive a position.

``public`` decides whether the public site and the bot hide sus and shunned players. Banned
players (hard or soft) are always hidden there. The hidden site has no section: it shows every
player and badges them.

Which profile a process uses is decided by the HIDDEN_FEATURES environment variable it already
runs with. Callers that want the public answer regardless of their own process, such as the bot,
use ``public_shows`` directly.

History: until 2026-09 visibility was two files, ``include_sus.json`` and ``include_shun.json``,
each with a default and per-page overrides. Pages never needed to differ from each other in
practice, and the per-page keys hid the fact that two of them (``create_tourney_rows`` and
``reposition``) controlled the standings rather than a page. The per-page functions in
``sus_config`` and ``shun_config`` still exist and still take a page name, but resolve through
this module and ignore the page except to recognise the two placement keys. If per-page control
is ever needed again, that is the place to reintroduce it, as an informed choice.
"""

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any, Dict

from thetower.backend.env_config import get_django_data

logger = logging.getLogger(__name__)

FILENAME = "visibility.json"
KINDS = ("sus", "shun")
# Page keys that historically controlled placement (positions) rather than display
PLACEMENT_KEYS = frozenset({"create_tourney_rows", "reposition"})

DEFAULTS: Dict[str, Dict[str, bool]] = {
    "placement": {"exclude_sus": False, "exclude_shun": False},
    "public": {"hide_sus": False, "hide_shun": False},
}

_LOCK = threading.Lock()
_CACHE: Dict[str, Any] = {"config": None, "mtime": None}


def _path() -> Path:
    return get_django_data() / FILENAME


def _normalise(payload: Any) -> Dict[str, Dict[str, bool]]:
    """Coerce whatever is on disk into the two-section shape, filling gaps from DEFAULTS."""
    config = {section: dict(values) for section, values in DEFAULTS.items()}
    if not isinstance(payload, dict):
        return config
    for section, values in DEFAULTS.items():
        found = payload.get(section)
        if isinstance(found, dict):
            for key in values:
                if key in found:
                    config[section][key] = bool(found[key])
    return config


def _legacy_flag(filename: str, page: str) -> bool | None:
    """The resolved include flag for a page from one of the pre-2026-09 files, or None if absent."""
    path = get_django_data() / filename
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf8"))
        pages = payload.get("pages", {}) if isinstance(payload, dict) else {}
        default = bool(payload.get("default", False)) if isinstance(payload, dict) else False
        return bool(pages.get(page, default)) if isinstance(pages, dict) else default
    except Exception:
        logger.exception("Could not read legacy %s; ignoring it for migration", filename)
        return None


def _migrate_from_legacy() -> Dict[str, Dict[str, bool]] | None:
    """Build the new config from include_sus.json / include_shun.json, or None if neither exists.

    Placement excludes a kind when either legacy placement key excluded it. Public hides a kind
    when the legacy default excluded it (per-page overrides are dropped by design).
    """
    legacy = {"sus": "include_sus.json", "shun": "include_shun.json"}
    if not any((get_django_data() / filename).exists() for filename in legacy.values()):
        return None
    config = {section: dict(values) for section, values in DEFAULTS.items()}
    for kind, filename in legacy.items():
        placement_flags = [_legacy_flag(filename, key) for key in sorted(PLACEMENT_KEYS)]
        placement_flags = [flag for flag in placement_flags if flag is not None]
        if placement_flags:
            config["placement"][f"exclude_{kind}"] = not all(placement_flags)
        default_flag = _legacy_flag(filename, "__default__")
        if default_flag is not None:
            config["public"][f"hide_{kind}"] = not default_flag
    return config


def _load() -> Dict[str, Dict[str, bool]]:
    path = _path()
    if path.exists():
        try:
            return _normalise(json.loads(path.read_text(encoding="utf8")))
        except Exception:
            logger.exception("Failed to read %s; using defaults", FILENAME)
            return _normalise(None)
    migrated = _migrate_from_legacy()
    if migrated is not None:
        try:
            _write(migrated)
            logger.info("Wrote %s from the legacy include_sus.json / include_shun.json files", FILENAME)
        except Exception:
            logger.exception("Could not write migrated %s; using the migrated values in memory", FILENAME)
        return migrated
    return _normalise(None)


def _write(config: Dict[str, Dict[str, bool]]) -> None:
    path = _path()
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(config, indent=2) + "\n", encoding="utf8")
    os.replace(tmp, path)


def get_visibility() -> Dict[str, Dict[str, bool]]:
    """The current config, re-read whenever the file's mtime changes (one stat per call)."""
    path = _path()
    try:
        mtime = path.stat().st_mtime_ns
    except FileNotFoundError:
        mtime = None
    with _LOCK:
        if _CACHE["config"] is None or _CACHE["mtime"] != mtime:
            _CACHE["config"] = _load()
            try:
                _CACHE["mtime"] = path.stat().st_mtime_ns
            except FileNotFoundError:
                _CACHE["mtime"] = None
        return {section: dict(values) for section, values in _CACHE["config"].items()}


def save_visibility(config: Dict[str, Dict[str, bool]]) -> Dict[str, Dict[str, bool]]:
    """Validate, write atomically and return the stored config. Readers pick it up on their next call."""
    normalised = _normalise(config)
    _write(normalised)
    with _LOCK:
        _CACHE["config"] = None
        _CACHE["mtime"] = None
    return normalised


def invalidate() -> None:
    """Drop the in-process copy so the next call re-reads the file."""
    with _LOCK:
        _CACHE["config"] = None
        _CACHE["mtime"] = None


def is_hidden_site() -> bool:
    """True in a process that shows everything (the hidden and admin sites)."""
    return bool(os.environ.get("HIDDEN_FEATURES"))


def placement_excludes(kind: str) -> bool:
    """Whether players of this kind (sus or shun) are left out of the standings."""
    return bool(get_visibility()["placement"][f"exclude_{kind}"])


def public_shows(kind: str) -> bool:
    """Whether the public profile (public site and bot) shows players of this kind."""
    return not get_visibility()["public"][f"hide_{kind}"]


def include_enabled_for(kind: str, page: str) -> bool:
    """Compatibility resolver behind include_sus_enabled_for / include_shun_enabled_for.

    The two placement keys answer from the placement section. Every other page name is ignored:
    the hidden site includes everyone, the public site follows the public section.
    """
    if kind not in KINDS:
        raise ValueError(f"unknown moderation kind {kind!r}")
    if page in PLACEMENT_KEYS:
        return not placement_excludes(kind)
    if is_hidden_site():
        return True
    return public_shows(kind)
