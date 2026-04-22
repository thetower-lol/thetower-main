import datetime
import html as html_mod
import json
import os
from pathlib import Path
from typing import Optional

import streamlit as st
import streamlit.components.v1 as components

from thetower.backend.tourney_results.constants import Graph, Options, leagues, legend
from thetower.backend.tourney_results.models import TourneyResult
from thetower.backend.tourney_results.overview_cache import read_overview_cache

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
        st.info("ℹ️ Tournament countdown unavailable - thetower-bcs package not installed")
        return

    try:
        tourney_id, tourney_date, days_until, _ = TournamentPredictor.get_tournament_info()

        # Get battle conditions for Legend league if available
        bcs_html = ""
        if days_until <= 1:  # Only show BCs within 24 hours of tournament
            try:
                legend_bcs = predict_future_tournament(tourney_id, legend)
                if legend_bcs:
                    bc_names = " • ".join(legend_bcs)
                    bcs_html = f"""
<div style="margin-top: 1rem; padding-top: 1rem; border-top: 1px solid rgba(255,255,255,0.2);">
    <div style="font-size: 0.85rem; color: #f0f0f0; margin-bottom: 0.3rem;">Legend Battle Conditions:</div>
    <div style="font-size: 0.95rem; color: white; font-weight: 500;">{bc_names}</div>
    <a href="bcs" target="_self" style="font-size: 0.8rem; color: #ffd700; text-decoration: underline; margin-top: 0.3rem; display: inline-block;">View all league BCs →</a>
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
                    "label": f"📊 {prev_short} Results Available In",
                    "sub": "Tournament complete — awaiting import",
                    "t": int(prev_results_available.timestamp() * 1000),
                }
            )
        phases.append({"label": "⏰ Tournament Starts In", "sub": tourney_date_str, "t": int(tourney_start.timestamp() * 1000)})
        phases.append({"label": "🎮 Entry Closes In", "sub": "Tournament is live — join now!", "t": int(entry_closes.timestamp() * 1000)})
        phases.append({"label": "⏱️ Tournament Ends In", "sub": "Entry closed — finishing runs only", "t": int(tourney_ends.timestamp() * 1000)})
        tourney_short = tourney_date.strftime("%b %d")
        phases.append(
            {
                "label": f"📊 {tourney_short} Results Available In",
                "sub": "Tournament complete — awaiting import",
                "t": int(results_available.timestamp() * 1000),
            }
        )
        phases.append({"label": "⏰ Next Tournament Starts In", "sub": next_tourney_date_str, "t": int(next_tourney_start.timestamp() * 1000)})

        phases_json = json.dumps(phases, ensure_ascii=False)

        components.html(
            f"""<!DOCTYPE html>
<html><head><style>
body{{margin:0;padding:0;font-family:sans-serif;}}
</style></head><body>
<div style="text-align:center;padding:1.5rem;background:linear-gradient(135deg,#667eea 0%,#764ba2 100%);border-radius:10px;margin:2px;box-shadow:0 4px 6px rgba(0,0,0,.3);">
    <p id="phase-sub" style="margin:0 0 .6rem 0;color:#f0f0f0;font-size:1.05rem;font-weight:500;"></p>
    <h2 id="phase-label" style="margin:0;color:white;font-size:1.5rem;"></h2>
    <div style="display:flex;justify-content:center;align-items:flex-start;gap:.8rem;margin-top:1rem;flex-wrap:wrap;">
        <div style="text-align:center;"><div id="cd-d" style="font-size:2.4rem;font-weight:bold;color:white;font-family:monospace;line-height:1;">--</div><div style="font-size:.7rem;color:#ddd;text-transform:uppercase;letter-spacing:.08em;margin-top:.2rem;">Days</div></div>
        <div style="font-size:2.4rem;font-weight:bold;color:rgba(255,255,255,.4);line-height:1;">:</div>
        <div style="text-align:center;"><div id="cd-h" style="font-size:2.4rem;font-weight:bold;color:white;font-family:monospace;line-height:1;">--</div><div style="font-size:.7rem;color:#ddd;text-transform:uppercase;letter-spacing:.08em;margin-top:.2rem;">Hours</div></div>
        <div style="font-size:2.4rem;font-weight:bold;color:rgba(255,255,255,.4);line-height:1;">:</div>
        <div style="text-align:center;"><div id="cd-m" style="font-size:2.4rem;font-weight:bold;color:white;font-family:monospace;line-height:1;">--</div><div style="font-size:.7rem;color:#ddd;text-transform:uppercase;letter-spacing:.08em;margin-top:.2rem;">Mins</div></div>
        <div style="font-size:2.4rem;font-weight:bold;color:rgba(255,255,255,.4);line-height:1;">:</div>
        <div style="text-align:center;"><div id="cd-s" style="font-size:2.4rem;font-weight:bold;color:white;font-family:monospace;line-height:1;">--</div><div style="font-size:.7rem;color:#ddd;text-transform:uppercase;letter-spacing:.08em;margin-top:.2rem;">Secs</div></div>
    </div>
    {bcs_html}
