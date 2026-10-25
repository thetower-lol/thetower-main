"""Creator Codes: every player with a creator code to support in the TechTree store.

One card per creator: in-game avatar, name (linking to their player page), the code with a copy button, what they're
known for (staff-curated categories, which visitors filter by), an optional staff note, and their links (curated by
staff, plus accounts the player linked and made public). Shown in a random order per visit, so the same creators
aren't always first; visitors can switch to A–Z.
"""

import random
from html import escape
from urllib.parse import urlencode

import streamlit as st

from thetower.backend.sus.models import CreatorCategory, KnownPlayer, PlayerId
from thetower.backend.sus.social import link_site_name, profile_accounts
from thetower.backend.tourney_results.data import get_banned_ids
from thetower.backend.tourney_results.formatting import BASE_URL
from thetower.backend.tourney_results.models import TourneyRow
from thetower.web.towermedia import avatar_img

STORE_URL = "https://store.techtreegames.com/thetower/"
COLUMNS_PER_ROW = 3
ORDER_SHUFFLED = "Shuffled"
ORDER_AZ = "A–Z"

CARD_CSS = """
<style>
.creator-head { display: flex; align-items: center; gap: 0.6rem; min-height: 48px; }
.creator-name { font-size: 1.1rem; font-weight: 600; }
.creator-tags { margin: 0.3rem 0 0.1rem; }
.creator-tag { display: inline-block; font-size: 0.75rem; padding: 0.05rem 0.5rem; margin: 0 0.25rem 0.25rem 0;
  border-radius: 999px; border: 1px solid rgba(128, 128, 128, 0.45); }
.creator-links { font-size: 0.9rem; }
</style>
"""


@st.cache_data(ttl=600, show_spinner=False)
def load_creators() -> list[dict]:
    """One entry per player with a creator code; players without a primary tower id, or whose primary game instance is banned, are left out."""
    players = list(
        KnownPlayer.objects.exclude(creator_code__isnull=True).exclude(creator_code="").prefetch_related("creator_categories", "creator_links")
    )
    primary_ids = dict(
        PlayerId.objects.filter(game_instance__player__in=[p.id for p in players], game_instance__primary=True, primary=True).values_list(
            "game_instance__player_id", "id"
        )
    )
    banned_ids = set(get_banned_ids())

    creators = []
    for player in players:
        tower_id = primary_ids.get(player.id)
        if not tower_id or tower_id in banned_ids:
            continue
        # The tower skin from their latest published tourney
        avatar_id = (
            TourneyRow.objects.filter(player_id=tower_id, result__public=True).order_by("-result__date").values_list("avatar_id", flat=True).first()
        )
        links = [{"text": link.label or link_site_name(link.url), "url": link.url} for link in player.creator_links.all()]
        curated_urls = {link["url"] for link in links}
        links += [
            {"text": account["text"], "url": account["url"]}
            for account in profile_accounts(player, include_private=False)
            if account["url"] and account["url"] not in curated_urls
        ]
        creators.append(
            {
                "name": player.name or "",
                "code": player.creator_code.strip(),
                "tower_id": tower_id,
                "avatar_id": avatar_id,
                "categories": [category.name for category in player.creator_categories.all()],
                "note": player.creator_note.strip(),
                "links": links,
            }
        )
    return creators


def _matches(creator: dict, query: str, chosen: list[str]) -> bool:
    """Whether a creator has any chosen category and contains the search text in its name, code, categories or note."""
    if chosen and not set(chosen) & set(creator["categories"]):
        return False
    if not query:
        return True
    haystack = " ".join([creator["name"], creator["code"], creator["note"], *creator["categories"]]).lower()
    return query.lower() in haystack


def _render_card(creator: dict) -> None:
    player_url = f"https://{BASE_URL}/player?" + urlencode({"player": creator["tower_id"]})
    avatar = avatar_img(creator["avatar_id"], size=48) if creator["avatar_id"] is not None else ""
    name = escape(creator["name"] or creator["code"])
    st.markdown(
        f"<div class='creator-head'>{avatar}<a class='creator-name' href='{escape(player_url)}' target='_self'>{name}</a></div>",
        unsafe_allow_html=True,
    )
    if creator["categories"]:
        tags = "".join(f"<span class='creator-tag'>{escape(category)}</span>" for category in creator["categories"])
        st.markdown(f"<div class='creator-tags'>{tags}</div>", unsafe_allow_html=True)
    # A code block for its built-in copy button
    st.code(creator["code"], language=None)
    if creator["note"]:
        st.markdown(f"<div>{escape(creator['note'])}</div>", unsafe_allow_html=True)
    if creator["links"]:
        links = " · ".join(f"<a href='{escape(link['url'])}' target='_blank'>{escape(link['text'])}</a>" for link in creator["links"])
        st.markdown(f"<div class='creator-links'>{links}</div>", unsafe_allow_html=True)


def get_creator_codes():
    st.markdown("# Creator Codes")
    st.markdown(f"Support a community creator by entering their code in the [TechTree Store]({STORE_URL}).")
    st.markdown(CARD_CSS, unsafe_allow_html=True)

    creators = load_creators()
    if not creators:
        st.info("No creator codes yet.")
        return

    # Only categories some listed creator has, in the order staff set
    used = {category for creator in creators for category in creator["categories"]}
    categories = [name for name in CreatorCategory.objects.values_list("name", flat=True) if name in used]

    search_col, order_col = st.columns([3, 1])
    query = search_col.text_input("Search", placeholder="Search by name, code or category", label_visibility="collapsed").strip()
    order = order_col.radio("Order", [ORDER_SHUFFLED, ORDER_AZ], horizontal=True, label_visibility="collapsed")
    chosen = st.pills("Known for", categories, selection_mode="multi") if categories else []

    shown = [creator for creator in creators if _matches(creator, query, chosen or [])]
    if order == ORDER_AZ:
        shown.sort(key=lambda creator: (creator["name"] or creator["code"]).lower())
    else:
        # One shuffle per visit: the order stays put while the visitor searches and filters
        seed = st.session_state.setdefault("creator_codes_shuffle_seed", random.randrange(2**32))
        shown.sort(key=lambda creator: creator["code"].lower())
        random.Random(seed).shuffle(shown)

    st.caption(f"{len(shown)} of {len(creators)} creators" if len(shown) != len(creators) else f"{len(creators)} creators")
    if not shown:
        st.info("No creator matches your search.")
        return

    for start in range(0, len(shown), COLUMNS_PER_ROW):
        columns = st.columns(COLUMNS_PER_ROW)
        for column, creator in zip(columns, shown[start : start + COLUMNS_PER_ROW]):
            with column.container(border=True):
                _render_card(creator)


get_creator_codes()
