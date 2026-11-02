import datetime
import html as html_mod
import json
import os
from pathlib import Path
from typing import Optional

import streamlit as st
import streamlit.components.v1 as components

from thetower.backend.tourney_results.constants import Graph, Options, leagues, top_league
from thetower.backend.tourney_results.formatting import format_wave
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
<div style="margin-top: 16px; padding-top: 16px; border-top: 1px solid rgba(250,250,250,0.08);">
    <div style="font-size: 13px; line-height: 20px; color: #a3a8b8; margin-bottom: 4px;">{top_league} battle conditions</div>
    <div style="font-size: 14px; line-height: 20px; color: #fafafa;">{bc_names}</div>
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

        phases_json = json.dumps(phases, ensure_ascii=False)

        components.html(
            f"""<!DOCTYPE html>
<html><head><style>
body{{margin:0;padding:0;font-family:"Source Sans 3","Source Sans Pro",system-ui,sans-serif;}}
.cd{{margin:2px;padding:24px;text-align:center;background:#161922;border:1px solid rgba(250,250,250,.08);border-radius:16px;}}
.sub{{margin:0 0 4px 0;color:#a3a8b8;font-size:14px;line-height:20px;}}
.label{{margin:0;color:#fafafa;font-size:20px;line-height:28px;font-weight:500;}}
.row{{display:flex;justify-content:center;align-items:flex-start;gap:12px;margin-top:16px;flex-wrap:wrap;}}
.num,.sep{{font-family:"Source Code Pro",ui-monospace,monospace;font-size:40px;line-height:1;}}
.num{{font-variant-numeric:tabular-nums;font-weight:500;color:#52b9e4;}}
.sep{{color:rgba(250,250,250,.24);}}
.unit{{margin-top:4px;font-size:12px;line-height:16px;color:#8b90a0;}}
</style></head><body>
<div class="cd">
    <p id="phase-sub" class="sub"></p>
    <h2 id="phase-label" class="label"></h2>
    <div class="row">
        <div><div id="cd-d" class="num">--</div><div class="unit">Days</div></div>
        <div class="sep">:</div>
        <div><div id="cd-h" class="num">--</div><div class="unit">Hours</div></div>
        <div class="sep">:</div>
        <div><div id="cd-m" class="num">--</div><div class="unit">Mins</div></div>
        <div class="sep">:</div>
        <div><div id="cd-s" class="num">--</div><div class="unit">Secs</div></div>
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
            height=268 if bcs_html else 192,
        )

        if bcs_html:
            # Outside the iframe: components.html is a sandboxed iframe, so a link inside it can only
            # navigate the iframe itself (the whole app ends up nested inside the countdown box)
            with st.container(horizontal_alignment="center"):
                st.page_link("live/bcs.py", label="View all league BCs →")

    except Exception as e:
        st.warning(f"Could not load tournament countdown: {str(e)}")


# ---------------------------------------------------------------------------
# Shared pill-building helpers
# ---------------------------------------------------------------------------

_MEDAL_COLORS = ["#d4af37", "#b8bcc4", "#c0804a"]
_MEDAL_LABELS = ["1st", "2nd", "3rd"]
_MONO = "font-family: 'Source Code Pro', ui-monospace, monospace; font-variant-numeric: tabular-nums;"


def _podium_html(top: list[tuple[str, str]], rest: list[tuple[int, str, str]]) -> str:
    """Flat podium cards for the top three plus one card listing the runners-up.

    *top* holds ``(name, value)`` for places 1-3, *rest* holds ``(place, name, value)``.
    Names must already be escaped.
    """
    cards = []
    for idx, (name, value) in enumerate(top):
        color = _MEDAL_COLORS[idx]
        cards.append(
            f'<div style="flex: 1; min-width: 112px; padding: 12px; background: #1c1f29; border: 1px solid rgba(250,250,250,0.08); '
            f'border-top: 3px solid {color}; border-radius: 8px;">'
            f'<div style="{_MONO} font-size: 12px; line-height: 16px; color: {color};">{_MEDAL_LABELS[idx]}</div>'
            f'<div style="font-size: 15px; line-height: 20px; font-weight: 500; color: #fafafa; margin-top: 4px;">{name}</div>'
            f'<div style="{_MONO} font-size: 13px; line-height: 20px; color: #a3a8b8;">{value}</div>'
            "</div>"
        )
    if rest:
        rows = "".join(
            f'<div style="padding: 4px 0;">'
            f'<div style="display: flex; gap: 8px; align-items: baseline;">'
            f'<span style="{_MONO} font-size: 12px; color: #8b90a0; width: 12px;">{place}</span>'
            f'<span style="font-size: 14px; line-height: 20px; color: #fafafa; overflow-wrap: anywhere;">{name}</span></div>'
            f'<div style="{_MONO} font-size: 13px; line-height: 20px; color: #a3a8b8; padding-left: 20px;">{value}</div>'
            "</div>"
            for place, name, value in rest
        )
        cards.append(
            f'<div style="flex: 1; min-width: 112px; padding: 8px 12px; background: #1c1f29; border: 1px solid rgba(250,250,250,0.08); '
            f'border-radius: 8px;">{rows}</div>'
        )
    return f'<div style="display: flex; flex-wrap: wrap; gap: 8px;">{"".join(cards)}</div>'


def _leaderboard_panel(title: str, meta: str, podium: str) -> str:
    """Panel holding a titled podium, used for the patch-wide leaderboards."""
    return (
        '<div style="margin: 24px 0; padding: 16px; background: #161922; border: 1px solid rgba(250,250,250,0.08); border-radius: 16px;">'
        '<div style="display: flex; flex-wrap: wrap; gap: 8px; align-items: baseline; margin-bottom: 12px;">'
        f'<span style="font-size: 16px; line-height: 24px; font-weight: 500; color: #fafafa;">{title}</span>'
        f'<span style="font-size: 13px; color: #8b90a0;">{meta}</span></div>'
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
        '<div style="margin-top: 32px; margin-bottom: 12px; padding-bottom: 8px; border-bottom: 1px solid rgba(250,250,250,0.08);">'
        f'<a href="results?league={league_query}" target="_self" style="text-decoration: none; font-size: 18px; font-weight: 500;">'
        f'{_esc(league)} <span style="color: #8b90a0;">→</span></a>'
        "</div>",
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

    # Load custom CSS
    css_path = Path(__file__).parent.parent / "static" / "styles" / "style.css"
    with open(css_path, "r") as infile:
        st.write(f"<style>{infile.read()}</style>", unsafe_allow_html=True)
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
        f"<span style='font-size: 24px; line-height: 32px; color: #fafafa; font-weight: 500;'>Results for {_esc(last_tourney_date_str)}</span>"
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
