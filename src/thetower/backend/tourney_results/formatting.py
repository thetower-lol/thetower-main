from __future__ import annotations

import os
from functools import partial
from operator import ge, le
from urllib.parse import quote

import pandas as pd

from .constants import (
    colors,
    colors_018,
    medals,
    position_colors,
    position_stratas,
    stratas,
    stratas_boundaries_018,
    sus_person,
)


def barebones_format(color):
    return color


def simple_format(color):
    return f"color: {color}"


def detailed_format(color, which, how_many):
    color_results = [None for _ in range(how_many)]
    color_results[which] = f"color: {color}"
    return color_results


def color_strata(wave, stratas, colors, operator, formatting_function):
    for strata, color in zip(stratas[::-1], colors[::-1]):
        if operator(wave, strata):
            return formatting_function(color)


color_top = partial(color_strata, stratas=stratas, colors=colors, operator=ge, formatting_function=simple_format)
color_top_18 = partial(color_strata, stratas=stratas_boundaries_018, colors=colors_018, operator=ge, formatting_function=simple_format)
color_position = partial(color_strata, stratas=position_stratas, colors=position_colors, operator=le, formatting_function=simple_format)
color_position_barebones = partial(color_strata, stratas=position_stratas, colors=position_colors, operator=le, formatting_function=barebones_format)


def color_nickname__detail(row, roles_by_id, stratas, colors, operator, formatting_function):
    return color_strata(roles_by_id[row.id], stratas, colors, operator, formatting_function)


detailed_format__base = partial(detailed_format, which=2, how_many=6)
detailed_format__top = partial(detailed_format, which=1, how_many=5)
detailed_format__top_position = partial(detailed_format, which=3, how_many=5)

color_nickname = partial(color_nickname__detail, stratas=stratas, colors=colors, operator=ge, formatting_function=detailed_format__base)
color_nickname__top = partial(color_nickname__detail, stratas=stratas, colors=colors, operator=ge, formatting_function=detailed_format__top)
color_position__top = partial(color_strata, stratas=position_stratas, colors=position_colors, operator=le, formatting_function=simple_format)


def am_i_sus(name):
    if name == sus_person:
        return "color: #FF6666"


def color(value):
    strvalue = str(value)

    if strvalue.startswith("0"):
        return "color: orange"
    elif strvalue.startswith("-"):
        return "color: red"
    else:
        return "color: green"


def strike(text):
    return "\u0336".join(text)


BASE_URL = os.getenv("BASE_URL", "thetower.lol")


def get_url(path, base_url=BASE_URL):
    return f"https://{base_url}/{path}"


def make_url(username, path, id=None, base_url=BASE_URL):
    original_username = username

    if id:
        username = id
    else:
        for medal in medals:
            username = username.strip(medal)

    url = get_url(path, base_url=base_url)

    return f"<a href='{url}?player={quote(username.strip())}'>{original_username}</a>"


make_player_url = partial(make_url, path="player")


def html_to_rgb(color_code, transparency=None):
    """let's trust chatgpt blindly"""
    if color_code.startswith("#") and (len(color_code) == 7 or len(color_code) == 9):
        r = int(color_code[1:3], 16)
        g = int(color_code[3:5], 16)
        b = int(color_code[5:7], 16)

        if transparency is not None:
            return f"rgba({r},{g},{b},{transparency})"
        else:
            return f"rgb({r},{g},{b})"
    else:
        raise ValueError("Invalid HTML color code")


def format_wave(wave) -> str:
    """Render a wave for display: whole waves as integers, fractional ones (the v29+ tiebreaker) trimmed to at most 4 decimals."""
    if wave is None or pd.isna(wave):
        return ""
    value = float(wave)
    if value.is_integer():
        return str(int(value))
    return f"{value:.4f}".rstrip("0").rstrip(".")


def style_wave_and_position(df: pd.DataFrame, wave_col: str = "wave", position_col: str = "#") -> pd.io.formats.style.Styler:
    """Return a Pandas Styler with wave-strata and position coloring applied.

    Prefers the pre-computed ``wave_role_color`` column (present on DataFrames from
    ``get_details()``) for wave coloring; falls back to ``color_top_18`` applied
    directly to raw wave values when that column is absent.  Position coloring via
    ``color_position`` is applied whenever the position column exists.

    Usable on any tournament-results DataFrame — historical standings, live snapshots,
    per-player history, etc.
    """
    styler = df.style

    if "wave_role_color" in df.columns and wave_col in df.columns:

        def _wave_color(row: pd.Series):
            styles = [""] * len(row)
            color = df.at[row.name, "wave_role_color"]
            if color and wave_col in row.index:
                styles[row.index.get_loc(wave_col)] = f"color: {color}"
            return styles

        styler = styler.apply(_wave_color, axis=1)
    elif wave_col in df.columns:
        styler = styler.map(color_top_18, subset=[wave_col])

    if position_col in df.columns:
        styler = styler.map(color_position, subset=[position_col])

    if wave_col in df.columns:
        styler = styler.format(format_wave, subset=[wave_col])

    return styler
