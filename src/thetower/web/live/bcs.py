"""Display tournament battle conditions in Streamlit interface."""

import datetime
import json
import logging
from time import perf_counter

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from thetower.backend.tourney_results.constants import leagues

# Try to import thetower_bcs with graceful fallback
try:
    from thetower_bcs import TournamentPredictor, predict_future_tournament

    TOWERBCS_AVAILABLE = True
except ImportError:
    TOWERBCS_AVAILABLE = False
    predict_future_tournament = None
    TournamentPredictor = None

logging.info("Starting battle conditions analysis")
t2_start = perf_counter()

# Check if thetower_bcs is available
if not TOWERBCS_AVAILABLE:
    st.markdown("# Battle Conditions")
    st.error("⚠️ Battle Conditions module not available")
    st.stop()

tourney_id, tourney_date, days_until, _ = TournamentPredictor.get_tournament_info()

# BCs are revealed this many days before the tournament
BC_DAYS_EARLY = 1


def _predict(league: str) -> list[str]:
    """Battle conditions for a league, or a one-line notice when the predictor does not model it yet."""
    try:
        return predict_future_tournament(tourney_id, league)
    except ValueError:
        logging.warning(f"Battle conditions unavailable for {league}: the predictor does not know this league yet")
        return [f"{league} battle conditions are unavailable"]


st.markdown("# Battle Conditions")
if days_until > BC_DAYS_EARLY:
    bc_dt = datetime.datetime.combine(tourney_date, datetime.time.min, tzinfo=datetime.timezone.utc) - datetime.timedelta(days=BC_DAYS_EARLY)
    tourney_date_str = tourney_date.strftime("%A, %B %d, %Y")
    phases = [
        {"label": "Battle conditions revealed in", "sub": tourney_date_str, "t": int(bc_dt.timestamp() * 1000)},
    ]
    phases_json = json.dumps(phases)
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
        height=192,
    )
else:
    st.markdown(f"## Tournament {'is today!' if days_until == 0 else f'is on {tourney_date}'}")

    st.dataframe(
        pd.DataFrame.from_dict({league: _predict(league) for league in leagues}, orient="index").transpose().fillna(""),
        width="stretch",
    )

# Log execution time at the end of the file
t2_stop = perf_counter()
logging.info(f"Full battle conditions analysis took {t2_stop - t2_start}")
