"""
fvg.py — Fair Value Gaps: three-candle imbalances.

From "Smart Money Concepts (SMC)" by LuxAlgo (TradingView, open source). An FVG
is the cleanest-defined object in that whole family, which is why it is the one
built first: there is no judgement in it at all.

    BULLISH   high[i-2] < low[i]     price jumped up leaving untraded air
              zone = (high[i-2], low[i])
    BEARISH   low[i-2]  > high[i]    price jumped down leaving untraded air
              zone = (high[i],  low[i-2])

The middle candle is the impulse; the gap is the range no trading happened in.
The claim is that price tends to return and "fill" it.

WHY THIS IS DIFFERENT FROM EVERYTHING ELSE HERE

zones.py needs price to have turned at a level repeatedly. supply_demand.py
needs a quiet base before a departure. An FVG needs neither -- it is a property
of three consecutive candles and nothing else. No pivots, no confirmation
window, no lookback. That makes it the most abundant object in the codebase and
the one least likely to be confounded with the others.

CAUSALITY comes free: the gap is knowable at bar i, the third candle, because
all three candles have closed. `confirmed_i = i`. There is no fractal lag to
account for, which is unusual here and worth noting -- every other detector in
sim/tl/ carries a `confirmed_i` later than the bar it describes.

THRESHOLD. Every three-candle window with any gap at all qualifies under the
raw definition, which on 15m data produces thousands of one-tick "imbalances"
that are spread artefacts rather than structure. `min_size_atr` requires the gap
to be a real fraction of ATR. The published indicator offers an "auto threshold"
that compares each gap to a rolling average of gap sizes; `auto_threshold` does
the same, and the two can be combined.

MITIGATION. A gap is filled when price trades back through it. `mitigation`
selects how much counts: 'touch' (any entry), 'half' (to the midpoint) or
'full' (all the way through). The technique's usual claim is that unfilled gaps
attract price, so tracking this is what makes that claim falsifiable.
"""

from dataclasses import dataclass
from typing import List, Optional

import numpy as np

BULLISH = 'bullish'
BEARISH = 'bearish'

TOUCH, HALF, FULL = 'touch', 'half', 'full'


@dataclass
class FVG:
    """One three-candle imbalance."""

    id: str
    timeframe: str
    kind: str                  # bullish | bearish
    low: float
    high: float
    i: int                     # the third candle: the bar it became knowable
    t: int
    size_atr: float = 0.0
    mitigated_i: Optional[int] = None    # bar it was filled, None while open
    mitigated_t: Optional[int] = None

    @property
    def mid(self) -> float:
        return 0.5 * (self.low + self.high)

    @property
    def open(self) -> bool:
        """Unfilled. The state the technique claims attracts price."""
        return self.mitigated_i is None

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
                'i': self.i, 't': self.t, 'size_atr': round(self.size_atr, 4),
                'mitigated_i': self.mitigated_i, 'open': self.open}


@dataclass
class FVGParams:
    # A gap smaller than this is spread and rounding, not an imbalance.
    min_size_atr: float = 0.25
    # The published "auto threshold": also require the gap to exceed this
    # multiple of the recent average gap size. 0 disables it.
    auto_threshold: float = 0.0
    auto_window: int = 200
    mitigation: str = TOUCH
    max_age: int = 500          # stop tracking a gap this many bars after it forms
    max_open: int = 20          # most recent N still-open gaps returned


def detect(bars, tf, atr, params: FVGParams = None, upto=None, times=None):
    """
    Every FVG up to bar `upto`, oldest first, each carrying whether and when it
    was mitigated. Only information at or before `upto` is ever read.
    """
    p = params or FVGParams()
    high = np.asarray(bars['high'], dtype=float)
    low = np.asarray(bars['low'], dtype=float)
    n = len(high)
    i_end = (n - 1) if upto is None else min(upto, n - 1)
    if times is None:
        times = np.asarray(bars.index.astype('int64') // 1_000_000)

    # rolling mean gap size, for the auto threshold. Built forward so the value
    # at bar i uses only gaps that had already formed.
    sizes = np.zeros(n)
    for i in range(2, i_end + 1):
        if high[i - 2] < low[i]:
            sizes[i] = low[i] - high[i - 2]
        elif low[i - 2] > high[i]:
            sizes[i] = low[i - 2] - high[i]

    out: List[FVG] = []
    seq = 0
    for i in range(2, i_end + 1):
        a = atr[i]
        if not np.isfinite(a) or a <= 0:
            continue
        if high[i - 2] < low[i]:
            kind, lo, hi = BULLISH, float(high[i - 2]), float(low[i])
        elif low[i - 2] > high[i]:
            kind, lo, hi = BEARISH, float(high[i]), float(low[i - 2])
        else:
            continue
        size = hi - lo
        if size / a < p.min_size_atr:
            continue
        if p.auto_threshold > 0:
            w0 = max(0, i - p.auto_window)
            seen = sizes[w0:i]
            seen = seen[seen > 0]
            if len(seen) >= 10 and size < p.auto_threshold * float(np.mean(seen)):
                continue

        seq += 1
        g = FVG(id='%s-FVG-%d' % (tf, seq), timeframe=tf, kind=kind,
                low=lo, high=hi, i=i, t=int(times[i]), size_atr=size / a)

        # --- mitigation: walk forward only as far as `upto` ---------------
        last = min(i_end, i + p.max_age)
        for m in range(i + 1, last + 1):
            if p.mitigation == TOUCH:
                hit = (low[m] <= hi) if kind == BULLISH else (high[m] >= lo)
            elif p.mitigation == HALF:
                mid = 0.5 * (lo + hi)
                hit = (low[m] <= mid) if kind == BULLISH else (high[m] >= mid)
            else:
                hit = (low[m] <= lo) if kind == BULLISH else (high[m] >= hi)
            if hit:
                g.mitigated_i = m
                g.mitigated_t = int(times[m])
                break
        out.append(g)
    return out


def open_gaps(bars, tf, atr, params: FVGParams = None, upto=None, times=None):
    """The still-unfilled gaps, most recent first — what a chart draws."""
    p = params or FVGParams()
    gaps = [g for g in detect(bars, tf, atr, p, upto=upto, times=times) if g.open]
    gaps.sort(key=lambda g: -g.i)
    return gaps[:p.max_open]
