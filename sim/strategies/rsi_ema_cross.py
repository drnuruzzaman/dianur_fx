"""
rsi_ema_cross.py — RSI crossing its own EMA. A hypothesis, tested before use.

    LONG   when RSI(14) crosses ABOVE EMA(9) of RSI(14)
    SHORT  when it crosses BELOW
    exit   on the opposite cross, or a 2.0 ATR stop

WHY THIS EXISTS AS A STRATEGY AND NOT AS A CHART LINE. Adding an EMA to the RSI
pane is a display change and costs nothing. Reading a cross as a reason to
trade is a claim, and this project's rule is that a claim gets measured before
it reaches the live chart.

THE PRIOR IS AGAINST IT, stated here so the result cannot be read as a surprise
either way:

  * An EMA of RSI cannot contain information RSI does not. It is a linear
    filter over a series that is itself already a smoothed average -- Wilder's
    RSI is an EMA of gains over an EMA of losses. Smoothing a smoothing trades
    noise for lag; it does not manufacture signal.
  * RSI divergence was measured on this data at the confirmation bar against
    matched controls over three eras: hidden -1.51 / -0.24 / +0.07 pp, regular
    +0.12 / +0.71 / +4.79 pp -- the one era that looked real being the era the
    work was developed on.
  * The composite signal engine's momentum component (RSI distance from 50)
    walk-forwards at 49.9% against a 50.2% majority baseline over 2,235
    predictions. Below chance, on the same instrument.

So the honest expectation is that this fails. It is run anyway because a prior
is not a measurement, and because the cost of finding out is twenty minutes.

PARAMETERS ARE PRE-COMMITTED AND NOT SWEPT. rsi_len 14 and ema_len 9 are the
conventional pair (9 is the MACD signal length); atr_mult 2.0 matches the
validated Donchian so the R units are comparable. If a later version sweeps
them, the result stops being a test of this hypothesis and becomes a search,
and the honest thing then is to report the whole sweep rather than its best row.

ENTRY IS ON THE CROSS, NOT THE STATE. `rsi > ema` is true for long stretches
and entering on it would be entering at an arbitrary point inside a condition
that had already been true for days. sim/strategies/price_ema.py carries the
same note for the same reason: the two are different rules and only one of them
is what "crossover" means.
"""

import numpy as np

from ..core import FLAT, LONG, SHORT, Intent, Strategy
from ..indicators import atr, rsi


class RsiEmaCross(Strategy):
    name = 'rsi_ema_cross'

    def __init__(self, rsi_len=14, ema_len=9, atr_len=14, atr_mult=2.0):
        self.rsi_len, self.ema_len = int(rsi_len), int(ema_len)
        self.atr_len, self.atr_mult = int(atr_len), float(atr_mult)
        self.warmup = max(self.rsi_len + self.ema_len, self.atr_len) + 2

    def params(self):
        return {'rsi_len': self.rsi_len, 'ema_len': self.ema_len,
                'atr_len': self.atr_len, 'atr_mult': self.atr_mult}

    def prepare(self, bars):
        import pandas as pd

        r = np.asarray(rsi(bars, self.rsi_len), dtype=float)
        # EMA of the RSI itself, NaN-safe: the first `rsi_len` values of RSI are
        # undefined and an ewm over them would seed the average with garbage
        # that then decays through the whole series rather than being absent.
        s = pd.Series(r)
        e = s.ewm(span=self.ema_len, adjust=False,
                  min_periods=self.ema_len).mean().to_numpy(float)
        e[~np.isfinite(r)] = np.nan
        return {'rsi': r, 'sig': e,
                'atr': np.asarray(atr(bars, self.atr_len), dtype=float)}

    def on_bar(self, view, position):
        r, e = view.series('rsi'), view.series('sig')
        rp, ep = view.series('rsi', 1), view.series('sig', 1)
        a = view.series('atr')
        if not all(np.isfinite(x) for x in (r, e, rp, ep, a)) or a <= 0:
            return None

        crossed_up = rp <= ep and r > e
        crossed_dn = rp >= ep and r < e

        if position is not None:
            if (position.side == LONG and crossed_dn) or \
               (position.side == SHORT and crossed_up):
                return Intent(FLAT, tag='cross_back')
            return None

        c = view.close()
        if crossed_up:
            return Intent(LONG, stop=c - self.atr_mult * a, tag='rsi_cross_up')
        if crossed_dn:
            return Intent(SHORT, stop=c + self.atr_mult * a, tag='rsi_cross_dn')
        return None
