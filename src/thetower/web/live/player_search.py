"""Player search shared by the live pages (Live Bracket, Live Placement Analysis, Peer Watch).

Every player search box on the site follows one matching rule. The historical Search page applies the same rule
in its database queries (`search_players_optimized` in historical/search.py, with Advanced search off):

- Player ID box: the ID starts with the entered text, case-insensitive.
- Name box: the first word starts the player's name, and any further words appear anywhere in that same name.
  On the live pages "name" means the player's known name or their tourney name.

Each page passes its own per-league data, so anti-snipe and shun/sus filtering stay the page's choice.
"""

import logging
from dataclasses import dataclass
from typing import Callable, Literal

import pandas as pd
import streamlit as st

from thetower.backend.tourney_results.constants import leagues as ALL_LEAGUES
from thetower.web.util import add_player_id

logger = logging.getLogger(__name__)

SearchBy = Literal["name", "id"]


@dataclass(frozen=True)
class LiveMatch:
    name: str
    player_id: str
    league: str


def _text(series: pd.Series) -> pd.Series:
    """A column as plain strings, with missing values as "" (columns may be categorical)."""
    return series.astype("string").fillna("")


def name_matches(series: pd.Series, term: str) -> pd.Series:
    """Rows whose name starts with the term's first word and contains each further word."""
    fragments = term.lower().split()
    if not fragments:
        return pd.Series(False, index=series.index)
    names = _text(series).str.lower()
    mask = names.str.startswith(fragments[0])
    for fragment in fragments[1:]:
        mask &= names.str.contains(fragment, regex=False)
    return mask


def match_player_rows(df: pd.DataFrame, term: str, by: SearchBy) -> pd.DataFrame:
    """Rows of one league's live data that match the search, under the site-wide rule above."""
    term = term.strip()
    if not term or df.empty:
        return df.iloc[0:0]
    if by == "id":
        return df[_text(df["player_id"]).str.upper().str.startswith(term.upper())]
    mask = name_matches(df["real_name"], term)
    if "name" in df.columns:
        mask |= name_matches(df["name"], term)
    return df[mask]


def find_live_players(term: str, by: SearchBy, load_frame: Callable[[str], pd.DataFrame]) -> list[LiveMatch]:
    """Players matching the search across every league, sorted by name then ID.

    load_frame(league) returns the page's live data for that league; a league that fails to load is skipped.
    """
    matches: list[LiveMatch] = []
    for league in ALL_LEAGUES:
        try:
            df = load_frame(league)
        except Exception:
            logger.debug("Player search skipped %s: no live data", league, exc_info=True)
            continue
        if df is None or df.empty:
            continue
        rows = match_player_rows(df, term, by).drop_duplicates(subset=["player_id"])
        matches.extend(LiveMatch(str(row.real_name), str(row.player_id), league) for row in rows.itertuples())
    matches.sort(key=lambda m: (m.name.lower(), m.player_id))
    return matches


def no_match_message(term: str, by: SearchBy) -> str:
    """Not-found text for a search; says which rule was applied so a near miss is easy to correct."""
    if by == "id":
        return f"No player ID starting with '{term.strip().upper()}' found in the current tournament."
    return f"No player name starting with '{term.strip()}' found in the current tournament."


def show_player_choices(matches: list[LiveMatch], key_prefix: str) -> None:
    """The "Multiple players match" list; Select stores the player ID through add_player_id."""
    st.warning("Multiple players match. Please select one:")
    for match in matches:
        name_col, id_col, league_col, button_col = st.columns([3, 1, 1, 1])
        name_col.write(match.name)
        id_col.write(match.player_id)
        league_col.write(match.league)
        button_col.button("Select", key=f"{key_prefix}_select_{match.player_id}_{match.league}", on_click=add_player_id, args=(match.player_id,))
