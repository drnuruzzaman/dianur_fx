"""
trendlong.py — the ONE filter the regime analysis pointed at, and nothing else.

    donchian_trendlong = Donchian(20/10, 2 ATR), entries restricted to
                         LONG only, and only when the prior 50-bar return
                         is positive.

WHERE THIS CAME FROM, because a filter with no provenance is a guess. The regime
split of the validated cell (runs/regime_XAUUSDa_4h_donchian.csv, 363 trades,
gold 4h 2018-2026) put every ex-ante dimension into buckets and only one cell of
one 2x2 had a bootstrap interval clear of zero:

    long  / with prior trend   139  +0.4927  [+0.1365, +0.8503]
    long  / against prior      61   +0.0086  [-0.3729, +0.4202]
    short / with prior trend  103   +0.0035  [-0.2282, +0.2462]
    short / against prior      60   -0.0677  [-0.3144, +0.1814]

It is an INTERACTION: neither marginal survives on its own, so the filter has to
carry both conditions or it is not the thing that was observed.

PRE-COMMITTED, AND THE PARAMETERS ARE NOT FOR TUNING. The 50-bar lookback is the
one the regime tool used; it was not chosen by trying several. If a later version
sweeps that number, the result stops being a test of this hypothesis and becomes
a search, and the honest thing then is to report the whole sweep rather than its
best row.

WHAT IT IS EXPECTED TO FAIL AT. The filter was found on the same 363 trades that
validated the rule, over a window where gold rose almost throughout, and it was
one of ~22 bucket comparisons at 90% intervals -- chance alone yields about two.
"Go long with the uptrend on an asset in a bull run" is close to restating the
asset. So the burden is on the re-test, not on this file.

EXITS ARE NEVER FILTERED. A rule that can be blocked from LEAVING is a different
and far worse rule than one blocked from entering: it would sit in a loser
because a filter disagreed with closing it. Only entries are gated.
"""

import numpy as np

from ..core import FLAT, LONG
from .donchian import Donchian

#: Bars of prior return used as the trend test. FIXED -- see the docstring.
TREND_LEN = 50


class DonchianTrendLong(Donchian):
    """Donchian, entering only long and only with the prior trend."""

    name = 'donchian_trendlong'

    def __init__(self, entry=20, exit=10, atr_len=14, atr_mult=2.0,
                 trigger='close', trend_len=TREND_LEN):
        Donchian.__init__(self, entry=entry, exit=exit, atr_len=atr_len,
                          atr_mult=atr_mult, trigger=trigger)
        self.trend_len = int(trend_len)
        self.name = 'donchian_trendlong'
        self.warmup = max(self.warmup, self.trend_len + 2)

    def params(self):
        return {**Donchian.params(self), 'trend_len': self.trend_len}

    def prepare(self, bars):
        series = Donchian.prepare(self, bars)
        close = bars['close'].to_numpy(float)
        n = self.trend_len
        # Prior return, SHIFTED so the bar being decided on is not inside its
        # own trend measure. `regime.py` measured (close[i-1] - close[i-1-n]),
        # i.e. the window ending at the previous close, and this reproduces
        # exactly that -- a filter that used close[i] would be reading the bar
        # it is deciding on and would not be the thing that was observed.
        prior = np.full(len(close), np.nan)
        for i in range(n + 1, len(close)):
            prior[i] = close[i - 1] - close[i - 1 - n]
        series['prior'] = prior
        return series

    def on_bar(self, view, position):
        intent = Donchian.on_bar(self, view, position)
        if intent is None or intent.side == FLAT:
            return intent                      # exits pass through untouched
        if intent.side != LONG:
            return None                        # shorts contributed nothing
        prior = view.series('prior')
        if not np.isfinite(prior) or prior <= 0:
            return None                        # long, but against the prior move
        return intent
