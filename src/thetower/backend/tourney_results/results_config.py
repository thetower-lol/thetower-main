import json
import logging
import threading
import time
from typing import Any, Dict

from thetower.backend.env_config import get_django_data
from thetower.backend.tourney_results.constants import how_many_results_public_site, leagues

RESULTS_LIMITS_FILENAME = "results_limits.json"

_CACHE: Dict[str, Any] = {"mapping": None, "expiry": 0.0}
_LOCK = threading.Lock()


def _load_mapping_from_disk() -> Dict[str, Any]:
    django_data = get_django_data()
    json_path = django_data / RESULTS_LIMITS_FILENAME

    if json_path.exists():
        try:
            text = json_path.read_text(encoding="utf8")
            payload = json.loads(text)
            if not isinstance(payload, dict):
                logging.warning("%s did not contain a JSON object; treating as empty", RESULTS_LIMITS_FILENAME)
                return {"leagues": {}}

            raw_leagues = payload.get("leagues", {}) or {}

            leagues_clean: Dict[str, int] = {}
            if isinstance(raw_leagues, dict):
                for k, v in raw_leagues.items():
                    try:
                        value = int(v)
                        if value > 0:
                            leagues_clean[str(k)] = value
                    except Exception:
                        logging.warning("Ignoring non-integer results limit for league %s: %r", k, v)
            else:
                logging.warning("%s.leagues is not a dict; ignoring leagues", RESULTS_LIMITS_FILENAME)

            return {"leagues": leagues_clean}
        except Exception:
            logging.exception("Failed to read/parse %s; treating as empty mapping", RESULTS_LIMITS_FILENAME)
            return {"leagues": {}}
    else:
        return {"leagues": {}}


def _get_mapping(ttl_seconds: int = 300) -> Dict[str, Any]:
    now = time.time()
    with _LOCK:
        if _CACHE["mapping"] is None or now >= _CACHE["expiry"]:
            _CACHE["mapping"] = _load_mapping_from_disk()
            _CACHE["expiry"] = now + float(ttl_seconds)

        return _CACHE["mapping"]


def get_results_limit(league: str, ttl_seconds: int = 300) -> int:
    """Return the public-site results row cap for the given league.

    The mapping is read from `results_limits.json` in the DJANGO_DATA directory and cached
    in-process for `ttl_seconds` (default 300s). Leagues without a configured value fall
    back to the `how_many_results_public_site` constant, so a missing file is behavior-neutral.
    """
    mapping = _get_mapping(ttl_seconds)

    try:
        return int(mapping.get("leagues", {}).get(league, how_many_results_public_site))
    except Exception:
        logging.exception("Error resolving results limit for league %s; using default", league)
        return how_many_results_public_site


def get_max_results_limit(ttl_seconds: int = 300) -> int:
    """Return the highest public-site cap across all known leagues.

    Used by cross-league queries (search, player history, comparison) that filter on a single
    position bound rather than per-league bounds.
    """
    mapping = _get_mapping(ttl_seconds)
    league_limits = mapping.get("leagues", {})
    return max(int(league_limits.get(league, how_many_results_public_site)) for league in leagues)


def results_limits_invalidate() -> None:
    """Invalidate the in-process cache so the next call will reload from disk."""
    with _LOCK:
        _CACHE["mapping"] = None
        _CACHE["expiry"] = 0.0


def read_results_limits_from_disk() -> Dict[str, Any]:
    """Read and return the mapping directly from disk (no cache used).

    Useful for admin pages that want to show the authoritative on-disk configuration
    without influencing the in-process cache.
    """
    return _load_mapping_from_disk()


def write_results_limits_to_disk(league_limits: Dict[str, int]) -> None:
    """Write the per-league limits mapping to disk. Caller should invalidate the cache after."""
    django_data = get_django_data()
    json_path = django_data / RESULTS_LIMITS_FILENAME
    payload = {"leagues": {str(k): int(v) for k, v in league_limits.items()}}
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf8")
