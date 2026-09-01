import csv
import datetime
import logging
import os
import re
from collections import Counter, defaultdict
from glob import glob
from pathlib import Path
from typing import Optional

import django
import numpy as np
import pandas as pd

# TODO: load_tourney_results/_load_tourney_results still need st.cache_data wrapping and progress
# reporting; restore once deprecated pages are rewritten.
# import streamlit as st
from cachetools.func import ttl_cache
from django.db.models import Q, QuerySet

from ..sus.models import ModerationRecord, PlayerId
from .archive_utils import STRING_COLUMN_DTYPES
from .constants import (
    champ,
    data_folder_name_mapping,
    how_many_results_hidden_site,
    how_many_results_public_site,
    how_many_results_public_site_other,
)
from .formatting import color_position_barebones
from .models import BattleCondition
from .models import PatchNew as Patch
from .models import Role, TourneyResult, TourneyRow
from .results_config import get_results_limit

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "thetower.backend.towerdb.settings")
django.setup()


@ttl_cache(maxsize=128, ttl=60)
def get_patches():
    return Patch.objects.all().order_by("start_date")


@ttl_cache(maxsize=512, ttl=60)
def date_to_patch(date: datetime.datetime) -> Optional[Patch]:
    for patch in get_patches():
        if date >= patch.start_date and date <= patch.end_date:
            return patch


@ttl_cache(maxsize=512, ttl=60)
def wave_to_role_in_patch(roles: list[Role], role_bot_top: list[tuple[int, int]], wave: float) -> Optional[Role]:
    for role, (wave_bottom, wave_top) in zip(roles, role_bot_top):
        if wave >= wave_bottom and wave < wave_top:
            return role


@ttl_cache(maxsize=512, ttl=60)
def patch_to_roles(league):
    patch_to_roles = defaultdict(list)

    patches = get_patches()
    last_patch_with_roles = None

    for i, patch in enumerate(patches):
        roles = tuple(Role.objects.filter(patch=patch, league=league))

        if not roles:
            if not last_patch_with_roles:
                last_patch_with_roles = i - 1

            roles = patch_to_roles[patches[last_patch_with_roles]]

        if not roles:
            print("WARNING")

        patch_to_roles[patch] = roles

    return patch_to_roles


def wave_to_role(wave: float, patch: Optional[Patch], league: str) -> Optional[Role]:
    if not patch:
        return None

    roles = patch_to_roles(league)[patch]

    if not roles:
        return None

    roles_bot_top = tuple((role.wave_bottom, role.wave_top) for role in roles)
    return wave_to_role_in_patch(roles, roles_bot_top, wave)


def load_data(folder):  # Potentially unused
    result_files = sorted(glob(f"{folder}/*"))
    total_results = {}

    for result_file in result_files:
        tourney_results = []

        with open(result_file, "r") as infile:
            file = csv.reader(infile)
            contents = [line for line in file]

        for id_, raw_name, raw_wave in contents:
            name = raw_name.strip()
            wave = int(raw_wave.strip())
            tourney_results.append((id_, name, wave))

        result_date = datetime.datetime.fromisoformat(result_file.split("/")[1].split(".")[0]).date()
        total_results[result_date] = tourney_results

    results_by_id = defaultdict(list)

    for tourney_name, results in total_results.items():
        for id_, name, wave in results:
            results_by_id[id_].append((tourney_name, name, wave))

    position_by_id = defaultdict(list)

    for tourney_name, results in total_results.items():
        for positition, (id_, name, wave) in enumerate(results, 1):
            position_by_id[id_].append((tourney_name, name, positition))

    return total_results, results_by_id, position_by_id


def get_player_id_lookup():
    return dict(PlayerId.objects.filter(game_instance__player__approved=True).values_list("id", "game_instance__player__name"))


def get_player_id_approved_lookup():
    return dict(PlayerId.objects.filter(game_instance__player__approved=True).values_list("id", "game_instance__player__approved"))


def get_id_lookup():
    id_objs = PlayerId.objects.filter(game_instance__player__approved=True).values_list("id", "game_instance__player__name", "primary")
    player_primary_id = {name: id_ for id_, name, primary in id_objs if primary}
    return {id_: player_primary_id[name] for id_, name, _ in id_objs if name in player_primary_id}


