"""ocr.py
-------
Optional OCR utilities for extracting player ID and version information
from The Tower game screenshots.

Requires the ``ocr`` optional dependency group to be installed::

    pip install "thetower[ocr]"
    # or: pip install pytesseract opencv-python-headless numpy Pillow

On Linux/macOS you also need the Tesseract binary::

    apt install tesseract-ocr   # Debian/Ubuntu
    brew install tesseract      # macOS

On Windows, install from https://github.com/UB-Mannheim/tesseract/wiki
(the script looks for it automatically in Program Files).

Usage::

    from thetower.utils.ocr import is_available, analyze_verification_screenshot

    if is_available():
        result = analyze_verification_screenshot("/path/to/image.png")
        print(result.player_id, result.version, result.build)
"""

import logging
import os
import platform
import re
import shutil
from collections import Counter
from dataclasses import dataclass
from typing import Optional

from thetower.backend.sus.services import is_valid_tower_id

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Availability – try importing the heavy deps at module level so callers can
# check is_available() without triggering an ImportError.
# ---------------------------------------------------------------------------
try:
    import cv2
    import numpy as np  # noqa: F401  (required by cv2)
    import pytesseract

    _OCR_LIBS_AVAILABLE = True
except ImportError:
    _OCR_LIBS_AVAILABLE = False

# ---------------------------------------------------------------------------
# Regular expressions
# ---------------------------------------------------------------------------

# Matches: v27.5.0  or  v27.5.0 (843)  (build in parens is optional)
_VERSION_RE = re.compile(
    r"\bv(?P<ver>\d+\.\d+(?:\.\d+)?)(?:\s*\(\s*(?P<build>\d+)\s*\))?",
    re.IGNORECASE,
)

# Matches the "ID:" label at the start of the ID line
_ID_LINE_RE = re.compile(r"\bID[\s:;]+", re.IGNORECASE)

# OCR character correction map for hex strings
_HEX_FIXES = str.maketrans(
    {
        "O": "0",  # Letter O  -> zero
        "Q": "0",  # Letter Q  -> zero
        "I": "1",  # Letter I  -> one
        "L": "1",  # Letter L  -> one
        "Z": "2",  # Z         -> two
        "G": "6",  # G         -> six
        "S": "5",  # S         -> five
    }
)


# ---------------------------------------------------------------------------
# Public result type
# ---------------------------------------------------------------------------


@dataclass
class OcrResult:
    """Result of OCR analysis on a verification screenshot."""

    has_valid_labels: bool = False  # True if 'subreddit' label detected (Settings screen)
    player_id: Optional[str] = None  # 16-char uppercase hex, or None
    version: Optional[str] = None  # e.g. "27.5.0"
    build: Optional[str] = None  # e.g. "843"
    error: Optional[str] = None  # set if something went wrong during analysis


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def is_available() -> bool:
    """Return True if the OCR libraries (cv2, numpy, pytesseract) are importable."""
    return _OCR_LIBS_AVAILABLE


