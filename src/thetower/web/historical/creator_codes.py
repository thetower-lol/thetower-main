"""Creator Codes: every player with a creator code to support in the TechTree store.

One card per creator: in-game avatar, name (linking to their player page), the code with a copy button, what they're
known for (staff-curated categories, which visitors filter by), an optional staff note, and their links (curated by
staff, plus accounts the player linked and made public). Shown in a random order per visit, so the same creators
aren't always first; visitors can switch to A–Z.
"""

import random
from html import escape
from urllib.parse import quote, urlencode

import streamlit as st

from thetower.backend.sus.models import CreatorCategory, KnownPlayer, PlayerId
from thetower.backend.sus.social import link_site_name, profile_accounts
from thetower.backend.tourney_results.data import get_banned_ids
from thetower.backend.tourney_results.formatting import BASE_URL
from thetower.backend.tourney_results.models import TourneyRow
from thetower.web.towermedia import avatar_img

STORE_URL = "https://store.techtreegames.com/thetower/"
ORDER_SHUFFLED = "Shuffled"
ORDER_AZ = "A–Z"

# One CSS grid for all cards, so every card in a row is the same height and the codes line up along the bottom
CARD_CSS = """
<style>
.creator-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 16px; }
.creator-card { display: flex; flex-direction: column; gap: 12px; padding: 20px; border-radius: 12px;
  background: #161922; border: 1px solid rgba(250, 250, 250, 0.08); }
.creator-head { display: flex; align-items: center; gap: 12px; min-width: 0; }
.creator-name { flex: 1; font-size: 17px; font-weight: 500; color: #fafafa; text-decoration: none; overflow-wrap: anywhere; }
.creator-name:hover { text-decoration: underline; }
.creator-icons { display: flex; gap: 4px; }
.creator-icons a { display: flex; align-items: center; justify-content: center; width: 30px; height: 30px;
  border-radius: 8px; color: #8b90a0; }
.creator-icons a:hover { color: #52b9e4; background: rgba(82, 185, 228, 0.1); }
.creator-icon { width: 17px; height: 17px; background: currentColor; mask: center / contain no-repeat; }
.creator-tags { display: flex; flex-wrap: wrap; gap: 6px; }
.creator-tag { font-size: 12px; line-height: 1; padding: 5px 8px; border-radius: 999px; color: #b4b8c5;
  background: rgba(250, 250, 250, 0.05); }
.creator-note { font-size: 14px; line-height: 1.5; color: #c4c7d0; }
.creator-links { display: flex; flex-wrap: wrap; gap: 4px 14px; font-size: 14px; }
.creator-links a { color: #52b9e4; text-decoration: none; }
.creator-links a:hover { text-decoration: underline; }
.creator-code { margin-top: auto; display: flex; align-items: center; justify-content: space-between; gap: 12px;
  width: 100%; padding: 10px 12px; border-radius: 8px; border: 1px solid rgba(250, 250, 250, 0.08);
  background: #0e1117; color: #fafafa; cursor: pointer; font: inherit; text-align: left; }
.creator-code:hover { border-color: rgba(82, 185, 228, 0.5); }
.creator-code code { font-family: "Source Code Pro", monospace; font-size: 15px; background: none; color: inherit;
  padding: 0; overflow-wrap: anywhere; }
.creator-copy { font-size: 12px; color: #8b90a0; white-space: nowrap; }
.creator-copy::after { content: "Copy"; }
.creator-code.copied .creator-copy::after { content: "Copied"; color: #52b9e4; }
</style>
"""

