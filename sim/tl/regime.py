"""
regime.py — market regime per timeframe: trending up, trending down, sideways,
or in transition.

Three causal readings, combined:

  * SLOPE     EMA(fast) vs EMA(slow), normalised by ATR so it means the same
              thing on gold and on yen.
  * BREADTH   where price sits inside its own recent range (a Donchian position).
              A market pinned mid-range is sideways however the EMAs look.
  * ENERGY    ATR now vs ATR over a longer window. A contracting range that is
              still directional is TRANSITION, not TREND — that distinction is
              the whole point of having a fourth state.

Sideways detection is therefore not a separate switch: it is the regime, and it
is what a bounce strategy wants while a breakout strategy wants its opposite.
"""

import numpy as np

from ..indicators import atr as atr_series, ema

TRENDING_UP = 'trending_up'
TRENDING_DOWN = 'trending_down'
SIDEWAYS = 'sideways'
TRANSITION = 'transition'

DIR_UP, DIR_DOWN, DIR_FLAT = 'up', 'down', 'flat'


def compute(bars, fast=21, slow=50, range_window=40, atr_len=14,
            slope_atr=0.35, band=0.22, squeeze=0.75):
    """
    Returns (regime, direction, features) as arrays aligned to `bars`.

    All three are causal: every value at i comes from bars <= i, so a feature row
    built at bar i is legal to trade on at bar i+1.
    """
    close = np.asarray(bars['close'], dtype=float)
    high = np.asarray(bars['high'], dtype=float)
    low = np.asarray(bars['low'], dtype=float)
    n = len(close)

    ef = ema(close, fast)
    es = ema(close, slow)
    a = atr_series(bars, atr_len)

    # trend pressure: EMA separation in ATR units
    with np.errstate(invalid='ignore', divide='ignore'):
        sep = (ef - es) / np.where(a > 0, a, np.nan)

    # position inside the recent range, 0 = at the low, 1 = at the high
    pos = np.full(n, np.nan)
    rng = np.full(n, np.nan)
    for i in range(range_window - 1, n):
        hi = float(np.max(high[i - range_window + 1:i + 1]))
        lo = float(np.min(low[i - range_window + 1:i + 1]))
        rng[i] = hi - lo
        pos[i] = 0.5 if hi == lo else (close[i] - lo) / (hi - lo)

    # energy: is the range expanding or contracting?
    long_atr = np.full(n, np.nan)
    w = atr_len * 4
    for i in range(w - 1, n):
        seg = a[i - w + 1:i + 1]
        if not np.all(np.isnan(seg)):
            long_atr[i] = float(np.nanmean(seg))
    with np.errstate(invalid='ignore', divide='ignore'):
        energy = a / np.where(long_atr > 0, long_atr, np.nan)

    regime = np.full(n, TRANSITION, dtype=object)
    direction = np.full(n, DIR_FLAT, dtype=object)

    for i in range(n):
        s, p, e = sep[i], pos[i], energy[i]
        if np.isnan(s) or np.isnan(p):
            regime[i] = TRANSITION
            direction[i] = DIR_FLAT
            continue
        direction[i] = DIR_UP if s > 0.08 else DIR_DOWN if s < -0.08 else DIR_FLAT

        strong = abs(s) >= slope_atr
        mid_range = (0.5 - band) <= p <= (0.5 + band)
        contracting = (not np.isnan(e)) and e < squeeze

        if strong and not mid_range and not contracting:
            regime[i] = TRENDING_UP if s > 0 else TRENDING_DOWN
        elif (not strong) and mid_range:
            regime[i] = SIDEWAYS
        else:
            # directional but out of energy, or ranging but stretched: neither
            # a trend to follow nor a range to fade
            regime[i] = TRANSITION

    return regime, direction, {
        'ema_fast': ef, 'ema_slow': es, 'atr': a,
        'ema_sep_atr': sep, 'range_pos': pos, 'range_width': rng, 'energy': energy,
    }
