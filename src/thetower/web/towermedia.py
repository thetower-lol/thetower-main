"""Relic and tower-skin (avatar) icons from the private TTGtowermedia checkout.

The checkout's ``media/`` folder holds the images under random names and is the only part linked into
Streamlit's static dir (``static/towermedia``, see setup_streamlit).  ``media_lookup.json`` maps IDs to
those names and stays outside the served folder, so an image is only addressable once a page renders it.
"""

import json
import logging
from html import escape

from thetower.backend.env_config import get_towermedia_dir

logger = logging.getLogger(__name__)

STATIC_SUBDIR = "towermedia"

_cache: dict = {"mtime": None, "lookup": {}}


def _lookup() -> dict:
    """The parsed media_lookup.json, reloaded when the file changes (e.g. after a git pull)."""
    root = get_towermedia_dir()
    if root is None:
        return {}
    path = root / "media_lookup.json"
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return {}
    if mtime != _cache["mtime"]:
        try:
            _cache["lookup"] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            logger.exception(f"Could not read {path}")
            _cache["lookup"] = {}
        _cache["mtime"] = mtime
    return _cache["lookup"]


def _img(category: str, item_id, size: int, title: str) -> str:
    try:
        key = str(int(item_id))  # pandas hands over 57.0 when the column has nulls
    except (TypeError, ValueError):
        return ""
    entry = _lookup().get(category, {}).get(key)
    if entry is None:
        return ""
    return (
        f"<img src='./app/static/{STATIC_SUBDIR}/{entry['file']}' width='{size}' height='{size}' "
        f"style='object-fit:contain' title='{escape(title.format(**entry), quote=True)}'>"
    )


def relic_img(relic_id: int, size: int = 32) -> str:
    """``<img>`` for a relic ID, titled with its name and bonus, or "" when there is no media for it."""
    return _img("relic", relic_id, size, "{name}: {bonus_text}")


def avatar_img(avatar_id: int, size: int = 32) -> str:
    """``<img>`` for an avatar (tower skin) ID, titled with its name, or "" when there is no media for it."""
    return _img("tower", avatar_id, size, "{name}")
