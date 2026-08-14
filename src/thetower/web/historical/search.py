import os
import time

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "thetower.backend.towerdb.settings")
django.setup()


from itertools import groupby
from pathlib import Path

import streamlit as st
from django.db import connection
from django.db.models import Q

from thetower.backend.sus.models import ModerationRecord, PlayerId
from thetower.backend.tourney_results.models import TourneyRow

# Cross-league queries (including raw SQL) can't cheaply apply per-league caps, so search
# filters on the highest cap configured across leagues.
from thetower.backend.tourney_results.results_config import get_max_results_limit
from thetower.backend.tourney_results.sus_config import include_sus_enabled_for
from thetower.web.util import add_player_id, add_to_comparison


def _next_prefix(s: str) -> str:
    """Return the string that sorts immediately after all strings starting with s."""
    return s[:-1] + chr(ord(s[-1]) + 1)


def _get_excluded_from_results(player_ids: list[str], include_sus: bool = False) -> set[str]:
    """Check only the given player IDs against moderation records (sus/ban/soft-ban)."""
    if not player_ids:
        return set()
    banned_types = [
        ModerationRecord.ModerationType.BAN,
        ModerationRecord.ModerationType.SOFT_BAN,
    ]
    if not include_sus:
        banned_types.append(ModerationRecord.ModerationType.SUS)
    # Standalone records (not yet linked to a GameInstance)
    standalone = set(
        ModerationRecord.objects.filter(
            tower_id__in=player_ids,
            game_instance__isnull=True,
            resolved_at__isnull=True,
            moderation_type__in=banned_types,
        ).values_list("tower_id", flat=True)
    )
    # Records via GameInstance
    game_instance_ids = list(
        PlayerId.objects.filter(id__in=player_ids, game_instance__isnull=False).values_list("game_instance_id", flat=True).distinct()
    )
    via_instance: set[str] = set()
    if game_instance_ids:
        moderated_instances = set(
            ModerationRecord.objects.filter(
                game_instance_id__in=game_instance_ids,
                resolved_at__isnull=True,
                moderation_type__in=banned_types,
            ).values_list("game_instance_id", flat=True)
        )
        if moderated_instances:
            via_instance = set(
                PlayerId.objects.filter(
                    id__in=player_ids,
                    game_instance_id__in=moderated_instances,
                ).values_list("id", flat=True)
            )
    return standalone | via_instance


