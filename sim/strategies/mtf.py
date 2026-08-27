"""
mtf.py — the two validated-family baselines, each gated by a higher timeframe.

THE HYPOTHESIS, stated so the result can contradict it. XAUUSD 4h Donchian is
the one cell that passed every gate, and walk-forward attributed its return to
gold trending: correlation +0.90 with the absolute market move, and avg R
collapsing to +0.057 and +0.076 in the two quietest windows. If the edge IS
trend participation, refusing trades that fight a higher-timeframe trend should
keep the good ones and drop the rest.

THE PRIOR IS AGAINST IT, and that belongs here rather than in a footnote. A
regime gate was already tried on these baselines: it improved all four cells and
halved their drawdowns, and every improvement sat inside the noise. There is a
mechanical reason to expect that. A filter removes trades, a smaller sample has
a wider confidence interval, and an unchanged edge measured on fewer trades
looks better about half the time. Gold 4h has 231 and 207 trades against a
200-trade floor, so a filter that halves the count makes the cell UNJUDGEABLE
rather than better -- which is the first thing to check in the output.

Both variants inherit their base rule untouched: entries, the ATR stop and the
exit are the parent's, so any difference measured is the filter and nothing
else. The filter itself lives in context.py, once, for both.
"""

from ..core import FLAT, LONG, SHORT
from .context import context_direction, require_context
from .donchian import Donchian
from .ema_cross import EmaCross


class _ContextGated:
    """Mixin: adds a context-frame direction filter to a base strategy."""

    def _init_context(self, context_bars, context_tf, exec_tf, context_len):
        if context_bars is None:
            raise ValueError(
                '%s needs `context_bars`: the higher-timeframe series. It cannot '
                'be resampled from the execution bars here -- that would build '
                'the context out of the very data the rule is trading, which is '
                'not what a higher timeframe is.' % self.name)
        self.context_bars = context_bars
        self.context_tf = context_tf
        self.exec_tf = exec_tf
        self.context_len = int(context_len)

    def _context_params(self):
        return {'context_tf': self.context_tf, 'context_len': self.context_len,
                'exec_tf': self.exec_tf}

    def _add_context(self, series, bars):
        series['ctx_dir'] = context_direction(
            bars.index, self.exec_tf, self.context_bars, self.context_tf,
            self.context_len)
        return series


class DonchianMTF(_ContextGated, Donchian):
    name = 'donchian_mtf'

    def __init__(self, entry=20, exit=10, atr_len=14, atr_mult=2.0,
                 context_bars=None, context_tf='1d', exec_tf='4h', context_len=20):
        Donchian.__init__(self, entry=entry, exit=exit, atr_len=atr_len,
                          atr_mult=atr_mult)
        self._init_context(context_bars, context_tf, exec_tf, context_len)

    def params(self):
        return {**Donchian.params(self), **self._context_params()}

    def prepare(self, bars):
        return self._add_context(Donchian.prepare(self, bars), bars)

    def on_bar(self, view, position):
        return require_context(Donchian.on_bar(self, view, position),
                               view.series('ctx_dir'), FLAT, LONG, SHORT)


class EmaCrossMTF(_ContextGated, EmaCross):
    name = 'ema_cross_mtf'

    def __init__(self, fast=21, slow=50, atr_len=14, atr_mult=2.5,
                 context_bars=None, context_tf='1d', exec_tf='4h', context_len=20):
        EmaCross.__init__(self, fast=fast, slow=slow, atr_len=atr_len,
                          atr_mult=atr_mult)
        self._init_context(context_bars, context_tf, exec_tf, context_len)

    def params(self):
        return {**EmaCross.params(self), **self._context_params()}

    def prepare(self, bars):
        return self._add_context(EmaCross.prepare(self, bars), bars)

    def on_bar(self, view, position):
        return require_context(EmaCross.on_bar(self, view, position),
                               view.series('ctx_dir'), FLAT, LONG, SHORT)
