"""
zones.py — horizontal support/resistance BANDS, not lines.

A line is a claim about one price. A zone is a claim about a region, and it is
the more honest object: price does not turn at 1.16847, it turns somewhere in a
band a few tenths of an ATR wide, and every chart annotated by a human draws it
that way -- "Supply", "Demand", "Seller Zone", "Buyer Zone".

HOW A ZONE IS BUILT

  1. Every confirmed swing pivot inside the lookback is a candidate level.
  2. Levels within `cluster_atr` of each other are one zone. Agglomerative on a
     sorted list, which is the right shape here: levels are one-dimensional, and
     merging nearest-neighbours in price order gives the same answer as anything
     more elaborate at a fraction of the cost.
  3. A zone must have `min_touches` distinct pivots from at least
     `min_separation` bars apart. Three pivots inside one week's consolidation
     is one event seen three times, not three independent tests.

WHY PIVOTS AND NOT LINE VALUES. A trendline's value moves with time, so
clustering line values would produce a band that means something only at the
instant it was computed. Pivots are fixed prices. A zone is horizontal by
definition -- if the structure slopes, that is a channel, and channels.py
already has it.

ROLE IS ASSIGNED FROM PRICE, NOT FROM THE PIVOT TYPE. A zone built from swing
highs is resistance while price is below it and support once price is above it:
that flip is the single most-taught idea in support/resistance, and hard-coding
the role from the pivots would throw it away. `role_at(price)` decides.

STRENGTH combines how many times the zone was tested, how far apart those tests
were in time, and how TIGHT the cluster is -- a band 0.15 ATR wide that price
turned at four times is a very different object from one 0.9 ATR wide that also
has four touches, and averaging them into "4 touches" loses the distinction.

CAUSALITY: pure function of pivots confirmed at or before bar `i`, and bars up
to `i`. Nothing later is read because nothing later is passed.
"""

from dataclasses import dataclass
from typing import List, Optional

import numpy as np

from .pivots import find_pivots, pivots_confirmed_by

SUPPORT = 'support'
RESISTANCE = 'resistance'


@dataclass
class Zone:
    """A horizontal band price has repeatedly reacted to."""

    id: str
    timeframe: str
    low: float
    high: float
    touches: int
    first_i: int
    last_i: int
    first_t: int
    last_t: int
    width_atr: float = 0.0
    strength: float = 0.0
    # Pivot prices that formed it, so a chart can mark the evidence.
    levels: tuple = ()
    from_highs: int = 0
    from_lows: int = 0

    @property
    def mid(self) -> float:
        return 0.5 * (self.low + self.high)

    def contains(self, price) -> bool:
        return self.low <= price <= self.high

    def role_at(self, price) -> str:
        """
        Resistance while price is below the band, support once it is above.
        Inside the band the nearer edge decides, because that is the edge the
        next move has to clear.
        """
        if price < self.low:
            return RESISTANCE
        if price > self.high:
            return SUPPORT
        return SUPPORT if (price - self.low) > (self.high - price) else RESISTANCE

    def distance_atr(self, price, atr) -> float:
        if not atr:
            return float('nan')
        if self.contains(price):
            return 0.0
        d = (self.low - price) if price < self.low else (price - self.high)
        return d / atr

    def to_row(self) -> dict:
        return {
            'id': self.id, 'timeframe': self.timeframe,
            'low': self.low, 'high': self.high, 'mid': self.mid,
            'touches': self.touches, 'from_highs': self.from_highs,
            'from_lows': self.from_lows,
            'first_t': self.first_t, 'last_t': self.last_t,
            'width_atr': round(self.width_atr, 3),
            'strength': self.strength,
        }


@dataclass
class ZoneParams:
    strength_pivots: int = 3      # fractal size for the pivots that feed it
    close_confirm: bool = False   # see sim/tl/pivots.py find_pivots
    lookback: int = 500           # bars considered
    cluster_atr: float = 0.35     # levels closer than this are one zone
    min_touches: int = 3          # distinct reactions required
    min_separation: int = 8       # bars apart before two touches count as two
    max_width_atr: float = 1.2    # wider than this is not a level, it is a range
    max_zones: int = 6            # best first
    min_strength: float = 25.0
    # A zone 20 ATR away is true and untradeable. The line engine already
    # discounts distance for exactly this reason; without it the strongest
    # zones on a trending instrument are all historic ones price left long ago.
    max_distance_atr: float = 12.0


