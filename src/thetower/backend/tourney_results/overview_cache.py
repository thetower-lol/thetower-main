"""
Overview page statistics cache.

Computes and serialises the expensive DB queries needed by the overview page
(patch leaderboard, legend avg-wave leaderboard, per-league standings) to a
single JSON file so Streamlit can serve them without hitting the DB on every
render.

Cache lifecycle
---------------
- Written by ``regenerate_overview_cache()`` — call it after importing
  tournament results or after any moderation action (sus/ban/etc.) that could
  change what is displayed.
- Read by ``read_overview_cache()`` — returns ``None`` if the file is absent.
- The file lives in ``<DJANGO_DATA>/overview_cache.json``.

All public functions are safe to call from any context where Django is already
set up (management commands, import scripts, admin views, Streamlit, etc.).
"""

import json
import logging
import os
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_CACHE_FILENAME = "overview_cache.json"


def _get_cache_path() -> Path:
    django_data = os.getenv("DJANGO_DATA")
    if not django_data:
        raise RuntimeError("DJANGO_DATA environment variable is not set.")
    return Path(django_data) / _CACHE_FILENAME


# ---------------------------------------------------------------------------
# Stats computation
# ---------------------------------------------------------------------------


def _compute_league_standings(last_tourney_date, leagues_list: list[str], excluded_ids: set) -> dict[str, list[dict]]:
    """Return top players for each league for the given date, sus/banned filtered."""
    from .data import get_player_id_lookup
    from .models import TourneyResult, TourneyRow

    hidden_features = bool(os.environ.get("HIDDEN_FEATURES"))
    public = {"public": True} if not hidden_features else {}
    lookup = get_player_id_lookup()
    standings: dict[str, list[dict]] = {}

    for league in leagues_list:
        try:
            qs = TourneyResult.objects.filter(league=league, date=last_tourney_date, **public)
            if not qs.exists():
                standings[league] = []
                continue

            result = qs.first()
            limit = 6 if league == "Legend" else 4

            rows = (
                TourneyRow.objects.filter(result=result, position__gt=0)
                .exclude(player_id__in=excluded_ids)
                .order_by("position")
                .values("player_id", "nickname", "wave", "position")[:limit]
            )

            standings[league] = [
                {
                    "real_name": lookup.get(r["player_id"], r["nickname"]),
                    "wave": r["wave"],
                    "position": r["position"],
                }
                for r in rows
            ]
        except Exception:
            logger.exception("Error computing standings for league %s", league)
            standings[league] = []

    return standings


def _compute_patch_leaderboard(excluded_ids: set) -> list[dict]:
    """Return top-5 players with most first-place finishes in the current patch."""
    from .data import get_player_id_lookup
    from .models import PatchNew, TourneyResult, TourneyRow

    hidden_features = bool(os.environ.get("HIDDEN_FEATURES"))
    public = {"public": True} if not hidden_features else {}

    try:
        latest_patch = PatchNew.objects.order_by("-start_date").first()
        if not latest_patch:
            return []

        tourney_results = TourneyResult.objects.filter(date__gte=latest_patch.start_date, date__lte=latest_patch.end_date, **public)
        if not tourney_results.exists():
            return []

        first_place = (
            TourneyRow.objects.filter(result__in=tourney_results, position=1).exclude(player_id__in=excluded_ids).values_list("player_id", flat=True)
        )
        second_place = (
            TourneyRow.objects.filter(result__in=tourney_results, position=2).exclude(player_id__in=excluded_ids).values_list("player_id", flat=True)
        )

        player_first = Counter(first_place)
        player_second = Counter(second_place)

        stats = [(pid, count, player_second.get(pid, 0)) for pid, count in player_first.items()]
        stats.sort(key=lambda x: (x[1], x[2]), reverse=True)

        lookup = get_player_id_lookup()
        return [
            {
                "real_name": lookup.get(pid, f"Player {pid}"),
                "first_wins": fw,
                "second_wins": sw,
                "patch_name": str(latest_patch),
            }
            for pid, fw, sw in stats[:5]
        ]
    except Exception:
        logger.exception("Error computing patch leaderboard")
        return []


