"""
scaleout.py -- take part of the position off at a fixed R, let the rest run.

    WithScaleOut(Donchian(), r_mult=1.5, frac=0.5)

WHY THIS AND NOT A TAKE-PROFIT. tools/tp_sweep.py measured what a full cap costs
XAUUSD 4h donchian, and the answer depends on the era in a way that condemns it:
a 1.5R cap keeps 124% of net R out of sample and 42% in sample. The reason is
structural rather than statistical -- 2016-2020 was range-bound gold and
2021-2026 was the run to 4,700, so a cap helps in chop and hurts in trend, and
this rule's edge IS a trend edge (walk-forward put the return at +0.90
correlation with gold's absolute move). A take-profit is therefore a bet against
the thing that makes the strategy work.

A partial exit is the only version of the idea that is not that bet. Half the
position books a win at 1.5R -- which is what raises the win rate -- and half
stays in the tail that actually pays for the 64% of trades that lose.

WHAT IT CANNOT DO. Beat the uncapped rule on net R. Half a position capped is
still capping, and the tail is where the money is. What it can plausibly do is
lift the win rate and cut drawdown for a SMALLER loss of return than a full cap,
and hold in both eras because half the tail survives. That is the hypothesis;
tools/scaleout_sweep.py is the test, against the same bar the tp sweep used --
keep >=80% of net R in BOTH eras or it is not worth having.

The stop and every exit rule are untouched. The runner is not moved to
break-even: that is a second change, it would need its own measurement, and
bundling two changes makes the result unattributable.
"""

from ..core import FLAT, Strategy


class WithScaleOut(Strategy):
    """`inner`, unchanged, plus a partial exit `r_mult` R away from the signal."""

    def __init__(self, inner: Strategy, r_mult: float = 1.5, frac: float = 0.5):
        if not 0.0 < frac < 1.0:
            raise ValueError('frac must be strictly between 0 and 1, got %r' % frac)
        if r_mult <= 0:
            raise ValueError('r_mult must be positive, got %r' % r_mult)
        self.inner = inner
        self.r_mult = float(r_mult)
        self.frac = float(frac)
        self.name = '%s_so%gx%g' % (getattr(inner, 'name', 'strategy'),
                                    self.r_mult, self.frac)
        self.warmup = inner.warmup

    def params(self):
        return {**self.inner.params(),
                'scale_r': self.r_mult, 'scale_frac': self.frac}

    def prepare(self, bars):
        return self.inner.prepare(bars)

    def on_bar(self, view, position):
        intent = self.inner.on_bar(view, position)
        # exits pass through, and an entry with no stop has no R to measure in
        if intent is None or intent.side == FLAT or intent.stop is None:
            return intent
        intent.scale_r = self.r_mult
        intent.scale_frac = self.frac
        return intent