def get_id_real_name_mapping(df: pd.DataFrame, lookup: dict[str, str]) -> dict[str, str]:
    def get_most_common(df):
        return Counter(df["tourney_name"]).most_common()[0][0]

    return {id_: lookup.get(id_, get_most_common(group)) for id_, group in df.groupby("id")}


def get_row_to_role(df: pd.DataFrame, league):
    name_roles: dict[int, Optional[Role]] = {}

    for patch, roles in patch_to_roles(league).items():
        patch_start = np.datetime64(patch.start_date)
        patch_end = np.datetime64(patch.end_date)
        id_df = df[(df["date"] >= patch_start) & (df["date"] <= patch_end)]

        for _, filtered_df in id_df.groupby("id"):
            if not filtered_df.empty:
                wave_role = sorted(filtered_df["wave_role"], reverse=True)[0]
                name_roles.update({index: wave_role for index in filtered_df.index})

    df["name_role"] = df.index.map(name_roles.get)
    df["name_role_color"] = df.name_role.map(lambda role: getattr(role, "color", None))
    return df


def _load_tourney_results(
    result_files: list[tuple[Path, str, list[BattleCondition]]],
    league=champ,
    result_cutoff: Optional[int] = None,
) -> pd.DataFrame:
    hidden_features = os.environ.get("HIDDEN_FEATURES")

    dfs = []

    sus_ids = get_sus_ids()
    # TODO: progress reporting removed; restore via st.progress callback once pages are rewritten
    # load_data_bar = st.progress(0)

    for index, (result_file, date, bcs) in enumerate(result_files, 1):
        # This is deprecated for new tourney server responses introduced in 0.25
        # It's not that it's hard to adapt, but we wont, cause it makes sense to keep all the functions that use this code
        # locked at around end of champ era, or if we extend it to legends, move away from using load_tourney_results.

        try:
            result_date = datetime.datetime.fromisoformat(result_file.stem[:10])
        except ValueError:
            logging.info("Malformed csv file in legacy load function, skipping")
            continue

        if result_date > datetime.datetime(2024, 10, 18):
            continue

        # Pandas automatically handles .csv.gz files
        df = pd.read_csv(result_file, header=None, dtype=STRING_COLUMN_DTYPES)

        cutoff = handle_result_cutoff(hidden_features, league, result_cutoff)

        df = df.iloc[:cutoff]

        df.columns = ["id", "tourney_name", "wave"]

        result_date = datetime.date.fromisoformat(date)
        df["tourney_name"] = df["tourney_name"].map(lambda x: x.strip())
        df["date"] = result_date
        df["bcs"] = [bcs for _ in range(len(df))]

        positions = []
        current = 0
        borrow = 1

        for id_, idx, wave in zip(df.id, df.index, df.wave):
            if id_ in sus_ids:
                positions.append(-1)
                continue

            if idx - 1 in df.index and wave == df.loc[idx - 1, "wave"]:
                borrow += 1
            else:
                current += borrow
                borrow = 1

            positions.append(current)

        df["position"] = positions
        dfs.append(df)
        # load_data_bar.progress(index / len(result_files) * 0.5)

    df = pd.concat(dfs)

    df["avatar"] = df.tourney_name.map(lambda name: int(avatar[0]) if (avatar := re.findall(r"\#avatar=([-\d]+)\${5}", name)) else -1)
    df["relic"] = df.tourney_name.map(lambda name: int(relic[0]) if (relic := re.findall(r"\#avatar=\d+\${5}relic=([-\d]+)", name)) else -1)
    df["tourney_name"] = df.tourney_name.map(lambda name: name.split("#")[0])

    df["raw_id"] = df.id
    id_mapping = get_id_lookup()
    df["id"] = df.id.map(lambda id_: id_mapping.get(id_, id_))  # id renormalization

    lookup = get_player_id_lookup()
    id_to_real_name = get_id_real_name_mapping(df, lookup)

    df["verified"] = df.id.map(lambda id_: "✓" if lookup.get(id_) else "")
    # load_data_bar.progress(0.6)

    df["real_name"] = df.id.map(lambda id_: id_to_real_name[id_])

    df["patch"] = df.date.map(date_to_patch)
    df["patch_version"] = df.patch.map(lambda x: x.version_minor)
    # load_data_bar.progress(0.75)

    df["wave_role"] = [wave_to_role(wave, date_to_patch(date), league) for wave, date in zip(df["wave"], df["date"])]
    df["wave_role_color"] = df.wave_role.map(lambda role: getattr(role, "color", None))
    # load_data_bar.progress(0.95)

    df["position_role_color"] = [color_position_barebones(position) for position in df["position"]]
    df = df.reset_index(drop=True)
    # load_data_bar.progress(1.0)

    df = get_row_to_role(df, league=league)
    # load_data_bar.empty()
    return df


