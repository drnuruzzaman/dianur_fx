"""
adxfilter.py -- take the breakout only when ADX says the market is trending.

    WithADX(Donchian(20/10), min_adx=25)

WHY THIS ONE IS WORTH TESTING, when the other filters were not. Splitting the
validated cell into quarters showed the edge earning in trends and giving it
back in chop -- gold's in-sample quarters ran -0.33 / -0.47 / +0.47 / +0.85 --
and the walk-forward put the return at +0.90 correlation with gold's absolute
move. ADX is the standard EX-ANTE measure of exactly that distinction: it rises
in a strong move in either direction and falls in a range, and it is
direction-blind, so it cannot smuggle in a "go long in a bull market" bias the
way sim/strategies/trendlong.py did.

WHAT WILL PROBABLY KILL IT, stated before the run rather than after:

  THE SAMPLE. On gold 4h, ADX>20 holds on 70% of bars, >25 on 51%, >30 on 35%.
  The cell has 237 in-sample trades, so a 25 threshold lands near 120 -- under
  the 200-trade floor, which is precisely how the last filter died (it cut 363
  trades to 167). The finer timeframes are the only cells with room to be
  filtered and still be measurable, so they carry the real test.

  THE LAG. ADX is doubly smoothed -- a Wilder average of DX, which is itself a
  Wilder average of the DI spread -- so it confirms a trend well after it
  starts. A breakout IS the start of a trend, so the filter is being asked to
  confirm the very thing the entry is betting on, using a statistic that by
  construction arrives late. It may reject the best entries.

THRESHOLDS ARE PRE-COMMITTED at the conventional 20 / 25 / 30 and are not for
tuning. Sweeping until something passes turns a test into a search; if a later
version widens the grid, the honest report is the whole grid, not its best row.
"""

from ..core import FLAT, Strategy
from ..indicators import adx


class WithADX(Strategy):
    """`inner`, but entries are suppressed unless ADX >= `min_adx`."""

    def __init__(self, inner: Strategy, min_adx: float = 25.0, length: int = 14):
        self.inner = inner
        self.min_adx = float(min_adx)
        self.length = int(length)
        self.name = '%s_adx%g' % (getattr(inner, 'name', 'strategy'), self.min_adx)
        # ADX needs ~2x length before it means anything, on top of the inner
        # rule's own warmup.
        self.warmup = max(inner.warmup, 2 * self.length + 2)

    def params(self):
        return {**self.inner.params(),
                'min_adx': self.min_adx, 'adx_len': self.length}

    def prepare(self, bars):
        series = dict(self.inner.prepare(bars))
        if 'adx' in series:
            raise KeyError('inner strategy already publishes an "adx" series')
        series['adx'] = adx(bars, self.length)
        return series

    def on_bar(self, view, position):
        intent = self.inner.on_bar(view, position)
        # EXITS ARE NEVER FILTERED. A filter that could block an exit would let
        # a losing position run because the market went quiet, which is the
        # opposite of what a trend filter is for -- and it would change the
        # rule's risk, not just its selectivity.
        if intent is None or intent.side == FLAT or position is not None:
            return intent
        a = view.series('adx')
        if a is None or a != a or a < self.min_adx:      # NaN-safe
            return None
        return intent
