"""Namechangers: players who have competed under more than one tournament nickname while in the top league."""

from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Iterable

import pandas as pd
import streamlit as st
from django.db.models import Count, Max

from thetower.backend.sus.models import PlayerId
from thetower.backend.tourney_results.constants import leagues
from thetower.backend.tourney_results.data import get_sus_ids
from thetower.backend.tourney_results.formatting import make_player_url
from thetower.backend.tourney_results.models import TourneyResult, TourneyRow
from thetower.backend.tourney_results.sus_config import include_sus_enabled_for

COLUMNS = ["real_name", "id", "namechanged_times", "total"]


def top_league_result_ids(results: Iterable[tuple[int, date, str]]) -> list[int]:
    """Ids of every result that was in the top league on its own date.

    The top league on a date is the highest league (in the constants' order) with a result that day, so
    Champion counts until Legend appeared, Legend until Mythic, and so on as leagues are added. A name
    change made in Champion after Legend became the top league is not a top-league name change.
    """
    rank = {league: position for position, league in enumerate(leagues)}
    by_date: dict[date, list[tuple[int, int]]] = defaultdict(list)
    for result_id, result_date, league in results:
        if league in rank:
            by_date[result_date].append((rank[league], result_id))
    ids = []
    for entries in by_date.values():
        top = min(position for position, _ in entries)
        ids.extend(result_id for position, result_id in entries if position == top)
    return ids


def summarize_namechangers(name_counts: pd.DataFrame, player_ids: pd.DataFrame, sus_ids: set) -> pd.DataFrame:
    """Players with more than one tournament nickname, from per-(player_id, nickname) row counts.

    name_counts: one row per (player_id, nickname) with the number of results under that name.
    player_ids: one row per known tower id with its person (player), real_name and primary flag.
    Every tower id a person owns counts toward that person, who is listed under their primary id;
    unknown ids, people without a primary id and (unless included) sus players are dropped.
    """
    if name_counts.empty or player_ids.empty:
        return pd.DataFrame(columns=COLUMNS)

    ids = player_ids.rename(columns={"id": "tower_id"})
    known = name_counts.merge(ids[["tower_id", "player", "real_name"]], left_on="player_id", right_on="tower_id")
    primary_id = ids[ids.primary].drop_duplicates("player").set_index("player")["tower_id"]
    known["id"] = known["player"].map(primary_id)
    known = known[known["id"].notna() & ~known["id"].isin(sus_ids)]

    summary = (
        known.groupby("id").agg(real_name=("real_name", "first"), namechanged_times=("nickname", "nunique"), total=("total", "sum")).reset_index()
    )
    summary = summary[summary.namechanged_times > 1]
    return summary.sort_values(["namechanged_times", "total"], ascending=False).reset_index(drop=True)[COLUMNS]


@st.cache_data(ttl=6 * 3600, persist="disk", show_spinner="Finding name changers...")
def load_namechangers(latest_result_date, include_sus: bool) -> pd.DataFrame:
    """The name-changer table, keyed on the newest result so it is rebuilt only after an import.

    The database does the heavy lifting: it collapses every row into one count per (player_id, nickname),
    tens of thousands of rows instead of the 1.7M the old page pulled into pandas on every visit. The rows
    are selected by result id on purpose: filtering by league through the result join makes SQLite scan
    the whole 22M-row table via the player_id index (75s); result ids use the result_id index (seconds).
    """
    result_ids = top_league_result_ids(TourneyResult.objects.values_list("id", "date", "league"))
    name_counts = pd.DataFrame(
        list(TourneyRow.objects.filter(result_id__in=result_ids).order_by().values("player_id", "nickname").annotate(total=Count("id"))),
        columns=["player_id", "nickname", "total"],
    )
    player_ids = pd.DataFrame(
        list(PlayerId.objects.values("id", "game_instance__player__id", "game_instance__player__name", "primary")),
        columns=["id", "game_instance__player__id", "game_instance__player__name", "primary"],
    ).rename(columns={"game_instance__player__id": "player", "game_instance__player__name": "real_name"})
    sus_ids = set() if include_sus else set(get_sus_ids())
    return summarize_namechangers(name_counts, player_ids, sus_ids)


def get_namechangers():
    st.markdown("# Namechangers")
    css_path = Path(__file__).parent.parent / "static" / "styles" / "style.css"
    with open(css_path, "r") as infile:
        table_styling = f"<style>{infile.read()}</style>"

    st.write(table_styling, unsafe_allow_html=True)

    latest_result_date = TourneyResult.objects.aggregate(latest=Max("date"))["latest"]
    table = load_namechangers(latest_result_date, include_sus_enabled_for("namechangers"))
    if table.empty:
        st.info("No name changes found.")
        return

    to_be_displayed = table.style.format(make_player_url, subset=["id"])
    st.write(to_be_displayed.to_html(escape=False, index=False), unsafe_allow_html=True)


get_namechangers()
