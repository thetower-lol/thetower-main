from pathlib import Path

import pandas as pd
import streamlit as st

from thetower.backend.sus.models import PlayerId
from thetower.backend.tourney_results.constants import champ, legend
from thetower.backend.tourney_results.data import get_sus_ids
from thetower.backend.tourney_results.formatting import make_player_url
from thetower.backend.tourney_results.models import TourneyRow
from thetower.backend.tourney_results.sus_config import include_sus_enabled_for


def get_namechangers():
    st.markdown("# Namechangers")
    css_path = Path(__file__).parent.parent / "static" / "styles" / "style.css"
    with open(css_path, "r") as infile:
        table_styling = f"<style>{infile.read()}</style>"

    st.write(table_styling, unsafe_allow_html=True)

    id_data = PlayerId.objects.all().select_related("game_instance__player").values("id", "game_instance__player__name", "primary")
    iddf = pd.DataFrame(id_data)
    iddf = iddf.rename(columns={"game_instance__player__name": "real_name"})

    piddf = iddf[iddf.primary]
    real_name_id_mapping = dict(zip(piddf.real_name, piddf.id))
    id_real_name_mapping = dict(zip(iddf.id, iddf.real_name))

    all_ids = iddf.id.unique()

    rows = (
        TourneyRow.objects.all()
        .select_related("result")
        .filter(player_id__in=all_ids, result__league__in=[champ, legend])
        .values("player_id", "nickname", "wave", "result__date")
        .order_by("-result__date")
    )
    rdf = pd.DataFrame(rows)

    rdf["real_name"] = [id_real_name_mapping[id] for id in rdf.player_id]

    # Safely get primary IDs and handle missing keys
    player_ids = []
    for real_name in rdf.real_name:
        player_ids.append(real_name_id_mapping.get(real_name))

    rdf["player_id"] = player_ids
    # Filter out rows with no matching primary ID
    rdf = rdf[rdf.player_id.notna()]

    if not include_sus_enabled_for("namechangers"):
        rdf = rdf[~rdf.player_id.isin(get_sus_ids())]
    rdf = rdf.rename(columns={"player_id": "id", "nickname": "tourney_name"})

    combined_data = []

    df = rdf

    for id, data in df.groupby("id"):
        if len(data.tourney_name.unique()) == 1:
            continue

        real_name = data.real_name.iloc[0]
        how_many_rows = len(data)
        how_many_names = len(data.tourney_name.unique())

        combined_data.append(
            {
                "real_name": real_name,
                "id": id,
                "namechanged_times": how_many_names,
                "total": how_many_rows,
            }
        )

    new_df = pd.DataFrame(combined_data)
    new_df = new_df.sort_values("namechanged_times", ascending=False).reset_index(drop=True)
    new_df.index = new_df.index + 1

    to_be_displayed = new_df.style.format(make_player_url, subset=["id"])

    st.write(to_be_displayed.to_html(escape=False, index=False), unsafe_allow_html=True)


get_namechangers()
