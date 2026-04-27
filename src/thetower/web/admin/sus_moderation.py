import os
from pathlib import Path

import pandas as pd
import requests
import streamlit as st
from django.db.models import Max
from django.utils import timezone

from thetower.backend.sus.models import ModerationRecord
from thetower.backend.tourney_results.data import get_player_id_lookup
from thetower.backend.tourney_results.models import TourneyRow
from thetower.web.util import fmt_dt

ZENDESK_VERDICT_FIELD_ID = 41952257656987


@st.dialog("Zendesk Result", width="large")
def show_zendesk_result(success: bool, title: str, message: str) -> None:
    if success:
        st.success(title)
    else:
        st.error(title)
    st.markdown(message)
    if st.button("✅ Close & Refresh", width="stretch", type="primary"):
        st.cache_data.clear()
        st.rerun()


def _do_create_ticket(record_pk: int, tower_id: str, created_by: str, reason: str) -> tuple[bool, str, str]:
    """Create a Zendesk ticket for a sus record and save the ID back to the record."""
    from thetower.backend.zendesk_utils import ZendeskError, create_sus_report_ticket

    try:
        result = create_sus_report_ticket(
            player_id=tower_id,
            reporter=created_by,
            reason="Suspicious",
            details=reason,
        )
        ticket_id = result["ticket"]["id"]
        record = ModerationRecord.objects.get(pk=record_pk)
        record.zendesk_ticket_id = ticket_id
        record.needs_zendesk_ticket = False
        record.save(update_fields=["zendesk_ticket_id", "needs_zendesk_ticket"])
        return True, f"Ticket #{ticket_id} created", f"Zendesk ticket **#{ticket_id}** created for player `{tower_id}`."
    except ZendeskError as e:
        return False, "Failed to create ticket", str(e)
    except Exception as e:
        return False, "Error", str(e)


def _do_check_status(record_pk: int, tower_id: str, ticket_id: int) -> tuple[bool, str, str]:
    """Poll Zendesk for ticket verdict and act on it."""
    base_url = os.getenv("ZENDESK_BASE_URL", "https://techtreegames.zendesk.com")
    auth_token = os.getenv("ZENDESK_AUTH_TOKEN")
    if not auth_token:
        return False, "Missing configuration", "ZENDESK\\_AUTH\\_TOKEN environment variable is not set."

    try:
        r = requests.get(
            f"{base_url}/api/v2/tickets/{ticket_id}.json",
            headers={"Authorization": f"Basic {auth_token}", "Content-Type": "application/json"},
            timeout=10,
        )
    except requests.RequestException as e:
        return False, "Network error", str(e)

    if not r.ok:
        return False, f"Zendesk API error ({r.status_code})", r.text

    ticket = r.json()["ticket"]
    status = ticket["status"]
    verdict = next((cf["value"] for cf in ticket.get("custom_fields", []) if cf["id"] == ZENDESK_VERDICT_FIELD_ID), None)

    lines = [
        f"**Ticket #{ticket_id}** — Status: `{status}`",
        f"**Verdict field:** `{verdict or 'not set'}`",
        "",
    ]

    if status not in ("solved", "closed"):
        lines.append("ℹ️ Ticket is still open — no action taken.")
        return True, f"Ticket #{ticket_id}: {status}", "\n\n".join(lines)

    if verdict is None:
        lines.append("ℹ️ No verdict set on ticket — no action taken.")
        return True, f"Ticket #{ticket_id}: no verdict", "\n\n".join(lines)

    try:
        record = ModerationRecord.objects.get(pk=record_pk)
    except ModerationRecord.DoesNotExist:
        lines.append("⚠️ Sus record no longer exists — nothing to act on.")
        return True, "Record already gone", "\n\n".join(lines)

    if verdict == "sus":
        # Resolve sus record with audit note (mirrors create_for_api ban path)
        resolution_info = f"Automatically resolved due to ban escalation (Zendesk ticket #{ticket_id} verdict: sus)"
        if record.reason:
            record.reason = f"{record.reason}\n\n{resolution_info}"
        else:
            record.reason = resolution_info
        record.save(update_fields=["reason"])
        record.resolve()

        new_ban = ModerationRecord.objects.create(
            tower_id=tower_id,
            moderation_type=ModerationRecord.ModerationType.BAN,
            source=ModerationRecord.ModerationSource.AUTOMATED,
            game_instance=record.game_instance,
            reason=f"Banned via Zendesk ticket #{ticket_id} (verdict: sus)",
            needs_zendesk_ticket=False,
            started_at=timezone.now(),
        )
        new_ban._queue_recalculation()
        lines.append(f"🔨 Player `{tower_id}` has been **banned** and sus record resolved.")
        return True, f"Player {tower_id} banned", "\n\n".join(lines)

    if verdict == "not_sus":
        record.resolve()
        lines.append(f"✅ Sus record for `{tower_id}` resolved — player cleared.")
        return True, f"Player {tower_id} cleared", "\n\n".join(lines)

    lines.append(f"⚠️ Unrecognised verdict `{verdict}` — no action taken.")
    return True, f"Unknown verdict: {verdict}", "\n\n".join(lines)


