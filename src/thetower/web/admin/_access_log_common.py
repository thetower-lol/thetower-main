"""
Shared helpers for access log viewer and stats pages.
"""

import re
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

import streamlit as st

# Log line formats produced by request_logger.py:
#   New (7 fields): 2026-04-01 12:00:00 UTC | public | 203.0.113.42    | /player | - | C249A9 | a3f2b1c4e9d07b21
#   Old (6 fields): 2026-03-21 14:32:01 UTC | public | 203.0.113.42    | /player | - | C249A98DEA598A8D
#   Old (4 fields): 2026-03-21 14:32:01 UTC | 203.0.113.42    | /comparison | player_id=abc123
# Parsed keys in all cases: dt, site, ip, path, qs, ctx, render_id
#
# Render log format produced by request_logger.log_render_complete():
#   New (8 fields, 2.3.14+): a3f2b1c4e9d07b21 | 2026-04-01 12:00:01 UTC | 1423 | 812 | 3 | /livebracketview | Legend | ok
#   Old (3 fields):          a3f2b1c4e9d07b21 | 2026-04-01 12:00:01 UTC | 1423
# Parsed keys: render_id, dt, elapsed_ms; new lines add cpu_ms, inflight, path, league, status

# Matches rotated filenames: web_access.log.2026-03-21_14
FILE_RE = re.compile(r"^web_access\.log\.(\d{4}-\d{2}-\d{2})_(\d{2})$")
# Matches rotated filenames: web_render.log.2026-03-21_14
RENDER_FILE_RE = re.compile(r"^web_render\.log\.(\d{4}-\d{2}-\d{2})_(\d{2})$")


def get_log_dir() -> Path:
    from thetower.backend.env_config import get_csv_data

    log_dir = Path(get_csv_data()) / "web_logs"
    log_dir.mkdir(exist_ok=True)
    return log_dir


def catalog_files(log_dir: Path) -> dict[date, list[tuple[int, Path]]]:
    """Return {date: [(hour, path), ...]} with hours sorted ascending, dates newest first."""
    catalog: dict[date, list[tuple[int, Path]]] = defaultdict(list)

    for f in log_dir.glob("web_access.log.*"):
        m = FILE_RE.match(f.name)
        if m:
            d = date.fromisoformat(m.group(1))
            h = int(m.group(2))
            catalog[d].append((h, f))

    # Current active file — assign to current UTC hour
    current = log_dir / "web_access.log"
    if current.exists():
        now = datetime.now(timezone.utc)
        catalog[now.date()].append((now.hour, current))

    for d in catalog:
        catalog[d].sort(key=lambda x: x[0])

    return dict(sorted(catalog.items(), reverse=True))


def catalog_render_files(log_dir: Path) -> dict[date, list[tuple[int, Path]]]:
    """Return {date: [(hour, path), ...]} for web_render.log files, newest first."""
    catalog: dict[date, list[tuple[int, Path]]] = defaultdict(list)

    for f in log_dir.glob("web_render.log.*"):
        m = RENDER_FILE_RE.match(f.name)
        if m:
            d = date.fromisoformat(m.group(1))
            h = int(m.group(2))
            catalog[d].append((h, f))

    current = log_dir / "web_render.log"
    if current.exists():
        now = datetime.now(timezone.utc)
        catalog[now.date()].append((now.hour, current))

    for d in catalog:
        catalog[d].sort(key=lambda x: x[0])

    return dict(sorted(catalog.items(), reverse=True))


def parse_files(paths: list[Path]) -> list[dict]:
    rows = []
    for path in paths:
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                parts = [p.strip() for p in line.split("|")]
                if len(parts) == 7:
                    dt, site, ip, pg_path, qs, ctx, render_id = parts
                elif len(parts) == 6:
                    dt, site, ip, pg_path, qs, ctx = parts
                    render_id = "-"
                elif len(parts) == 4:
                    # Old format without site/ctx fields
                    dt, ip, pg_path, qs = parts
                    site, ctx, render_id = "-", "-", "-"
                else:
                    continue
                rows.append({"dt": dt, "site": site, "ip": ip, "path": pg_path, "qs": qs, "ctx": ctx, "render_id": render_id})
        except Exception as e:
            st.warning(f"Could not read {path.name}: {e}")
    return rows


def parse_render_files(paths: list[Path]) -> list[dict]:
    """Parse web_render.log files into {render_id, dt, elapsed_ms, ...} dicts.

    Lines written by 2.3.14+ also carry cpu_ms, inflight, path, league and
    status; those keys are present only for such rows.
    """
    rows = []
    for path in paths:
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                parts = [p.strip() for p in line.split("|")]
                if len(parts) < 3:
                    continue
                render_id, dt, elapsed_raw = parts[:3]
                try:
                    elapsed_ms = int(elapsed_raw)
                except ValueError:
                    continue
                row = {"render_id": render_id, "dt": dt, "elapsed_ms": elapsed_ms}
                if len(parts) >= 8:
                    try:
                        row["cpu_ms"] = int(parts[3])
                        row["inflight"] = int(parts[4])
                    except ValueError:
                        pass
                    row["path"], row["league"], row["status"] = parts[5], parts[6], parts[7]
                rows.append(row)
        except Exception as e:
            st.warning(f"Could not read {path.name}: {e}")
    return rows


def all_paths_for_dates(catalog: dict[date, list[tuple[int, Path]]], dates: list[date]) -> list[Path]:
    """Return all file paths across the given dates, in chronological order."""
    paths = []
    for d in sorted(dates):
        paths.extend(path for _, path in catalog.get(d, []))
    return paths
