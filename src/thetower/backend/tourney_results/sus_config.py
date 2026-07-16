import json
import logging
import threading
import time
from typing import Any, Dict

from thetower.backend.env_config import get_django_data

_CACHE: Dict[str, Any] = {"mapping": None, "expiry": 0.0}
_LOCK = threading.Lock()


def _load_mapping_from_disk() -> Dict[str, Any]:
    django_data = get_django_data()
    json_path = django_data / "include_sus.json"

    if json_path.exists():
        try:
            text = json_path.read_text(encoding="utf8")
            payload = json.loads(text)
            if not isinstance(payload, dict):
                logging.warning("include_sus.json did not contain a JSON object; treating as empty")
                return {"pages": {}, "default": False}

            pages = payload.get("pages", {}) or {}
            default = bool(payload.get("default", False))

            # Ensure pages is a dict of str->bool
            pages_clean = {}
            if isinstance(pages, dict):
                for k, v in pages.items():
                    try:
                        pages_clean[str(k)] = bool(v)
                    except Exception:
                        pages_clean[str(k)] = False
            else:
                logging.warning("include_sus.json.pages is not a dict; ignoring pages")

            return {"pages": pages_clean, "default": default}
        except Exception:
            logging.exception("Failed to read/parse include_sus.json; treating as empty mapping")
            return {"pages": {}, "default": False}
    else:
        return {"pages": {}, "default": False}


def include_sus_enabled_for(page: str, ttl_seconds: int = 300) -> bool:
    """Return whether sus players should be included for the given page.

    The mapping is read from `include_sus.json` in the DJANGO_DATA directory and cached
    in-process for `ttl_seconds` (default 300s).

    Args:
        page: page key, e.g. 'live_bracket' or 'tourney_results'
        ttl_seconds: cache TTL in seconds

    Returns:
        bool: True to include sus players for this page
    """
    now = time.time()
    with _LOCK:
        if _CACHE["mapping"] is None or now >= _CACHE["expiry"]:
            _CACHE["mapping"] = _load_mapping_from_disk()
            _CACHE["expiry"] = now + float(ttl_seconds)

        mapping = _CACHE["mapping"]

    try:
        return bool(mapping.get("pages", {}).get(page, mapping.get("default", False)))
    except Exception:
        logging.exception("Error resolving include_sus flag for page %s; defaulting to False", page)
        return False


def include_sus_invalidate() -> None:
    """Invalidate the in-process cache so the next call will reload from disk."""
    with _LOCK:
        _CACHE["mapping"] = None
        _CACHE["expiry"] = 0.0


def get_sus_cache_status() -> Dict[str, Any]:
    """Return the current in-process cache status.

    Returns a dict with keys:
    - 'cached': bool whether a mapping is currently cached
    - 'mapping': the cached mapping (or None)
    - 'expiry': absolute timestamp when the cache expires (0.0 if not set)
    - 'ttl_remaining': seconds remaining until expiry (0 if expired or not cached)
    """
    now = time.time()
    with _LOCK:
        mapping = _CACHE.get("mapping")
        expiry = float(_CACHE.get("expiry", 0.0) or 0.0)

    cached = mapping is not None
    ttl_remaining = max(0.0, expiry - now) if cached else 0.0
    return {
        "cached": cached,
        "mapping": mapping,
        "expiry": expiry,
        "ttl_remaining": ttl_remaining,
    }


def read_sus_mapping_from_disk() -> Dict[str, Any]:
    """Read and return the mapping directly from disk (no cache used).

    This is useful for admin/debug pages that want to show the authoritative
    on-disk configuration without influencing the in-process cache.
    """
    return _load_mapping_from_disk()