@st.cache_data(ttl=300)  # Cache for 5 minutes
def get_sus_moderation_raw_data():
    """Get all people with active sus moderation and their last tournament participation - DATA ONLY."""

    # Get all active sus moderation records
    sus_records = (
        ModerationRecord.objects.filter(moderation_type="sus", resolved_at__isnull=True)  # Active records only
        .select_related("game_instance__player")
        .order_by("-created_at")
    )

    if not sus_records.exists():
        return None

    sus_data = []
    tower_ids = []

    for record in sus_records:
        if record.game_instance:
            player_name = f"{record.game_instance.player.name} ({record.game_instance.name})"
        else:
            player_name = "Unverified Player"

        sus_data.append(
            {
                "record_pk": record.pk,
                "tower_id": record.tower_id,
                "player_name": player_name,
                "created_at": record.created_at,
                "created_by": record.created_by_display,
                "reason": record.reason or "No reason provided",
                "started_at": record.started_at,
                "zendesk_ticket_id": record.zendesk_ticket_id,
            }
        )

        tower_ids.append(record.tower_id)

    # Get last tournament participation for each tower_id
    # Get the most recent tournament result for each player
    last_tournaments = (
        TourneyRow.objects.filter(player_id__in=tower_ids, result__public=True).values("player_id").annotate(last_tournament_date=Max("result__date"))
    )

    # Convert to dict for easy lookup
    last_tournament_lookup = {item["player_id"]: item["last_tournament_date"] for item in last_tournaments}

    # Get player name lookup for any unverified players
    player_lookup = get_player_id_lookup()

    # Combine the data
    combined_data = []
    for sus_record in sus_data:
        tower_id = sus_record["tower_id"]

        # Use known_player name if available, otherwise lookup
        if sus_record["player_name"] == "Unverified Player":
            real_name = player_lookup.get(tower_id, "Unknown Player")
        else:
            real_name = sus_record["player_name"]

        last_tournament = last_tournament_lookup.get(tower_id)

        combined_data.append(
            {
                "record_pk": sus_record["record_pk"],
                "tower_id": tower_id,
                "player_name": real_name,
                "last_tournament_date": last_tournament,
                "days_since_last_tournament": (pd.Timestamp.now().date() - last_tournament).days if last_tournament else None,
                "sus_created_at": sus_record["created_at"],
                "sus_created_by": sus_record["created_by"],
                "sus_reason": sus_record["reason"],
                "sus_started_at": sus_record["started_at"],
                "zendesk_ticket_id": sus_record["zendesk_ticket_id"],
            }
        )

    return combined_data