def _score(touches, span_bars, width_atr, lookback, dist_atr, p: ZoneParams) -> float:
    """
    0-100. Touches lead, but TIGHTNESS is weighted heavily because it is what
    separates a zone from a vague area: a band price turned at four times within
    0.15 ATR is a level, the same four touches spread over a full ATR is a
    neighbourhood. Distance from price then discounts the whole thing, because a
    level you cannot reach today is not a level you can trade today.
    """
    touch_pts = min(35.0, (touches - 2) * 12.0 + 11.0)
    span_pts = min(20.0, 20.0 * (span_bars / max(lookback, 1)))
    if p.max_width_atr <= 0:
        tight_pts = 0.0
    else:
        tight_pts = 25.0 * max(0.0, 1.0 - (width_atr / p.max_width_atr))
    if p.max_distance_atr <= 0 or not np.isfinite(dist_atr):
        prox_pts = 0.0
    else:
        prox_pts = 20.0 * max(0.0, 1.0 - (dist_atr / p.max_distance_atr))
    return round(max(0.0, min(100.0, touch_pts + span_pts + tight_pts + prox_pts)), 2)


def _cluster(levels, tol):
    """
    Agglomerate a price-sorted list: walk it once, starting a new group whenever
    the gap to the previous level exceeds `tol`. `levels` is [(price, i, kind)].
    """
    if not levels:
        return []
    levels = sorted(levels, key=lambda x: x[0])
    groups = [[levels[0]]]
    for lv in levels[1:]:
        if lv[0] - groups[-1][-1][0] <= tol:
            groups[-1].append(lv)
        else:
            groups.append([lv])
    return groups


def detect(bars, i, timeframe, atr, params: ZoneParams = None, times=None):
    """
    Zones visible at bar `i`, strongest first.

    Only pivots CONFIRMED by bar `i` are used, so a swing is never counted
    before the bar it actually became visible.
    """
    p = params or ZoneParams()
    a = float(atr[i]) if i < len(atr) and np.isfinite(atr[i]) else 0.0
    if a <= 0:
        return []

    high = np.asarray(bars['high'], dtype=float)
    low = np.asarray(bars['low'], dtype=float)
    close = np.asarray(bars['close'], dtype=float)
    last_close = float(close[i])
    if times is None:
        times = np.asarray(bars.index.astype('int64') // 1_000_000)

    i0 = max(0, i - p.lookback)
    piv_hi, piv_lo = find_pivots(high, low, p.strength_pivots, close=close,
                                 close_confirm=getattr(p, 'close_confirm', False))
    piv_hi = [q for q in pivots_confirmed_by(piv_hi, i) if q['i'] >= i0]
    piv_lo = [q for q in pivots_confirmed_by(piv_lo, i) if q['i'] >= i0]

    levels = [(q['price'], q['i'], 'high') for q in piv_hi]
    levels += [(q['price'], q['i'], 'low') for q in piv_lo]
    if len(levels) < p.min_touches:
        return []

    out = []
    seq = 0
    for g in _cluster(levels, p.cluster_atr * a):
        # distinct touches: collapse pivots that sit within min_separation bars
        g_sorted = sorted(g, key=lambda x: x[1])
        kept = []
        for lv in g_sorted:
            if not kept or lv[1] - kept[-1][1] >= p.min_separation:
                kept.append(lv)
        if len(kept) < p.min_touches:
            continue
        prices = [x[0] for x in kept]
        lo, hi = min(prices), max(prices)
        width_atr = (hi - lo) / a
        if width_atr > p.max_width_atr:
            continue
        first_i, last_i = kept[0][1], kept[-1][1]
        seq += 1
        z = Zone(
            id='%s-Z-%d' % (timeframe, seq),
            timeframe=timeframe, low=lo, high=hi,
            touches=len(kept), first_i=first_i, last_i=last_i,
            first_t=int(times[first_i]), last_t=int(times[last_i]),
            width_atr=width_atr,
            levels=tuple(round(x, 10) for x in prices),
            from_highs=sum(1 for x in kept if x[2] == 'high'),
            from_lows=sum(1 for x in kept if x[2] == 'low'),
        )
        dist = z.distance_atr(last_close, a)
        if np.isfinite(dist) and dist > p.max_distance_atr:
            continue
        z.strength = _score(z.touches, last_i - first_i, width_atr, p.lookback,
                            dist, p)
        if z.strength < p.min_strength:
            continue
        out.append(z)

    out.sort(key=lambda z: -z.strength)
    return out[:p.max_zones]
