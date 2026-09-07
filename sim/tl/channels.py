"""
channels.py — parallel channels, derived from the lines the engine already found.

A channel is the structure a trader actually reads off a chart: not "there is a
support line here" but "price has been travelling between these two rails". The
engine finds single lines; this pairs them.

TWO KINDS, because charts contain both:

  PAIRED     a support line and a resistance line that are independently
             confirmed and happen to run parallel. The strong case: both rails
             have their own touches, so neither is an assumption.

  PROJECTED  one confirmed line plus a parallel copy pushed out to the furthest
             opposite-side pivot inside its span. This is how a parallel channel
             is normally drawn by hand, and it is a WEAKER claim -- the far rail
             has one anchor and no confirmation. It is marked as such and scored
             below any paired channel, rather than being quietly presented as
             the same kind of object.

CAUSALITY comes for free. This is a pure function of one Snapshot's live lines
plus bars up to that snapshot's index. It never looks at a later bar, because it
is never handed one. That is also why it is not a second incremental engine:
there is no state to leak.

CONTAINMENT is the test that stops this finding channels everywhere. Two roughly
parallel lines can always be drawn; what makes them a channel is that price
respected them, so the fraction of closes actually sitting between the rails is
measured over the overlap window and a channel below `min_containment` is
discarded. Without it, every pair of same-slope lines in the population becomes
a "channel" and the object means nothing.

WIDTH is bounded in ATR at both ends. A channel narrower than ~1 ATR is inside
the noise and price crosses it constantly; one wider than `max_width_atr` is not
describing a channel, it is describing the chart.
"""

from dataclasses import dataclass, field
from typing import List, Optional

import numpy as np

from .lines import Direction, Role, Status, Trendline

# The two rails must agree on slope this closely, expressed as the difference in
# price-per-bar as a fraction of ATR. Real channels are drawn by eye and are
# never exactly parallel, so demanding equality would find nothing.
SLOPE_TOL_ATR_PER_BAR = 0.035


@dataclass
class Channel:
    """Two rails and the corridor between them."""

    id: str
    timeframe: str
    kind: str                      # 'paired' | 'projected'
    direction: Direction
    lower: Trendline               # the support-side rail
    upper: Trendline               # the resistance-side rail
    # Rails are anchored in TIME like the lines they come from, so a channel
    # found on 4h can be drawn on a 15m chart without re-detection.
    slope: float                   # mean price-per-ms of the two rails
    t_start: int
    t_end: int
    width_atr: float = 0.0
    containment: float = 0.0       # fraction of closes between the rails
    touches_lower: int = 0
    touches_upper: int = 0
    bars: int = 0
    quality_score: float = 0.0
    projected_side: Optional[str] = None   # which rail was assumed, if any

    # ---- geometry ------------------------------------------------------- #
    def lower_at(self, t_ms) -> float:
        return self.lower.value_at(t_ms)

    def upper_at(self, t_ms) -> float:
        return self.upper.value_at(t_ms)

    def median_at(self, t_ms) -> float:
        """The dashed centre line every hand-drawn channel carries."""
        return 0.5 * (self.lower_at(t_ms) + self.upper_at(t_ms))

    def position_at(self, t_ms, price) -> float:
        """
        Where a price sits in the corridor: 0 at the lower rail, 1 at the upper.
        Outside the channel this goes below 0 or above 1, which is the useful
        part -- it is how a break reads numerically.
        """
        lo, hi = self.lower_at(t_ms), self.upper_at(t_ms)
        if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
            return float('nan')
        return (price - lo) / (hi - lo)

    @property
    def type(self) -> str:
        if self.direction is Direction.UP:
            return 'ascending_channel'
        if self.direction is Direction.DOWN:
            return 'descending_channel'
        return 'horizontal_channel'

    def to_row(self) -> dict:
        return {
            'id': self.id, 'timeframe': self.timeframe, 'type': self.type,
            'kind': self.kind, 'direction': self.direction.value,
            'slope': self.slope, 't_start': self.t_start, 't_end': self.t_end,
            'lower_id': self.lower.id, 'upper_id': self.upper.id,
            'width_atr': round(self.width_atr, 3),
            'containment': round(self.containment, 3),
            'touches_lower': self.touches_lower,
            'touches_upper': self.touches_upper,
            'bars': self.bars,
            'quality_score': self.quality_score,
            'projected_side': self.projected_side,
        }


@dataclass
class ChannelParams:
    slope_tol: float = SLOPE_TOL_ATR_PER_BAR
    min_width_atr: float = 1.0     # narrower than this is inside the noise
    max_width_atr: float = 8.0     # wider than this describes the chart, not a channel
    min_containment: float = 0.75  # closes that must sit between the rails
    min_overlap_bars: int = 20     # rails must coexist for at least this long
    # BOTH rails must have been tested. Without this the detector happily
    # returns a channel whose upper rail price never went near -- containment
    # is satisfied trivially by a corridor drawn wide enough to contain
    # everything, and the object stops meaning "price travelled between these".
    min_touches_each: int = 2
    # Two channels whose rails sit within this many ATR of each other at the
    # current bar are the same corridor found from slightly different anchors.
    dedupe_atr: float = 0.5
    allow_projected: bool = True
    max_channels: int = 3          # per timeframe, best first


