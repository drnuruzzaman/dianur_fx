"""
supply_demand.py — zones found from IMPULSE ORIGINS, not from pivot clusters.

zones.py answers "where has price turned repeatedly?" by clustering confirmed
swing pivots. This answers a different question: "where did price leave from in
a hurry?" -- the consolidation a large directional move departed from. The claim
behind it is that unfilled orders remain at the origin, so price returning there
meets them again.

They are genuinely different objects and can disagree completely. A pivot
cluster needs price to have visited a level SEVERAL times; an impulse origin can
be a level price visited ONCE and then ran from, which is exactly the case
pivot-clustering cannot see. Conversely a range that oscillates without ever
producing an impulse gives many pivot-cluster zones and no supply/demand ones.

HOW ONE IS BUILT

  1. IMPULSE   a run of up to `impulse_bars` whose net close-to-close move is at
               least `impulse_atr` ATR and whose bars mostly agree in direction.
               Requiring agreement is what separates a genuine departure from a
               volatile chop that happens to end higher.

  2. BASE      walking BACK from the impulse start, the consecutive bars whose
               individual ranges are under `base_range_atr` -- the quiet part
               before the move. Capped at `max_base_bars`: a "base" fifty bars
               long is a range, and its edges are not one level.

  3. ZONE      the base's high and low. Demand when the impulse went up (the
               zone sits below and is expected to hold price up), supply when it
               went down.

FRESHNESS is the property this method has and pivot clustering cannot have. A
pivot-cluster zone is defined BY being touched repeatedly, so "untested" is not
expressible. Here a zone starts untested and is consumed by use, which is the
central claim of the technique -- and therefore the thing most worth measuring,
since it is also the easiest to believe without evidence.

CAUSALITY. A zone is only knowable once its impulse has completed, so
`confirmed_i` is the impulse's LAST bar, never the base's. Nothing may use a
zone before then, and `detect(..., upto=i)` returns only zones confirmed by i.
"""

from dataclasses import dataclass, field
from typing import List, Optional

import numpy as np

DEMAND = 'demand'          # base a rally departed from; expected to support
SUPPLY = 'supply'          # base a decline departed from; expected to resist


@dataclass
class SDZone:
    """A base that price left in a hurry."""

    id: str
    timeframe: str
    kind: str                  # demand | supply
    low: float
    high: float
    base_i0: int               # first bar of the base
    base_i1: int               # last bar of the base
    impulse_i1: int            # last bar of the impulse
    confirmed_i: int           # == impulse_i1; the bar it became knowable
    t_base: int                # ms of the base's first bar
    t_confirmed: int
    impulse_atr: float = 0.0   # size of the departing move, in ATR
    base_bars: int = 0
    width_atr: float = 0.0
    touches: int = 0           # returns to the zone since it formed
    broken: bool = False       # a close through it the wrong way
    strength: float = 0.0

    @property
    def mid(self) -> float:
        return 0.5 * (self.low + self.high)

    @property
    def fresh(self) -> bool:
        """Never revisited. The property pivot-clustering cannot express."""
        return self.touches == 0

    def contains(self, price) -> bool:
        return self.low <= price <= self.high

    def distance_atr(self, price, atr) -> float:
        if not atr:
            return float('nan')
        if self.contains(price):
            return 0.0
        d = (self.low - price) if price < self.low else (price - self.high)
        return d / atr

    def to_row(self) -> dict:
        return {'id': self.id, 'timeframe': self.timeframe, 'kind': self.kind,
                'low': self.low, 'high': self.high, 'mid': self.mid,
                'base_bars': self.base_bars, 'impulse_atr': round(self.impulse_atr, 3),
                'width_atr': round(self.width_atr, 3), 'touches': self.touches,
                'fresh': self.fresh, 'broken': self.broken,
                'confirmed_i': self.confirmed_i, 'strength': self.strength}


@dataclass
class SDParams:
    impulse_atr: float = 2.5       # net move that counts as a departure
    impulse_bars: int = 6          # over at most this many bars
    min_agree: float = 0.6         # fraction of impulse bars agreeing in direction
    base_range_atr: float = 0.8    # a base bar's range must be under this
    max_base_bars: int = 8         # longer than this is a range, not a base
    min_base_bars: int = 1
    max_width_atr: float = 2.5     # a base wider than this is not one level
    touch_atr: float = 0.15        # how close a return counts as a touch
    max_distance_atr: float = 12.0
    max_zones: int = 8
    lookback: int = 600


