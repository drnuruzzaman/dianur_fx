"""
pivots.py — swing high/low detection, ported from js/chart/trendlines.js.

Same comparison rule as the JS (strict on the left, tolerant on the right, so a
flat top reports once) because tests/test_parity.py compares the two over real
bars: the chart and the backtest must see the same swings.

LOOK-AHEAD, FIRST CLASS: a fractal pivot is only knowable `strength` bars after
it happened — that is what "beats the bars on each side" means. `confirmed_at_i`
records the bar at which the pivot became visible, and the engine may only use a
pivot once the simulated clock has passed it. The convenience `findPivots`
equivalent is kept for parity, but the engine uses `pivots_confirmed_by`.
"""

import numpy as np


def find_pivots(high, low, strength=3):
    """
    Fractal pivots over full arrays. Returns (highs, lows), each a list of
    dicts: {i, t_index, price, confirmed_i}.

    `i` is where the pivot IS; `confirmed_i = i + strength` is the earliest bar
    from which it may legally be used.
    """
    h = np.asarray(high, dtype=float)
    l = np.asarray(low, dtype=float)
    n = len(h)
    highs, lows = [], []
    for i in range(strength, n - strength):
        is_high = True
        is_low = True
        for k in range(1, strength + 1):
            if not (h[i] > h[i - k] and h[i] >= h[i + k]):
                is_high = False
            if not (l[i] < l[i - k] and l[i] <= l[i + k]):
                is_low = False
            if not is_high and not is_low:
                break
        if is_high:
            highs.append({'i': i, 'price': float(h[i]), 'confirmed_i': i + strength})
        if is_low:
            lows.append({'i': i, 'price': float(l[i]), 'confirmed_i': i + strength})
    return highs, lows


def pivots_confirmed_by(pivots, upto_i):
    """
    Only the pivots a bar-`upto_i` observer could know about.

    This is the engine-level guard, not a test: a pivot at bar 100 with strength
    3 is invisible until bar 103, and using it earlier is the classic MTF /
    fractal look-ahead leak that makes a trendline backtest look prescient.
    """
    return [p for p in pivots if p['confirmed_i'] <= upto_i]