def analyze_verification_screenshot(image_path: str) -> OcrResult:
    """Analyse a verification screenshot for labels, player ID, and game version.

    This is the single entry-point the validation cog calls.  It performs three
    passes over the image:

    1. A 2× CLAHE pass for label detection and version parsing.
    2. Multi-scale binary/CLAHE variants for robust ID extraction.

    Args:
        image_path: Absolute path to the saved verification image.

    Returns:
        :class:`OcrResult` with all fields populated as well as possible.
        If the OCR libraries are unavailable, returns an OcrResult with
        :attr:`OcrResult.error` set.
    """
    if not _OCR_LIBS_AVAILABLE:
        return OcrResult(error="OCR libraries not installed (pip install 'thetower[ocr]')")

    _ensure_tesseract()

    try:
        img = cv2.imread(str(image_path))
        if img is None:
            return OcrResult(error=f"Could not load image: {image_path}")

        # ------------------------------------------------------------------
        # Pass 1 – label check + version extraction
        # Word-level detection (PSM 3) at 3× is more reliable than full-text
        # PSM 6 for sparse game-UI text.  We require "subreddit" to confirm
        # the screenshot is the Settings screen.  "Discord" is intentionally
        # NOT checked: that button text is rendered in a font weight that
        # Tesseract consistently misreads, while "Subreddit" is reliably
        # detected and is sufficient to confirm the correct screen.
        # ------------------------------------------------------------------
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        gray3x = cv2.resize(gray, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
        label_data = pytesseract.image_to_data(
            gray3x,
            config="--oem 3 --psm 3",
            output_type=pytesseract.Output.DICT,
        )
        label_words = {w.lower() for w in label_data["text"] if w.strip()}
        # "Subreddit" is the primary gate: it reliably OCRs on the Settings
        # screen and is unique to it.  "Discord" uses a heavy font weight that
        # Tesseract sometimes mangles, so it is treated as a secondary signal —
        # either one present is sufficient to confirm the correct screen.
        has_valid_labels = any("subreddit" in w or "reddit" in w or "discord" in w for w in label_words)

        # Fallback: tall screenshots (portrait orientation) can dilute the button
        # region at full-image scale.  Crop to the center button rows (45–75% of
        # height) where the Settings buttons live and re-scan at 3× to catch "Subreddit".
        if not has_valid_labels:
            h, w_img = gray.shape[:2]
            crop = gray[int(h * 0.45) : int(h * 0.75), int(w_img * 0.05) : int(w_img * 0.95)]
            crop3x = cv2.resize(crop, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
            crop_data = pytesseract.image_to_data(crop3x, config="--oem 3 --psm 3", output_type=pytesseract.Output.DICT)
            crop_words = {w.lower() for w in crop_data["text"] if w.strip()}
            has_valid_labels = any("subreddit" in w or "reddit" in w or "discord" in w for w in crop_words)

        # Version + build: run image_to_string on the same 3× gray for line parsing
        gray2x = cv2.resize(gray, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        proc = clahe.apply(gray2x)
        full_text = pytesseract.image_to_string(proc, config="--oem 3 --psm 6")

        # Version + build extraction from the ID line
        version: Optional[str] = None
        build: Optional[str] = None
        lines = [ln.strip() for ln in full_text.splitlines() if ln.strip()]
        for i, line in enumerate(lines):
            if _ID_LINE_RE.search(line):
                after = re.sub(r"^.*?\bID[\s:;]+", "", line, flags=re.IGNORECASE)
                m = _VERSION_RE.search(after) or _VERSION_RE.search(line)
                if not m and i + 1 < len(lines):
                    m = _VERSION_RE.search(lines[i + 1])
                if m:
                    version = m.group("ver")
                    build = m.group("build")
                break

        # ------------------------------------------------------------------
        # Pass 2 – multi-scale variants for ID extraction
        # ------------------------------------------------------------------
        # Restrict Tesseract output to the hex alphabet plus the "ID:" label
        # characters.  In Exo 2 (the font used for player IDs in The Tower)
        # the main error sources are characters outside this set, so the
        # whitelist eliminates most phantom misreads without any training data.
        # "I" is included because the "ID:" label prefix requires it.
        # Note: Tesseract 5 requires -c syntax for config variables.
        _ID_CONFIG = r"--oem 3 --psm 6 -c tessedit_char_whitelist=0123456789ABCDEFabcdefIi:; "
        # Collect all valid 16-char hex candidates across every scale variant and
        # pick the plurality winner.  A single scale can bias toward artifacts
        # introduced by heavy upsampling, so voting across scales improves accuracy.
        candidate_votes: Counter = Counter()
        for _label, variant in _get_variants(img):
            text = pytesseract.image_to_string(variant, config=_ID_CONFIG)
            pid = _parse_id_from_text(text)
            if pid:
                candidate_votes[pid] += 1

        player_id: Optional[str] = candidate_votes.most_common(1)[0][0] if candidate_votes else None

        return OcrResult(
            has_valid_labels=has_valid_labels,
            player_id=player_id,
            version=version,
            build=build,
        )

    except Exception as exc:
        logger.error("OCR analysis failed for %s: %s", image_path, exc, exc_info=True)
        return OcrResult(error=str(exc))


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _ensure_tesseract() -> None:
    """Configure pytesseract to use the system Tesseract binary.

    Checks PATH first, then common Windows install locations.
    Safe to call on Linux/macOS (prefers whatever ``shutil.which`` finds).
    """
    if not _OCR_LIBS_AVAILABLE:
        return
    try:
        cmd = getattr(pytesseract.pytesseract, "tesseract_cmd", None)
        if cmd and shutil.which(cmd):
            return
        which = shutil.which("tesseract")
        if which:
            pytesseract.pytesseract.tesseract_cmd = which
            return
        if platform.system().lower().startswith("win"):
            for p in (
                r"C:\Program Files\Tesseract-OCR\tesseract.exe",
                r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
            ):
                if os.path.isfile(p):
                    pytesseract.pytesseract.tesseract_cmd = p
                    return
    except Exception:
        # Don't raise here; pytesseract will raise with a clear error later.
        pass


def _fix_hex(s: str) -> str:
    """Apply OCR correction map to a candidate hex string."""
    return s.upper().translate(_HEX_FIXES).replace(" ", "")


def _parse_id_from_text(text: str) -> Optional[str]:
    """Search OCR output for the ID line and return a validated hex string.

    Uses centralized is_valid_tower_id() validation (13-16 hex characters).
    """
    for line in text.splitlines():
        if not _ID_LINE_RE.search(line):
            continue
        candidate = re.sub(r"^.*?\bID[\s:;]+", "", line, flags=re.IGNORECASE).strip()
        candidate = candidate.split()[0] if candidate.split() else candidate
        fixed = _fix_hex(candidate)
        # Use centralized validation that matches web verification and services
        if is_valid_tower_id(fixed):
            return fixed
    return None


def _get_variants(img):
    """Yield (label, processed_image) pairs at multiple scales for Tesseract."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    for scale in [3, 4, 2, 1]:
        g = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC) if scale > 1 else gray.copy()
        _, t_binary = cv2.threshold(g, 160, 255, cv2.THRESH_BINARY)
        yield (f"{scale}x_binary", t_binary)
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        _, t_clahe = cv2.threshold(clahe.apply(g), 150, 255, cv2.THRESH_BINARY)
        yield (f"{scale}x_clahe", t_clahe)


# Run tesseract discovery at import time so the first analysis call is fast.
if _OCR_LIBS_AVAILABLE:
    _ensure_tesseract()
