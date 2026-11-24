import datetime
import html as html_mod
import os
from pathlib import Path
from typing import Optional

import streamlit as st

from thetower.backend.tourney_results.constants import Graph, Options, leagues, top_league
from thetower.backend.tourney_results.formatting import format_wave
from thetower.backend.tourney_results.models import TourneyResult
from thetower.backend.tourney_results.overview_cache import read_overview_cache
from thetower.web.countdown import render_countdown

# Try to import thetower_bcs for tournament countdown
try:
    from thetower_bcs import TournamentPredictor, predict_future_tournament

    TOWERBCS_AVAILABLE = True
except ImportError:
    TOWERBCS_AVAILABLE = False
    TournamentPredictor = None
    predict_future_tournament = None


def _esc(text: str) -> str:
    """HTML-escape a display name."""
    return html_mod.escape(str(text))


def render_tournament_countdown():
    """Render the tournament countdown header with progressive phase tracking.

    Phases cycle automatically in the browser based on current time:
    - If the most recently completed tourney's results aren't imported yet,
      prepend Entry Closes In / Tournament Ends In / Results Available In for
      that tourney so the countdown reflects where we actually are.
    - Then: Tournament Starts In → Entry Closes In → Tournament Ends In →
      Results Available In → Next Tournament Starts In (for tourney_date).
    """
    if not TOWERBCS_AVAILABLE:
        st.info("Tournament countdown unavailable - thetower-bcs package not installed")
        return

    try:
        tourney_id, tourney_date, days_until, _ = TournamentPredictor.get_tournament_info()

        # Get battle conditions for the top league if available
        bcs_html = ""
        if days_until <= 1:  # Only show BCs within 24 hours of tournament
            try:
                top_bcs = predict_future_tournament(tourney_id, top_league)
                if top_bcs:
                    bc_names = " • ".join(top_bcs)
                    bcs_html = f"""
<div class="tt-countdown-bcs">
    <div class="tt-countdown-bcs-title">{top_league} battle conditions</div>
    <div class="tt-countdown-bcs-list">{bc_names}</div>
</div>"""
            except Exception:
                pass  # Silently fail BC prediction

        utc = datetime.timezone.utc

        # ── Previous tournament milestones ────────────────────────────────────
        # Determine the most recently completed tournament (one cycle before tourney_date).
        # Sat→Wed: 3 days back; Wed→Sat: 4 days back.
        days_back = 3 if tourney_date.weekday() == 5 else 4
        prev_tourney_date = tourney_date - datetime.timedelta(days=days_back)
        prev_tourney_start = datetime.datetime.combine(prev_tourney_date, datetime.time.min, tzinfo=utc)
        prev_results_available = prev_tourney_start + datetime.timedelta(hours=29, minutes=5)

        # Check whether the previous tourney's results have been imported yet.
        public = {"public": True} if not os.environ.get("HIDDEN_FEATURES") else {}
        try:
            last_tourney_date = TourneyResult.objects.filter(**public).latest("date").date
        except Exception:
            last_tourney_date = None
        results_pending = last_tourney_date is None or last_tourney_date < prev_tourney_date

        # ── Current / upcoming tournament milestones ──────────────────────────
        tourney_start = datetime.datetime.combine(tourney_date, datetime.time.min, tzinfo=utc)
        entry_closes = tourney_start + datetime.timedelta(hours=24)
        tourney_ends = tourney_start + datetime.timedelta(hours=28)
        results_available = tourney_start + datetime.timedelta(hours=29, minutes=5)

        # Next tournament after tourney_date: Wed→Sat (+3), Sat→Wed (+4)
        days_to_next = 3 if tourney_date.weekday() == 2 else 4
        next_tourney_date = tourney_date + datetime.timedelta(days=days_to_next)
        next_tourney_start = datetime.datetime.combine(next_tourney_date, datetime.time.min, tzinfo=utc)

        tourney_date_str = tourney_date.strftime("%A, %B %d, %Y")
        next_tourney_date_str = next_tourney_date.strftime("%A, %B %d, %Y")

        # ── Build ordered phases list (Python-side, injected as JSON) ─────────
        # JS finds the first phase whose timestamp is still in the future.
        phases = []
        if results_pending:
            prev_short = prev_tourney_date.strftime("%b %d")
            phases.append(
                {
                    "label": f"{prev_short} results available in",
                    "sub": "Tournament complete — awaiting import",
                    "t": int(prev_results_available.timestamp() * 1000),
                }
            )
        phases.append({"label": "Tournament starts in", "sub": tourney_date_str, "t": int(tourney_start.timestamp() * 1000)})
        phases.append({"label": "Entry closes in", "sub": "Tournament is live, entry open", "t": int(entry_closes.timestamp() * 1000)})
        phases.append({"label": "Tournament ends in", "sub": "Entry closed — finishing runs only", "t": int(tourney_ends.timestamp() * 1000)})
        tourney_short = tourney_date.strftime("%b %d")
        phases.append(
            {
                "label": f"{tourney_short} results available in",
                "sub": "Tournament complete — awaiting import",
                "t": int(results_available.timestamp() * 1000),
            }
        )
        phases.append({"label": "Next tournament starts in", "sub": next_tourney_date_str, "t": int(next_tourney_start.timestamp() * 1000)})

        render_countdown(phases, footer_html=bcs_html)

        if bcs_html:
            with st.container(horizontal_alignment="center"):
                st.page_link("live/bcs.py", label="View all league BCs →")

    except Exception as e:
        st.warning(f"Could not load tournament countdown: {str(e)}")


