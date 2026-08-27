"""
ema_cross.py — fast/slow EMA cross with an ATR stop.

The most widely used naive trend rule in retail trading, which is exactly why it
belongs here: it is the yardstick anything cleverer has to beat. Reacts later
than a channel breakout and holds longer, so the two baselines fail in different
places — useful when deciding whether a result is a strategy or a market.
"""

import numpy as np

from ..core import FLAT, LONG, SHORT, Intent, Strategy
from .donchian import atr


class EmaCross(Strategy):
    name = 'ema_cross'

    def __init__(self, fast=21, slow=50, atr_len=14, atr_mult=2.5):
        self.fast, self.slow = int(fast), int(slow)
        self.atr_len, self.atr_mult = int(atr_len), float(atr_mult)
        self.warmup = max(self.slow, self.atr_len) + 2

    def params(self):
        return {'fast': self.fast, 'slow': self.slow,
                'atr_len': self.atr_len, 'atr_mult': self.atr_mult}

    def prepare(self, bars):
        c = bars['close']
        ema = lambda n: c.ewm(span=n, adjust=False, min_periods=n).mean().to_numpy(float)
        return {'fast': ema(self.fast), 'slow': ema(self.slow),
                'atr': np.asarray(atr(bars, self.atr_len), dtype=float)}

    def on_bar(self, view, position):
        f, s = view.series('fast'), view.series('slow')
        fp, sp = view.series('fast', 1), view.series('slow', 1)
        a = view.series('atr')
        if not all(np.isfinite(x) for x in (f, s, fp, sp, a)) or a <= 0:
            return None

        crossed_up = fp <= sp and f > s
        crossed_dn = fp >= sp and f < s

        if position is not None:
            if (position.side == LONG and crossed_dn) or \
               (position.side == SHORT and crossed_up):
                return Intent(FLAT, tag='cross_back')
            return None

        c = view.close()
        if crossed_up:
            return Intent(LONG, stop=c - self.atr_mult * a, tag='cross_up')
        if crossed_dn:
            return Intent(SHORT, stop=c + self.atr_mult * a, tag='cross_dn')
        return None
