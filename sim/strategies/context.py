"""
context.py — one higher-timeframe direction filter, shared by the MTF variants.

Written once on purpose. The project's worst silent bug came from three
strategies each carrying a private ATR that seeded the Wilder recursion
differently; they converged, but diverged by up to 0.86 price units during
warmup and the chart was drawing stops the backtest had never used. A
per-strategy copy of the alignment logic would be the same mistake with a worse
failure mode, because a leak here is look-ahead rather than a wrong number.

THE ALIGNMENT IS THE WHOLE RISK. An execution bar may only see context bars that
had already CLOSED when it closed. `sim.tl.mtf.align_index` enforces that and
RAISES rather than clipping, so a mistake stops the run instead of inflating it.
"""

import numpy as np

from ..tl.mtf import align_index


def context_direction(exec_index, exec_tf, ctx_bars, ctx_tf, length=20):
    """
    Per execution bar: +1 when the context frame sits above the midpoint of its
    own `length`-bar Donchian channel, -1 below, 0 while the channel is unformed
    or no context bar has closed yet.

    The midpoint of a channel is used rather than a moving average because it is
    the same construct the execution rule already trades -- adding an EMA here
    would introduce a second, unrelated definition of "trend" and make any
    result impossible to attribute.
    """
    hi = ctx_bars['high'].rolling(length).max().shift(1).to_numpy(float)
    lo = ctx_bars['low'].rolling(length).min().shift(1).to_numpy(float)
    mid = (hi + lo) / 2.0
    close = ctx_bars['close'].to_numpy(float)

    direction = np.zeros(len(close))
    formed = np.isfinite(mid)
    direction[formed & (close > mid)] = 1.0
    direction[formed & (close < mid)] = -1.0

    pos = align_index(exec_index, exec_tf, ctx_bars.index, ctx_tf, strict=True)
    out = np.zeros(len(exec_index))
    have = pos >= 0
    out[have] = direction[pos[have]]
    return out


def require_context(intent, direction, FLAT, LONG, SHORT):
    """
    Gate an entry on the context direction. Exits pass through untouched.

    A rule that can be blocked from LEAVING is a different and much worse rule
    than one blocked from entering -- it would hold a loser because a higher
    timeframe disagreed with closing it.
    """
    if intent is None or intent.side == FLAT:
        return intent
    if intent.side == LONG and direction <= 0:
        return None
    if intent.side == SHORT and direction >= 0:
        return None
    return intent
