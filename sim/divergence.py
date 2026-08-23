"""
divergence.py — RSI divergence detection, the single Python definition.

Mirrored by js/chart/divergence.js and compared value for value in
tests/test_parity.py, so the divergence you see drawn on the chart is exactly the
one the backtester traded. Two implementations that disagree would be worse than
none: you would trust the wrong one.

    Bullish: price prints a LOWER swing low  while RSI prints a HIGHER low
    Bearish: price prints a HIGHER swing high while RSI prints a LOWER high

TIMING. A fractal pivot is only visible `strength` bars after it happens, so each
divergence carries two different bar indexes:

    pivot_i       where the swing actually is  — where a line should be DRAWN
    confirmed_i   where it became knowable     — the earliest bar it may be USED

Drawing at `pivot_i` and trading at `confirmed_i` is the honest combination. A
chart that draws the line and implies you could have acted at the swing low is
the most common way divergence is oversold as a technique.
"""

import numpy as np

from .indicators import rsi as rsi_series
from .tl.pivots import find_pivots

DEFAULTS = {
    'rsi_len': 14,
    'strength': 3,
    'min_rsi_gap': 2.0,      # RSI points, so the divergence is more than noise
    'min_pivot_gap': 5,      # swings closer than this are the same swing
    'max_pivot_gap': 60,     # swings further apart than this are unrelated
    'oversold': 40.0,        # a bullish divergence needs genuinely weak RSI
    'overbought': 60.0,
}


def find_divergences(bars, **opts):
    """
    Every divergence in the series, oldest first.

    Each is a dict:
        kind          'bullish' | 'bearish'
        pivot_i       bar index of the swing that completes the divergence
        prev_i        bar index of the swing it is compared against
        confirmed_i   bar index from which it may legally be used
        price, prev_price, rsi, prev_rsi, rsi_gap, bars_apart
    """
    o = {**DEFAULTS, **opts}
    high = np.asarray(bars['high'], dtype=float)
    low = np.asarray(bars['low'], dtype=float)
    r = rsi_series(bars, o['rsi_len'])
    highs, lows = find_pivots(high, low, o['strength'])

    out = []
    for kind, pivots, sign in (('bullish', lows, 1), ('bearish', highs, -1)):
        for k in range(1, len(pivots)):
            cur, prev = pivots[k], pivots[k - 1]
            gap = cur['i'] - prev['i']
            if not (o['min_pivot_gap'] <= gap <= o['max_pivot_gap']):
                continue
            rc, rp = r[cur['i']], r[prev['i']]
            if np.isnan(rc) or np.isnan(rp):
                continue
            if kind == 'bullish':
                ok = (cur['price'] < prev['price']
                      and rc > rp + o['min_rsi_gap']
                      and rc < o['oversold'])
            else:
                ok = (cur['price'] > prev['price']
                      and rc < rp - o['min_rsi_gap']
                      and rc > o['overbought'])
            if not ok:
                continue
            out.append({
                'kind': kind,
                'pivot_i': cur['i'], 'prev_i': prev['i'],
                'confirmed_i': cur['confirmed_i'],
                'price': float(cur['price']), 'prev_price': float(prev['price']),
                'rsi': float(rc), 'prev_rsi': float(rp),
                'rsi_gap': round(float(abs(rc - rp)), 6),
                'bars_apart': int(gap),
            })
    out.sort(key=lambda d: (d['confirmed_i'], d['kind']))
    return out


def as_arrays(bars, **opts):
    """
    Per-bar arrays keyed at `confirmed_i`, for a strategy to consume through the
    simulator's BarView. Indexing by the confirmation bar is what keeps the
    causality guarantee: nothing appears before it could have been known.
    """
    n = len(bars)
    side = np.zeros(n)            # +1 bullish, -1 bearish, 0 none
    pivot_price = np.full(n, np.nan)
    rsi_gap = np.full(n, np.nan)
    bars_apart = np.full(n, np.nan)
    for d in find_divergences(bars, **opts):
        i = d['confirmed_i']
        if i >= n:
            continue
        side[i] = 1.0 if d['kind'] == 'bullish' else -1.0
        pivot_price[i] = d['price']
        rsi_gap[i] = d['rsi_gap']
        bars_apart[i] = d['bars_apart']
    return {'div_side': side, 'div_pivot_price': pivot_price,
            'div_rsi_gap': rsi_gap, 'div_bars_apart': bars_apart}
