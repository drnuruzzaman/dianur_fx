"""
structure.py — swing-sequence classification: HH, HL, LH, LL.

regime.py answers "is this market trending?" from moving averages and range
position. This answers a different and more literal question: what has the
sequence of swings actually been doing? A market can have EMAs pointing up
while printing a lower high, and that disagreement is the useful part.

    HH  higher high   — this swing high above the previous swing high
    LH  lower high    — this swing high below the previous swing high
    HL  higher low    — this swing low above the previous swing low
    LL  lower low     — this swing low below the previous swing low

CAUSALITY. A swing is classified at the bar it was CONFIRMED, never at the bar
it occurred: a fractal high at bar 100 with strength 3 is not knowable until
bar 103. `label_swings` therefore emits (confirmed_i, label) and the per-bar
arrays only ever carry a label from a bar that had already passed.

STRUCTURE, not trend. The bias returned here is a statement about the last two
highs and the last two lows, nothing more:

    HH + HL -> up          both extremes advancing
    LH + LL -> down        both extremes retreating
    HH + LL -> broadening  expanding, no directional claim
    LH + HL -> contracting compressing, no directional claim

The last two are genuinely undecided rather than "sideways" — they say the
market is changing shape, which is what makes them worth distinguishing.
"""

import numpy as np

from .clockguard import require_naive
from .pivots import find_pivots

HH, HL, LH, LL = 'HH', 'HL', 'LH', 'LL'

UP = 'up'
DOWN = 'down'
BROADENING = 'broadening'
CONTRACTING = 'contracting'
UNDECIDED = 'undecided'

# A swing within this many ATR of the one before it is neither higher nor
# lower in any meaningful sense. Without it, a 0.02 ATR difference flips the
# structure label and the panel flickers between HH and LH on noise.
EQUAL_ATR = 0.10


def label_swings(pivots, prices_are_highs, atr, equal_atr=EQUAL_ATR):
    """
    Label each pivot against the PREVIOUS pivot of the same kind.

    Returns a list of dicts: {i, confirmed_i, price, label}. The first pivot of
    a series has no predecessor and is labelled None. `atr` is the full ATR
    array; the value at the pivot's own bar sets the equality band, so the
    threshold scales with volatility the way every other tolerance here does.
    """
    out = []
    prev = None
    for p in pivots:
        label = None
        if prev is not None:
            a = atr[p['i']] if p['i'] < len(atr) else np.nan
            band = (equal_atr * a) if (a == a and a > 0) else 0.0
            delta = p['price'] - prev['price']
            if abs(delta) <= band:
                # Equal-ish: carry the previous label forward rather than
                # inventing a new one. A double top is not a new higher high.
                label = prev.get('label')
            elif prices_are_highs:
                label = HH if delta > 0 else LH
            else:
                label = HL if delta > 0 else LL
        rec = {'i': p['i'], 'confirmed_i': p['confirmed_i'],
               'price': p['price'], 'label': label}
        out.append(rec)
        prev = rec
    return out


def swing_points(bars, strength=3, atr_len=14, equal_atr=EQUAL_ATR,
                 close_confirm=True):
    """
    The labelled swing points themselves — the JS mirror of this feeds the
    chart overlay (js/chart/structure.js swingPoints).

    classify() answers "what is the structure AT each bar" and returns per-bar
    arrays. This returns the events: one record per pivot.

    Pivots whose confirming bar has not printed yet are DROPPED, so this never
    reports a swing the engine could not have acted on.
    """
    from ..indicators import atr as atr_series

    require_naive(bars, 'structure.swing_points bars')
    high = np.asarray(bars['high'], dtype=float)
    low = np.asarray(bars['low'], dtype=float)
    a = atr_series(bars, atr_len)
    piv_hi, piv_lo = find_pivots(high, low, strength,
                                 close=np.asarray(bars['close'], dtype=float),
                                 close_confirm=close_confirm)
    last = len(high) - 1
    out = []
    for pivots, is_high in ((piv_hi, True), (piv_lo, False)):
        for r in label_swings(pivots, is_high, a, equal_atr):
            if r['confirmed_i'] > last:
                continue
            out.append({**r, 'is_high': is_high})
    out.sort(key=lambda r: r['i'])
    return out


def classify(bars, strength=3, atr_len=14, equal_atr=EQUAL_ATR):
    """
    Per-bar market structure, causal.

    Returns a dict of arrays aligned to `bars`:
        high_label / low_label  most recent confirmed swing label, or None
        bias                    up | down | broadening | contracting | undecided
        last_high / last_low    price of the most recent confirmed swing
    """
    from ..indicators import atr as atr_series

    require_naive(bars, 'structure.classify bars')
    high = np.asarray(bars['high'], dtype=float)
    low = np.asarray(bars['low'], dtype=float)
    n = len(high)
    a = atr_series(bars, atr_len)

    piv_hi, piv_lo = find_pivots(high, low, strength)
    hi_lab = label_swings(piv_hi, True, a, equal_atr)
    lo_lab = label_swings(piv_lo, False, a, equal_atr)

    high_label = np.full(n, None, dtype=object)
    low_label = np.full(n, None, dtype=object)
    last_high = np.full(n, np.nan)
    last_low = np.full(n, np.nan)

    # Walk forward, adopting a label only once its confirming bar has passed.
    def _fill(labels, lab_arr, px_arr):
        k = 0
        cur_lab, cur_px = None, np.nan
        for i in range(n):
            while k < len(labels) and labels[k]['confirmed_i'] <= i:
                cur_lab = labels[k]['label']
                cur_px = labels[k]['price']
                k += 1
            lab_arr[i] = cur_lab
            px_arr[i] = cur_px

    _fill(hi_lab, high_label, last_high)
    _fill(lo_lab, low_label, last_low)

    bias = np.full(n, UNDECIDED, dtype=object)
    for i in range(n):
        h, l = high_label[i], low_label[i]
        if h == HH and l == HL:
            bias[i] = UP
        elif h == LH and l == LL:
            bias[i] = DOWN
        elif h == HH and l == LL:
            bias[i] = BROADENING
        elif h == LH and l == HL:
            bias[i] = CONTRACTING

    return {'high_label': high_label, 'low_label': low_label, 'bias': bias,
            'last_high': last_high, 'last_low': last_low}
