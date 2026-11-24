"""Live countdown box shared by the Overview and Battle Conditions pages (styled by tt-countdown-* in style.css)."""

import json

import streamlit as st


def render_countdown(phases: list[dict], footer_html: str = "") -> None:
    """Render a countdown that ticks in the browser through *phases*.

    Each phase is ``{"label": str, "sub": str, "t": epoch_ms}``; the box shows the first phase still in the future,
    or the last one once all have passed. *footer_html* goes inside the box under the clock and must be trusted HTML.
    Rendered inline rather than in an iframe so it sizes to its content and gets the page fonts and stylesheet.
    """
    phases_json = json.dumps(phases, ensure_ascii=False)
    st.html(
        f"""
<div class="tt-countdown">
    <p id="tt-cd-sub" class="tt-countdown-sub"></p>
    <h2 id="tt-cd-label" class="tt-countdown-label"></h2>
    <div class="tt-countdown-row">
        <div><div id="tt-cd-d" class="tt-countdown-num">--</div><div class="tt-countdown-unit">Days</div></div>
        <div class="tt-countdown-sep">:</div>
        <div><div id="tt-cd-h" class="tt-countdown-num">--</div><div class="tt-countdown-unit">Hours</div></div>
        <div class="tt-countdown-sep">:</div>
        <div><div id="tt-cd-m" class="tt-countdown-num">--</div><div class="tt-countdown-unit">Mins</div></div>
        <div class="tt-countdown-sep">:</div>
        <div><div id="tt-cd-s" class="tt-countdown-num">--</div><div class="tt-countdown-unit">Secs</div></div>
    </div>
    {footer_html}
</div>
<script>
// Keep a space after every '<' in here: Streamlit's sanitizer drops a script whose text has '<' before a letter
(function(){{
    var phases={phases_json};
    // One timer per page: each rerun replaces the previous one, and it stops once the box leaves the page
    clearInterval(window.ttCountdownTimer);
    function p(n){{return String(n).padStart(2,'0');}}
    function tick(){{
        var label=document.getElementById('tt-cd-label');
        if(!label){{clearInterval(window.ttCountdownTimer);return;}}
        var now=Date.now(),ph=phases[phases.length-1];
        for(var i=0; i < phases.length; i++){{if(now < phases[i].t){{ph=phases[i];break;}}}}
        label.innerText=ph.label;
        document.getElementById('tt-cd-sub').innerText=ph.sub;
        var r=Math.max(0,ph.t-now);
        document.getElementById('tt-cd-d').innerText=p(Math.floor(r/86400000));
        document.getElementById('tt-cd-h').innerText=p(Math.floor(r%86400000/3600000));
        document.getElementById('tt-cd-m').innerText=p(Math.floor(r%3600000/60000));
        document.getElementById('tt-cd-s').innerText=p(Math.floor(r%60000/1000));
    }}
    tick();
    window.ttCountdownTimer=setInterval(tick,1000);
}})();
</script>""",
        unsafe_allow_javascript=True,
    )
