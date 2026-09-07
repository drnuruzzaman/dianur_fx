"""
emafilter.py — Donchian's entry, gated by a long EMA. A REGIME FILTER.

The idea is the standard Turtle companion and appears in most public Donchian
EAs: take the breakout only in the direction of the longer trend, so longs need
close > EMA(n) and shorts need close < it.

WHY THIS IS A SUBCLASS AND NOT A FLAG ON Donchian. It was written as a
`ema_len` parameter on the base class first, and that broke two strategies
silently. `DonchianTrendLong` already owns `trend_len` for a PRIOR-RETURN
window, and `DonchianExitEma` already owns `ema_len` for its EXIT ema -- and
both delegate their entry to `Donchian.on_bar`. A filter living in the base
therefore read whichever attribute those subclasses had set and applied an
entry gate neither of them ever had, using a series one of them had already
overwritten with a shifted copy. Nothing failed loudly; the strategies simply
became different strategies.

The lesson is the one this file exists to encode: a variant belongs in a
variant, and a shared base class must not grow a feature only one child wants.
`exits.py` had it right -- subclass, override one branch, change nothing else.

CAUSALITY. The EMA is NOT shifted, unlike the channels. An EMA of closes up to
and including this close is knowable at that close. The channel must exclude
the bar it judges or `close > highest_high` becomes unsatisfiable, since the
high would already contain the close. Two different quantities, two different
rules, and conflating them is its own class of bug.

DIRECTION ONLY. The filter never opens a trade the channel did not, and never
closes one early. An exit that consulted the EMA would be a second exit rule
competing with the channel that carries this strategy's edge -- and that
experiment already exists, as `donchian_exit_ema` in exits.py.
"""

import numpy as np

from ..core import FLAT
from .donchian import Donchian

#: The length public Donchian/Turtle EAs converge on for the regime filter.
TREND_EMA = 200


class DonchianEmaFilter(Donchian):
    """Donchian, taking breakouts only with the EMA(n) trend."""

    name = 'donchian_ema200'

    def __init__(self, entry=20, exit=10, atr_len=14, atr_mult=2.0,
                 trigger='close', entry_ema_len=TREND_EMA):
        Donchian.__init__(self, entry=entry, exit=exit, atr_len=atr_len,
                          atr_mult=atr_mult, trigger=trigger)
        # `entry_ema_len`, spelled out: `ema_len` and `trend_len` are both
        # taken by siblings that share this entry path.
        self.entry_ema_len = int(entry_ema_len)
        self.name = 'donchian_ema%d' % self.entry_ema_len
        self.warmup = max(self.warmup, self.entry_ema_len + 2)

    def params(self):
        return {**Donchian.params(self), 'entry_ema_len': self.entry_ema_len}

    def prepare(self, bars):
        series = Donchian.prepare(self, bars)
        # ewm(adjust=False, min_periods=n) is what js/chart/rules.js emaSeries
        # reproduces; anything else shifts every signal by a few bars without
        # ever looking wrong on a chart.
        series['entry_ema'] = (
            bars['close'].ewm(span=self.entry_ema_len, adjust=False,
                              min_periods=self.entry_ema_len)
            .mean().to_numpy(float))
        return series

    def on_bar(self, view, position):
        intent = Donchian.on_bar(self, view, position)
        if intent is None or intent.side == FLAT:
            return intent                      # exits pass through untouched
        ema = view.series('entry_ema')
        if not np.isfinite(ema):
            return None
        c = view.close()
        with_trend = (c > ema) if intent.side > 0 else (c < ema)
        return intent if with_trend else None
