"""Creator Codes: every player with a creator code to support in the TechTree store."""

from html import escape
from pathlib import Path

import pandas as pd
import streamlit as st

from thetower.backend.sus.models import KnownPlayer, PlayerId
from thetower.backend.tourney_results.data import get_sus_ids
from thetower.backend.tourney_results.formatting import make_player_url

STORE_URL = "https://store.techtreegames.com/thetower/"
COLUMNS = ["Player", "Creator code", "id"]


@st.cache_data(ttl=600, show_spinner=False)
def load_creator_codes() -> pd.DataFrame:
    """One row per player with a creator code, listed under their primary tower id; sus players are left out."""
    players = list(KnownPlayer.objects.exclude(creator_code__isnull=True).exclude(creator_code="").values("id", "name", "creator_code"))
    primary_ids = dict(
        PlayerId.objects.filter(game_instance__player__in=[p["id"] for p in players], game_instance__primary=True, primary=True).values_list(
            "game_instance__player_id", "id"
        )
    )
    sus_ids = set(get_sus_ids())
    rows = [
        {"Player": p["name"] or "", "Creator code": p["creator_code"].strip(), "id": primary_ids[p["id"]]}
        for p in players
        if p["id"] in primary_ids and primary_ids[p["id"]] not in sus_ids
    ]
    table = pd.DataFrame(rows, columns=COLUMNS)
    return table.sort_values("Creator code", key=lambda codes: codes.str.lower()).reset_index(drop=True)


def get_creator_codes():
    st.markdown("# Creator Codes")
    st.markdown(f"Support a community creator by entering their code in the [TechTree Store]({STORE_URL}).")
    css_path = Path(__file__).parent.parent / "static" / "styles" / "style.css"
    with open(css_path, "r") as infile:
        table_styling = f"<style>{infile.read()}</style>"

    st.write(table_styling, unsafe_allow_html=True)

    table = load_creator_codes()
    if table.empty:
        st.info("No creator codes yet.")
        return

    table["Player"] = table["Player"].map(escape)
    table["Creator code"] = table["Creator code"].map(lambda code: f"<span style='color:#cd4b3d; font-weight:bold;'>{escape(code)}</span>")
    to_be_displayed = table.style.format(make_player_url, subset=["id"])
    st.write(to_be_displayed.to_html(escape=False, index=False), unsafe_allow_html=True)


get_creator_codes()
