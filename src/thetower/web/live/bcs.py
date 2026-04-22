"""Display tournament battle conditions in Streamlit interface."""

import datetime
import json
import logging
from time import perf_counter

import streamlit as st
import streamlit.components.v1 as components

import pandas as pd
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
    st.markdown("The `thetower-bcs` package is not installed. To use battle conditions prediction, install it with: `pip install -e /path/to/thetower-bcs`")
    st.stop()

tourney_id, tourney_date, days_until, _ = TournamentPredictor.get_tournament_info()

# BCs are revealed this many days before the tournament
BC_DAYS_EARLY = 1

st.markdown("# Battle Conditions")
if days_until > BC_DAYS_EARLY:
    bc_dt = datetime.datetime.combine(tourney_date, datetime.time.min, tzinfo=datetime.timezone.utc) - datetime.timedelta(days=BC_DAYS_EARLY)
    tourney_date_str = tourney_date.strftime("%A, %B %d, %Y")
    phases = [
        {"label": "🔮 Battle Conditions Revealed In", "sub": tourney_date_str, "t": int(bc_dt.timestamp() * 1000)},
    ]
    phases_json = json.dumps(phases)
    components.html(
        f"""<!DOCTYPE html>
<html><head><style>body{{margin:0;padding:0;font-family:sans-serif;}}</style></head><body>
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
        height=230,
    )
else:
    st.markdown(f"## Tournament {'is today!' if days_until == 0 else f'is on {tourney_date}'}")

    st.dataframe(
        pd.DataFrame.from_dict({league: predict_future_tournament(tourney_id, league) for league in leagues}, orient="index").transpose().fillna(""),
        width="stretch",
    )

# Log execution time at the end of the file
t2_stop = perf_counter()
logging.info(f"Full battle conditions analysis took {t2_stop - t2_start}")
