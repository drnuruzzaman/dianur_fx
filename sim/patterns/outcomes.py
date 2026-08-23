"""
outcomes.py — what happened after every bar, computed once.

The insight this package is built on: a triple-barrier outcome is a property of
(bar, direction, geometry) ALONE. It does not depend on what proposed the entry.
So the expensive part is computed once per instrument and geometry, over every
bar in the series, and a proposer's events are then a cheap integer index into
that table.

That is what makes it affordable to score thousands of candidate patterns. The
alternative -- walking barriers per event, per pattern, as sim/tl/diagnostics.py
does -- costs O(patterns x events x horizon) and does not survive contact with a
discovery sweep.

VECTORISATION. The walk is turned inside out: instead of looping bars and
walking forward, it loops the horizon once and tests every bar in parallel.
`horizon` numpy passes over n-length arrays replaces n serial walks of up to
`horizon` steps each. On 685k bars at horizon 48 this is about a second.

THE TIE-BREAK, AND WHY DISCOVERY USES PESSIMISTIC. A bar that reaches both
barriers has to be settled. sim/intrabar.py can go and look at sub-bars, and is
validated to do so without error, but it needs one lookup per ambiguous bar and
ambiguity is not rare at these geometries -- roughly a quarter of events at a
0.4 ATR stop. Hundreds of thousands of lookups per geometry is not a sweep.

So discovery uses PESSIMISTIC: every ambiguous bar goes to the stop. It is
conservative, and more importantly it is applied identically to every pattern
and to the null, so it cannot manufacture a difference between them -- it only
shifts the whole surface down. Survivors are then re-scored with INTRABAR,
where the cost is trivial because there are few of them. Never report a
discovery-phase number as an expectancy; it is a comparison, not a P/L.
"""

import numpy as np

from ..indicators import atr as atr_series
from ..intrabar import INTRABAR, PESSIMISTIC, STOP, TARGET, SubBars
from ..intrabar import resolve as ib_resolve

# outcome codes. UNDEFINED is distinct from CHOP on purpose: a bar too close to
# the end of the series to resolve is missing data, not a trade that went
# nowhere, and averaging the two together biases the tail of every sample.
TARGET_FIRST, STOP_FIRST, CHOP, UNDEFINED = 1, -1, 0, -128


def triple_barrier(bars, direction, stop_atr, target_atr, horizon=48,
                   atr_len=14, resolution=PESSIMISTIC, symbol=None, tf=None,
                   atr=None):
    """
    For an entry at the close of EVERY bar, which barrier came first.

    Barriers are anchored at the ENTRY PRICE, not at any level the pattern is
    about. That is deliberate and it is a change from sim/tl/diagnostics.py,
    which anchors a bounce's barriers on the trendline. A real stop order sits
    at a distance from YOUR FILL; anchoring anywhere else prices an order nobody
    can place, and it also makes the fair-value null geometry-dependent, so
    patterns stop being comparable to each other. Price-anchored, the null is
    the same single number for every pattern.

    Returns (outcome, ambiguous) where outcome is an int8 array of
    TARGET_FIRST / STOP_FIRST / CHOP / UNDEFINED per bar, and ambiguous is a
    bool array marking bars whose outcome was decided by the tie-break rather
    than observed.
    """
    close = np.asarray(bars['close'], dtype=float)
    high = np.asarray(bars['high'], dtype=float)
    low = np.asarray(bars['low'], dtype=float)
    a = atr_series(bars, atr_len) if atr is None else atr
    n = len(close)

    out = np.full(n, CHOP, dtype=np.int8)
    ambiguous = np.zeros(n, dtype=bool)
    amb_at = np.full(n, -1, dtype=np.int64)     # which bar was ambiguous

    ok = np.isfinite(a) & (a > 0)
    tgt = close + direction * target_atr * a
    stp = close - direction * stop_atr * a

    done = ~ok                                   # no ATR -> never resolves
    for k in range(1, horizon + 1):
        m = n - k
        if m <= 0:
            break
        live = ~done[:m]
        if not live.any():
            break
        hj, lj = high[k:], low[k:]
        if direction > 0:
            hit_t, hit_s = hj >= tgt[:m], lj <= stp[:m]
        else:
            hit_t, hit_s = lj <= tgt[:m], hj >= stp[:m]
        hit_t, hit_s = hit_t & live, hit_s & live

        both = hit_t & hit_s
        out[:m][hit_t & ~both] = TARGET_FIRST
        out[:m][hit_s & ~both] = STOP_FIRST
        # tie-break; overwritten below when resolution is INTRABAR
        out[:m][both] = STOP_FIRST
        ambiguous[:m] |= both
        amb_at[:m] = np.where(both, k, amb_at[:m])
        done[:m] |= hit_t | hit_s

    # A bar whose horizon runs off the end of the series never had the chance
    # to resolve. Calling that CHOP would quietly score the last `horizon` bars
    # of every run as a loss.
    out[~ok] = UNDEFINED
    tail = max(0, n - horizon)
    out[tail:][out[tail:] == CHOP] = UNDEFINED

    if resolution == INTRABAR and symbol and tf:
        out = _settle_intrabar(bars, out, ambiguous, amb_at, direction,
                               tgt, stp, symbol, tf)
    return out, ambiguous


def _settle_intrabar(bars, out, ambiguous, amb_at, direction, tgt, stp,
                     symbol, tf):
    """
    Revisit only the bars the tie-break decided, and ask sub-bars what really
    happened. Everything else is already an observed fact and is left alone.
    """
    sub = SubBars(symbol, tf)
    idx = np.flatnonzero(ambiguous & (out != UNDEFINED))
    times = bars.index
    for i in idx:
        j = i + amb_at[i]
        verdict, _ = ib_resolve(INTRABAR, sub, times[j], direction,
                                stp[i], tgt[i])
        out[i] = TARGET_FIRST if verdict == TARGET else STOP_FIRST
    return out


def fair_value(stop_atr, target_atr):
    """
    P(target first) for a driftless price, from optional stopping.

    For any continuous martingale the probability of reaching one barrier before
    the other is the OTHER barrier's distance over the total span. With barriers
    anchored at the entry those distances are exactly stop_atr and target_atr,
    so this needs no simulation, no placebo, and no second arm.

    It is also, exactly, the breakeven hit rate at this geometry: a driftless
    price has expectancy zero at EVERY geometry, which is why a deviation from
    this number is the whole signal. Real prices are only approximately
    martingales over a horizon, and discrete bars overshoot barriers, so the
    empirical null in evaluate.py calibrates the residual rather than assuming
    it away.
    """
    return stop_atr / (stop_atr + target_atr)