def search_players_optimized(search_term, page=20, advanced_search=False):
    """
    Optimized search function that combines all searches into unified queries.
    Returns results prioritized by relevance.
    If advanced_search is True, also runs P3/P4 (contains) passes after the startswith passes.
    """
    t0 = time.perf_counter()
    search_term = search_term.strip()
    if not search_term:
        return []

    fragments = [part.strip() for part in search_term.split()]

    # Check if this looks like a player ID search (mostly hex characters)
    hex_chars = sum(1 for c in search_term.upper() if c in "ABCDEF0123456789")
    is_player_id_search = (
        len(search_term.replace(" ", "")) >= 12  # Player IDs are long
        and hex_chars / len(search_term.replace(" ", "")) > 0.7  # Mostly hex characters
    )

    if is_player_id_search:
        # Normalize to uppercase to align with stored player IDs
        search_term = search_term.upper()

        # Single query: player IDs starting with the search term
        t1 = time.perf_counter()
        pid_results = list(
            TourneyRow.objects.filter(
                player_id__istartswith=search_term,
                position__lte=get_max_results_limit(),
            )
            .values_list("player_id", "nickname")
            .order_by("player_id")
            .distinct()[:page]
        )
        # Dedup to one row per player_id
        seen: set[str] = set()
        unique_pids: list[tuple[str, str]] = []
        for pid, nick in pid_results:
            if pid not in seen:
                seen.add(pid)
                unique_pids.append((pid, nick))

        # Advanced: also search player IDs containing the term (not just startswith)
        if advanced_search and len(unique_pids) < page:
            t1c = time.perf_counter()
            contains_results = list(
                TourneyRow.objects.filter(
                    player_id__icontains=search_term,
                    position__lte=get_max_results_limit(),
                )
                .exclude(player_id__istartswith=search_term)
                .values_list("player_id", "nickname")
                .order_by("player_id")
                .distinct()[: (page - len(unique_pids)) * 5]
            )
            for pid, nick in contains_results:
                if pid not in seen:
                    seen.add(pid)
                    unique_pids.append((pid, nick))
                    if len(unique_pids) >= page:
                        break
            print(f"[search] player_id contains query: {time.perf_counter() - t1c:.3f}s")

        # Batch league lookup
        ids = [pid for pid, _ in unique_pids]
        t1b = time.perf_counter()
        league_rows = (
            TourneyRow.objects.filter(player_id__in=ids, position__lte=get_max_results_limit())
            .values_list("player_id", "result__league")
            .distinct()
        )
        league_map: dict[str, set[str]] = {}
        for pid, lg in league_rows:
            league_map.setdefault(pid, set()).add(lg)
        print(f"[search] player_id query: {t1b - t1:.3f}s ({len(unique_pids)} rows)  league batch: {time.perf_counter() - t1b:.3f}s")

        grouped_results = [(pid, nick, ", ".join(sorted(league_map.get(pid, set())))) for pid, nick in unique_pids]
        print(f"[search] player_id total: {time.perf_counter() - t0:.3f}s")
        return grouped_results

    else:
        # Name search - unified query with prioritized results
        all_results = []

        # Build match conditions for name fragments
        primary_fragment = fragments[0] if fragments else ""
        additional_fragments = fragments[1:] if len(fragments) > 1 else []

        # Priority 1: Primary names starting with first fragment
        if primary_fragment:
            base_condition = Q(player__name__istartswith=primary_fragment)
            for fragment in additional_fragments:
                base_condition &= Q(player__name__icontains=fragment)

            t1 = time.perf_counter()
            priority1_results = list(
                PlayerId.objects.filter(base_condition & Q(primary=True))
                .select_related("game_instance__player")
                .order_by("id")
                .values_list("id", "game_instance__player__name")[:page]
            )
            # Batch league lookup — one query for all players instead of N+1
            p1_ids = [pid for pid, _ in priority1_results]
            t2 = time.perf_counter()
            p1_league_rows = (
                TourneyRow.objects.filter(player_id__in=p1_ids, position__lte=get_max_results_limit())
                .values_list("player_id", "result__league")
                .distinct()
            )
            p1_league_map: dict[str, set[str]] = {}
            for pid, lg in p1_league_rows:
                p1_league_map.setdefault(pid, set()).add(lg)
            print(f"[search] p1 name query: {t2 - t1:.3f}s ({len(priority1_results)} rows)  p1 league batch: {time.perf_counter() - t2:.3f}s")
            priority1_with_league = [(pid, name, ", ".join(sorted(p1_league_map.get(pid, set())))) for pid, name in priority1_results]
            all_results.extend(priority1_with_league)

        # Priority 2: Nicknames starting with first fragment (if we need more results)
        existing_player_ids = {player_id for player_id, _, _ in all_results}
        if len(all_results) < page and primary_fragment:
            lo = primary_fragment.upper()
            hi = _next_prefix(lo)
            limit = page - len(all_results)

            t2 = time.perf_counter()
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT player_id, nickname FROM tourney_results_tourneyrow "
                    "WHERE UPPER(nickname) >= %s AND UPPER(nickname) < %s "
                    "AND position <= %s LIMIT %s",
                    [lo, hi, get_max_results_limit(), limit * 20],
                )
                raw = cursor.fetchall()

            seen = set(existing_player_ids)
            priority2_results: list[tuple[str, str]] = []
            for pid, nick in raw:
                if pid not in seen:
                    seen.add(pid)
                    if not additional_fragments or all(frag.lower() in nick.lower() for frag in additional_fragments):
                        priority2_results.append((pid, nick))
                        if len(priority2_results) >= limit:
                            break

            p2_ids = [pid for pid, _ in priority2_results]
            t2b = time.perf_counter()
            p2_league_rows = (
                TourneyRow.objects.filter(player_id__in=p2_ids, position__lte=get_max_results_limit())
                .values_list("player_id", "result__league")
                .distinct()
            )
            p2_league_map: dict[str, set[str]] = {}
            for pid, lg in p2_league_rows:
                p2_league_map.setdefault(pid, set()).add(lg)
            print(f"[search] p2 nickname query: {t2b - t2:.3f}s ({len(priority2_results)} rows)  p2 league batch: {time.perf_counter() - t2b:.3f}s")

            priority2_with_league = [(pid, nick, ", ".join(sorted(p2_league_map.get(pid, set())))) for pid, nick in priority2_results]
            all_results.extend(priority2_with_league)
            existing_player_ids.update(pid for pid, _, _ in priority2_with_league)

        if advanced_search:
            # Priority 3: Primary names containing all fragments
            if len(all_results) < page and fragments:
                base_condition = Q()
                for fragment in fragments:
                    base_condition &= Q(player__name__icontains=fragment)

                t3 = time.perf_counter()
                priority3_results = list(
                    PlayerId.objects.filter(base_condition & Q(primary=True) & ~Q(id__in=existing_player_ids))
                    .select_related("game_instance__player")
                    .order_by("id")
                    .values_list("id", "game_instance__player__name")[: page - len(all_results)]
                )
                p3_ids = [pid for pid, _ in priority3_results]
                t3b = time.perf_counter()
                p3_league_rows = (
                    TourneyRow.objects.filter(player_id__in=p3_ids, position__lte=get_max_results_limit())
                    .values_list("player_id", "result__league")
                    .distinct()
                )
                p3_league_map: dict[str, set[str]] = {}
                for pid, lg in p3_league_rows:
                    p3_league_map.setdefault(pid, set()).add(lg)
                print(f"[search] p3 name query: {t3b - t3:.3f}s ({len(priority3_results)} rows)  p3 league batch: {time.perf_counter() - t3b:.3f}s")
                priority3_with_league = [(pid, name, ", ".join(sorted(p3_league_map.get(pid, set())))) for pid, name in priority3_results]
                all_results.extend(priority3_with_league)
                existing_player_ids.update(pid for pid, _, _ in priority3_with_league)

            # Priority 4: Nicknames containing all fragments
            if len(all_results) < page and fragments:
                limit = page - len(all_results)
                conditions = " AND ".join(["UPPER(nickname) LIKE %s"] * len(fragments))
                params: list = [f"%{frag.upper()}%" for frag in fragments] + [get_max_results_limit(), limit * 20]
                sql = f"SELECT player_id, nickname FROM tourney_results_tourneyrow WHERE {conditions} AND position <= %s LIMIT %s"

                t4 = time.perf_counter()
                with connection.cursor() as cursor:
                    cursor.execute(sql, params)
                    raw = cursor.fetchall()

                seen = set(existing_player_ids)
                priority4_results: list[tuple[str, str]] = []
                for pid, nick in raw:
                    if pid not in seen:
                        seen.add(pid)
                        priority4_results.append((pid, nick))
                        if len(priority4_results) >= limit:
                            break

                p4_ids = [pid for pid, _ in priority4_results]
                t4b = time.perf_counter()
                p4_league_rows = (
                    TourneyRow.objects.filter(player_id__in=p4_ids, position__lte=get_max_results_limit())
                    .values_list("player_id", "result__league")
                    .distinct()
                )
                p4_league_map: dict[str, set[str]] = {}
                for pid, lg in p4_league_rows:
                    p4_league_map.setdefault(pid, set()).add(lg)
                print(
                    f"[search] p4 nickname query: {t4b - t4:.3f}s ({len(priority4_results)} rows)  p4 league batch: {time.perf_counter() - t4b:.3f}s"
                )
                priority4_with_league = [(pid, nick, ", ".join(sorted(p4_league_map.get(pid, set())))) for pid, nick in priority4_results]
                all_results.extend(priority4_with_league)

        # Sort name search results by nickname (case-insensitive)
        all_results.sort(key=lambda x: x[1].lower() if x[1] else "")
        print(f"[search] name search total: {time.perf_counter() - t0:.3f}s ({len(all_results)} results)")
        return all_results[:page]


