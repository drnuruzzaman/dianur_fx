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
    RECLAIMED  broke, then price closed back on the working side and STAYED
               there. Opt-in via `reclaim_confirm_bars`; see register_reclaim
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
    #: broke, then price closed back on the working side and stayed there
    RECLAIMED = 'RECLAIMED'
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
    reclaims: int = 0                           # times it came back
    #: Layer D, the wick/close split on breakouts. A wick through the line opens
    #: a CANDIDATE break; only a CLOSE through it confirms one. These count the
    #: candidates, including the ones the close then refused -- which is the
    #: number a breakout strategy needs to know its false-break rate, and which
    #: was previously invisible because a wick-only excursion left no trace.
    break_candidates: int = 0
    break_candidate_open: bool = False
    false_breaks: int = 0                       # wick through, close refused
    reclaimed_at: Optional[int] = None
    _last_touch_i: int = field(default=-10_000, repr=False)
    #: consecutive closes currently beyond tolerance; reset by a close back inside
    _run: int = field(default=0, repr=False)
    #: consecutive closes back on the working side while BROKEN
    _back: int = field(default=0, repr=False)

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
        return self.status in (Status.CANDIDATE, Status.CONFIRMED,
                               Status.ACTIVE, Status.RECLAIMED)

    @property
    def is_tradeable(self) -> bool:
        """Only a confirmed line is worth acting on; a candidate is a guess."""
        return self.status in (Status.CONFIRMED, Status.ACTIVE, Status.RECLAIMED)

    # ---- lifecycle ------------------------------------------------------ #
    def register_touch(self, t_ms, bar_i, min_gap_bars, min_touches=3):
        """
        A retest. Consecutive grazing bars are one event, so counts stay
        comparable across timeframes.

        `min_touches` is the number of DISTINCT touches that promotes a
        candidate to confirmed, anchors included. At the default of 3 a line
        needs one confirmation beyond the two swings that defined it.

        Setting it to 2 makes every line confirmed the moment it is formed,
        because the two anchors already count. That is the common
        hand-drawing convention and it is a real loosening: the
        CANDIDATE -> CONFIRMED distinction disappears, and with it the
        property that "a candidate breaking is a bad guess expiring, not
        news". Break events then include lines nothing ever validated.
        """
        if bar_i - self._last_touch_i < min_gap_bars:
            return False
        self._last_touch_i = bar_i
        self.touches += 1
        self.tests += 1
        self.last_test_at = t_ms
        if self.status is Status.CANDIDATE and self.touches >= min_touches:
            self.status = Status.CONFIRMED
            self.confirmed_at = t_ms
        elif self.status is Status.CONFIRMED:
            self.status = Status.ACTIVE
        return True

    def register_inside(self):
        """
        A close back on the correct side of the line. Resets the consecutive
        run, which is what makes `confirm_bars` mean CONSECUTIVE rather than
        cumulative.
        """
        self._run = 0

    def register_violation(self, t_ms, max_violations, confirm_bars=1):
        """
        A line already BROKEN does not keep breaking. Letting it accumulate
        violations decayed its quality_score for the 40 bars it lingered before
        archiving, so anything reading the score later saw ~0 instead of what the
        line was worth when the break happened.

        `confirm_bars` is the number of CONSECUTIVE closes beyond tolerance
        required before the break counts. At 1 (the default) behaviour is
        exactly as before.

        Why it exists: on gold H1 the engine found a rising support with five
        touches sitting one point under price -- the line a human would draw --
        and had marked it BROKEN, because price closed through it once during
        the rally and a single violation was permanent. The line went on working
        and the engine had already buried it. Requiring two consecutive closes
        is the difference between "price poked through" and "the line failed".
        """
        if self.status is Status.BROKEN:
            return False
        self._back = 0
        self._run += 1
        if self._run < max(1, confirm_bars):
            return False                     # not yet a confirmed break
        self.violations += 1
        if self.violations > max_violations:
            self.status = Status.BROKEN
            self.broken_at = t_ms
            self.quality_at_break = self.quality_score
            return True
        return False

    def register_reclaim(self, t_ms, confirm_bars):
        """
        Price closed back on the working side and STAYED there.

        BROKEN was terminal, and that buried a line the market was still using.
        Measured on gold H1: a rising support with five touches sitting one
        point under price, marked BROKEN because price had left it 29 bars
        earlier -- and of the ten bars since the last violation, seven closed
        back above it. The break was real; the death sentence was not.

        Reclaim is deliberately harder than a break: `confirm_bars` consecutive
        closes back on the working side, versus one to break by default. A line
        should not resurrect because price brushed past it.

        The violation is NOT forgiven -- `violations` stays, so the quality
        score keeps its 12-point penalty, and `quality_at_break` keeps what the
        line was worth when it failed. A reclaimed line is a line with a scar.
        """
        if self.status is not Status.BROKEN:
            self._back = 0
            return False
        self._back += 1
        if self._back < max(1, confirm_bars):
            return False
        self._back = 0
        self._run = 0
        self.status = Status.RECLAIMED
        self.reclaimed_at = t_ms
        self.reclaims += 1
        return True

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