def _overlap(a: Trendline, b: Trendline):
    """Bar-index window where both lines' anchor spans coexist."""
    a0, a1 = a.pivot_1['i'], a.pivot_2['i']
    b0, b1 = b.pivot_1['i'], b.pivot_2['i']
    return max(min(a0, a1), min(b0, b1)), min(max(a0, a1), max(b0, b1))


def _containment(lower, upper, high, low, close, times, i0, i1):
    """
    Fraction of closes inside the corridor over [i0, i1], and how often each
    rail was actually touched.

    Touches use the bar EXTREMES (a low reaching the lower rail) while
    containment uses CLOSES -- the same split the line engine makes, and for the
    same reason: a wick through a rail is a test of it, a close through it is a
    failure of it.
    """
    if i1 <= i0:
        return 0.0, 0, 0
    inside = 0
    t_lo = t_hi = 0
    n = 0
    for j in range(i0, i1 + 1):
        t = times[j]
        lo = lower.value_at(t)
        hi = upper.value_at(t)
        if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
            continue
        n += 1
        w = hi - lo
        if lo <= close[j] <= hi:
            inside += 1
        # within a tenth of the corridor width counts as a touch of that rail
        if abs(low[j] - lo) <= 0.10 * w:
            t_lo += 1
        if abs(high[j] - hi) <= 0.10 * w:
            t_hi += 1
    return (inside / n if n else 0.0), t_lo, t_hi


def _score(ch: ChannelParams, containment, touches_lower, touches_upper,
           bars, width_atr, kind) -> float:
    """
    Quality 0-100. Containment dominates, because it is the only component that
    distinguishes a channel from two lines that happen to be parallel. Touches
    on BOTH rails come next -- a corridor price has tested from both sides is a
    corridor, one it has only tested from below is a trendline with a ceiling
    drawn on it.
    """
    cont_pts = 45.0 * max(0.0, min(1.0, (containment - 0.5) / 0.5))
    # min() of the two touch counts, so one busy rail cannot carry the score
    both = min(touches_lower, touches_upper)
    touch_pts = min(25.0, both * 6.0)
    span_pts = min(15.0, bars / 12.0)
    # a corridor 2-5 ATR wide is the tradeable shape; score falls off outside it
    if width_atr <= 0:
        width_pts = 0.0
    elif width_atr < 2.0:
        width_pts = 15.0 * (width_atr / 2.0)
    elif width_atr <= 5.0:
        width_pts = 15.0
    else:
        width_pts = max(0.0, 15.0 * (1.0 - (width_atr - 5.0) / 7.0))
    # a projected rail is an assumption, not a measurement
    penalty = 18.0 if kind == 'projected' else 0.0
    return round(max(0.0, min(100.0, cont_pts + touch_pts + span_pts
                              + width_pts - penalty)), 2)


def _parallel_copy(src: Trendline, price_at_t, t_anchor, new_id, role) -> Trendline:
    """
    A rail with the SAME slope as `src`, shifted to pass through one point. Used
    for the projected kind, where only one side has real confirmation.
    """
    clone = Trendline(
        id=new_id, timeframe=src.timeframe, role=role,
        direction=src.direction,
        pivot_1={'t': t_anchor, 'price': price_at_t, 'i': src.pivot_1['i']},
        pivot_2={'t': t_anchor, 'price': price_at_t, 'i': src.pivot_2['i']},
        slope=src.slope, intercept=price_at_t,
        created_at=src.created_at, status=Status.CONFIRMED,
        touches=1, atr_at_creation=src.atr_at_creation,
    )
    return clone


