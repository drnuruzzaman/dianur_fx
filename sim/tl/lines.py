"""
lines.py — the Trendline object, its lifecycle and its quality score.

The trendline engine is deliberately independent of any trading strategy: it
describes structure, it does not decide trades. A strategy consumes these
objects; nothing here knows what an entry is.

LIFECYCLE

    CANDIDATE  two pivots define a line, nothing has confirmed it yet
    CONFIRMED  a third touch arrived: the market has acknowledged the line
    ACTIVE     confirmed and still respected, tested recently
    BROKEN     price closed through it beyond tolerance
    ARCHIVED   broken long enough ago, or drifted too far from price, to matter

Transitions are one-way except ACTIVE <-> CONFIRMED, and every transition
records the bar time it happened on, so a feature row can say how old a line was
and when it was last tested without recomputing anything.

A line is anchored in TIME, not bar index, so a 4H line can be evaluated on a
15M chart. `value_at(t)` is the price of the line at any timestamp.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class Status(str, Enum):
    CANDIDATE = 'CANDIDATE'
    CONFIRMED = 'CONFIRMED'
    ACTIVE = 'ACTIVE'
    BROKEN = 'BROKEN'
    ARCHIVED = 'ARCHIVED'


class Role(str, Enum):
    SUPPORT = 'support'
    RESISTANCE = 'resistance'


class Direction(str, Enum):
    UP = 'up'
    DOWN = 'down'
    HORIZONTAL = 'horizontal'


# A line whose slope moves less than this fraction of ATR per bar is flat enough
# to call horizontal: on gold a 0.01/bar slope is noise, on USDJPY it is not, so
# the threshold has to be volatility-relative rather than absolute.
HORIZONTAL_ATR_PER_BAR = 0.02


@dataclass
class Trendline:
    """One line, with the full per-line feature set from the spec."""

    id: str
    timeframe: str
    role: Role                      # support | resistance
    direction: Direction            # up | down | horizontal
    pivot_1: dict                   # {t, price, i}
    pivot_2: dict
    slope: float                    # price per millisecond
    intercept: float                # price at t = pivot_1.t
    created_at: int                 # ms, bar time the line was first formed
    status: Status = Status.CANDIDATE
    confirmed_at: Optional[int] = None
    last_test_at: Optional[int] = None
    broken_at: Optional[int] = None
    archived_at: Optional[int] = None
    touches: int = 2                # the two anchors count
    tests: int = 0                  # approaches that did not break it
    violations: int = 0             # closes beyond tolerance
    quality_score: float = 0.0
    age_bars: int = 0
    span_bars: int = 0              # anchor-to-anchor, in bars of its own tf
    atr_at_creation: float = 0.0
    last_price_distance_atr: float = 0.0
    quality_at_break: Optional[float] = None    # frozen when it broke
    _last_touch_i: int = field(default=-10_000, repr=False)

    # ---- geometry ------------------------------------------------------- #
    def value_at(self, t_ms) -> float:
        """Price of the line at any timestamp — the basis of MTF projection."""
        return self.intercept + self.slope * (t_ms - self.pivot_1['t'])

    @property
    def type(self) -> str:
        """`type` in the spec: what kind of structure this line represents."""
        if self.direction is Direction.HORIZONTAL:
            return 'horizontal_%s' % self.role.value
        rising = self.direction is Direction.UP
        if self.role is Role.SUPPORT:
            return 'rising_support' if rising else 'falling_support'
        return 'rising_resistance' if rising else 'falling_resistance'

    @property
    def age(self) -> int:
        """Bars since creation, in the line's own timeframe."""
        return self.age_bars

    @property
    def is_live(self) -> bool:
        return self.status in (Status.CANDIDATE, Status.CONFIRMED, Status.ACTIVE)

    @property
    def is_tradeable(self) -> bool:
        """Only a confirmed line is worth acting on; a candidate is a guess."""
        return self.status in (Status.CONFIRMED, Status.ACTIVE)

    # ---- lifecycle ------------------------------------------------------ #
    def register_touch(self, t_ms, bar_i, min_gap_bars):
        """
        A retest. Consecutive grazing bars are one event, so counts stay
        comparable across timeframes, and the third distinct touch is what
        promotes a candidate to confirmed.
        """
        if bar_i - self._last_touch_i < min_gap_bars:
            return False
        self._last_touch_i = bar_i
        self.touches += 1
        self.tests += 1
        self.last_test_at = t_ms
        if self.status is Status.CANDIDATE and self.touches >= 3:
            self.status = Status.CONFIRMED
            self.confirmed_at = t_ms
        elif self.status is Status.CONFIRMED:
            self.status = Status.ACTIVE
        return True

    def register_violation(self, t_ms, max_violations):
        """
        A line already BROKEN does not keep breaking. Letting it accumulate
        violations decayed its quality_score for the 40 bars it lingered before
        archiving, so anything reading the score later saw ~0 instead of what the
        line was worth when the break happened.
        """
        if self.status is Status.BROKEN:
            return False
        self.violations += 1
        if self.violations > max_violations:
            self.status = Status.BROKEN
            self.broken_at = t_ms
            self.quality_at_break = self.quality_score
            return True
        return False

    def archive(self, t_ms, reason=''):
        self.status = Status.ARCHIVED
        self.archived_at = t_ms
        self.archive_reason = reason

    # ---- scoring -------------------------------------------------------- #
    def score(self, bars_seen: int, window: int, last_close: float, atr: float) -> float:
        """
        Quality, 0..100. Weighted so the things that make a line tradeable
        dominate: retests first, then how far it reaches, then how recently the
        market respected it, minus distance from price (a true line 8 ATR away
        is not actionable today) and minus any violation history.
        """
        touch_pts = min(40.0, (self.touches - 2) * 13.0 + 14.0)
        span_pts = min(20.0, 20.0 * (self.span_bars / max(window, 1)))
        if self.last_test_at is None:
            recency_pts = 0.0
        else:
            gap = max(0, bars_seen - self._last_touch_i)
            recency_pts = 20.0 * max(0.0, 1.0 - gap / max(window * 0.5, 1))
        dist = abs(self.value_at(self.pivot_2['t']) - last_close)
        self.last_price_distance_atr = (dist / atr) if atr else 0.0
        prox_pts = 20.0 * max(0.0, 1.0 - self.last_price_distance_atr / 6.0)
        penalty = 12.0 * self.violations
        confirm_bonus = 6.0 if self.status in (Status.CONFIRMED, Status.ACTIVE) else 0.0
        self.quality_score = round(
            max(0.0, min(100.0, touch_pts + span_pts + recency_pts + prox_pts
                         + confirm_bonus - penalty)), 2)
        return self.quality_score

    # ---- serialisation -------------------------------------------------- #
    def to_row(self) -> dict:
        """Flat dict for a per-trendline CSV — the spec's field list."""
        return {
            'id': self.id, 'timeframe': self.timeframe, 'type': self.type,
            'direction': self.direction.value,
            'support_resistance': self.role.value,
            'pivot_1_t': self.pivot_1['t'], 'pivot_1_price': self.pivot_1['price'],
            'pivot_2_t': self.pivot_2['t'], 'pivot_2_price': self.pivot_2['price'],
            'slope': self.slope, 'intercept': self.intercept,
            'created_at': self.created_at, 'confirmed_at': self.confirmed_at,
            'last_test_at': self.last_test_at, 'broken_at': self.broken_at,
            'status': self.status.value, 'touches': self.touches,
            'tests': self.tests, 'violations': self.violations,
            'quality_score': self.quality_score,
            'quality_at_break': self.quality_at_break, 'age': self.age,
            'span_bars': self.span_bars,
            'distance_atr': round(self.last_price_distance_atr, 3),
        }


def classify_direction(slope_per_ms, tf_ms, atr) -> Direction:
    """Volatility-relative, so 'horizontal' means the same thing on any symbol."""
    if not atr:
        return Direction.HORIZONTAL
    per_bar = slope_per_ms * tf_ms
    if abs(per_bar) < HORIZONTAL_ATR_PER_BAR * atr:
        return Direction.HORIZONTAL
    return Direction.UP if per_bar > 0 else Direction.DOWN