</div>
<script>
(function(){{
    var phases={phases_json};
    function p(n){{return String(n).padStart(2,'0');}}
    function tick(){{
        var now=Date.now(),ph=phases[phases.length-1];
        for(var i=0;i<phases.length;i++){{if(now<phases[i].t){{ph=phases[i];break;}}}}
        document.getElementById('phase-label').innerText=ph.label;
        document.getElementById('phase-sub').innerText=ph.sub;
        var r=Math.max(0,ph.t-now);
        document.getElementById('cd-d').innerText=p(Math.floor(r/86400000));
        document.getElementById('cd-h').innerText=p(Math.floor(r%86400000/3600000));
        document.getElementById('cd-m').innerText=p(Math.floor(r%3600000/60000));
        document.getElementById('cd-s').innerText=p(Math.floor(r%60000/1000));
    }}
    tick();setInterval(tick,1000);
}})();
</script>
</body></html>""",
            height=310 if bcs_html else 230,
        )

    except Exception as e:
        st.warning(f"⚠️ Could not load tournament countdown: {str(e)}")


# ---------------------------------------------------------------------------
# Shared pill-building helpers
# ---------------------------------------------------------------------------

_MEDAL_ICONS = ["🥇", "🥈", "🥉"]
_PILL_COLORS = [
    "linear-gradient(135deg, #FFD700 0%, #FFA500 100%)",
    "linear-gradient(135deg, #C0C0C0 0%, #A8A8A8 100%)",
    "linear-gradient(135deg, #CD7F32 0%, #B87333 100%)",
    "#2a2a3e",
]


def render_patch_leaderboard(top_5: list[dict]) -> None:
    """Render the patch leaderboard from pre-fetched data.

    Each item in *top_5* must have keys: ``real_name``, ``first_wins``,
    ``second_wins``, ``patch_name``.
    """
    if not top_5:
        return
    try:
        patch_name = top_5[0].get("patch_name", "")
        pills = []
        for idx in range(min(3, len(top_5))):
            p = top_5[idx]
            name = _esc(p["real_name"])
            fw, sw = p["first_wins"], p["second_wins"]
            wins_text = f"{fw} Win{'s' if fw > 1 else ''}"
            if sw > 0:
                wins_text += f" (+{sw} 2nd)"
            bg = _PILL_COLORS[idx]
            pills.append(
                f'<div style="flex: 1; min-width: 110px; text-align: center; padding: 0.75rem; background: {bg}; border-radius: 7px; box-shadow: 0 1.5px 3px rgba(0,0,0,0.2);">'
                f'<div style="font-size: 1.5rem;">{_MEDAL_ICONS[idx]}</div>'
                f'<div style="font-size: 0.85rem; font-weight: bold; color: #1a1a1a; margin: 0.375rem 0;">{name}</div>'
                f'<div style="font-size: 0.8rem; font-weight: bold; color: #333333;">{wins_text}</div>'
                "</div>"
            )
        if len(top_5) >= 4:
            inner = ""
            for idx in range(3, min(5, len(top_5))):
                p = top_5[idx]
                name = _esc(p["real_name"])
                fw, sw = p["first_wins"], p["second_wins"]
                wins_text = f"{fw} Win{'s' if fw > 1 else ''}"
                if sw > 0:
                    wins_text += f" (+{sw} 2nd)"
                inner += (
                    f'<div style="margin-bottom: 0.5rem; padding: 0.5rem; background: rgba(255,255,255,0.05); border-radius: 5px;">'
                    f'<div style="font-size: 0.85rem; color: #e0e0e0;"><strong>#{idx + 1}</strong> {name}</div>'
                    f'<div style="font-size: 0.75rem; color: #a0a0a0;">{wins_text}</div>'
                    "</div>"
                )
            pills.append(
                f'<div style="flex: 1; min-width: 110px; padding: 0.75rem; background: {_PILL_COLORS[3]}; border-radius: 7px; box-shadow: 0 1.5px 3px rgba(0,0,0,0.2);">'
                f"{inner}</div>"
            )
        pills_html = "".join(pills)
        st.html(
            f'<div style="margin: 1.5rem 0; padding: 1.125rem; background: #1e1e2e; border-radius: 8px; box-shadow: 0 3px 4.5px rgba(0,0,0,0.3);">'
            f'<h3 style="margin: 0 0 0.75rem 0; color: #667eea; text-align: center; font-size: 1.1rem;">🏆 Patch {_esc(patch_name)} Leaderboard - Most First Place Finishes</h3>'
            f'<div style="display: flex; justify-content: space-around; flex-wrap: wrap; gap: 0.75rem;">{pills_html}</div>'
            "</div>"
        )
    except Exception:
        pass


def render_legend_avg_wave_leaderboard(top_5: list[dict]) -> None:
    """Render the Legend avg-wave leaderboard from pre-fetched data.

    Each item must have keys: ``real_name``, ``avg_wave``, ``tournaments``.
    """
    if not top_5:
        return
    try:
        pills = []
        for idx in range(min(3, len(top_5))):
            p = top_5[idx]
            name = _esc(p["real_name"])
            avg_wave = p["avg_wave"]
            tc = p["tournaments"]
            bg = _PILL_COLORS[idx]
            pills.append(
                f'<div style="flex: 1; min-width: 110px; text-align: center; padding: 0.75rem; background: {bg}; border-radius: 7px; box-shadow: 0 1.5px 3px rgba(0,0,0,0.2);">'
                f'<div style="font-size: 1.5rem;">{_MEDAL_ICONS[idx]}</div>'
                f'<div style="font-size: 0.85rem; font-weight: bold; color: #1a1a1a; margin: 0.375rem 0;">{name}</div>'
                f'<div style="font-size: 0.8rem; font-weight: bold; color: #333333;">{avg_wave:.1f} avg wave<br>'
                f'<span style="font-size:0.8em; color:{bg}">({tc} tournaments)</span></div>'
                "</div>"
            )
        if len(top_5) >= 4:
            inner = ""
            for idx in range(3, min(5, len(top_5))):
                p = top_5[idx]
                name = _esc(p["real_name"])
                avg_wave = p["avg_wave"]
                tc = p["tournaments"]
                inner += (
                    f'<div style="margin-bottom: 0.5rem; padding: 0.5rem; background: rgba(255,255,255,0.05); border-radius: 5px;">'
                    f'<div style="font-size: 0.85rem; color: #e0e0e0;"><strong>#{idx + 1}</strong> {name}</div>'
                    f'<div style="font-size: 0.75rem; color: #a0a0a0;">{avg_wave:.1f} avg wave '
                    f'<span style="font-size:0.8em; color:#888">({tc} tournaments)</span></div>'
                    "</div>"
                )
            pills.append(
                f'<div style="flex: 1; min-width: 110px; padding: 0.75rem; background: {_PILL_COLORS[3]}; border-radius: 7px; box-shadow: 0 1.5px 3px rgba(0,0,0,0.2);">'
                f"{inner}</div>"
            )
        pills_html = "".join(pills)
        st.html(
            '<div style="margin: 1.5rem 0; padding: 1.125rem; background: #1e1e2e; border-radius: 8px; box-shadow: 0 3px 4.5px rgba(0,0,0,0.3);">'
            '<h3 style="margin: 0 0 0.75rem 0; color: #667eea; text-align: center; font-size: 1.1rem;">📈 Legend - Highest Average Wave (Latest Patch, min 2 tournaments)</h3>'
            f'<div style="display: flex; justify-content: space-around; flex-wrap: wrap; gap: 0.75rem;">{pills_html}</div>'
            "</div>"
        )
    except Exception:
        pass


def render_league_standings(league: str, players: list[dict], is_legend: bool = False) -> None:
    """Render standings for a single league from pre-fetched data.

    Each item in *players* must have keys: ``real_name``, ``wave``.
    """
    if not players:
        return

    league_query = league.replace(" ", "%20")
    st.markdown(
        f'<div style="margin-top: 2rem; margin-bottom: 0.75rem;">'
        f'<h2 style="color: #667eea; border-bottom: 2px solid #667eea; padding-bottom: 0.5rem; font-size: 1.3rem;">'
        f'<a href="results?league={league_query}" target="_self" style="text-decoration: none; color: #667eea;">{_esc(league)} 🔗</a>'
        "</h2></div>",
        unsafe_allow_html=True,
    )

    max_display = 5 if is_legend else 3
    pills = []
    for idx in range(min(3, len(players))):
        p = players[idx]
        name = _esc(p["real_name"])
        wave = p["wave"]
        bg = _PILL_COLORS[idx]
        pills.append(
            f'<div style="flex: 1; min-width: 110px; text-align: center; padding: 0.75rem; background: {bg}; border-radius: 7px; box-shadow: 0 1.5px 3px rgba(0,0,0,0.2);">'
            f'<div style="font-size: 1.5rem;">{_MEDAL_ICONS[idx]}</div>'
            f'<div style="font-size: 0.85rem; font-weight: bold; color: #1a1a1a; margin: 0.375rem 0;">{name}</div>'
            f'<div style="font-size: 0.8rem; font-weight: bold; color: #333333;">Wave {wave}</div>'
            "</div>"
        )
    if len(players) >= 4:
        inner = ""
        for idx in range(3, min(max_display, len(players))):
            p = players[idx]
            name = _esc(p["real_name"])
            wave = p["wave"]
            inner += (
                f'<div style="margin-bottom: 0.5rem; padding: 0.5rem; background: rgba(255,255,255,0.05); border-radius: 5px;">'
                f'<div style="font-size: 0.85rem; color: #e0e0e0;"><strong>#{idx + 1}</strong> {name}</div>'
                f'<div style="font-size: 0.75rem; color: #a0a0a0;">Wave {wave}</div>'
                "</div>"
            )
        pills.append(
            f'<div style="flex: 1; min-width: 110px; padding: 0.75rem; background: {_PILL_COLORS[3]}; border-radius: 7px; box-shadow: 0 1.5px 3px rgba(0,0,0,0.2);">'
            f"{inner}</div>"
        )
    pills_html = "".join(pills)
    st.html(f'<div style="display: flex; justify-content: space-around; flex-wrap: wrap; gap: 0.75rem; margin-bottom: 1rem;">{pills_html}</div>')


def _load_overview_stats() -> Optional[dict]:
    """Load overview stats from file cache.

    Returns the stats dict on success, or ``None`` if the cache file is absent.
    Cache is written by import_results or via the Site Settings admin page.
    """
    return read_overview_cache()


def compute_overview(options: Options) -> None:
    print("overview")

    # Load custom CSS
    css_path = Path(__file__).parent.parent / "static" / "styles" / "style.css"
    with open(css_path, "r") as infile:
        st.write(f"<style>{infile.read()}</style>", unsafe_allow_html=True)
    # Display logo header with top anchor
    logo_path = Path(__file__).parent.parent / "static" / "images" / "TT.png"
    if logo_path.exists():
        col1, col2, col3 = st.columns([1, 2, 1])
        with col2:
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
        "<div style='text-align: center; margin: 0.5rem 0 1.2rem 0;'>"
        "<a href='#league-results' style='font-size: 0.92rem; color: #ffd700; text-decoration: underline;'>↓ Jump to League Results</a>"
        "</div>",
        unsafe_allow_html=True,
    )

    render_patch_leaderboard(stats.get("patch_leaderboard", []))

    # Overview text for Legend (single lightweight query, not in cache)
    from thetower.backend.tourney_results.models import TourneyResult

    public = {"public": True} if not os.environ.get("HIDDEN_FEATURES") else {}
    try:
        if overview_text := TourneyResult.objects.filter(league=legend, **public).latest("date").overview:
            st.markdown(overview_text, unsafe_allow_html=True)
    except Exception:
        pass

    render_legend_avg_wave_leaderboard(stats.get("legend_avg_wave_leaderboard", []))

    last_tourney_date_str = stats.get("last_tourney_date", "")
    st.markdown(
        f"<a id='league-results'></a>"
        f"<div style='margin: 2.5rem 0 1.5rem 0; text-align: center;'>"
        f"<span style='font-size: 1.25rem; color: #667eea; font-weight: 600;'>Results for {_esc(last_tourney_date_str)}</span>"
        "</div>",
        unsafe_allow_html=True,
    )

    league_standings = stats.get("league_standings", {})
    render_league_standings(legend, league_standings.get(legend, []), is_legend=True)

    other_leagues = leagues[1:]
    col1, spacer, col2 = st.columns([1, 0.15, 1])
    for idx, lg in enumerate(other_leagues):
        with col1 if idx % 2 == 0 else col2:
            render_league_standings(lg, league_standings.get(lg, []), is_legend=False)


options = Options(links_toggle=False, default_graph=Graph.last_16.value, average_foreground=True)
compute_overview(options)
st.markdown(
    """
<div style='text-align: center; margin: 2.5rem 0 1.5rem 0;'>
    <a href='#top' style='font-size: 0.92rem; color: #667eea; text-decoration: underline;'>↑ Back to Top</a>
</div>
""",
    unsafe_allow_html=True,
)
