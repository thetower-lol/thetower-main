"""
Basic web request logger for the Streamlit site.

Logs each unique page visit (by URL path) to a rotating access log file.
Only logs once per URL per session to avoid spamming on Streamlit re-runs.

Render timing is tracked via a separate web_render.log file.  Each access
log entry includes a render_id (16-char hex) that links to the matching
render timing entry.  Call start_render() before pg.run() and
log_render_complete() in a ``finally`` after it; the render line records wall
time, the script thread's CPU time, how many runs were in flight, the page,
the league and how the run ended.
"""

import logging
import logging.handlers
import os
import secrets
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple
from urllib.parse import urlparse

import streamlit as st
import streamlit.runtime.scriptrunner as _scriptrunner

logger = logging.getLogger("web.access")
_render_logger = logging.getLogger("web.render")
_configured = False
_render_configured = False

# Module-level dedup: {session_id: (dedup_key, monotonic_timestamp)}
# Keyed by Streamlit session ID so it works across both script runs per navigation,
# unlike session_state which can be cleared between the routing and render runs.
# Entries expire after _SESSION_DEDUP_TTL seconds, which:
#   - prevents double-logging from Streamlit's automatic reruns (< 2 seconds apart)
#   - allows re-logging a genuine refresh or revisit after the TTL elapses
_SESSION_DEDUP_TTL: float = 30.0  # seconds
_SESSION_PURGE_INTERVAL: int = 200  # purge stale entries every N writes
_session_write_count: int = 0
_session_last_url: dict[str, tuple[str, float]] = {}

# Script runs currently executing (every run, dedup'd or not), so each render
# line can record the queue depth it started behind.
_inflight: int = 0
_inflight_lock = threading.Lock()


class RenderStats(NamedTuple):
    """Per-run numbers returned by log_render_complete(), shown unlabeled in the sidebar badge."""

    elapsed_ms: int
    cpu_ms: int
    inflight: int


def _purge_stale_sessions() -> None:
    """Remove session dedup entries older than _SESSION_DEDUP_TTL seconds."""
    cutoff = time.monotonic() - _SESSION_DEDUP_TTL
    stale = [k for k, (_, ts) in _session_last_url.items() if ts < cutoff]
    for k in stale:
        del _session_last_url[k]


def _get_session_id() -> str:
    try:
        ctx = _scriptrunner.get_script_run_ctx()
        return ctx.session_id if ctx else "unknown"
    except Exception:
        return "unknown"


def _make_rotating_handler(log_file: Path) -> logging.handlers.TimedRotatingFileHandler:
    """Return a hourly-rotating file handler with our custom suffix format."""
    import re as _re

    handler = logging.handlers.TimedRotatingFileHandler(
        log_file,
        when="h",
        backupCount=720,  # keep 30 days × 24 hours
        encoding="utf-8",
        utc=True,
    )
    handler.suffix = "%Y-%m-%d_%H"
    handler.extMatch = _re.compile(r"^\d{4}-\d{2}-\d{2}_\d{2}$", _re.ASCII)
    handler.setFormatter(logging.Formatter("%(message)s"))
    return handler


def _setup_logger() -> None:
    global _configured
    if _configured:
        return

    try:
        from thetower.backend.env_config import get_csv_data

        log_dir = Path(get_csv_data()) / "web_logs"
        log_dir.mkdir(exist_ok=True)
        log_file = log_dir / "web_access.log"
    except Exception:
        log_file = Path("web_access.log")

    logger.addHandler(_make_rotating_handler(log_file))
    logger.setLevel(logging.INFO)
    logger.propagate = False
    _configured = True


def _setup_render_logger() -> None:
    global _render_configured
    if _render_configured:
        return

    try:
        from thetower.backend.env_config import get_csv_data

        log_dir = Path(get_csv_data()) / "web_logs"
        log_dir.mkdir(exist_ok=True)
        log_file = log_dir / "web_render.log"
    except Exception:
        log_file = Path("web_render.log")

    _render_logger.addHandler(_make_rotating_handler(log_file))
    _render_logger.setLevel(logging.INFO)
    _render_logger.propagate = False
    _render_configured = True


def _get_page_context(path: str) -> str:
    """Return page-specific context string from session/query state, or '-'.

    Called before pg.run(), so session_state reflects the *previous* script
    execution — which is exactly when meaningful player/comparison context exists.
    Using this as part of the dedup key means navigating to /player for a
    different player will generate a new log entry even though the URL is unchanged.
    """
    try:
        if path == "/player":
            player_id = st.session_state.get("player_id")
            if not player_id:
                options = st.session_state.get("options")
                player_id = getattr(options, "current_player", None) if options else None
            return str(player_id) if player_id else "-"

        if path == "/comparison":
            bracket_player = st.query_params.get("bracket_player")
            if bracket_player:
                return f"bracket={bracket_player}"
            players = st.session_state.get("comparison", [])
            if players:
                return "players=" + ",".join(str(p) for p in players[:5])
            return "-"
    except Exception:
        pass
    return "-"


