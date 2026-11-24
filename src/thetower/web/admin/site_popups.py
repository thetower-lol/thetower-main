"""
Site Popups admin page for the Tower hidden site.

Create, edit, preview and retire the one-time announcement popups (SitePopup) shown on every public page.
"""

import datetime
import logging
import re

import streamlit as st
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.text import slugify

from thetower.backend.tourney_results.models import SitePopup
from thetower.web.popups import clear_popup_cache, open_popup_dialog, render_popup_body

logger = logging.getLogger(__name__)

UTC = datetime.timezone.utc

# A pair of unescaped dollar signs on one line renders as LaTeX math in Streamlit Markdown.
_UNESCAPED_DOLLAR = re.compile(r"(?<!\\)\$")
# Streamlit Markdown directives: :red[text], :blue-background[text], :green-badge[text] and :icon: / :material/icon:.
_DIRECTIVE = re.compile(r":[a-z]+(?:-[a-z]+)*\[([^\]]*)\]")
_SHORTCODE = re.compile(r":[\w/-]+:")


def _suggest_key(title: str) -> str:
    """A cookie-safe key from the title's plain text."""
    plain = _SHORTCODE.sub("", _DIRECTIVE.sub(r"\1", title))
    return slugify(plain)[:50].strip("-")


def _unique_key(key: str, own_pk: int | None) -> str:
    """`key`, or key-2, key-3, ... when another popup has it or ever had it.

    Deleted and renamed popups count (via the history table): visitors may still hold cookies for their old keys,
    which would hide a new popup that reused one.
    """
    if not key:
        return key
    used = set(SitePopup.history.exclude(id=own_pk).values_list("key", flat=True))
    used |= set(SitePopup.objects.exclude(pk=own_pk).values_list("key", flat=True))
    candidate, n = key, 1
    while candidate in used:
        n += 1
        suffix = f"-{n}"
        candidate = key[: 50 - len(suffix)].rstrip("-") + suffix
    return candidate


MARKDOWN_HELP = r"""
| Type this | To get |
|---|---|
| `**bold**`, `*italic*`, `~~strike~~` | **bold**, *italic*, ~~strike~~ |
| `[a link](https://thetower.lol)` | [a link](https://thetower.lol) |
| `:red[red text]` (also blue, green, orange, violet, gray, rainbow) | :red[red text] |
| `:blue-background[highlight]` | :blue-background[highlight] |
| `:green-badge[New]` | :green-badge[New] |
| `:tada:` (emoji shortcode) or paste an emoji | :tada: |
| `:small[fine print]` | :small[fine print] |

Lists start lines with `- `; headings with `## `; a line of `---` draws a divider.
Write `\$5`, not `$5`: two plain dollar signs on a line turn the text between them into a math formula.
The title takes bold, italic, colors, emoji and icons, but not headings or lists.
"""


def _status(popup: SitePopup, now: datetime.datetime) -> str:
    if not popup.enabled:
        return "⚪ Disabled"
    if popup.end_at <= now:
        return "⚫ Ended"
    if popup.start_at > now:
        return "🔵 Scheduled"
    return "🟢 Live"


