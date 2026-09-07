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


def find_pivots(high, low, strength=3, close=None, close_confirm=False):
    """
    Fractal pivots over full arrays. Returns (highs, lows), each a list of
    dicts: {i, price, confirmed_i}.

    THE WICK SETS THE PRICE, ALWAYS. A swing high is the highest price TRADED,
    not the highest close — the wick is where the rejection happened, and that
    is the level price will be measured against later. `i` is where it IS.

    Two separate questions, on purpose, with two separate answers:

        candidate    the wick-fractal shape. `confirmed_i = i + strength` is
                     the earliest bar from which the SHAPE is knowable —
                     "event time" met "we've seen enough bars either side".

        close_confirm=True asks a DIFFERENT question: not just that the shape
                     exists, but that price went on to actually CLOSE past the
                     extreme, establishing the turn rather than merely pausing
                     at it. See _confirm_by_close for that walk. A candidate
                     under this rule ends up CONFIRMED (with `confirmed_i` now
                     the bar the closes actually established it — later than
                     i + strength, sometimes much later), INVALIDATED (a later
                     wick made a new extreme first, superseding it), or PENDING
                     (not enough bars yet to say either way). Invalidated and
                     pending candidates are dropped; only confirmed ones return.

    The first version of this filtered by requiring ALL of the next `strength`
    bars to close past the extreme, which is not the same rule: one weak close
    inside an otherwise-decisive window killed the whole candidate. That both
    discarded real turns AND, measured against the trendline/zone/BOS detectors
    that consume these pivots, made every one of them worse (their signal comes
    from pivot DENSITY, and the all-K filter starved it by a third). This walk
    instead lets confirmation take as long as the market actually takes.
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
    if close_confirm:
        if close is None:
            raise ValueError('close_confirm needs the close series')
        c = np.asarray(close, dtype=float)
        highs = _confirm_by_close(highs, h, c, strength, is_high=True)
        lows = _confirm_by_close(lows, l, c, strength, is_high=False)
    return highs, lows


def _confirm_by_close(pivots, wick, close, confirm_bars, is_high):
    """
    CANDIDATE -> CONFIRMED, INVALIDATED, or PENDING. Walks forward on CLOSES.

    Starting the bar after the candidate, count consecutive bars whose CLOSE
    sits past the candidate's own close — `confirm_bars` of them running is
    what confirms the turn. Two ways out before that happens:

        a later WICK exceeds the candidate's price     -> INVALIDATED (dropped)
        the data runs out first                         -> PENDING (dropped)

    Invalidation on wick rather than close is deliberate: once a later bar has
    actually traded through the level, this candidate is no longer the extreme
    regardless of what anything closed at.

    `confirmed_i` is clamped to at least i + confirm_bars — the run cannot
    complete in fewer bars than it requires, but the max() also guards the
    edge case where `confirm_bars` is set below the fractal `strength` used to
    find the candidates in the first place, so a claimed confirmation can never
    predate the candidate being knowable at all.
    """
    n = len(close)
    out = []
    for p in pivots:
        i, px = p['i'], p['price']
        floor = i + confirm_bars
        run = 0
        confirmed_at = None
        for j in range(i + 1, n):
            w = wick[j]
            if (w > px) if is_high else (w < px):
                break                               # superseded, never confirmed
            turned = (close[j] < close[i]) if is_high else (close[j] > close[i])
            run = run + 1 if turned else 0
            if run >= confirm_bars:
                confirmed_at = j
                break
        if confirmed_at is not None:
            out.append({**p, 'confirmed_i': max(confirmed_at, floor)})
    return out


def pivots_confirmed_by(pivots, upto_i):
    """
    Only the pivots a bar-`upto_i` observer could know about.

    This is the engine-level guard, not a test: a pivot at bar 100 with strength
    3 is invisible until bar 103, and using it earlier is the classic MTF /
    fractal look-ahead leak that makes a trendline backtest look prescient.
    """
    return [p for p in pivots if p['confirmed_i'] <= upto_i]
