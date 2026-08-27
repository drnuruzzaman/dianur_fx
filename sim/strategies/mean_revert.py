"""
mean_revert.py — fade a stretched market back to its own average.

The opposite bet to Donchian, and written for the same reason: as a CONTROL, not
a candidate. Two parameters, no discretion, no regime filter, no tuning. If the
engine makes this look brilliant, the engine is wrong.

The measurement that motivated it: across 19 walk-forward windows on gold and
EURUSD, Donchian 4h earned +0.0075 R per percent the market travelled and paid a
flat ~0.094 R per trade in costs, so it needed a 12.6% two-year move just to
break even. FX majors do not travel that far -- EURUSD spent most of 27 years
going nowhere in two-year chunks. Going nowhere is what this feeds on.

The rule:

    entry   close is `entry_atr` ATRs away from an `ma_len` simple average
            -- long when stretched BELOW it, short when stretched ABOVE
    exit    price touches the average again (the whole thesis), or
            the stop at `stop_atr` beyond entry, or
            `max_bars` elapse and the thesis has not paid

The time stop matters more here than in a breakout system. A trend follower's
loser exits itself when the trend resumes against it; a fader's loser is a
position on the wrong side of a market that has started to run, and without a
clock it becomes a permanent short of a bull market. `max_bars` is what stops
mean reversion from quietly turning into unlimited trend exposure.

Stop distance is ATR-relative so the same parameters mean the same thing on
gold and on yen, matching the other baselines.
"""

import numpy as np

from ..core import FLAT, LONG, SHORT, Intent, Strategy

# Same trigger, opposite sign. FOLLOW exists so the pair can be run against one
# another: if a signal loses to its own randomly-timed control, it carries real
# information pointed the wrong way, and the mirror is the way to find out.
FADE = 1
FOLLOW = -1


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


def _shift1(a):
    """Yesterday's value at today's index. The Series form was `.shift(1)`."""
    out = np.empty_like(a)
    out[0] = np.nan
    out[1:] = a[:-1]
    return out


class MeanRevert(Strategy):
    name = 'mean_revert'

    def __init__(self, ma_len=50, entry_atr=2.0, stop_atr=2.0, max_bars=40,
                 atr_len=14, direction=FADE):
        self.ma_len = int(ma_len)
        self.entry_atr = float(entry_atr)
        self.stop_atr = float(stop_atr)
        self.max_bars = int(max_bars)
        self.atr_len = int(atr_len)
        if int(direction) not in (FADE, FOLLOW):
            raise ValueError('direction must be FADE (+1) or FOLLOW (-1)')
        self.direction = int(direction)
        self.warmup = max(self.ma_len, self.atr_len) + 2

    def params(self):
        return {'ma_len': self.ma_len, 'entry_atr': self.entry_atr,
                'stop_atr': self.stop_atr, 'max_bars': self.max_bars,
                'atr_len': self.atr_len, 'direction': self.direction}

    def prepare(self, bars):
        # shift(1) so neither the average nor the ATR contains the bar being
        # decided on -- the same discipline as the Donchian channels
        return {
            'ma': bars['close'].rolling(self.ma_len).mean().shift(1).to_numpy(float),
            'atr': _shift1(np.asarray(atr(bars, self.atr_len), dtype=float)),
        }

    def on_bar(self, view, position):
        c = view.close()
        ma = view.series('ma')
        a = view.series('atr')
        if not np.isfinite(ma) or not np.isfinite(a) or a <= 0:
            return None

        if position is not None:
            held = view.i - position.entry_i
            if self.direction == FADE:
                # the thesis paid: price came back to the average
                if position.side == LONG and c >= ma:
                    return Intent(FLAT, tag='reverted')
                if position.side == SHORT and c <= ma:
                    return Intent(FLAT, tag='reverted')
            else:
                # FOLLOW is riding away from the average, so the mirror of
                # "reached the mean" is "gave up and came back to it"
                if position.side == LONG and c <= ma:
                    return Intent(FLAT, tag='lost_stretch')
                if position.side == SHORT and c >= ma:
                    return Intent(FLAT, tag='lost_stretch')
            # the thesis did not pay in time; leave before it becomes a trend bet
            if held >= self.max_bars:
                return Intent(FLAT, tag='time_stop')
            return None

        stretch = (c - ma) / a
        if abs(stretch) < self.entry_atr:
            return None
        below = stretch <= -self.entry_atr
        # FADE buys the dip; FOLLOW buys the stretch. Same trigger, same exits,
        # same costs -- only the sign differs, which is what makes the pair a
        # clean test of whether the signal carries direction at all.
        want_long = below if self.direction == FADE else not below
        side = LONG if want_long else SHORT
        stop = c - self.stop_atr * a if want_long else c + self.stop_atr * a
        return Intent(side, stop=stop,
                      tag=('fade_' if self.direction == FADE else 'follow_')
                          + ('down' if below else 'up'))