def _compute_legend_avg_wave_leaderboard(excluded_ids: set) -> list[dict]:
    """Return top-5 players by average wave in Legend for the current patch (all tourneys required)."""
    from .data import get_player_id_lookup
    from .models import PatchNew, TourneyResult, TourneyRow

    hidden_features = bool(os.environ.get("HIDDEN_FEATURES"))
    public = {"public": True} if not hidden_features else {}

    try:
        latest_patch = PatchNew.objects.order_by("-start_date").first()
        if not latest_patch:
            return []

        tourney_results = TourneyResult.objects.filter(date__gte=latest_patch.start_date, date__lte=latest_patch.end_date, league="Legend", **public)
        if not tourney_results.exists():
            return []

        rows = TourneyRow.objects.filter(result__in=tourney_results).exclude(player_id__in=excluded_ids).values_list("player_id", "wave", "result_id")
        if not rows:
            return []

        player_waves: dict[str, list[int]] = defaultdict(list)
        tourney_ids: set[int] = set()
        for pid, wave, rid in rows:
            player_waves[pid].append(wave)
            tourney_ids.add(rid)

        max_tourneys = len(tourney_ids)
        player_avg = [(pid, sum(waves) / len(waves), len(waves)) for pid, waves in player_waves.items() if len(waves) == max_tourneys]
        if not player_avg:
            return []

        player_avg.sort(key=lambda x: (x[1], x[2]), reverse=True)
        lookup = get_player_id_lookup()
        return [
            {
                "real_name": lookup.get(pid, f"Player {pid}"),
                "avg_wave": round(avg, 2),
                "tournaments": tc,
            }
            for pid, avg, tc in player_avg[:5]
        ]
    except Exception:
        logger.exception("Error computing legend avg wave leaderboard")
        return []


def compute_overview_stats() -> dict[str, Any]:
    """
    Run all expensive queries and return a plain dict ready for JSON
    serialisation.  Excludes sus/banned players.
    """
    from .constants import leagues
    from .data import get_banned_ids, get_sus_ids
    from .models import TourneyResult
    from .sus_config import include_sus_enabled_for

    hidden_features = bool(os.environ.get("HIDDEN_FEATURES"))
    public = {"public": True} if not hidden_features else {}

    excluded_ids = get_banned_ids()
    if not include_sus_enabled_for("overview_cache"):
        excluded_ids = excluded_ids | get_sus_ids()

    try:
        last_tourney = TourneyResult.objects.filter(**public).latest("date")
        last_tourney_date = last_tourney.date
        last_tourney_date_iso = last_tourney_date.isoformat()
    except TourneyResult.DoesNotExist:
        logger.warning("No tournament results found; aborting cache generation")
        return {}

    logger.info("Computing overview cache for %s", last_tourney_date_iso)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "last_tourney_date": last_tourney_date_iso,
        "league_standings": _compute_league_standings(last_tourney_date, leagues, excluded_ids),
        "patch_leaderboard": _compute_patch_leaderboard(excluded_ids),
        "legend_avg_wave_leaderboard": _compute_legend_avg_wave_leaderboard(excluded_ids),
    }


# ---------------------------------------------------------------------------
# Read / write
# ---------------------------------------------------------------------------


def write_overview_cache(data: dict[str, Any]) -> Path:
    """Serialise *data* to the cache file and return the file path."""
    path = _get_cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("Overview cache written to %s", path)
    return path


def read_overview_cache() -> Optional[dict[str, Any]]:
    """
    Read and return the cached stats dict, or ``None`` if the file is absent or
    unreadable.
    """
    try:
        path = _get_cache_path()
    except RuntimeError:
        return None

    if not path.exists():
        return None

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        logger.exception("Failed to read overview cache from %s", path)
        return None


def regenerate_overview_cache() -> Optional[dict[str, Any]]:
    """
    Convenience one-liner: compute stats, write the cache file, and return the
    data.  Returns ``None`` if an error occurs so callers can degrade
    gracefully.

    Usage examples::

        # After importing tournament results:
        from thetower.backend.tourney_results.overview_cache import regenerate_overview_cache
        regenerate_overview_cache()

        # After marking a player as sus:
        regenerate_overview_cache()
    """
    try:
        data = compute_overview_stats()
        if data:
            write_overview_cache(data)
            return data
    except Exception:
        logger.exception("regenerate_overview_cache failed")
    return None
