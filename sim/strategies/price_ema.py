"""
price_ema.py — price crossing a single EMA, with an ATR stop.

A DIFFERENT MECHANISM from ema_cross, which is the only reason to have it. That
one compares two averages to each other (EMA <-> EMA) and so reacts late and
smoothly; this compares price to one average (price <-> EMA) and so reacts
early and noisily. Same family, opposite failure mode: ema_cross misses the
first part of a move, this one takes every false start.

Having both matters for the head-to-head, because "trend following did not work"
is a much weaker claim if only one way of measuring a trend was tried. One EMA
also means one parameter instead of two, which is one fewer thing to overfit.

Entry is on the CROSS, not on the state. `close > ema` is true for most of a
trend, so trading the state would mean re-entering on every bar; the cross is
the event, and it fires once.

No take-profit, matching the other baselines: it exits on the cross back or on
the stop. tools/tp_sweep.py measured what capping the tail costs.
"""

import numpy as np

from ..core import FLAT, LONG, SHORT, Intent, Strategy
from ..indicators import atr


def ema(values, n):
    """pandas ewm(span=n, adjust=False, min_periods=n), as an array."""
    a = 2.0 / (n + 1.0)
    v = np.asarray(values, dtype=float)
    out = np.full(len(v), np.nan)
    if not len(v):
        return out
    prev = v[0]
    for i in range(len(v)):
        prev = v[0] if i == 0 else a * v[i] + (1 - a) * prev
        if i >= n - 1:
            out[i] = prev
    return out


class PriceEma(Strategy):
    name = 'price_ema'

    def __init__(self, length=50, atr_len=14, atr_mult=2.5):
        self.length = int(length)
        self.atr_len, self.atr_mult = int(atr_len), float(atr_mult)
        self.warmup = max(self.length, self.atr_len) + 2

    def params(self):
        return {'length': self.length, 'atr_len': self.atr_len,
                'atr_mult': self.atr_mult}

    def prepare(self, bars):
        return {'ema': ema(bars['close'].to_numpy(float), self.length),
                'atr': np.asarray(atr(bars, self.atr_len), dtype=float)}

    def on_bar(self, view, position):
        e, a = view.series('ema'), view.series('atr')
        ep = view.series('ema', 1)
        c, cp = view.close(), view.close(1)
        if not all(np.isfinite(x) for x in (e, ep, a)) or a <= 0:
            return None

        up = cp <= ep and c > e
        dn = cp >= ep and c < e

        if position is not None:
            if (position.side == LONG and dn) or (position.side == SHORT and up):
                return Intent(FLAT, tag='cross_back')
            return None
        if up:
            return Intent(LONG, stop=c - self.atr_mult * a, tag='cross_up')
        if dn:
            return Intent(SHORT, stop=c + self.atr_mult * a, tag='cross_dn')
        return None
