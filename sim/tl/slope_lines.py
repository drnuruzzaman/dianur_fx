"""
slope_lines.py — one-pivot trendlines with a VOLATILITY-DERIVED slope.

An implementation of the method published as "Trendlines with Breaks" by
LuxAlgo (TradingView, 2022, open source). It is included here because it is
structurally different from sim/tl/engine.py in a way worth measuring, not
because it is assumed to be better.

THE DIFFERENCE THAT MATTERS

engine.py builds a line from TWO pivots: the anchors define both position and
slope, and no line exists until a second pivot arrives. This method uses ONE
pivot for position and derives the slope from volatility:

    upper = (a pivot high just formed) ? that pivot's price : upper - slope
    lower = (a pivot low just formed)  ? that pivot's price : lower + slope

The upper line decays DOWN toward price and the lower line rises UP toward it,
both at `slope` per bar, until the next pivot resets them. So a line always
exists, converging on price until something breaks it.

That single property is the interesting one. This project has repeatedly hit
"no line exists": XAUUSD M15 currently has zero confirmed lines under the
two-pivot engine, and 11 years of six cells produced 11 channels because
confirmed lines are scarce. A method that always has a line cannot have that
failure -- at the cost that its slope is an assumption about volatility rather
than a measurement of where price actually turned.

SLOPE METHODS, as published:

    atr     ATR(length) / length * mult      -- most constant slope across lines
    stdev   stdev(close, length) / length * mult
    linreg  |SMA(close*n) - SMA(close)*SMA(n)| / var(n) / 2 * mult

`mult` is a steepness multiplier; mult = 0 makes them flat horizontal levels,
which is a useful degenerate case to test against.

CAUSALITY. A pivot is only knowable `length` bars after it happens, and the
published script offsets its drawing by that amount ("backpaint"). Here the
reset is applied at the bar the pivot became VISIBLE (i + length), never at the
bar it occurred, so nothing in this file can see the future. That is the single
place where a faithful port would leak, and the difference is documented rather
than silently corrected.
"""

from dataclasses import dataclass

import numpy as np

from ..indicators import atr as atr_series

ATR, STDEV, LINREG = 'atr', 'stdev', 'linreg'


@dataclass
class SlopeParams:
    length: int = 14          # pivot period AND the volatility window
    mult: float = 1.0         # steepness; 0 = flat levels
    method: str = ATR
    backpaint: bool = False   # False = reset when the pivot became VISIBLE
    # A decaying line keeps decaying until the next pivot resets it, so after a
    # break with no new pivot it can run a long way from price -- measured up to
    # 13.5 ATR on XAUUSD 1h, with 14-24% of bars beyond 6 ATR. Those stretches
    # are arithmetic, not structure. Above 0, the line is blanked wherever it is
    # further than this from that bar's close, which is the same reasoning as
    # Params.max_distance_atr archiving a stale trendline.
    # 0 = keep everything (the published behaviour, and the parity default).
    max_distance_atr: float = 0.0


def _pivots(high, low, length):
    """
    Fractal pivots with the same rule as pivots.py, returned as boolean arrays
    indexed by the bar the pivot OCCURRED on.
    """
    n = len(high)
    ph = np.zeros(n, dtype=bool)
    pl = np.zeros(n, dtype=bool)
    for i in range(length, n - length):
        h, l = high[i], low[i]
        is_h = is_l = True
        for k in range(1, length + 1):
            if not (h > high[i - k] and h >= high[i + k]):
                is_h = False
            if not (l < low[i - k] and l <= low[i + k]):
                is_l = False
            if not is_h and not is_l:
                break
        ph[i] = is_h
        pl[i] = is_l
    return ph, pl