def handle_result_cutoff(hidden_features, league, result_cutoff):
    if result_cutoff:
        cutoff = result_cutoff
    elif not hidden_features:
        cutoff = how_many_results_public_site

        if league != champ:
            cutoff = how_many_results_public_site_other

    else:
        cutoff = how_many_results_hidden_site

    return cutoff


# TODO: re-add @st.cache_data (or equivalent) once Streamlit dependency is properly separated
# @st.cache_data
def load_tourney_results(
    folder: str, patch_id: Optional[int] = None, limit_no_results: Optional[int] = None, result_cutoff: Optional[int] = None
) -> pd.DataFrame:
    # This is deprecated for new tourney server responses introduced in 0.25
    # It's not that it's hard to adapt, but we wont, cause it makes sense to keep all the functions that use this code
    # locked at around end of champ era, or if we extend it to legends, move away from using load_tourney_results.
    return load_tourney_results__uncached(folder, patch_id=patch_id, limit_no_results=limit_no_results, result_cutoff=result_cutoff)


def load_tourney_results__uncached(
    folder: str, patch_id: Optional[int] = None, limit_no_results: Optional[int] = None, result_cutoff: Optional[int] = None
) -> pd.DataFrame:
    hidden_features = os.environ.get("HIDDEN_FEATURES")
    additional_filter = {} if hidden_features else dict(public=True)

    if patch_id is not None:
        patch = Patch.objects.get(id=patch_id)
        additional_filter["date__gte"] = patch.start_date
        additional_filter["date__lte"] = patch.end_date

    league = data_folder_name_mapping.get(folder, folder)

    result_files = sorted(
        [
            (
                Path("src/thetower/backend") / str(result.result_file),
                result.date.isoformat(),
                result.conditions.all(),
            )
            for result in TourneyResult.objects.filter(league=league, **additional_filter)
        ],
        key=lambda x: x[1],
    )

    if limit_no_results is not None:
        result_files = result_files[-limit_no_results:]

    return _load_tourney_results(result_files, league, result_cutoff=result_cutoff)


def get_player_list(df):
    last_date = df.date.unique()[-1]
    sus_ids = get_sus_ids()
    first_choices = list(df[(df.date == last_date) & ~(df.id.isin(sus_ids))].real_name)
    set_of_first_choices = set(first_choices)
    all_real_names = set(df.real_name.unique()) - set_of_first_choices
    all_tourney_names = set(df.tourney_name.unique())
    all_user_ids = df.raw_id.unique().tolist()
    last_top_scorer = df[(df.date == sorted(df.date.unique())[-1]) & (df.position == 1)].tourney_name.iloc[0]
    return first_choices, all_real_names, all_tourney_names, all_user_ids, last_top_scorer


def get_sus_data():
    hidden_features = os.environ.get("HIDDEN_FEATURES")

    # Get unique tower_ids with active sus status
    sus_tower_ids = set(
        ModerationRecord.objects.filter(
            moderation_type=ModerationRecord.ModerationType.SUS, resolved_at__isnull=True  # Active = not resolved
        ).values_list("tower_id", flat=True)
    )

    if hidden_features:
        # Build detailed data with moderation info
        data = []
        for tower_id in sus_tower_ids:
            # Get name from any linked GameInstance record for this tower_id
            name = tower_id  # Default fallback
            try:
                record_with_game_instance = (
                    ModerationRecord.objects.filter(tower_id=tower_id, game_instance__isnull=False).select_related("game_instance__player").first()
                )
                if record_with_game_instance and record_with_game_instance.game_instance:
                    name = record_with_game_instance.game_instance.player.name
            except Exception:
                pass

            # Check for other active moderation types for this player
            banned = ModerationRecord.objects.filter(
                tower_id=tower_id, moderation_type=ModerationRecord.ModerationType.BAN, resolved_at__isnull=True  # Active = not resolved
            ).exists()

            # Get reason from any sus record for this player
            reason = ""
            try:
                sus_record = ModerationRecord.objects.filter(
                    tower_id=tower_id, moderation_type=ModerationRecord.ModerationType.SUS, resolved_at__isnull=True  # Active = not resolved
                ).first()
                if sus_record:
                    reason = sus_record.reason or ""
            except Exception:
                pass

            data.append(
                {
                    "name": name,
                    "player_id": tower_id,
                    "sus": True,  # Always true since we filtered for SUS tower_ids
                    "banned": banned,
                    "notes": reason,
                }
            )

        # Sort by name and return as QuerySet-like structure
        return sorted(data, key=lambda x: x["name"] or "")
    else:
        # Simple name/player_id data
        data = []
        for tower_id in sus_tower_ids:
            # Get name from any linked GameInstance record for this tower_id
            name = tower_id  # Default fallback
            try:
                record_with_game_instance = (
                    ModerationRecord.objects.filter(tower_id=tower_id, game_instance__isnull=False).select_related("game_instance__player").first()
                )
                if record_with_game_instance and record_with_game_instance.game_instance:
                    name = record_with_game_instance.game_instance.player.name
            except Exception:
                pass

            data.append({"name": name, "player_id": tower_id})

        return sorted(data, key=lambda x: x["name"] or "")


