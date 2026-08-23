"""
intrabar.py — which came first inside a bar, the stop or the target?

An OHLC bar records that both levels were touched but not in what order, and
that gap is where backtests flatter themselves. Three answers are possible and
this module names all three rather than picking one silently:

    PESSIMISTIC   the stop came first. Safe, and the engine's long-standing
                  default; the gates keep using it.
    OPTIMISTIC    the target came first. Not a belief -- the other end of the
                  interval, so the pair brackets the truth.
    INTRABAR      go and look. Walk the bar's sub-bars on a lower timeframe and
                  see which level a sub-bar reaches first.

INTRABAR is the realistic estimate, and it was checked before being trusted.
tools/intrabar_validate.py builds ambiguous cases from real bars, asks 5m and
1m what happened, and scores both against the tick record:

    EURUSD.a 1h, 323 cases    5m resolved 97.8%, correct 97.8%, WRONG 0.0%
    XAUUSD.a 1h, 317 cases    5m resolved 100%,  correct 100%,  WRONG 0.0%
    pessimistic on the same cases: correct ~50%

Zero errors is not luck, it is the shape of the problem. A sub-bar that touches
one level and not the other is a fact, not an inference; the only uncertainty
left is a sub-bar containing both, and that is reported as UNRESOLVED rather
than guessed. So the resolver can remove error but never introduce it, which is
what makes it safe to prefer over the pessimistic assumption.

Sub-bars are fetched only for bars that are actually ambiguous. Fewer than one
bar in three hundred is, so the lookup is rare enough to be nearly free -- and
loading a lower timeframe alongside every bar would cost an order of magnitude
for nothing.
"""

import pandas as pd

PESSIMISTIC = 'pessimistic'
OPTIMISTIC = 'optimistic'
INTRABAR = 'intrabar'
MODES = (PESSIMISTIC, OPTIMISTIC, INTRABAR)

STOP, TARGET, UNRESOLVED = 'stop', 'target', 'unresolved'

# a bar of this timeframe is resolved with sub-bars of that one
DEFAULT_SUB_TF = {'1d': '15m', '4h': '5m', '1h': '5m', '30m': '1m',
                  '15m': '1m', '5m': '1m'}
TF_DELTA = {'1m': '1min', '5m': '5min', '15m': '15min', '30m': '30min',
            '1h': '60min', '4h': '240min', '1d': '1440min'}


def sub_tf_for(tf, requested=None):
    """The timeframe to resolve `tf` with. None means it cannot be resolved."""
    if requested:
        return requested
    return DEFAULT_SUB_TF.get(tf)


class SubBars:
    """
    Lazy, cached access to the sub-bars inside a higher-timeframe bar.

    Loading is deferred until the first ambiguous bar so a run that never hits
    one -- which is most runs, and every run of a strategy with no target --
    pays nothing at all.
    """

    def __init__(self, symbol, tf, sub_tf=None, loader=None):
        self.symbol = symbol
        self.tf = tf
        self.sub_tf = sub_tf_for(tf, sub_tf)
        self._loader = loader
        self._bars = None
        self._failed = False
        self.lookups = 0
        self.delta = pd.Timedelta(TF_DELTA[tf]) if tf in TF_DELTA else None

    @property
    def available(self):
        return self.sub_tf is not None and not self._failed

    def _load(self):
        if self._bars is not None or self._failed:
            return
        try:
            load = self._loader
            if load is None:
                from .instruments import load as load
            self._bars = load(self.symbol, self.sub_tf, None, None)
            if self._bars is None or self._bars.empty:
                self._failed = True
        except Exception:                                   # noqa: BLE001
            # no sub-bar history for this symbol/timeframe: fall back rather
            # than fail the run, and let the caller report the fallback rate
            self._failed = True

    def slice(self, t0):
        """Sub-bars inside the higher-timeframe bar that opens at t0."""
        self._load()
        if self._failed or self.delta is None:
            return None
        self.lookups += 1
        return self._bars.loc[t0:t0 + self.delta - pd.Timedelta('1ms')]


def first_touch(sub, side, stop, target):
    """
    Walk sub-bars in order; return whichever level is reached first.

    A sub-bar holding BOTH is still ambiguous -- the recursion does not end just
    because the bars got smaller -- and says UNRESOLVED rather than guessing, so
    the caller can count how often finer data still fails to answer.
    """
    if sub is None or sub.empty:
        return UNRESOLVED
    lo = sub['low'].to_numpy()
    hi = sub['high'].to_numpy()
    for k in range(len(sub)):
        if side > 0:                                        # long
            hit_stop = stop is not None and lo[k] <= stop
            hit_target = target is not None and hi[k] >= target
        else:                                               # short
            hit_stop = stop is not None and hi[k] >= stop
            hit_target = target is not None and lo[k] <= target
        if hit_stop and hit_target:
            return UNRESOLVED
        if hit_stop:
            return STOP
        if hit_target:
            return TARGET
    return UNRESOLVED


def resolve(mode, sub_bars, t0, side, stop, target):
    """
    Which level came first, plus whether finer data was consulted.

    Returns (STOP|TARGET, resolved_by) where resolved_by is the mode's name, or
    the sub-timeframe when INTRABAR actually settled it, or 'fallback' when
    INTRABAR looked and could not tell.
    """
    if mode == OPTIMISTIC:
        return TARGET, OPTIMISTIC
    if mode != INTRABAR or sub_bars is None or not sub_bars.available:
        return STOP, PESSIMISTIC
    verdict = first_touch(sub_bars.slice(t0), side, stop, target)
    if verdict == UNRESOLVED:
        return STOP, 'fallback'
    return verdict, sub_bars.sub_tf
