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

from ..core import FLAT, LONG, SHORT, Intent, Strategy


# ATR comes from sim/indicators, which is the ONE implementation in this
# project and the one tests/test_parity.py holds to js/chart/tlengine.js at
# 1e-9. These files each carried a private copy that seeded the Wilder
# recursion differently: it converged, but differed by up to 0.86 price units
# during warmup, so the strategies were sizing stops from an ATR the chart
# could not reproduce and a JS signal service would have quoted levels the
# backtest never traded. Unifying was verified free -- gold 4h donchian keeps
# 207/231 trades, the same win rate, PF and drawdown, with avg_R moving 0.001.
# The name is re-exported because tools/ imports `atr` from here.
from ..indicators import atr  # noqa: F401


class Donchian(Strategy):
    name = 'donchian'

    #: What counts as a break of the channel. These are TWO HYPOTHESES, not a
    #: setting to tune, and they are not interchangeable:
    #:
    #:   'close'  Close[t] > Upper[t]. Confirmation: the bar had to finish
    #:            beyond the channel. Fewer signals, later, and the level is
    #:            known when the decision is made.
    #:   'high'   High[t] > Upper[t]. The intrabar touch. Fires earlier and more
    #:            often, and every signal that closes back inside is one the
    #:            'close' variant never took.
    #:
    #: Mixing them -- entering on the touch but measuring the channel on closes,
    #: or vice versa -- is how a backtest ends up trading a rule nobody could
    #: follow. They are run and reported separately.
    TRIGGERS = ('close', 'high')

    def __init__(self, entry=20, exit=10, atr_len=14, atr_mult=2.0,
                 trigger='close'):
        self.entry, self.exit = int(entry), int(exit)
        self.atr_len, self.atr_mult = int(atr_len), float(atr_mult)
        if trigger not in self.TRIGGERS:
            raise ValueError('trigger must be one of %s, got %r'
                             % (', '.join(self.TRIGGERS), trigger))
        self.trigger = trigger
        self.name = 'donchian' if trigger == 'close' else 'donchian_high'
        self.warmup = max(self.entry, self.exit, self.atr_len) + 2

    def params(self):
        return {'entry': self.entry, 'exit': self.exit,
                'atr_len': self.atr_len, 'atr_mult': self.atr_mult,
                'trigger': self.trigger}

    def prepare(self, bars):
        # shift(1) so a channel never contains the bar being decided on
        return {
            'hi': bars['high'].rolling(self.entry).max().shift(1).to_numpy(float),
            'lo': bars['low'].rolling(self.entry).min().shift(1).to_numpy(float),
            'exit_hi': bars['high'].rolling(self.exit).max().shift(1).to_numpy(float),
            'exit_lo': bars['low'].rolling(self.exit).min().shift(1).to_numpy(float),
            'atr': np.asarray(atr(bars, self.atr_len), dtype=float),
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
        # The trigger price is the bar's HIGH/LOW or its CLOSE depending on the
        # hypothesis; the STOP is always measured from the close, because that is
        # the price known when the order for the next open is placed.
        up_px = view.high() if self.trigger == 'high' else c
        dn_px = view.low() if self.trigger == 'high' else c
        if np.isfinite(hi) and up_px > hi:
            return Intent(LONG, stop=c - self.atr_mult * a, tag='breakout_up')
        if np.isfinite(lo) and dn_px < lo:
            return Intent(SHORT, stop=c + self.atr_mult * a, tag='breakout_dn')
        return None