def is_under_review(player_id: str):
    # Under review means active moderation records for this player
    return ModerationRecord.objects.filter(tower_id=player_id, resolved_at__isnull=True).exists()  # Active = not resolved


def is_support_flagged(player_id: str):
    return (
        ModerationRecord.objects.filter(
            tower_id=player_id, moderation_type=ModerationRecord.ModerationType.SOFT_BAN, resolved_at__isnull=True  # Active = not resolved
        ).exists()
        or ModerationRecord.objects.filter(
            tower_id=player_id, moderation_type=ModerationRecord.ModerationType.BAN, resolved_at__isnull=True  # Active = not resolved
        ).exists()
    )


def is_shun(player_id: str):
    """
    Check if a tower_id is shunned.
    Checks the GameInstance directly - simpler and more efficient.
    """
    from thetower.backend.sus.models import PlayerId

    # Try to find the GameInstance for this tower_id
    try:
        player_id_obj = PlayerId.objects.select_related("game_instance").get(id=player_id)
        if player_id_obj.game_instance:
            # Direct check: is this GameInstance shunned?
            return ModerationRecord.objects.filter(
                game_instance=player_id_obj.game_instance,
                moderation_type=ModerationRecord.ModerationType.SHUN,
                resolved_at__isnull=True,  # Active = not resolved
            ).exists()
    except PlayerId.DoesNotExist:
        pass

    # Fallback: check by tower_id (for unverified/unmigrated IDs)
    return ModerationRecord.objects.filter(
        tower_id=player_id, moderation_type=ModerationRecord.ModerationType.SHUN, resolved_at__isnull=True
    ).exists()


def is_sus(player_id: str):
    """
    Check if a tower_id is sus.
    Checks the GameInstance directly - simpler and more efficient.
    """
    from thetower.backend.sus.models import PlayerId

    # Try to find the GameInstance for this tower_id
    try:
        player_id_obj = PlayerId.objects.select_related("game_instance").get(id=player_id)
        if player_id_obj.game_instance:
            # Direct check: is this GameInstance sus?
            return ModerationRecord.objects.filter(
                game_instance=player_id_obj.game_instance,
                moderation_type=ModerationRecord.ModerationType.SUS,
                resolved_at__isnull=True,  # Active = not resolved
            ).exists()
    except PlayerId.DoesNotExist:
        pass

    # Fallback: check by tower_id (for unverified/unmigrated IDs)
    return ModerationRecord.objects.filter(tower_id=player_id, moderation_type=ModerationRecord.ModerationType.SUS, resolved_at__isnull=True).exists()


def is_soft_banned(player_id: str):
    """
    Check if a tower_id is soft banned.
    Checks the GameInstance directly - simpler and more efficient.
    """
    from thetower.backend.sus.models import PlayerId

    # Try to find the GameInstance for this tower_id
    try:
        player_id_obj = PlayerId.objects.select_related("game_instance").get(id=player_id)
        if player_id_obj.game_instance:
            # Direct check: is this GameInstance soft banned?
            return ModerationRecord.objects.filter(
                game_instance=player_id_obj.game_instance,
                moderation_type=ModerationRecord.ModerationType.SOFT_BAN,
                resolved_at__isnull=True,  # Active = not resolved
            ).exists()
    except PlayerId.DoesNotExist:
        pass

    # Fallback: check by tower_id (for unverified/unmigrated IDs)
    return ModerationRecord.objects.filter(
        tower_id=player_id, moderation_type=ModerationRecord.ModerationType.SOFT_BAN, resolved_at__isnull=True
    ).exists()