# Logos for links to these sites, shown as icons beside the name; links to any other site are written out under the note.
# 24x24 paths from Simple Icons (CC0), keyed by link_site_name
BRAND_ICONS = {
    "YouTube": (
        "M23.498 6.186a3.016 3.016 0 0 0-2.122-2.136C19.505 3.545 12 3.545 12 3.545s-7.505 0-9.377.505A3.017 3.017 0 0 0 .502 6.1"
        "86C0 8.07 0 12 0 12s0 3.93.502 5.814a3.016 3.016 0 0 0 2.122 2.136c1.871.505 9.376.505 9.376.505s7.505 0 9.377-.505a3.01"
        "5 3.015 0 0 0 2.122-2.136C24 15.93 24 12 24 12s0-3.93-.502-5.814zM9.545 15.568V8.432L15.818 12l-6.273 3.568z"
    ),
    "Twitch": (
        "M11.571 4.714h1.715v5.143H11.57zm4.715 0H18v5.143h-1.714zM6 0L1.714 4.286v15.428h5.143V24l4.286-4.286h3.428L22.286 12V0z"
        "m14.571 11.143l-3.428 3.428h-3.429l-3 3v-3H6.857V1.714h13.714Z"
    ),
    "Discord": (
        "M20.317 4.3698a19.7913 19.7913 0 00-4.8851-1.5152.0741.0741 0 00-.0785.0371c-.211.3753-.4447.8648-.6083 1.2495-1.8447-.2"
        "762-3.68-.2762-5.4868 0-.1636-.3933-.4058-.8742-.6177-1.2495a.077.077 0 00-.0785-.037 19.7363 19.7363 0 00-4.8852 1.515."
        "0699.0699 0 00-.0321.0277C.5334 9.0458-.319 13.5799.0992 18.0578a.0824.0824 0 00.0312.0561c2.0528 1.5076 4.0413 2.4228 5"
        ".9929 3.0294a.0777.0777 0 00.0842-.0276c.4616-.6304.8731-1.2952 1.226-1.9942a.076.076 0 00-.0416-.1057c-.6528-.2476-1.27"
        "43-.5495-1.8722-.8923a.077.077 0 01-.0076-.1277c.1258-.0943.2517-.1923.3718-.2914a.0743.0743 0 01.0776-.0105c3.9278 1.79"
        "33 8.18 1.7933 12.0614 0a.0739.0739 0 01.0785.0095c.1202.099.246.1981.3728.2924a.077.077 0 01-.0066.1276 12.2986 12.2986"
        " 0 01-1.873.8914.0766.0766 0 00-.0407.1067c.3604.698.7719 1.3628 1.225 1.9932a.076.076 0 00.0842.0286c1.961-.6067 3.9495"
        "-1.5219 6.0023-3.0294a.077.077 0 00.0313-.0552c.5004-5.177-.8382-9.6739-3.5485-13.6604a.061.061 0 00-.0312-.0286zM8.02 1"
        "5.3312c-1.1825 0-2.1569-1.0857-2.1569-2.419 0-1.3332.9555-2.4189 2.157-2.4189 1.2108 0 2.1757 1.0952 2.1568 2.419 0 1.33"
        "32-.9555 2.4189-2.1569 2.4189zm7.9748 0c-1.1825 0-2.1569-1.0857-2.1569-2.419 0-1.3332.9554-2.4189 2.1569-2.4189 1.2108 0"
        " 2.1757 1.0952 2.1568 2.419 0 1.3332-.946 2.4189-2.1568 2.4189Z"
    ),
    "Reddit": (
        "M12 0C5.373 0 0 5.373 0 12c0 3.314 1.343 6.314 3.515 8.485l-2.286 2.286C.775 23.225 1.097 24 1.738 24H12c6.627 0 12-5.37"
        "3 12-12S18.627 0 12 0Zm4.388 3.199c1.104 0 1.999.895 1.999 1.999 0 1.105-.895 2-1.999 2-.946 0-1.739-.657-1.947-1.539v.0"
        "02c-1.147.162-2.032 1.15-2.032 2.341v.007c1.776.067 3.4.567 4.686 1.363.473-.363 1.064-.58 1.707-.58 1.547 0 2.802 1.254"
        " 2.802 2.802 0 1.117-.655 2.081-1.601 2.531-.088 3.256-3.637 5.876-7.997 5.876-4.361 0-7.905-2.617-7.998-5.87-.954-.447-"
        "1.614-1.415-1.614-2.538 0-1.548 1.255-2.802 2.803-2.802.645 0 1.239.218 1.712.585 1.275-.79 2.881-1.291 4.64-1.365v-.01c"
        "0-1.663 1.263-3.034 2.88-3.207.188-.911.993-1.595 1.959-1.595Zm-8.085 8.376c-.784 0-1.459.78-1.506 1.797-.047 1.016.64 1"
        ".429 1.426 1.429.786 0 1.371-.369 1.418-1.385.047-1.017-.553-1.841-1.338-1.841Zm7.406 0c-.786 0-1.385.824-1.338 1.841.04"
        "7 1.017.634 1.385 1.418 1.385.785 0 1.473-.413 1.426-1.429-.046-1.017-.721-1.797-1.506-1.797Zm-3.703 4.013c-.974 0-1.907"
        ".048-2.77.135-.147.015-.241.168-.183.305.483 1.154 1.622 1.964 2.953 1.964 1.33 0 2.47-.81 2.953-1.964.057-.137-.037-.29"
        "-.184-.305-.863-.087-1.795-.135-2.769-.135Z"
    ),
    "X": (
        "M18.901 1.153h3.68l-8.04 9.19L24 22.846h-7.406l-5.8-7.584-6.638 7.584H.474l8.6-9.83L0 1.154h7.594l5.243 6.932ZM17.61 20."
        "644h2.039L6.486 3.24H4.298Z"
    ),
    "TikTok": (
        "M12.525.02c1.31-.02 2.61-.01 3.91-.02.08 1.53.63 3.09 1.75 4.17 1.12 1.11 2.7 1.62 4.24 1.79v4.03c-1.44-.05-2.89-.35-4.2"
        "-.97-.57-.26-1.1-.59-1.62-.93-.01 2.92.01 5.84-.02 8.75-.08 1.4-.54 2.79-1.35 3.94-1.31 1.92-3.58 3.17-5.91 3.21-1.43.08"
        "-2.86-.31-4.08-1.03-2.02-1.19-3.44-3.37-3.65-5.71-.02-.5-.03-1-.01-1.49.18-1.9 1.12-3.72 2.58-4.96 1.66-1.44 3.98-2.13 6"
        ".15-1.72.02 1.48-.04 2.96-.04 4.44-.99-.32-2.15-.23-3.02.37-.63.41-1.11 1.04-1.36 1.75-.21.51-.15 1.07-.14 1.61.24 1.64 "
        "1.82 3.02 3.5 2.87 1.12-.01 2.19-.66 2.77-1.61.19-.33.4-.67.41-1.06.1-1.79.06-3.57.07-5.36.01-4.03-.01-8.05.02-12.07z"
    ),
    "GitHub": (
        "M12 .297c-6.63 0-12 5.373-12 12 0 5.303 3.438 9.8 8.205 11.385.6.113.82-.258.82-.577 0-.285-.01-1.04-.015-2.04-3.338.724"
        "-4.042-1.61-4.042-1.61C4.422 18.07 3.633 17.7 3.633 17.7c-1.087-.744.084-.729.084-.729 1.205.084 1.838 1.236 1.838 1.236"
        " 1.07 1.835 2.809 1.305 3.495.998.108-.776.417-1.305.76-1.605-2.665-.3-5.466-1.332-5.466-5.93 0-1.31.465-2.38 1.235-3.22"
        "-.135-.303-.54-1.523.105-3.176 0 0 1.005-.322 3.3 1.23.96-.267 1.98-.399 3-.405 1.02.006 2.04.138 3 .405 2.28-1.552 3.28"
        "5-1.23 3.285-1.23.645 1.653.24 2.873.12 3.176.765.84 1.23 1.91 1.23 3.22 0 4.61-2.805 5.625-5.475 5.92.42.36.81 1.096.81"
        " 2.22 0 1.606-.015 2.896-.015 3.286 0 .315.21.69.825.57C20.565 22.092 24 17.592 24 12.297c0-6.627-5.373-12-12-12"
    ),
    "Kick": "M1.333 0h8v5.333H12V2.667h2.667V0h8v8H20v2.667h-2.667v2.666H20V16h2.667v8h-8v-2.667H12v-2.666H9.333V24h-8Z",
}


