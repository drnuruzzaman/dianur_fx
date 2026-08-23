"""
donchian.py — N-bar channel breakout. The Turtle rule, stripped to essentials.

Long when a bar closes above the highest high of the previous `entry` bars,
short on the mirror image. Stop is ATR-based so the same parameters mean the
same thing on gold and on yen. Exit is the opposite side of a shorter channel,
or the stop, whichever comes first.

Two parameters and no discretion, which is the point: it is a control, not a
candidate. If the engine makes this look brilliant, the engine is wrong.
"""

import numpy as np
import pandas as pd

from ..core import FLAT, LONG, SHORT, Intent, Strategy


def atr(bars, length=14):
    """Wilder ATR — causal: value at i uses only bars <= i."""
    h, l, c = bars['high'], bars['low'], bars['close']
    prev = c.shift(1)
    tr = pd.concat([h - l, (h - prev).abs(), (l - prev).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / length, adjust=False, min_periods=length).mean()


class Donchian(Strategy):
    name = 'donchian'

    def __init__(self, entry=20, exit=10, atr_len=14, atr_mult=2.0):
        self.entry, self.exit = int(entry), int(exit)
        self.atr_len, self.atr_mult = int(atr_len), float(atr_mult)
        self.warmup = max(self.entry, self.exit, self.atr_len) + 2

    def params(self):
        return {'entry': self.entry, 'exit': self.exit,
                'atr_len': self.atr_len, 'atr_mult': self.atr_mult}

    def prepare(self, bars):
        # shift(1) so a channel never contains the bar being decided on
        return {
            'hi': bars['high'].rolling(self.entry).max().shift(1).to_numpy(float),
            'lo': bars['low'].rolling(self.entry).min().shift(1).to_numpy(float),
            'exit_hi': bars['high'].rolling(self.exit).max().shift(1).to_numpy(float),
            'exit_lo': bars['low'].rolling(self.exit).min().shift(1).to_numpy(float),
            'atr': atr(bars, self.atr_len).to_numpy(float),
        }

    def on_bar(self, view, position):
        c = view.close()
        a = view.series('atr')
        if not np.isfinite(a) or a <= 0:
            return None

        if position is not None:
            # give back the trade when price closes through the shorter channel
            if position.side == LONG and c < view.series('exit_lo'):
                return Intent(FLAT, tag='channel_exit')
            if position.side == SHORT and c > view.series('exit_hi'):
                return Intent(FLAT, tag='channel_exit')
            return None

        hi, lo = view.series('hi'), view.series('lo')
        if np.isfinite(hi) and c > hi:
            return Intent(LONG, stop=c - self.atr_mult * a, tag='breakout_up')
        if np.isfinite(lo) and c < lo:
            return Intent(SHORT, stop=c + self.atr_mult * a, tag='breakout_dn')
        return None
