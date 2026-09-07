"""
target.py — bolt a fixed R target onto a strategy that has none, to measure
what capping the tail costs.

XAUUSD 4h donchian is the only strategy in this project that has passed every
gate, and it has NO take-profit: 138 of its 207 out-of-sample exits are the
trailing channel and none are a target. At a 36% win rate and PF 1.47 the
arithmetic only closes because a few winners run for weeks, so a take-profit
caps precisely the tail that pays for the 64% that lose.

That is an argument, not a measurement. This makes it a measurement.

WHAT IT DOES. Wraps any strategy and adds `r_mult` R of target to every entry,
leaving the stop and every exit rule untouched. R is measured at the signal, as
|close - stop|, which is the same reference the stop itself uses -- the realised
risk differs slightly once the fill gaps and pays costs, exactly as it already
does for the stop, so the wrapper introduces no new approximation.

WHAT IT DOES NOT DO. Scale out. `Position.lots` is a scalar and the engine
closes a position whole, so this measures FULL exit at the target, which is the
pessimistic bound on a partial-exit rule: taking half off at 1R must land
between this and the uncapped original. If capping at a given R barely hurts,
partial exits at that R are safe and the engine change is worth making. If even
a distant cap hurts badly, the tail is everything and no take-profit survives --
and that is worth knowing before writing scale-out logic for it.
"""

from ..core import FLAT, LONG, Intent, Strategy


class WithTarget(Strategy):
    """`inner`, unchanged, plus a target `r_mult` R away from the signal."""

    def __init__(self, inner: Strategy, r_mult: float = 2.0):
        self.inner = inner
        self.r_mult = float(r_mult)
        self.name = '%s_tp%g' % (getattr(inner, 'name', 'strategy'), self.r_mult)
        self.warmup = inner.warmup

    def params(self):
        return {**self.inner.params(), 'tp_r_mult': self.r_mult}

    def prepare(self, bars):
        return self.inner.prepare(bars)

    def on_bar(self, view, position):
        intent = self.inner.on_bar(view, position)
        # exits, and entries the inner strategy declined to put a stop on, pass
        # through untouched: without a stop there is no R to measure a target in
        if intent is None or intent.side == FLAT or intent.stop is None:
            return intent
        if intent.target is not None:
            return intent                      # the inner strategy meant it
        c = view.close()
        risk = abs(c - intent.stop)
        if risk <= 0:
            return intent
        target = c + self.r_mult * risk if intent.side == LONG else c - self.r_mult * risk
        return Intent(intent.side, stop=intent.stop, target=target, tag=intent.tag)