def _icon_rule(site: str, path: str) -> str:
    """CSS that masks a .creator-icon to the site's logo, so it's painted in the link's text colour (st.html strips inline <svg>)."""
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="{path}"/></svg>'
    return f'.creator-icon-{site.lower()} {{ mask-image: url("data:image/svg+xml,{quote(svg)}"); }}'


ICON_CSS = "<style>" + "".join(_icon_rule(site, path) for site, path in BRAND_ICONS.items()) + "</style>"

# Streamlit strips inline onclick handlers, so one listener on the document handles every copy button; the flag stops
# reruns from adding it again
COPY_SCRIPT = """
<script>
if (!window.creatorCopyListener) {
  window.creatorCopyListener = true;
  document.addEventListener("click", (event) => {
    const button = event.target.closest(".creator-code");
    if (!button) return;
    navigator.clipboard.writeText(button.dataset.code);
    button.classList.add("copied");
    setTimeout(() => button.classList.remove("copied"), 1500);
  });
}
</script>
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
        links = [
            {"text": link.label or link_site_name(link.url), "url": link.url, "site": link_site_name(link.url)} for link in player.creator_links.all()
        ]
        curated_urls = {link["url"] for link in links}
        links += [
            {"text": account["text"], "url": account["url"], "site": link_site_name(account["url"])}
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


def _card_html(creator: dict) -> str:
    player_url = f"https://{BASE_URL}/player?" + urlencode({"player": creator["tower_id"]})
    avatar = avatar_img(creator["avatar_id"], size=40) if creator["avatar_id"] is not None else ""
    name = escape(creator["name"] or creator["code"])
    # Links are logos and categories are words, so "YouTube" as something they're known for never reads like a link
    icons = "".join(
        f"<a href='{escape(link['url'])}' target='_blank' title='{escape(link['text'])}' aria-label='{escape(link['text'])}'>"
        f"<span class='creator-icon creator-icon-{link['site'].lower()}'></span></a>"
        for link in creator["links"]
        if link["site"] in BRAND_ICONS
    )
    parts = [
        f"<div class='creator-head'>{avatar}<a class='creator-name' href='{escape(player_url)}' target='_self'>{name}</a>"
        f"<div class='creator-icons'>{icons}</div></div>"
    ]
    if creator["categories"]:
        tags = "".join(f"<span class='creator-tag'>{escape(category)}</span>" for category in creator["categories"])
        parts.append(f"<div class='creator-tags'>{tags}</div>")
    if creator["note"]:
        parts.append(f"<div class='creator-note'>{escape(creator['note'])}</div>")
    other_links = [link for link in creator["links"] if link["site"] not in BRAND_ICONS]
    if other_links:
        link_html = "".join(f"<a href='{escape(link['url'])}' target='_blank'>{escape(link['text'])} ↗</a>" for link in other_links)
        parts.append(f"<div class='creator-links'>{link_html}</div>")
    code = escape(creator["code"])
    parts.append(
        f"<button type='button' class='creator-code' data-code='{code}' title='Copy code'>"
        f"<code>{code}</code><span class='creator-copy'></span></button>"
    )
    return f"<div class='creator-card'>{''.join(parts)}</div>"


def get_creator_codes():
    st.markdown("# Creator Codes")
    st.markdown(f"Support a community creator by entering their code in the [TechTree Store]({STORE_URL}).")

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

    # JavaScript only for the copy buttons; every value in the cards is escaped
    cards = "".join(_card_html(creator) for creator in shown)
    st.html(f"{CARD_CSS}{ICON_CSS}<div class='creator-grid'>{cards}</div>{COPY_SCRIPT}", unsafe_allow_javascript=True)


get_creator_codes()
