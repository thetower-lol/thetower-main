"""Compatibility shim: per-page shun flags now resolve through the visibility profiles.

Kept so the many call sites keep working unchanged. The page name is ignored except for the two
placement keys; see ``visibility`` for the rules and for how to bring per-page control back if
it is ever needed.
"""

from thetower.backend.tourney_results import visibility


def include_shun_enabled_for(page: str, ttl_seconds: int = 300) -> bool:
    """Whether shunned players are included for this page. ``ttl_seconds`` is accepted and ignored."""
    return visibility.include_enabled_for("shun", page)


def include_shun_invalidate() -> None:
    """Drop the in-process copy of the visibility config."""
    visibility.invalidate()