def _slope_series(close, high, low, p: SlopeParams, bars):
    n = len(close)
    if p.method == ATR:
        s = atr_series(bars, p.length) / p.length
    elif p.method == STDEV:
        s = np.full(n, np.nan)
        for i in range(p.length - 1, n):
            s[i] = np.std(close[i - p.length + 1:i + 1]) / p.length
    elif p.method == LINREG:
        s = np.full(n, np.nan)
        idx = np.arange(n, dtype=float)
        for i in range(p.length - 1, n):
            w = slice(i - p.length + 1, i + 1)
            x, y = idx[w], close[w]
            vx = np.var(x)
            if vx <= 0:
                continue
            # CENTRED covariance, not mean(xy) - mean(x)mean(y). The latter is
            # the published form and is numerically unstable here: bar index
            # ~1900 times a gold price ~2400 gives products around 4.5e6 whose
            # difference is small, so the subtraction loses most of its
            # significant digits. Centring first keeps the magnitudes small and
            # made the JS port agree to 1e-9 instead of drifting past it.
            cov = np.mean((x - x.mean()) * (y - y.mean()))
            s[i] = abs(cov) / vx / 2.0
        # already a per-bar slope; no /length
    else:
        raise ValueError('unknown slope method: %s' % p.method)
    return s * p.mult


def compute(bars, params: SlopeParams = None):
    """
    Returns dict of arrays aligned to `bars`:

        upper, lower      the two lines
        slope_up/slope_dn the per-bar decay in force for each
        break_up          close crossed ABOVE the upper line this bar
        break_dn          close crossed BELOW the lower line this bar

    Both lines exist from the first pivot onward, so unlike the two-pivot engine
    there is no "no line available" state.
    """
    p = params or SlopeParams()
    high = np.asarray(bars['high'], dtype=float)
    low = np.asarray(bars['low'], dtype=float)
    close = np.asarray(bars['close'], dtype=float)
    n = len(close)

    ph, pl = _pivots(high, low, p.length)
    slope = _slope_series(close, high, low, p, bars)

    upper = np.full(n, np.nan)
    lower = np.full(n, np.nan)
    s_up = np.full(n, np.nan)
    s_dn = np.full(n, np.nan)
    brk_up = np.zeros(n, dtype=bool)
    brk_dn = np.zeros(n, dtype=bool)

    cur_u = cur_l = np.nan
    cur_su = cur_sl = np.nan
    for i in range(n):
        # A pivot at bar j is only VISIBLE at j + length. Applying it at j is
        # the backpaint the published script offers as an option, and it is
        # look-ahead: the line would move at a bar nobody could have known it at.
        j = i - p.length
        reset_h = ph[j] if (not p.backpaint and 0 <= j < n) else (ph[i] if p.backpaint else False)
        reset_l = pl[j] if (not p.backpaint and 0 <= j < n) else (pl[i] if p.backpaint else False)
        sv = slope[i]

        if reset_h:
            cur_u = high[j] if not p.backpaint else high[i]
            cur_su = sv
        elif np.isfinite(cur_u) and np.isfinite(cur_su):
            cur_u = cur_u - cur_su

        if reset_l:
            cur_l = low[j] if not p.backpaint else low[i]
            cur_sl = sv
        elif np.isfinite(cur_l) and np.isfinite(cur_sl):
            cur_l = cur_l + cur_sl

        upper[i], lower[i] = cur_u, cur_l
        s_up[i], s_dn[i] = cur_su, cur_sl

        if i and np.isfinite(upper[i]) and np.isfinite(upper[i - 1]):
            brk_up[i] = close[i] > upper[i] and close[i - 1] <= upper[i - 1]
        if i and np.isfinite(lower[i]) and np.isfinite(lower[i - 1]):
            brk_dn[i] = close[i] < lower[i] and close[i - 1] >= lower[i - 1]

    if p.max_distance_atr > 0:
        a_arr = atr_series(bars, p.length)
        with np.errstate(invalid='ignore'):
            for arr in (upper, lower):
                far = np.abs(arr - close) > p.max_distance_atr * a_arr
                arr[np.where(far)] = np.nan

    return {'upper': upper, 'lower': lower, 'slope_up': s_up, 'slope_dn': s_dn,
            'break_up': brk_up, 'break_dn': brk_dn}