def _get_client_ip() -> str:
    """Return the real visitor IP, preferring Cloudflare's header over generic proxy headers."""
    try:
        headers = st.context.headers
        for header in ("cf-connecting-ip", "x-forwarded-for", "x-real-ip"):
            value = headers.get(header)
            if value:
                # x-forwarded-for can be a comma-separated chain; take the first (client) IP
                return value.split(",")[0].strip()
        # No proxy headers — likely a direct/local request; use the Host as a hint
        return headers.get("host", "localhost")
    except Exception:
        pass
    return "unknown"


def log_request() -> tuple[str, str]:
    """Log a web request, skipping duplicate logs within the same session.

    Should be called once per page run, just before pg.run() in pages.py.
    Uses a module-level dict keyed by Streamlit session ID to deduplicate across
    both script runs that Streamlit performs per navigation event.

    Returns (path, render_id).  render_id is a 16-char hex token written as the
    7th field of the access log line; pass it to log_render_complete() after
    pg.run() to link the render timing back to this visit.  On a dedup'd run
    (second script execution for the same navigation), render_id is "" — callers
    should skip log_render_complete() in that case.
    """
    _setup_logger()

    try:
        current_url = str(st.context.url)
        path = urlparse(current_url).path or "/"
    except Exception:
        try:
            path = f"/?{dict(st.query_params)}"
        except Exception:
            path = "/"
        current_url = path

    render_id = secrets.token_hex(8)

    site = "hidden" if os.environ.get("HIDDEN_FEATURES") else "public"
    ctx = _get_page_context(path)

    session_id = _get_session_id()
    dedup_key = f"{current_url}|{ctx}"
    cached = _session_last_url.get(session_id)
    if cached is not None:
        cached_key, ts = cached
        if cached_key == dedup_key and time.monotonic() - ts < _SESSION_DEDUP_TTL:
            return path, ""
    global _session_write_count
    _session_last_url[session_id] = (dedup_key, time.monotonic())
    _session_write_count += 1
    if _session_write_count % _SESSION_PURGE_INTERVAL == 0:
        _purge_stale_sessions()

    try:
        query_params = dict(st.query_params)
    except Exception:
        query_params = {}

    ip = _get_client_ip()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    qs = "&".join(f"{k}={v}" for k, v in query_params.items()) if query_params else "-"

    logger.info("%s | %-6s | %-15s | %s | %s | %s | %s", now, site, ip, path, qs, ctx, render_id)
    return path, render_id


def start_render() -> tuple[float, float, int]:
    """Mark the start of a script run; call just before pg.run().

    Increments the in-flight counter and returns (wall_start, cpu_start,
    inflight) for log_render_complete().  ``inflight`` is the number of script
    runs executing at this moment, this one included.
    """
    global _inflight
    with _inflight_lock:
        _inflight += 1
        inflight = _inflight
    return time.perf_counter(), time.thread_time(), inflight


def _get_league(path: str) -> str:
    """League the page rendered for, or '-'.

    An explicit ``league`` query param wins.  Live pages set
    ``session_state.selected_league`` on every run via setup_common_ui, so it is
    current for them; on other pages it may be left over from an earlier visit,
    so it is only trusted under /live.
    """
    try:
        league = st.query_params.get("league")
        if not league and path.startswith("/live"):
            league = st.session_state.get("selected_league")
        return str(league) if league else "-"
    except Exception:
        return "-"


def log_render_complete(render_id: str, path: str, clock: tuple[float, float, int], status: str = "ok") -> RenderStats:
    """Release the in-flight slot and write a render-timing entry to web_render.log.

    Call in a ``finally`` after pg.run() in pages.py with the render_id and path
    from log_request() and the tuple from start_render().  Always decrements the
    in-flight counter; only writes a line when render_id is non-empty (i.e. the
    visit was logged rather than dedup'd).  Returns RenderStats in every case,
    for the sidebar badge.

    Line format (pipe-separated):
        render_id | dt | elapsed_ms | cpu_ms | inflight | path | league | status
    elapsed_ms is wall time.  cpu_ms is CPU time consumed by this script thread
    (time.thread_time), so elapsed minus cpu is time spent waiting on the GIL or
    IO.  inflight is the number of script runs executing when this one started.
    status is "ok" or the class name of the exception that ended the run;
    Streamlit's control-flow exceptions (StopException, RerunException) appear
    by name too.
    """
    global _inflight
    with _inflight_lock:
        _inflight -= 1
    wall_start, cpu_start, inflight = clock
    elapsed_ms = int((time.perf_counter() - wall_start) * 1000)
    cpu_ms = int((time.thread_time() - cpu_start) * 1000)
    stats = RenderStats(elapsed_ms, cpu_ms, inflight)
    if not render_id:
        return stats
    _setup_render_logger()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    _render_logger.info(
        "%s | %s | %d | %d | %d | %s | %s | %s",
        render_id,
        now,
        elapsed_ms,
        cpu_ms,
        inflight,
        path,
        _get_league(path),
        status,
    )
    return stats