def compute_search(player=False, comparison=False):
    css_path = Path(__file__).parent.parent / "static" / "styles" / "style.css"
    with open(css_path, "r") as infile:
        table_styling = f"<style>{infile.read()}</style>"

    st.write(table_styling, unsafe_allow_html=True)

    hidden_features = os.environ.get("HIDDEN_FEATURES")

    advanced_search = False
    if hidden_features:
        with st.sidebar:
            advanced_search = st.toggle("Advanced search (slow)", value=False)

    name_col, id_col = st.columns([1, 1])

    page = 20

    if advanced_search:
        name_label = "Enter any part of the player name"
        id_label = "Enter any part of the player id to be queried"
    else:
        name_label = "Enter beginning of the player name"
        id_label = "Enter beginning of the player id to be queried"

    real_name_part = name_col.text_input(name_label)
    player_id_part = id_col.text_input(id_label)

    # Determine which search to perform
    search_term = ""
    if real_name_part.strip():
        search_term = real_name_part.strip()
    elif player_id_part.strip():
        search_term = player_id_part.strip()

    if search_term:
        t_search = time.perf_counter()
        nickname_ids = search_players_optimized(search_term, page, advanced_search=advanced_search)
        if not hidden_features:
            t_excl = time.perf_counter()
            page_key = "player" if player else "comparison" if comparison else "search"
            result_ids = [pid for pid, _, _ in nickname_ids]
            excluded = _get_excluded_from_results(result_ids, include_sus=include_sus_enabled_for(page_key))
            print(f"[search] exclusion check: {time.perf_counter() - t_excl:.3f}s")
            nickname_ids = [(pid, name, lg) for pid, name, lg in nickname_ids if pid not in excluded]
        print(f"[search] compute_search total: {time.perf_counter() - t_search:.3f}s")
    else:
        nickname_ids = []

    # Process results for display
    data_to_be_shown = []

    # Handle the optimized search results format
    if nickname_ids:
        # nickname_ids is a list of (player_id, nickname, leagues) tuples
        # Group by player_id in case there are duplicates
        for player_id, group in groupby(nickname_ids, lambda x: x[0]):
            group_list = list(group)
            nicknames_list = [nickname for _, nickname, _ in group_list]
            leagues_list = [leagues for _, _, leagues in group_list if leagues]
            datum = {
                "player_id": player_id,
                "nicknames": ", ".join(set(nicknames_list)),
                "leagues": ", ".join(set(leagues_list)) if leagues_list else "",
                "how_many_results": len(nicknames_list),
            }
            data_to_be_shown.append(datum)

    for datum in data_to_be_shown:
        nickname_col, player_id_col, league_col, button_col = st.columns([2, 1, 1, 1])
        nickname_col.write(datum["nicknames"])
        player_id_col.write(datum["player_id"])
        league_col.write(datum.get("leagues", ""))

        if player:
            button_col.button(
                "See player page", on_click=add_player_id, args=(datum["player_id"],), key=f'{datum["player_id"]}{datum["nicknames"]}but'
            )

        if comparison:
            button_col.button(
                "Add to comparison", on_click=add_to_comparison, args=(datum["player_id"], datum["nicknames"]), key=f'{datum["player_id"]}comp'
            )

    if not data_to_be_shown and search_term:
        st.info("No results found")

    return data_to_be_shown


if __name__ == "__main__":
    compute_search()