def _fmt(dt: datetime.datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%d %H:%M")


def _field_key(target: str, field: str) -> str:
    return f"site_popup_{target}_{field}"


def _start_editing(target: str) -> None:
    """Open the editor on a popup (its pk as a string) or a new one ("new"), loading its values into the form."""
    if target == "new":
        start = datetime.datetime.combine(timezone.now().astimezone(UTC).date() + datetime.timedelta(days=1), datetime.time(0, 0))
        values = {
            "title": "",
            "key": "",
            "body": "",
            "start_date": start.date(),
            "start_time": start.time(),
            "end_date": (start + datetime.timedelta(days=7)).date(),
            "end_time": start.time(),
            "enabled": True,
        }
    else:
        popup = SitePopup.objects.get(pk=int(target))
        start, end = popup.start_at.astimezone(UTC), popup.end_at.astimezone(UTC)
        values = {
            "title": popup.title,
            "key": popup.key,
            "body": popup.body,
            "start_date": start.date(),
            "start_time": start.time().replace(second=0, microsecond=0),
            "end_date": end.date(),
            "end_time": end.time().replace(second=0, microsecond=0),
            "enabled": popup.enabled,
        }
    for field, value in values.items():
        st.session_state[_field_key(target, field)] = value
    st.session_state.site_popup_editing = target


def _stop_editing() -> None:
    st.session_state.site_popup_editing = None


def _escape_dollars(body_key: str) -> None:
    st.session_state[body_key] = _UNESCAPED_DOLLAR.sub(r"\\$", st.session_state[body_key])


def _render_list(now: datetime.datetime) -> None:
    popups = list(SitePopup.objects.order_by("-start_at"))
    current = [p for p in popups if p.end_at > now]
    ended = [p for p in popups if p.end_at <= now]

    st.subheader("Current and upcoming")
    if not current:
        st.caption("No live or scheduled popups.")
    for popup in current:
        _render_list_row(popup, now)

    if ended:
        with st.expander(f"Ended ({len(ended)})"):
            for popup in ended:
                _render_list_row(popup, now)


def _render_list_row(popup: SitePopup, now: datetime.datetime) -> None:
    with st.container(border=True):
        info_col, button_col = st.columns([5, 1], vertical_alignment="center")
        with info_col:
            st.markdown(f"**{popup.title}** &nbsp; {_status(popup, now)}")
            st.caption(f"`{popup.key}` · {_fmt(popup.start_at)} → {_fmt(popup.end_at)} UTC")
        with button_col:
            st.button("Edit", key=f"site_popup_edit_{popup.pk}", on_click=_start_editing, args=(str(popup.pk),), width="stretch")


def _render_editor(target: str, now: datetime.datetime) -> None:
    existing = None if target == "new" else SitePopup.objects.filter(pk=int(target)).first()
    if target != "new" and existing is None:
        st.warning("That popup no longer exists.")
        _stop_editing()
        return

    def k(field: str) -> str:
        return _field_key(target, field)

    st.subheader(f"Editing: {existing.title}" if existing else "New popup")

    title = st.text_input("Title", key=k("title"), max_chars=100)
    suggested_key = _suggest_key(title)
    key = (
        st.text_input(
            "Key",
            key=k("key"),
            max_chars=50,
            placeholder=suggested_key or "letters-numbers-and-dashes",
            help="Names the cookie that remembers who acknowledged this popup. Leave blank to use the title. "
            "A number is added if another popup has used it. Changing it later shows the popup again to everyone.",
        ).strip()
        or suggested_key
    )
    resolved_key = _unique_key(key, existing.pk if existing else None)
    if resolved_key != key:
        st.info(f"Another popup has used `{key}`, so this one saves as `{resolved_key}`.")
        key = resolved_key
    if existing and key != existing.key:
        st.warning(f"Changing the key from `{existing.key}` shows this popup again to everyone who already acknowledged it.")

    start_col, end_col = st.columns(2)
    with start_col:
        start_date = st.date_input("Start date (UTC)", key=k("start_date"))
        start_time = st.time_input("Start time (UTC)", key=k("start_time"), step=300)
    with end_col:
        end_date = st.date_input("End date (UTC)", key=k("end_date"))
        end_time = st.time_input("End time (UTC)", key=k("end_time"), step=300)
    start_at = datetime.datetime.combine(start_date, start_time, tzinfo=UTC)
    end_at = datetime.datetime.combine(end_date, end_time, tzinfo=UTC)
    enabled = st.toggle("Enabled", key=k("enabled"), help="Turn off to stop showing the popup without changing its dates.")

    if existing and existing.start_at <= now and end_at > existing.end_at:
        st.warning(
            f"This popup has already been shown. Visitors who acknowledged it have a cookie that expires at the old end "
            f"({_fmt(existing.end_at)} UTC), so they will see it again between then and the new end."
        )

    edit_col, preview_col = st.columns(2)
    with edit_col:
        body = st.text_area("Message (Markdown)", key=k("body"), height=320)
        with st.expander("Formatting help"):
            st.markdown(MARKDOWN_HELP)
            # Shown as code: Streamlit's Markdown rewrites this icon syntax even inside backticks.
            st.markdown("Icons :material/campaign: use a name from the [Material icon list](https://fonts.google.com/icons):")
            st.code(":material/campaign:", language=None)
    with preview_col:
        st.markdown("**Preview**")
        with st.container(border=True):
            st.markdown(f"#### {title or '(no title)'}")
            render_popup_body(body or "*(no message)*")

    unescaped = [line for line in body.splitlines() if len(_UNESCAPED_DOLLAR.findall(line)) >= 2]
    if unescaped:
        st.warning("A line has two or more plain `$` signs, which turns the text between them into a math formula.")
        st.button("Escape the dollar signs", key=f"site_popup_escape_{target}", on_click=_escape_dollars, args=(k("body"),))

    save_col, preview_btn_col, cancel_col, delete_col = st.columns(4)
    with save_col:
        save = st.button("💾 Save", type="primary", width="stretch", key=f"site_popup_save_{target}")
    with preview_btn_col:
        preview = st.button(
            "👁️ Preview", width="stretch", help="Open the real popup. Closing it acknowledges nothing.", key=f"site_popup_preview_{target}"
        )
    with cancel_col:
        st.button("Cancel", width="stretch", key=f"site_popup_cancel_{target}", on_click=_stop_editing)
    if existing:
        with delete_col:
            with st.popover("🗑️ Delete", width="stretch"):
                st.markdown("Permanently delete this popup? To stop showing it but keep it, turn off **Enabled** instead.")
                if st.button("Delete permanently", type="primary", key=f"site_popup_delete_{target}"):
                    logger.info("Site popup %s deleted from the admin page", existing.key)
                    existing.delete()
                    clear_popup_cache()
                    st.session_state.site_popup_flash = f"Deleted **{existing.title}**."
                    _stop_editing()
                    st.rerun()

    if preview:
        open_popup_dialog({"key": f"preview-{target}", "title": title or "(no title)", "body": body or "*(no message)*"}, preview=True)

    if save:
        popup = existing or SitePopup()
        popup.title = title.strip()
        popup.key = key
        popup.body = body
        popup.start_at = start_at
        popup.end_at = end_at
        popup.enabled = enabled
        try:
            popup.full_clean()
        except ValidationError as exc:
            for field, messages in exc.message_dict.items():
                label = "" if field == "__all__" else f"**{field.replace('_', ' ').capitalize()}:** "
                st.error(label + " ".join(messages))
            return
        popup.save()
        logger.info("Site popup %s saved from the admin page", popup.key)
        clear_popup_cache()
        st.session_state.site_popup_flash = f"Saved **{popup.title}**. The public site shows the change within a minute."
        _stop_editing()
        st.rerun()


def main() -> None:
    st.title("📣 Site Popups")
    st.markdown(
        "One-time announcements shown as a popup on every page of the site between their start and end (UTC). "
        "Each visitor sees a popup until they close it; one popup shows at a time, earliest start first."
    )

    if flash := st.session_state.pop("site_popup_flash", None):
        st.success(flash)

    now = timezone.now()
    target = st.session_state.get("site_popup_editing")
    if target:
        _render_editor(target, now)
    else:
        st.button("➕ New popup", type="primary", on_click=_start_editing, args=("new",))
        _render_list(now)


main()