# ---------------------------------------------------------------------------
# Shared pill-building helpers
# ---------------------------------------------------------------------------

_MEDALS = [("gold", "1st"), ("silver", "2nd"), ("bronze", "3rd")]


def _podium_html(top: list[tuple[str, str]], rest: list[tuple[int, str, str]]) -> str:
    """Flat podium cards for the top three plus one card listing the runners-up (styled by tt-* classes in style.css).

    *top* holds ``(name, value)`` for places 1-3, *rest* holds ``(place, name, value)``.
    Names must already be escaped.
    """
    cards = [
        f'<div class="tt-card tt-card--{medal}"><div class="tt-card-place">{label}</div>'
        f'<div class="tt-card-name">{name}</div><div class="tt-card-value">{value}</div></div>'
        for (medal, label), (name, value) in zip(_MEDALS, top)
    ]
    if rest:
        rows = "".join(
            f'<div class="tt-runner"><div class="tt-runner-line"><span class="tt-runner-place">{place}</span>'
            f'<span class="tt-runner-name">{name}</span></div><div class="tt-card-value">{value}</div></div>'
            for place, name, value in rest
        )
        cards.append(f'<div class="tt-card tt-runners">{rows}</div>')
    return f'<div class="tt-podium">{"".join(cards)}</div>'


def _leaderboard_panel(title: str, meta: str, podium: str) -> str:
    """Panel holding a titled podium, used for the patch-wide leaderboards."""
    return (
        '<div class="tt-panel"><div class="tt-panel-head">'
        f'<span class="tt-panel-title">{title}</span><span class="tt-meta">{meta}</span></div>'
        f"{podium}</div>"
    )


def _wins_text(first_wins: int, second_wins: int) -> str:
    text = f"{first_wins} win{'s' if first_wins > 1 else ''}"
    if second_wins > 0:
        text += f" +{second_wins} 2nd"
    return text


def render_patch_leaderboard(top_5: list[dict]) -> None:
    """Render the patch leaderboard from pre-fetched data.

    Each item in *top_5* must have keys: ``real_name``, ``first_wins``,
    ``second_wins``, ``patch_name``.
    """
    if not top_5:
        st.info(f"Missing {top_league} data: no {top_league} first-place finishes recorded for the current patch yet.")
        return
    try:
        patch_name = top_5[0].get("patch_name", "")
        top = [(_esc(p["real_name"]), _wins_text(p["first_wins"], p["second_wins"])) for p in top_5[:3]]
        rest = [(idx + 1, _esc(p["real_name"]), _wins_text(p["first_wins"], p["second_wins"])) for idx, p in enumerate(top_5[3:5], start=3)]
        st.html(_leaderboard_panel(f"{top_league}: most first place finishes", f"Patch {_esc(patch_name)}", _podium_html(top, rest)))
    except Exception:
        pass


def render_top_league_avg_wave_leaderboard(top_5: list[dict]) -> None:
    """Render the top-league avg-wave leaderboard from pre-fetched data.

    Each item must have keys: ``real_name``, ``avg_wave``, ``tournaments``.
    """
    if not top_5:
        st.info(f"Missing {top_league} data: no {top_league} tournaments in the current patch yet.")
        return
    try:
        top = [(_esc(p["real_name"]), f"{p['avg_wave']:.1f} avg · {p['tournaments']} tourneys") for p in top_5[:3]]
        rest = [(idx + 1, _esc(p["real_name"]), f"{p['avg_wave']:.1f}") for idx, p in enumerate(top_5[3:5], start=3)]
        st.html(_leaderboard_panel(f"{top_league}: highest average wave", "Latest patch, min 2 tournaments", _podium_html(top, rest)))
    except Exception:
        pass