def _score(z: SDZone, p: SDParams, dist_atr) -> float:
    """
    0-100. The impulse dominates: the size of the move that left is the only
    evidence the zone has, since unlike a pivot cluster it need never have been
    touched. Freshness is scored, not assumed -- it is the technique's central
    claim and this is what makes it falsifiable.
    """
    imp_pts = min(40.0, 40.0 * (z.impulse_atr / (p.impulse_atr * 2.0)))
    tight_pts = 20.0 * max(0.0, 1.0 - z.width_atr / max(p.max_width_atr, 1e-9))
    fresh_pts = 20.0 if z.touches == 0 else max(0.0, 20.0 - 7.0 * z.touches)
    if p.max_distance_atr <= 0 or not np.isfinite(dist_atr):
        prox_pts = 0.0
    else:
        prox_pts = 20.0 * max(0.0, 1.0 - dist_atr / p.max_distance_atr)
    return round(max(0.0, min(100.0, imp_pts + tight_pts + fresh_pts + prox_pts)), 2)


def detect(bars, tf, atr, params: SDParams = None, upto=None, times=None):
    """
    Supply/demand zones knowable at bar `upto`, strongest first.

    A zone is confirmed at the END of its impulse, so nothing here is visible
    before the move that created it has finished.
    """
    p = params or SDParams()
    high = np.asarray(bars['high'], dtype=float)
    low = np.asarray(bars['low'], dtype=float)
    close = np.asarray(bars['close'], dtype=float)
    n = len(close)
    i_end = (n - 1) if upto is None else min(upto, n - 1)
    if times is None:
        times = np.asarray(bars.index.astype('int64') // 1_000_000)

    zones = []
    seq = 0
    start = max(1, i_end - p.lookback)

    for i in range(start, i_end + 1):
        a = atr[i]
        if not np.isfinite(a) or a <= 0:
            continue
        # --- 1. does an impulse END at bar i? -----------------------------
        best = None
        for k in range(2, p.impulse_bars + 1):
            j = i - k + 1
            if j <= start:
                break
            move = close[i] - close[j - 1]
            if abs(move) < p.impulse_atr * a:
                continue
            d = 1 if move > 0 else -1
            agree = sum(1 for m in range(j, i + 1)
                        if (close[m] - close[m - 1]) * d > 0)
            if agree / k < p.min_agree:
                continue
            best = (j, d, abs(move) / a)
            break                    # shortest qualifying impulse wins
        if best is None:
            continue
        j, d, imp_atr = best

        # --- 2. walk back for the base ------------------------------------
        b1 = j - 1
        b0 = b1
        while (b0 > start and (b1 - b0 + 1) < p.max_base_bars
               and (high[b0] - low[b0]) <= p.base_range_atr * atr[b0]):
            b0 -= 1
        b0 += 1
        if b1 - b0 + 1 < p.min_base_bars or b1 < b0:
            continue
        lo, hi = float(np.min(low[b0:b1 + 1])), float(np.max(high[b0:b1 + 1]))
        width_atr = (hi - lo) / a
        if width_atr <= 0 or width_atr > p.max_width_atr:
            continue

        seq += 1
        z = SDZone(id='%s-SD-%d' % (tf, seq), timeframe=tf,
                   kind=DEMAND if d > 0 else SUPPLY, low=lo, high=hi,
                   base_i0=b0, base_i1=b1, impulse_i1=i, confirmed_i=i,
                   t_base=int(times[b0]), t_confirmed=int(times[i]),
                   impulse_atr=imp_atr, base_bars=b1 - b0 + 1,
                   width_atr=width_atr)

        # --- 3. how has it been used SINCE it formed? ---------------------
        for m in range(i + 1, i_end + 1):
            am = atr[m] if np.isfinite(atr[m]) and atr[m] > 0 else a
            if low[m] <= hi + p.touch_atr * am and high[m] >= lo - p.touch_atr * am:
                z.touches += 1
            # a close clean through it the wrong way retires the zone
            if z.kind == DEMAND and close[m] < lo - p.touch_atr * am:
                z.broken = True
                break
            if z.kind == SUPPLY and close[m] > hi + p.touch_atr * am:
                z.broken = True
                break
        if z.broken:
            continue

        dist = z.distance_atr(close[i_end], atr[i_end])
        if np.isfinite(dist) and dist > p.max_distance_atr:
            continue
        z.strength = _score(z, p, dist)
        zones.append(z)

    # nearer duplicates: two bases at the same price are one zone
    zones.sort(key=lambda x: -x.strength)
    out = []
    a_end = atr[i_end] if np.isfinite(atr[i_end]) else 0.0
    for z in zones:
        if any(abs(z.mid - k.mid) <= 0.5 * a_end for k in out):
            continue
        out.append(z)
        if len(out) >= p.max_zones:
            break
    return out