def render_sus_moderation_page():
    """Render the sus moderation page with UI components."""

    # Apply styling
    css_path = Path(__file__).parent.parent / "static" / "styles" / "style.css"
    with open(css_path, "r") as infile:
        table_styling = f"<style>{infile.read()}</style>"
    st.write(table_styling, unsafe_allow_html=True)

    # Override centered layout — make this page use full width
    st.markdown(
        "<style>.block-container { max-width: 100% !important; padding-left: 2rem !important; padding-right: 2rem !important; }</style>",
        unsafe_allow_html=True,
    )

    st.markdown("# Active Sus Moderation Records")
    st.markdown("All players currently marked as suspicious with their last tournament participation")

    with st.spinner("🔍 Getting active sus moderation records..."):
        combined_data = get_sus_moderation_raw_data()

        if combined_data is None:
            st.success("🎉 No active sus moderation records found!")
            return

        st.success(f"Found **{len(combined_data)}** active sus moderation records")

    with st.spinner("📊 Processing tournament participation data..."):
        # Convert to DataFrame
        df = pd.DataFrame(combined_data)
        df = df.sort_values(["last_tournament_date", "sus_created_at"], ascending=[False, False], na_position="last").reset_index(drop=True)

    # Summary statistics
    total_sus = len(df)
    never_participated = len(df[df["last_tournament_date"].isna()])
    participated = total_sus - never_participated
    with_zendesk = len(df[df["zendesk_ticket_id"].notna()])
    without_zendesk = total_sus - with_zendesk

    if participated > 0:
        avg_days_since = df[df["days_since_last_tournament"].notna()]["days_since_last_tournament"].mean()
        recent_participants = len(df[df["days_since_last_tournament"] <= 30])
    else:
        avg_days_since = 0
        recent_participants = 0

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Total Sus Records", total_sus)
    with col2:
        st.metric("Never Participated", never_participated)
    with col3:
        st.metric("Recent Participants (30d)", recent_participants)
    with col4:
        if participated > 0:
            st.metric("Avg Days Since Last", f"{avg_days_since:.0f}")
        else:
            st.metric("Avg Days Since Last", "N/A")

    col5, col6 = st.columns(2)
    with col5:
        st.metric("With Zendesk Ticket", with_zendesk)
    with col6:
        st.metric("Without Zendesk Ticket", without_zendesk)

    # Filters
    days_filter = st.selectbox(
        "Filter by days since last tournament:",
        ["All", "Never participated", "Within 7 days", "Within 30 days", "Within 90 days", "Over 90 days ago"],
    )

    if days_filter == "Never participated":
        df = df[df["last_tournament_date"].isna()]
    elif days_filter == "Within 7 days":
        df = df[df["days_since_last_tournament"] <= 7]
    elif days_filter == "Within 30 days":
        df = df[df["days_since_last_tournament"] <= 30]
    elif days_filter == "Within 90 days":
        df = df[df["days_since_last_tournament"] <= 90]
    elif days_filter == "Over 90 days ago":
        df = df[df["days_since_last_tournament"] > 90]

    if days_filter != "All":
        st.caption(f"Showing {len(df)} of {total_sus} records")

    # ── Columns-based table with per-row action buttons ──────────────────────
    st.subheader("📊 Active Sus Moderation Records")

    # Column proportions:  #   Tower ID  Player       Last Tourn  Days   Created       By      Ticket  Action
    COL_WIDTHS = [0.35, 1.5, 2.0, 1.4, 0.9, 1.8, 1.4, 0.9, 1.4]

    # Header row
    h0, h1, h2, h3, h4, h5, h6, h7, h8 = st.columns(COL_WIDTHS)
    h0.markdown("**#**")
    h1.markdown("**Tower ID**")
    h2.markdown("**Player**")
    h3.markdown("**Last Tourn.**")
    h4.markdown("**Days**")
    h5.markdown("**Sus Created**")
    h6.markdown("**Created By**")
    h7.markdown("**Ticket #**")
    h8.markdown("**Action**")
    st.divider()

    for row_num, (_, row) in enumerate(df.iterrows(), start=1):
        tower_id = row["tower_id"]
        record_pk = int(row["record_pk"])
        ticket_id = row["zendesk_ticket_id"]
        has_ticket = pd.notnull(ticket_id)

        last_tourn = row["last_tournament_date"].strftime("%Y-%m-%d") if pd.notnull(row["last_tournament_date"]) else "Never"
        days_since = f"{int(row['days_since_last_tournament'])}d" if pd.notnull(row["days_since_last_tournament"]) else "—"
        sus_created = fmt_dt(row["sus_created_at"], fmt="%Y-%m-%d %H:%M %Z") if pd.notnull(row["sus_created_at"]) else ""
        ticket_display = str(int(ticket_id)) if has_ticket else "—"

        c0, c1, c2, c3, c4, c5, c6, c7, c8 = st.columns(COL_WIDTHS)
        c0.markdown(str(row_num))
        c1.markdown(f"`{tower_id}`")
        c2.markdown(row["player_name"])
        c3.markdown(last_tourn)
        c4.markdown(days_since)
        c5.markdown(sus_created)
        c6.markdown(row["sus_created_by"])
        c7.markdown(ticket_display)

        with c8:
            if has_ticket:
                if st.button("🔍 Check", key=f"check_{record_pk}", help=f"Poll Zendesk ticket #{int(ticket_id)} for verdict"):
                    with st.spinner("Checking Zendesk..."):
                        ok, title, msg = _do_check_status(record_pk, tower_id, int(ticket_id))
                    show_zendesk_result(ok, title, msg)
            else:
                if st.button("🎫 Create", key=f"create_{record_pk}", help="Create a Zendesk ticket for this sus record"):
                    with st.spinner("Creating ticket..."):
                        ok, title, msg = _do_create_ticket(record_pk, tower_id, row["sus_created_by"], row["sus_reason"])
                    show_zendesk_result(ok, title, msg)


render_sus_moderation_page()