def render_league_standings(league: str, players: list[dict], is_top: bool = False) -> None:
    """Render standings for a single league from pre-fetched data.

    Each item in *players* must have keys: ``real_name``, ``wave``.
    """
    if not players:
        return

    league_query = league.replace(" ", "%20")
    st.markdown(
        f'<div class="tt-section-link"><a href="results?league={league_query}" target="_self">{_esc(league)} <span class="tt-arrow">→</span></a></div>',
        unsafe_allow_html=True,
    )

    max_display = 5 if is_top else 3
    top = [(_esc(p["real_name"]), f"Wave {format_wave(p['wave'])}") for p in players[:3]]
    rest = [(idx + 1, _esc(p["real_name"]), format_wave(p["wave"])) for idx, p in enumerate(players[3:max_display], start=3)]
    st.html(f'<div style="margin-bottom: 16px;">{_podium_html(top, rest)}</div>')


def _load_overview_stats() -> Optional[dict]:
    """Load overview stats from file cache.

    Returns the stats dict on success, or ``None`` if the cache file is absent.
    Cache is written by import_results or via the Site Settings admin page.
    """
    return read_overview_cache()


def compute_overview(options: Options) -> None:
    print("overview")

    # Display logo header with top anchor
    logo_path = Path(__file__).parent.parent / "static" / "images" / "TT.png"
    if logo_path.exists():
        with st.container(horizontal_alignment="center"):
            st.markdown("<a id='top'></a>", unsafe_allow_html=True)
            st.image(str(logo_path), width=400)

    # Load all stats from cache (regenerate on first-run miss; None means unavailable)
    stats = _load_overview_stats()

    # Render tournament countdown (fast — just timezone math + 1 lightweight DB query)
    render_tournament_countdown()

    if stats is None:
        hidden_features = bool(os.environ.get("HIDDEN_FEATURES"))
        msg = "Summary statistics are unavailable at this time."
        if hidden_features:
            msg += " Visit [Site Settings](/sitesettings) to regenerate the overview cache."
        st.warning(msg)
        return

    st.markdown(
        "<div style='text-align: center; margin: 8px 0 20px 0;'>"
        "<a href='#league-results' style='font-size: 14px;'>Jump to league results ↓</a>"
        "</div>",
        unsafe_allow_html=True,
    )

    render_patch_leaderboard(stats.get("patch_leaderboard", []))

    # Overview text for the top league (single lightweight query, not in cache)
    from thetower.backend.tourney_results.models import TourneyResult

    public = {"public": True} if not os.environ.get("HIDDEN_FEATURES") else {}
    try:
        if overview_text := TourneyResult.objects.filter(league=top_league, **public).latest("date").overview:
            st.markdown(overview_text, unsafe_allow_html=True)
    except TourneyResult.DoesNotExist:
        st.warning(f"Missing {top_league} data: no {top_league} tournament results have been imported yet.")
    except Exception:
        pass

    render_top_league_avg_wave_leaderboard(stats.get("top_league_avg_wave_leaderboard", []))

    last_tourney_date_str = stats.get("last_tourney_date", "")
    st.markdown(
        f"<a id='league-results'></a>"
        f"<div style='margin: 40px 0 8px 0;'>"
        f"<h2 style='margin: 0; padding: 0;'>Results for {_esc(last_tourney_date_str)}</h2>"
        "</div>",
        unsafe_allow_html=True,
    )

    # Only leagues with a tournament on the latest date are in the cache; the top league is always featured.
    league_standings = stats.get("league_standings", {})
    if league_standings.get(top_league):
        render_league_standings(top_league, league_standings[top_league], is_top=True)
    else:
        st.warning(f"Missing {top_league} data: no {top_league} results for the latest tournament date.")

    other_leagues = [lg for lg in leagues if lg != top_league and league_standings.get(lg)]
    col1, spacer, col2 = st.columns([1, 0.15, 1])
    for idx, lg in enumerate(other_leagues):
        with col1 if idx % 2 == 0 else col2:
            render_league_standings(lg, league_standings[lg], is_top=False)


options = Options(links_toggle=False, default_graph=Graph.last_16.value, average_foreground=True)
compute_overview(options)
st.markdown(
    """
<div style='text-align: center; margin: 40px 0 24px 0;'>
    <a href='#top' style='font-size: 14px;'>Back to top ↑</a>
</div>
""",
    unsafe_allow_html=True,
)