def is_banned(player_id: str):
    """
    Check if a tower_id is banned.
    Checks the GameInstance directly - simpler and more efficient.
    """
    from thetower.backend.sus.models import PlayerId

    # Try to find the GameInstance for this tower_id
    try:
        player_id_obj = PlayerId.objects.select_related("game_instance").get(id=player_id)
        if player_id_obj.game_instance:
            # Direct check: is this GameInstance banned?
            return ModerationRecord.objects.filter(
                game_instance=player_id_obj.game_instance,
                moderation_type=ModerationRecord.ModerationType.BAN,
                resolved_at__isnull=True,  # Active = not resolved
            ).exists()
    except PlayerId.DoesNotExist:
        pass

    # Fallback: check by tower_id (for unverified/unmigrated IDs)
    return ModerationRecord.objects.filter(tower_id=player_id, moderation_type=ModerationRecord.ModerationType.BAN, resolved_at__isnull=True).exists()


def get_shun_ids():
    """
    Get all tower_ids that should be filtered as shunned.
    Returns all tower_ids in shunned GameInstances, plus standalone shunned tower_ids.
    """
    from thetower.backend.sus.models import PlayerId

    # Get tower_ids directly shunned (no game_instance link yet)
    standalone_shunned = set(
        ModerationRecord.objects.filter(
            moderation_type=ModerationRecord.ModerationType.SHUN, resolved_at__isnull=True, game_instance__isnull=True
        ).values_list("tower_id", flat=True)
    )

    # Get shunned GameInstances
    shunned_instances = (
        ModerationRecord.objects.filter(moderation_type=ModerationRecord.ModerationType.SHUN, resolved_at__isnull=True, game_instance__isnull=False)
        .values_list("game_instance_id", flat=True)
        .distinct()
    )

    # Get ALL tower_ids in those GameInstances
    instance_ids = set(PlayerId.objects.filter(game_instance_id__in=shunned_instances).values_list("id", flat=True))

    return standalone_shunned | instance_ids


def get_sus_ids():
    """
    Get all tower_ids that should be filtered as sus.
    Returns all tower_ids in sus GameInstances, plus standalone sus tower_ids.
    """
    from thetower.backend.sus.models import PlayerId

    # Get tower_ids directly sus (no game_instance link yet)
    standalone_sus = set(
        ModerationRecord.objects.filter(
            moderation_type=ModerationRecord.ModerationType.SUS, resolved_at__isnull=True, game_instance__isnull=True
        ).values_list("tower_id", flat=True)
    )

    # Get sus GameInstances
    sus_instances = (
        ModerationRecord.objects.filter(moderation_type=ModerationRecord.ModerationType.SUS, resolved_at__isnull=True, game_instance__isnull=False)
        .values_list("game_instance_id", flat=True)
        .distinct()
    )

    # Get ALL tower_ids in those GameInstances
    instance_ids = set(PlayerId.objects.filter(game_instance_id__in=sus_instances).values_list("id", flat=True))

    return standalone_sus | instance_ids


def get_banned_ids():
    """
    Get all tower_ids that should be filtered as banned.
    Returns all tower_ids in banned GameInstances, plus standalone banned tower_ids.
    """
    from thetower.backend.sus.models import PlayerId

    # Get tower_ids directly banned (no game_instance link yet)
    standalone_banned = set(
        ModerationRecord.objects.filter(
            moderation_type=ModerationRecord.ModerationType.BAN, resolved_at__isnull=True, game_instance__isnull=True
        ).values_list("tower_id", flat=True)
    )

    # Get banned GameInstances
    banned_instances = (
        ModerationRecord.objects.filter(moderation_type=ModerationRecord.ModerationType.BAN, resolved_at__isnull=True, game_instance__isnull=False)
        .values_list("game_instance_id", flat=True)
        .distinct()
    )

    # Get ALL tower_ids in those GameInstances
    instance_ids = set(PlayerId.objects.filter(game_instance_id__in=banned_instances).values_list("id", flat=True))

    return standalone_banned | instance_ids


