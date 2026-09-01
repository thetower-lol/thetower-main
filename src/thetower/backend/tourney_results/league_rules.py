"""League promotion/relegation rules.

This module provides a single source of truth for bracket mechanics so that
multiple pages (live placement analysis, regression trends, etc.) all apply the
same rules. Rules are kept per era in ``_RULE_ERAS``: when game mechanics change
in a future patch, add a new rules dict and prepend it with its minimum minor
version, keeping the older eras so historical pages show the rewards of their day.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import NamedTuple, Optional


class RewardTier(NamedTuple):
    """Reward values for a bracket place tier."""

    max_rank: int
    """Inclusive upper bound of this tier (e.g. 4 means ranks 1–4 after the previous tier's max_rank+1)."""
    gems: int
    stones: int
    keys: int = 0


@dataclass(frozen=True)
class LeagueRules:
    """Promotion/relegation rules for a league in a given patch era."""

    promote_cutoff: Optional[int]
    """Last place that earns promotion (None = top tier, no promotion possible)."""

    relegate_cutoff: Optional[int]
    """First place that triggers relegation (None = protected tier, no demotion)."""

    bracket_size: int = 30
    """Standard number of players per bracket."""

    reward_boundaries: tuple[float, ...] = field(default_factory=tuple)
    """Reward tier boundary positions (used for histogram rendering)."""

    reward_tiers: tuple[RewardTier, ...] = field(default_factory=tuple)
    """Reward tiers ordered by ascending max_rank."""

    @property
    def last_safe(self) -> Optional[int]:
        """Last place that is safe from relegation, or None if no relegation."""
        return self.relegate_cutoff - 1 if self.relegate_cutoff is not None else None

    @property
    def median_place(self) -> int:
        """Approximate median bracket position (middle of bracket_size)."""
        return self.bracket_size // 2

    def key_places(self) -> list[int]:
        """Signature placement positions for trend analysis.

        Returns the promote cutoff (if applicable), the median position, and
        the last safe position (if applicable), in ascending order.
        """
        places: list[int] = []
        if self.promote_cutoff is not None:
            places.append(self.promote_cutoff)
        places.append(self.median_place)
        if self.last_safe is not None:
            places.append(self.last_safe)
        return places

    @property
    def has_keys(self) -> bool:
        """True if any reward tier in this league awards keys."""
        return any(t.keys > 0 for t in self.reward_tiers)

    def rewards_for_place(self, place: int) -> RewardTier | None:
        """Return the RewardTier for a given bracket place, or None if no data."""
        for tier in self.reward_tiers:
            if place <= tier.max_rank:
                return tier
        return None

    def place_label(self, place: int) -> str:
        """Human-readable label describing a place's bracket significance."""
        if place == self.promote_cutoff:
            return f"#{place} (last promote)"
        if place == self.relegate_cutoff:
            return f"#{place} (first relegate)"
        if place == self.last_safe:
            return f"#{place} (last safe)"
        if place == self.median_place:
            return f"#{place} (median)"
        return f"#{place}"


# ---------------------------------------------------------------------------
# Historical rules — patches v25 through v28 (30-player brackets, Legend on top).
# ---------------------------------------------------------------------------
_RULES_V25: dict[str, LeagueRules] = {
    "Copper": LeagueRules(
        promote_cutoff=4,
        relegate_cutoff=None,
        reward_boundaries=(1.5, 2.5, 4.5, 6.5, 8.5, 10.5, 12.5, 15.5, 22.5),
        reward_tiers=(
            RewardTier(1, 100, 20),
            RewardTier(2, 80, 18),
            RewardTier(4, 65, 16),
            RewardTier(6, 50, 12),
            RewardTier(8, 45, 10),
            RewardTier(10, 40, 9),
            RewardTier(12, 30, 8),
            RewardTier(15, 20, 7),
            RewardTier(22, 15, 6),
            RewardTier(30, 10, 5),
        ),
    ),
    "Silver": LeagueRules(
        promote_cutoff=4,
        relegate_cutoff=None,
        reward_boundaries=(1.5, 2.5, 4.5, 6.5, 8.5, 10.5, 12.5, 15.5, 22.5),
        reward_tiers=(
            RewardTier(1, 200, 40),
            RewardTier(2, 150, 35),
            RewardTier(4, 100, 30),
            RewardTier(6, 75, 20),
            RewardTier(8, 65, 19),
            RewardTier(10, 60, 18),
            RewardTier(12, 55, 17),
            RewardTier(15, 50, 16),
            RewardTier(22, 45, 14),
            RewardTier(30, 40, 12),
        ),
    ),
    "Gold": LeagueRules(
        promote_cutoff=4,
        relegate_cutoff=None,
        reward_boundaries=(1.5, 2.5, 4.5, 6.5, 8.5, 10.5, 12.5, 15.5, 22.5),
        reward_tiers=(
            RewardTier(1, 300, 80),
            RewardTier(2, 250, 70),
            RewardTier(4, 200, 60),
            RewardTier(6, 150, 40),
            RewardTier(8, 125, 30),
            RewardTier(10, 100, 28),
            RewardTier(12, 90, 26),
            RewardTier(15, 80, 24),
            RewardTier(22, 70, 22),
            RewardTier(30, 50, 20),
        ),
    ),
    "Platinum": LeagueRules(
        promote_cutoff=4,
        relegate_cutoff=25,
        reward_boundaries=(1.5, 2.5, 4.5, 6.5, 8.5, 10.5, 12.5, 15.5, 24.5),
        reward_tiers=(
            RewardTier(1, 400, 160),
            RewardTier(2, 350, 140),
            RewardTier(4, 300, 120),
            RewardTier(6, 250, 70),
            RewardTier(8, 225, 65),
            RewardTier(10, 200, 60),
            RewardTier(12, 175, 56),
            RewardTier(15, 150, 53),
            RewardTier(24, 125, 50),
            RewardTier(30, 100, 20),
        ),
    ),
    "Champion": LeagueRules(
        promote_cutoff=4,
        relegate_cutoff=25,
        reward_boundaries=(1.5, 2.5, 4.5, 6.5, 8.5, 10.5, 12.5, 15.5, 24.5),
        reward_tiers=(
            RewardTier(1, 600, 320),
            RewardTier(2, 500, 300),
            RewardTier(4, 400, 280),
            RewardTier(6, 350, 200),
            RewardTier(8, 325, 175),
            RewardTier(10, 300, 150),
            RewardTier(12, 275, 125),
            RewardTier(15, 250, 100),
            RewardTier(24, 200, 90),
            RewardTier(30, 150, 20),
        ),
    ),
    "Legend": LeagueRules(
        promote_cutoff=None,
        relegate_cutoff=25,
        reward_boundaries=(1.5, 2.5, 4.5, 6.5, 8.5, 10.5, 12.5, 15.5, 24.5),
        reward_tiers=(
            RewardTier(1, 800, 425, 25),
            RewardTier(2, 700, 400, 20),
            RewardTier(4, 600, 375, 15),
            RewardTier(6, 500, 350, 10),
            RewardTier(8, 475, 325, 8),
            RewardTier(10, 450, 300, 6),
            RewardTier(12, 425, 275, 4),
            RewardTier(15, 400, 250, 2),
            RewardTier(24, 375, 225, 0),
            RewardTier(30, 200, 120, 0),
        ),
    ),
}


# ---------------------------------------------------------------------------
# Current rules — valid for patch v29 onwards (30-player brackets, Mythic league).
# ---------------------------------------------------------------------------
_RULES_V29: dict[str, LeagueRules] = {
    "Copper": LeagueRules(
        promote_cutoff=4,
        relegate_cutoff=None,
        reward_boundaries=(1.5, 2.5, 4.5, 6.5, 8.5, 10.5, 12.5, 15.5, 22.5),
        reward_tiers=(
            RewardTier(1, 100, 20),
            RewardTier(2, 80, 18),
            RewardTier(4, 65, 16),
            RewardTier(6, 50, 12),
            RewardTier(8, 45, 10),
            RewardTier(10, 40, 9),
            RewardTier(12, 30, 8),
            RewardTier(15, 20, 7),
            RewardTier(22, 15, 6),
            RewardTier(30, 10, 5),
        ),
    ),
    "Silver": LeagueRules(
        promote_cutoff=4,
        relegate_cutoff=None,
        reward_boundaries=(1.5, 2.5, 4.5, 6.5, 8.5, 10.5, 12.5, 15.5, 22.5),
        reward_tiers=(
            RewardTier(1, 200, 40),
            RewardTier(2, 150, 35),
            RewardTier(4, 100, 30),
            RewardTier(6, 75, 20),
            RewardTier(8, 65, 19),
            RewardTier(10, 60, 18),
            RewardTier(12, 55, 17),
            RewardTier(15, 50, 16),
            RewardTier(22, 45, 14),
            RewardTier(30, 40, 12),
        ),
    ),
    "Gold": LeagueRules(
        promote_cutoff=4,
        relegate_cutoff=None,
        reward_boundaries=(1.5, 2.5, 4.5, 6.5, 8.5, 10.5, 12.5, 15.5, 22.5),
        reward_tiers=(
            RewardTier(1, 300, 80),
            RewardTier(2, 250, 70),
            RewardTier(4, 200, 60),
            RewardTier(6, 150, 40),
            RewardTier(8, 125, 30),
            RewardTier(10, 100, 28),
            RewardTier(12, 90, 26),
            RewardTier(15, 80, 24),
            RewardTier(22, 70, 22),
            RewardTier(30, 50, 20),
        ),
    ),
    "Platinum": LeagueRules(
        promote_cutoff=4,
        relegate_cutoff=25,
        reward_boundaries=(1.5, 2.5, 4.5, 6.5, 8.5, 10.5, 12.5, 15.5, 24.5),
        reward_tiers=(
            RewardTier(1, 425, 180),
            RewardTier(2, 400, 160),
            RewardTier(4, 375, 140),
            RewardTier(6, 275, 80),
            RewardTier(8, 260, 70),
            RewardTier(10, 245, 60),
            RewardTier(12, 230, 56),
            RewardTier(15, 215, 53),
            RewardTier(24, 200, 50),
            RewardTier(30, 100, 20),
        ),
    ),
    "Champion": LeagueRules(
        promote_cutoff=4,
        relegate_cutoff=25,
        reward_boundaries=(1.5, 2.5, 4.5, 6.5, 8.5, 10.5, 12.5, 15.5, 24.5),
        reward_tiers=(
            RewardTier(1, 550, 300),
            RewardTier(2, 525, 280),
            RewardTier(4, 500, 260),
            RewardTier(6, 375, 180),
            RewardTier(8, 360, 170),
            RewardTier(10, 345, 160),
            RewardTier(12, 330, 150),
            RewardTier(15, 315, 125),
            RewardTier(24, 300, 100),
            RewardTier(30, 175, 20),
        ),
    ),
    "Legend": LeagueRules(
        promote_cutoff=4,
        relegate_cutoff=25,
        reward_boundaries=(1.5, 2.5, 4.5, 6.5, 8.5, 10.5, 12.5, 15.5, 24.5),
        reward_tiers=(
            RewardTier(1, 675, 375, 25),
            RewardTier(2, 650, 350, 20),
            RewardTier(4, 625, 325, 15),
            RewardTier(6, 475, 275, 10),
            RewardTier(8, 460, 260, 8),
            RewardTier(10, 445, 245, 6),
            RewardTier(12, 430, 230, 4),
            RewardTier(15, 415, 215, 2),
            RewardTier(24, 400, 200, 1),
            RewardTier(30, 250, 100, 0),
        ),
    ),
    "Mythic": LeagueRules(
        promote_cutoff=None,
        relegate_cutoff=25,
        reward_boundaries=(1.5, 2.5, 4.5, 6.5, 8.5, 10.5, 12.5, 15.5, 24.5),
        reward_tiers=(
            RewardTier(1, 900, 475, 50),
            RewardTier(2, 800, 450, 45),
            RewardTier(4, 700, 425, 40),
            RewardTier(6, 650, 400, 37),
            RewardTier(8, 600, 385, 34),
            RewardTier(10, 575, 370, 31),
            RewardTier(12, 550, 355, 28),
            RewardTier(15, 525, 340, 25),
            RewardTier(24, 500, 325, 20),
            RewardTier(30, 325, 225, 8),
        ),
    ),
}


# Rule eras, newest first; each dict applies from its minimum minor version onward.
# Patches predating the oldest era fall back to it.
_RULE_ERAS: tuple[tuple[int, dict[str, LeagueRules]], ...] = (
    (29, _RULES_V29),
    (25, _RULES_V25),
)


def get_league_rules(league: str, patch=None) -> LeagueRules:
    """Return promotion/relegation rules for a league.

    Args:
        league: League name (e.g. ``"Mythic"``, ``"Legend"``).
        patch:  Optional ``PatchNew`` instance selecting the rule era by its
                minor version. Without one, the current (newest) era applies.

    Returns:
        :class:`LeagueRules` for the given league, defaulting to Legend rules
        if the league name is not recognised in the selected era.
    """
    version_minor = getattr(patch, "version_minor", None)
    if version_minor is None:
        rules = _RULE_ERAS[0][1]
    else:
        rules = _RULE_ERAS[-1][1]
        for min_minor, era_rules in _RULE_ERAS:
            if version_minor >= min_minor:
                rules = era_rules
                break
    return rules.get(league, rules["Legend"])
