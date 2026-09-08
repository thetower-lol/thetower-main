"""Moderation Visibility: the placement and public-display toggles for sus and shunned players."""

import streamlit as st

from thetower.backend.tourney_results import visibility
from thetower.backend.tourney_results.models import TourneyResult


def _requeue_all_tournaments() -> int:
    return TourneyResult.objects.update(needs_recalc=True, recalc_retry_count=0)


def main() -> None:
    st.title("Moderation Visibility")
    st.markdown("""
        Two decisions, stored in `visibility.json` under the data directory and read by every site process,
        the placement generators and the bot.

        **Placement** is about the data: whether sus and shunned players receive a position at import and on
        recalculation. Changing it queues every tournament for repositioning.

        **Public display** is about the viewer: whether the public site and the bot hide sus and shunned
        players. The hidden and admin sites always show everyone, with a badge on the player page.
        """)

    config = visibility.get_visibility()

    with st.form(key="visibility_form"):
        st.subheader("Placement (standings)")
        exclude_sus = st.checkbox("Exclude sus players from positions", value=config["placement"]["exclude_sus"])
        exclude_shun = st.checkbox("Exclude shunned players from positions", value=config["placement"]["exclude_shun"])
        st.caption("Hard-banned players never receive a position.")

        st.subheader("Public display (public site and bot)")
        hide_sus = st.checkbox("Hide sus players", value=config["public"]["hide_sus"])
        hide_shun = st.checkbox("Hide shunned players", value=config["public"]["hide_shun"])
        st.caption("Hard- and soft-banned players are always hidden on the public site and in the bot.")

        if st.form_submit_button("Save"):
            new_config = {
                "placement": {"exclude_sus": exclude_sus, "exclude_shun": exclude_shun},
                "public": {"hide_sus": hide_sus, "hide_shun": hide_shun},
            }
            visibility.save_visibility(new_config)
            if new_config["placement"] != config["placement"]:
                requeued = _requeue_all_tournaments()
                st.success(f"Saved. Placement changed, so {requeued} tournaments were queued for repositioning.")
            else:
                st.success("Saved. Site processes and the bot pick the change up on their next read.")
            st.rerun()

    st.subheader("Fixed rules")
    st.markdown("""
        - Public site and bot: hard- and soft-banned players are hidden from every page, live view, search result
          and wave statistic.
        - Hidden and admin sites: everyone is shown; the player page badges sus, shunned, soft-banned and
          hard-banned players. Banned rows appear unplaced in the results table.
        - Wave statistics exclude banned players on both sites.
        - A hard ban always removes the player from the standings; soft bans do not (open decision).
        """)

    st.divider()
    st.subheader("Requeue all tournaments for recalculation")
    st.markdown("Marks every tournament for repositioning by the recalc worker. Saving a placement change does this automatically.")
    with st.form(key="requeue_form"):
        confirmed = st.checkbox("I understand this will requeue all tournaments")
        if st.form_submit_button("Requeue all"):
            if confirmed:
                st.success(f"Marked {_requeue_all_tournaments()} tournaments for recalculation.")
            else:
                st.warning("Check the confirmation box first.")


if __name__ == "__main__":
    main()
