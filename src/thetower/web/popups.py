"""Site-wide announcement popups (SitePopup), shown on every page until the visitor acknowledges them.

Acknowledging (the button or the dialog's X) is remembered for the session and in a browser cookie that
expires when the popup ends. Visitors without the cookie see the popup again until its end date.
"""

import functools
import logging

import streamlit as st
from django.utils import timezone
from django.utils.http import http_date
from streamlit_js_eval import streamlit_js_eval

logger = logging.getLogger(__name__)

COOKIE_PREFIX = "thetower_popup_"

# Streamlit's dismissible dialog also closes on a click outside it and on Escape. Only the X and the button
# should acknowledge a popup, so while a site popup is open these swallow the events Streamlit closes on
# (a document mousedown outside the dialog, Escape keydown/keyup) before they reach it. Other dialogs are unaffected.
_DISMISS_GUARD_HTML = """
<span class="thetower-site-popup"></span>
<style>.stElementContainer:has(> .stHtml > .thetower-site-popup) { display: none; }</style>
<script>
if (!window.thetowerPopupGuard) {
    window.thetowerPopupGuard = true;
    const popupOpen = () => document.querySelector(".thetower-site-popup") !== null;
    window.addEventListener("mousedown", (e) => {
        if (popupOpen() && !(e.target instanceof Element && e.target.closest('[role="dialog"]'))) {
            e.stopPropagation();
        }
    }, true);
    for (const type of ["keydown", "keyup"]) {
        window.addEventListener(type, (e) => {
            if (popupOpen() && e.key === "Escape") {
                e.stopPropagation();
            }
        }, true);
    }
}
</script>
"""


@st.cache_data(ttl=60, show_spinner=False)
def _enabled_popups() -> list[dict]:
    """Enabled popups that have not ended, earliest start first. Cached briefly so admin edits show within a minute."""
    from thetower.backend.tourney_results.models import SitePopup  # pages.py imports this module before django.setup()

    return list(SitePopup.objects.filter(enabled=True, end_at__gt=timezone.now()).values("key", "title", "body", "start_at", "end_at"))


def _acknowledge(popup: dict) -> None:
    """Hide the popup for this session and queue the cookie that hides it in future sessions."""
    st.session_state.popups_acknowledged.add(popup["key"])
    st.session_state.popup_cookie_writes[popup["key"]] = http_date(popup["end_at"].timestamp())


def _open_dialog(popup: dict) -> None:
    def body() -> None:
        st.html(_DISMISS_GUARD_HTML, unsafe_allow_javascript=True)
        st.markdown(popup["body"])
        if st.button("Got it", type="primary", key=f"popup_ack_{popup['key']}"):
            _acknowledge(popup)
            st.rerun()

    st.dialog(popup["title"], width="medium", on_dismiss=functools.partial(_acknowledge, popup))(body)()


def show_site_popups() -> None:
    """Write any pending acknowledgment cookies, then open the earliest live popup this visitor hasn't acknowledged."""
    st.session_state.setdefault("popups_acknowledged", set())
    st.session_state.setdefault("popup_cookie_writes", {})

    for key, expires in st.session_state.popup_cookie_writes.items():
        streamlit_js_eval(
            js_expressions=(
                f"document.cookie = '{COOKIE_PREFIX}{key}=1; expires={expires}; path=/; SameSite=Lax'"
                " + (location.protocol === 'https:' ? '; Secure' : ''); true"
            ),
            key=f"popup_cookie_write_{key}",
        )
    st.session_state.popup_cookie_writes = {}

    try:
        popups = _enabled_popups()
    except Exception:
        logger.exception("Could not load site popups")
        return

    now = timezone.now()
    cookies = st.context.cookies
    for popup in popups:
        if not popup["start_at"] <= now < popup["end_at"]:
            continue
        if popup["key"] in st.session_state.popups_acknowledged or cookies.get(f"{COOKIE_PREFIX}{popup['key']}"):
            continue
        _open_dialog(popup)
        return