def detect(lines, bars, atr, i, timeframe, params: ChannelParams = None,
           times=None):
    """
    Channels visible at bar `i`, best first.

    `lines` is the live, tradeable population from a Snapshot at bar i. `bars`
    is the full frame; only rows <= i are ever read.
    """
    p = params or ChannelParams()
    a = float(atr[i]) if i < len(atr) and np.isfinite(atr[i]) else 0.0
    if a <= 0:
        return []

    high = np.asarray(bars['high'], dtype=float)
    low = np.asarray(bars['low'], dtype=float)
    close = np.asarray(bars['close'], dtype=float)
    if times is None:
        times = np.asarray(bars.index.astype('int64') // 1_000_000)

    sups = [l for l in lines if l.role is Role.SUPPORT and l.is_tradeable]
    ress = [l for l in lines if l.role is Role.RESISTANCE and l.is_tradeable]
    t_now = int(times[i])

    out = []

    # ---- 1. paired: two independently confirmed rails --------------------- #
    for s in sups:
        for r in ress:
            # slope agreement, measured per bar in ATR units so it means the
            # same thing on gold and on yen
            tf_ms = _tf_ms_of(s, r, times)
            d_per_bar = abs(s.slope - r.slope) * tf_ms
            if d_per_bar > p.slope_tol * a:
                continue
            i0, i1 = _overlap(s, r)
            i1 = min(i1, i)
            if i1 - i0 < p.min_overlap_bars:
                continue
            lo_now, hi_now = s.value_at(t_now), r.value_at(t_now)
            if not (np.isfinite(lo_now) and np.isfinite(hi_now)):
                continue
            width_atr = (hi_now - lo_now) / a
            if width_atr < p.min_width_atr or width_atr > p.max_width_atr:
                continue
            cont, t_lo, t_hi = _containment(s, r, high, low, close, times, i0, i1)
            if cont < p.min_containment:
                continue
            if min(t_lo, t_hi) < p.min_touches_each:
                continue
            ch = _build('paired', s, r, timeframe, times, i0, i1, width_atr,
                        cont, t_lo, t_hi, p, None)
            if _duplicate(ch, out, t_now, a, p.dedupe_atr):
                continue
            out.append(ch)

    # ---- 2. projected: one confirmed rail, one assumed -------------------- #
    if p.allow_projected:
        for src in sups:
            out.extend(_projected(src, Role.RESISTANCE, high, low, close, times,
                                  i, a, timeframe, p, out))
        for src in ress:
            out.extend(_projected(src, Role.SUPPORT, high, low, close, times,
                                  i, a, timeframe, p, out))

    out.sort(key=lambda c: -c.quality_score)
    return out[:p.max_channels]


def _duplicate(ch, existing, t_now, atr, tol_atr):
    """
    Same corridor, different anchors. Compared by where the RAILS actually sit
    right now rather than by slope and intercept: two channels can differ in
    both and still draw the same two lines across the visible window, which is
    all a reader can see.
    """
    lo, hi = ch.lower_at(t_now), ch.upper_at(t_now)
    tol = tol_atr * atr
    for o in existing:
        if abs(o.lower_at(t_now) - lo) <= tol and abs(o.upper_at(t_now) - hi) <= tol:
            return True
    return False


def _tf_ms_of(a, b, times):
    """Bar interval in ms, taken from the series rather than assumed."""
    if len(times) > 1:
        d = int(times[1]) - int(times[0])
        if d > 0:
            return d
    return 1


def _build(kind, lower, upper, timeframe, times, i0, i1, width_atr, cont,
           t_lo, t_hi, p, projected_side):
    slope = 0.5 * (lower.slope + upper.slope)
    direction = lower.direction
    ch = Channel(
        # See the JS mirror: two different rail PAIRS can share one i0..i1
        # window, so the rails' own anchors disambiguate.
        id='%s-CH-%s-%d-%d-%d.%d' % (
            timeframe, 'P' if kind == 'paired' else 'J', i0, i1,
            lower.pivot_1['i'], upper.pivot_1['i']),
        timeframe=timeframe, kind=kind, direction=direction,
        lower=lower, upper=upper, slope=slope,
        t_start=int(times[i0]), t_end=int(times[i1]),
        width_atr=width_atr, containment=cont,
        touches_lower=t_lo, touches_upper=t_hi, bars=i1 - i0 + 1,
        projected_side=projected_side,
    )
    ch.quality_score = _score(p, cont, t_lo, t_hi, ch.bars, width_atr, kind)
    return ch


def _projected(src, role_other, high, low, close, times, i, a, timeframe, p, existing):
    """
    Push a parallel copy of `src` out to the furthest opposite extreme inside its
    own span, which is exactly how a parallel channel is drawn by hand.
    """
    i0, i1 = min(src.pivot_1['i'], src.pivot_2['i']), max(src.pivot_1['i'], src.pivot_2['i'])
    i1 = min(i1, i)
    if i1 - i0 < p.min_overlap_bars:
        return []
    want_high = role_other is Role.RESISTANCE
    best_d, best_j = 0.0, None
    for j in range(i0, i1 + 1):
        v = src.value_at(int(times[j]))
        if not np.isfinite(v):
            continue
        d = (high[j] - v) if want_high else (v - low[j])
        if d > best_d:
            best_d, best_j = d, j
    if best_j is None or best_d <= 0:
        return []
    width_atr = best_d / a
    if width_atr < p.min_width_atr or width_atr > p.max_width_atr:
        return []

    t_anchor = int(times[best_j])
    px = high[best_j] if want_high else low[best_j]
    clone = _parallel_copy(src, px, t_anchor, src.id + '-par', role_other)
    lower, upper = (src, clone) if want_high else (clone, src)

    cont, t_lo, t_hi = _containment(lower, upper, high, low, close, times, i0, i1)
    if cont < p.min_containment:
        return []
    if min(t_lo, t_hi) < p.min_touches_each:
        return []
    ch = _build('projected', lower, upper, timeframe, times, i0, i1,
                width_atr, cont, t_lo, t_hi, p,
                'upper' if want_high else 'lower')
    # `existing` is the caller's running list, passed by reference: the caller
    # extends it with what we return, so appending here too would add every
    # channel twice.
    if _duplicate(ch, existing, int(times[i]), a, p.dedupe_atr):
        return []
    return [ch]
