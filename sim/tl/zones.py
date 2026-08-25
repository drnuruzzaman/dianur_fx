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
    #: median excursion away from this zone's pivots, in ATR
    reaction_atr: float = float('nan')
    atr: float = float('nan')     # ATR at detection; lets a UI talk in points
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
            'reaction_atr': self.reaction_atr,
            'atr': self.atr,
        }


@dataclass
class ZoneParams:
    strength_pivots: int = 3      # fractal size for the pivots that feed it
    close_confirm: bool = False   # see sim/tl/pivots.py find_pivots
    lookback: int = 500           # bars considered
    cluster_atr: float = 0.35     # levels closer than this are one zone
    # 2 rather than 3. A clean double top IS a level a human draws, and the
    # scorer already ranks it accordingly -- touch_pts gives 2 touches 11 points
    # against 35 for four -- so a weak zone loses on merit instead of being
    # excluded before it can compete. Hard-gating at 3 removed 23 of 33 clusters
    # on gold H1 before anything had a chance to be judged.
    min_touches: int = 2          # distinct reactions required
    min_separation: int = 8       # bars apart before two touches count as two
    max_width_atr: float = 1.2    # wider than this is not a level, it is a range
    max_zones: int = 6            # best first
    min_strength: float = 25.0
    # How far price travelled AWAY from a pivot before turning back, in ATR,
    # measured over this many bars. A level that produced 3 ATR bounces is not
    # the same object as one price grazed and drifted from, and a raw touch
    # count cannot tell them apart.
    reaction_bars: int = 20
    reaction_full_atr: float = 2.0    # the excursion that earns full marks
    # HOW FAR IS TOO FAR. A zone price cannot reach is true and untradeable,
    # but "12 ATR" turned out to mean wildly different things per instrument:
    # measured over the same 500-bar window it covered 19% of the actual price
    # range on USDJPY 1h and 54% on XAUUSD 4h. A trader does not think in ATR
    # here -- they draw the levels that are ON THE CHART.
    #
    # So the allowance is the LARGER of an ATR budget and a fraction of the
    # range price has actually covered in the lookback. The range term is what
    # normally binds and self-scales with the instrument; the ATR term is a
    # floor that keeps the allowance sane through an unusually quiet window,
    # where the recent range can collapse to almost nothing.
    max_distance_atr: float = 12.0
    max_distance_range: float = 0.75


def _score(touches, span_bars, width_atr, lookback, dist, allow, reaction,
           p: ZoneParams) -> float:
    """
    0-100 over five terms.

    TIGHTNESS is weighted heavily because it separates a zone from a vague area:
    a band price turned at four times within 0.15 ATR is a level, the same four
    touches spread over a full ATR is a neighbourhood.

    REACTION is what a raw touch count cannot express. Three pivots that each
    produced a 3 ATR bounce and three that barely turned score the same on
    `touches` and are not the same object -- the first is a level being defended,
    the second is price passing through a price. Weight had to come from
    somewhere: touches gives up 7 points and span 5, because both were already
    proxies for the same underlying thing this measures directly.

    Distance discounts the whole thing last: a level you cannot reach today is
    not a level you can trade today.
    """
    touch_pts = min(28.0, (touches - 2) * 10.0 + 9.0)
    span_pts = min(15.0, 15.0 * (span_bars / max(lookback, 1)))
    react_pts = 0.0
    if p.reaction_full_atr > 0 and np.isfinite(reaction):
        react_pts = 17.0 * min(1.0, max(0.0, reaction / p.reaction_full_atr))
    if p.max_width_atr <= 0:
        tight_pts = 0.0
    else:
        tight_pts = 22.0 * max(0.0, 1.0 - (width_atr / p.max_width_atr))
    # Proximity is scored against the SAME allowance the filter uses, so a zone
    # that just survives the cut also scores near zero for closeness. Scoring it
    # against a different yardstick would let a zone be "far" for one purpose
    # and "near" for the other.
    if allow <= 0 or not np.isfinite(dist):
        prox_pts = 0.0
    else:
        prox_pts = 18.0 * max(0.0, 1.0 - (dist / allow))
    return round(max(0.0, min(100.0, touch_pts + span_pts + tight_pts
                              + react_pts + prox_pts)), 2)


def _reaction_atr(kept, high, low, atr, i, p: ZoneParams) -> float:
    """
    Median excursion away from the zone's pivots, in ATR.

    For a swing LOW the reaction is how far price rose afterwards; for a swing
    HIGH how far it fell. Median rather than mean, so one violent bounce cannot
    carry a level that otherwise did nothing.

    CAUSAL. Every bar read lies between the pivot and `i`, both already in the
    past at the moment the zone is computed. The window is also clipped at `i`,
    so a recent pivot is scored on the bars that actually exist rather than
    being credited with an excursion that has not happened yet.
    """
    out = []
    for price, k, kind in kept:
        a = atr[k] if k < len(atr) and np.isfinite(atr[k]) and atr[k] > 0 else np.nan
        if not np.isfinite(a):
            continue
        end = min(i, k + p.reaction_bars)
        if end <= k:
            continue
        if kind == 'low':
            move = float(np.max(high[k + 1:end + 1])) - price
        else:
            move = price - float(np.min(low[k + 1:end + 1]))
        out.append(max(0.0, move) / a)
    return float(np.median(out)) if out else float('nan')


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
    # The distance allowance, computed once: the larger of the ATR budget and a
    # fraction of the range price actually covered in the lookback.
    win_hi = float(np.max(high[i0:i + 1]))
    win_lo = float(np.min(low[i0:i + 1]))
    allow = max(p.max_distance_atr * a, p.max_distance_range * (win_hi - win_lo))
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
        dist = abs(last_close - z.mid)
        if np.isfinite(dist) and allow > 0 and dist > allow:
            continue
        reaction = _reaction_atr(kept, high, low, atr, i, p)
        z.reaction_atr = reaction
        z.atr = a
        z.strength = _score(z.touches, last_i - first_i, width_atr, p.lookback,
                            dist, allow, reaction, p)
        if z.strength < p.min_strength:
            continue
        out.append(z)

    out.sort(key=lambda z: -z.strength)
    return out[:p.max_zones]
