"""
retest.py — STEP 6. Wait for the break to be retested instead of buying it.

    break above the 20-bar channel
        -> price pulls back to the broken level
        -> it holds
        -> enter

versus the baseline, which enters at the next open after the break and never
waits for anything.

THE HYPOTHESIS CUTS BOTH WAYS, which is why it is measured rather than assumed.
A retest filters out breaks that fail immediately, and Step 5 already found that
the weakest quartile by displacement returns -0.0345 R -- those marginal breaks
are exactly what a retest requirement should remove. But the strongest trends
never come back to be retested at all, so waiting means missing the trades that
carry the rule: the top quartile of market moves supplies +187 R of the +65 R
total, and skipping even a few of those would sink it.

So the outcome to watch is not avg_R on its own. It is avg_R AND the trade
count AND whether the biggest winners survive the wait.

WHAT COUNTS AS A RETEST, pre-committed and not tuned:

    break at bar b      close[b] > upper[b]        level L = upper[b]
    window              the next MAX_WAIT bars
    touched             low[t] <= L                (price came back to it)
    holds               close[t] > L               (and closed back above)
    invalidated         close[t] < L               (the break failed outright)

Entry fires on the first bar that is both touched and holding, and fills at the
next open like every other rule here. MAX_WAIT is 10 and stays 10: sweeping it
would turn a hypothesis test into a search, and the honest thing then would be
to report the whole sweep rather than its best row.

NO CROSS-BAR STATE. The scan is vectorised in prepare() rather than tracked
through on_bar, because a strategy instance is reused across all 61 runs of a
time-shift control and any state that survived between them would quietly
contaminate the controls with the real run's history. prepare() is handed the
whole series and returns arrays; on_bar only ever reads index i.

CAUSALITY. The break at b and the retest scan for bar t use only bars b..t, all
of which have closed by the time bar t closes. tests/test_lookahead.py drives
BarView, which refuses any index beyond i, and the arrays here are built with
the same forward-only scan.
"""

import numpy as np

from ..core import FLAT, LONG, SHORT, Intent
from .donchian import Donchian

#: Bars allowed between the break and the retest. FIXED -- see the docstring.
MAX_WAIT = 10


class DonchianRetest(Donchian):
    """Donchian's break, entered only after the level is retested and holds."""

    name = 'donchian_retest'

    def __init__(self, entry=20, exit=10, atr_len=14, atr_mult=2.0,
                 trigger='close', max_wait=MAX_WAIT):
        Donchian.__init__(self, entry=entry, exit=exit, atr_len=atr_len,
                          atr_mult=atr_mult, trigger=trigger)
        self.max_wait = int(max_wait)
        self.name = 'donchian_retest'
        self.warmup = max(self.warmup, self.entry + self.max_wait + 2)

    def params(self):
        return {**Donchian.params(self), 'max_wait': self.max_wait}

    def prepare(self, bars):
        series = Donchian.prepare(self, bars)
        hi, lo = series['hi'], series['lo']
        c = bars['close'].to_numpy(float)
        h = bars['high'].to_numpy(float)
        l = bars['low'].to_numpy(float)
        n = len(c)

        fire_long = np.zeros(n, dtype=bool)
        fire_short = np.zeros(n, dtype=bool)

        for b in range(1, n):
            # ---- a break above, then a pullback that holds ----------------
            if np.isfinite(hi[b]) and c[b] > hi[b]:
                level = hi[b]
                touched = False
                for t in range(b + 1, min(n, b + 1 + self.max_wait)):
                    if c[t] < level:
                        break                       # the break failed outright
                    if l[t] <= level:
                        touched = True
                    if touched and c[t] > level:
                        fire_long[t] = True
                        break
            # ---- the mirror image below ----------------------------------
            if np.isfinite(lo[b]) and c[b] < lo[b]:
                level = lo[b]
                touched = False
                for t in range(b + 1, min(n, b + 1 + self.max_wait)):
                    if c[t] > level:
                        break
                    if h[t] >= level:
                        touched = True
                    if touched and c[t] < level:
                        fire_short[t] = True
                        break

        series['fire_long'] = fire_long.astype(float)
        series['fire_short'] = fire_short.astype(float)
        return series

    def on_bar(self, view, position):
        a = view.series('atr')
        if not np.isfinite(a) or a <= 0:
            return None
        if position is not None:
            # the exit is the BASELINE's, untouched: this experiment changes
            # when you get in, and mixing an exit change in would make the
            # result impossible to attribute to either
            return Donchian.on_bar(self, view, position)
        c = view.close()
        if view.series('fire_long') > 0:
            return Intent(LONG, stop=c - self.atr_mult * a, tag='retest_up')
        if view.series('fire_short') > 0:
            return Intent(SHORT, stop=c + self.atr_mult * a, tag='retest_dn')
        return None
