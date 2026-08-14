import json
import time
from typing import Any, Callable, Dict

import streamlit as st

from thetower.backend.env_config import get_django_data
from thetower.backend.tourney_results.models import TourneyResult
from thetower.backend.tourney_results.shun_config import (
    get_cache_status,
    include_shun_enabled_for,
    include_shun_invalidate,
    read_mapping_from_disk,
)
from thetower.backend.tourney_results.sus_config import (
    get_sus_cache_status,
    include_sus_enabled_for,
    include_sus_invalidate,
    read_sus_mapping_from_disk,
)

# All page keys that can be toggled.  Union of shun- and sus-controlled surfaces.
PAGE_KEYS = [
    "comparison",
    "create_tourney_rows",
    "live_bracket",
    "live_bracket_analysis",
    "live_placement_cache",  # controls both Live Placement Analysis and Live Quantile Analysis
    "live_progress",
    "live_results",
    "namechangers",
    "overview_cache",
    "player",
    "reposition",
    "search",
    "tourney_results",
]


def _write_mapping_to_disk(filename: str, mapping: Dict[str, Any]) -> None:
    django_data = get_django_data()
    json_path = django_data / filename
    json_path.write_text(json.dumps(mapping, indent=2), encoding="utf8")


def _render_config_section(
    title: str,
    description: str,
    filename: str,
    file_mapping: Dict[str, Any],
    cache_status: Dict[str, Any],
    invalidate_fn: Callable,
    resolve_fn: Callable,
    rerun_key: str,
) -> None:
    st.subheader(title)
    st.markdown(description)

    # ── Edit form ─────────────────────────────────────────────────────────────
    st.markdown("**Edit configuration**")
    pages_on_disk = file_mapping.get("pages", {})
    default_on_disk = bool(file_mapping.get("default", False))

    with st.form(key=f"form_{rerun_key}"):
        new_default = st.checkbox("Default (apply to pages not listed below)", value=default_on_disk, key=f"default_{rerun_key}")
        st.markdown("Per-page overrides ('(not set)' inherits the default; Save writes only explicit true/false entries):")
        # Show all known keys; also show any extra keys present in the file
        all_keys = sorted(set(PAGE_KEYS) | set(pages_on_disk.keys()))
        options = ["(not set)", "true", "false"]
        new_pages: Dict[str, bool] = {}
        for page in all_keys:
            if page in pages_on_disk:
                current = "true" if pages_on_disk[page] else "false"
            else:
                current = "(not set)"
            choice = st.selectbox(page, options=options, index=options.index(current), key=f"{rerun_key}_{page}")
            if choice != "(not set)":
                new_pages[page] = choice == "true"

        if st.form_submit_button("Save"):
            new_mapping = {"default": new_default, "pages": new_pages}
            _write_mapping_to_disk(filename, new_mapping)
            invalidate_fn()
            st.success(f"Saved to {filename} and cache invalidated.")
            st.rerun()

    # ── Cache status ──────────────────────────────────────────────────────────
    st.markdown("**In-process cache status**")
    col1, col2 = st.columns([2, 1])
    with col1:
        st.write("Cached:", cache_status.get("cached"))
        expiry = cache_status.get("expiry")
        ttl = cache_status.get("ttl_remaining")
        if cache_status.get("cached"):
            st.write(f"Expires at: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(expiry))} (in {ttl:.0f}s)")
        else:
            st.write("No mapping currently cached")
    with col2:
        if st.button(f"Invalidate cache##{rerun_key}"):
            invalidate_fn()
            st.rerun()

    # ── Read-only resolved values ─────────────────────────────────────────────
    st.markdown("**Resolved values (from cache / disk)**")
    rows = []
    for p in sorted(set(PAGE_KEYS) | set(pages_on_disk.keys())):
        on_disk = pages_on_disk.get(p)
        rows.append({
            "page": p,
            "in file": "(not set)" if on_disk is None else str(bool(on_disk)).lower(),
            "resolved": resolve_fn(p),
        })
    st.table(rows)


def main() -> None:
    st.title("Moderation Visibility Config")

    st.markdown("""
        Controls per-page visibility for **shunned** and **sus** players.
        Check a box and click **Save** to update the JSON file and invalidate the in-process cache.

        Notes:
        - `live_placement_cache` controls both "Live Placement Analysis" and "Live Quantile Analysis".
        - The player search box follows the page it is embedded on (`player`, `comparison`);
          `search` covers the standalone search page.
        - For `tourney_results`, `include_sus` only affects players re-ranked via a reposition run
          with sus enabled.
        """)

    # ── Shun config ──────────────────────────────────────────────────────────
    _render_config_section(
        title="Shun (`include_shun.json`)",
        description="Controls whether shunned players are shown on each page.",
        filename="include_shun.json",
        file_mapping=read_mapping_from_disk(),
        cache_status=get_cache_status(),
        invalidate_fn=include_shun_invalidate,
        resolve_fn=include_shun_enabled_for,
        rerun_key="shun",
    )

    st.divider()

    # ── Sus config ────────────────────────────────────────────────────────────
    _render_config_section(
        title="Sus (`include_sus.json`)",
        description="Controls whether sus players are shown on each page.",
        filename="include_sus.json",
        file_mapping=read_sus_mapping_from_disk(),
        cache_status=get_sus_cache_status(),
        invalidate_fn=include_sus_invalidate,
        resolve_fn=include_sus_enabled_for,
        rerun_key="sus",
    )

    st.divider()

    # ── Requeue all tournaments ───────────────────────────────────────────────
    st.subheader("Requeue all tournaments for recalculation")
    st.markdown(
        "Marks every `TourneyResult` as `needs_recalc=True` so the `process_recalc_queue` "
        "worker will reposition all tournaments.  Use this after toggling the **reposition** "
        "flag above and saving."
    )
    total = TourneyResult.objects.count()
    st.write(f"Total TourneyResults in DB: **{total}**")
    with st.form(key="requeue_form"):
        confirmed = st.checkbox("I understand this will requeue all tournaments")
        if st.form_submit_button("Requeue all"):
            if confirmed:
                updated = TourneyResult.objects.update(needs_recalc=True)
                st.success(f"Marked {updated} tournaments as needs_recalc=True.")
            else:
                st.warning("Check the confirmation box first.")


if __name__ == "__main__":
    main()
