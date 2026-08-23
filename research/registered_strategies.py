"""Pre-registered Trendline strategy hypotheses.

These definitions are research contracts. They intentionally do not replace the
existing simulator strategies; they specify exactly which fact events may be
interpreted as a setup and which parameters are strategy-level, not event-level.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict


@dataclass(frozen=True)
class BounceSpec:
    name: str = "bounce"
    no_break_bars: int = 5
    rejection_max_bars: int = 1


@dataclass(frozen=True)
class BreakoutSpec:
    name: str = "breakout"
    continuation_atr: float = 0.25


@dataclass(frozen=True)
class BreakoutRetestSpec:
    name: str = "breakout_retest"
    retest_max_bars: int = 12
    retest_distance_atr: float = 0.40
    rejection_max_bars: int = 1


REGISTERED = {
    "bounce": BounceSpec(),
    "breakout": BreakoutSpec(),
    "breakout_retest": BreakoutRetestSpec(),
}


def registry_payload() -> dict:
    return {name: asdict(spec) for name, spec in REGISTERED.items()}