def get_soft_banned_ids():
    """
    Get all tower_ids that should be filtered as soft banned.
    Returns all tower_ids in soft banned GameInstances, plus standalone soft banned tower_ids.
    """
    from thetower.backend.sus.models import PlayerId

    # Get tower_ids directly soft banned (no game_instance link yet)
    standalone_soft_banned = set(
        ModerationRecord.objects.filter(
            moderation_type=ModerationRecord.ModerationType.SOFT_BAN, resolved_at__isnull=True, game_instance__isnull=True
        ).values_list("tower_id", flat=True)
    )

    # Get soft banned GameInstances
    soft_banned_instances = (
        ModerationRecord.objects.filter(
            moderation_type=ModerationRecord.ModerationType.SOFT_BAN, resolved_at__isnull=True, game_instance__isnull=False
        )
        .values_list("game_instance_id", flat=True)
        .distinct()
    )

    # Get ALL tower_ids in those GameInstances
    instance_ids = set(PlayerId.objects.filter(game_instance_id__in=soft_banned_instances).values_list("id", flat=True))

    return standalone_soft_banned | instance_ids


def get_last_tourney(league=champ):
    hidden_features = os.environ.get("HIDDEN_FEATURES")
    public = {"public": True} if not hidden_features else {}
    return TourneyResult.objects.filter(league=league, **public).latest("date")


def get_results_for_patch(patch: Patch, league=champ):
    hidden_features = os.environ.get("HIDDEN_FEATURES")
    public = {"public": True} if not hidden_features else {}
    return TourneyResult.objects.filter(date__gte=patch.start_date, date__lte=patch.end_date, league=league, **public).order_by("-date")


@ttl_cache(maxsize=128, ttl=60)
def get_patch_for_result(date: datetime) -> Patch:
    return Patch.objects.get(start_date__lte=date, end_date__gte=date)


def get_tourneys(
    tourney_results: QuerySet[TourneyResult],
    offset: int = 0,
    limit: int = how_many_results_public_site,
    filter_sus: bool = True,
    ids: list[int] | None = None,
) -> pd.DataFrame:
    hidden_features = os.environ.get("HIDDEN_FEATURES")
    upper_limit = offset + limit

    if not hidden_features:
        result_leagues = {result.league for result in tourney_results}
        cutoff = max(get_results_limit(league) for league in result_leagues) if result_leagues else how_many_results_public_site
        upper_limit = min(upper_limit, cutoff)

    id_filtering = {"player_id__in": ids} if ids else {}

    rows = TourneyRow.objects.filter(result__in=tourney_results, position__gte=offset, position__lt=upper_limit, **id_filtering)

    if filter_sus:
        rows = rows.filter(~Q(player_id__in=get_sus_ids()) & Q(position__gt=0))

    rows = rows.order_by("result__date", "position")
    return get_details(rows)


def get_details(rows: QuerySet[TourneyRow]) -> pd.DataFrame:
    rows = rows.prefetch_related("result")

    df = pd.DataFrame(
        rows.values("player_id", "position", "nickname", "wave", "avatar_id", "relic_id", "result__date", "result__league", "result_id")
    )
    df = df.rename(
        columns={
            "player_id": "id",
            "nickname": "tourney_name",
            "avatar_id": "avatar",
            "relic_id": "relic",
            "result__date": "date",
            "result__league": "league",
        }
    )

    if df.empty:
        return df

    lookup = get_player_id_lookup()
    approved_lookup = get_player_id_approved_lookup()

    conditions_mapping = {
        result.id: BattleCondition.objects.filter(results__id=result.id) for result in TourneyResult.objects.filter(id__in=df.result_id.unique())
    }

    patches = [get_patch_for_result(date) for date in df.date]
    bcs = [conditions_mapping.get(id_) for id_ in df.result_id]

    df["real_name"] = [lookup.get(id, name) for id, name in zip(df.id, df.tourney_name)]
    df["verified"] = ["✓" if approved_lookup.get(id) else "" for id, name in zip(df.id, df.tourney_name)]
    df["wave_role"] = [wave_to_role(wave, patch, league) for wave, patch, league in zip(df["wave"], patches, df.league)]
    df["wave_role_color"] = df.wave_role.map(lambda role: getattr(role, "color", None))
    df["bcs"] = bcs
    df["patch"] = patches

    return df
