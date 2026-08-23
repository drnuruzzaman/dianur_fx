"""
nulls.py — what a pattern has to beat.

Three mechanisms, in increasing order of how much they hurt.

STRATIFICATION answers "compared to what, in what conditions". A pattern does
not fire uniformly: it clusters in particular eras, particular volatility, and
particular recent-move states, and it inherits whatever those conditions did.
Comparing against a whole-series rate scores the conditions as skill. The first
pattern this harness ever ran hit z = 5.6 that way, and the effect was mostly
"these bars drifted up".

TIME-SHIFT answers "compared to the same pattern pointed somewhere else". Events
are not independent draws. Two approaches nine bars apart, with a 48-bar
horizon, share four fifths of their outcome window, so a binomial standard error
that assumes independence understates the true spread and inflates every z built
on it. Rather than estimate an effective sample size, the alignment between the
pattern and the series is broken directly: shift every event by the same random
offset and re-measure. Event count, clustering structure, direction mix and the
series' own autocorrelation all survive the shift; only the claim that THESE
bars are special does not.

DEFLATION (in stats.py) answers "compared to how hard you looked".

The three are complements, not alternatives. Stratification removes a confound
the shift cannot see, because a shifted copy of a pattern that fires only in
2020 still fires only in 2020. The shift removes a dependence stratification
cannot see, because cells do not know that their members overlap.
"""

import numpy as np

from ..indicators import atr as atr_series
from .outcomes import STOP_FIRST, TARGET_FIRST


def covariate_strata(bars, time_blocks=10, vol_buckets=3, mom_buckets=3,
                     mom_lookback=20, atr_len=14, rank_window=2000):
    """
    A cell id per bar, from the cross of time, volatility and recent move.

    All three covariates are known at the bar's own close: ATR looks back, and
    the momentum term is close[i] - close[i - k] over ATR. Nothing here reads
    forward.

    The bucket edges are TRAILING: a bar's volatility or momentum bucket is its
    rank within the preceding `rank_window` bars, so nothing here reads forward
    either. Global quantile edges were tried first and rejected -- rebuilding
    the strata on truncated history moved 27% of bars into different buckets,
    which is a look-ahead however mild its content, and this project has already
    shipped one leak that looked obviously fine.

    Choosing WHAT to stratify on is a modelling decision, not a technicality:

      time   removes era and drift. Almost always wanted.
      vol    removes "this pattern only appears in quiet markets, and quiet
             markets resolve differently". Almost always wanted.
      mom    removes recent directional state. Wanted only when you are asking
             whether the pattern adds something BEYOND what the last few bars
             already told you -- and it is over-control if the pattern's whole
             mechanism IS the recent move, as it is for a trendline approach,
             which fires precisely because price just travelled to a level.

    Set a bucket count to 1 to drop that covariate.
    """
    n = len(bars)
    close = np.asarray(bars['close'], dtype=float)
    parts, sizes, names = [], [], []

    if time_blocks > 1:
        edges = np.linspace(0, n, time_blocks + 1).astype(int)
        t = np.zeros(n, dtype=np.int64)
        for b in range(time_blocks):
            t[edges[b]:edges[b + 1]] = b
        parts.append(t)
        sizes.append(time_blocks)
        names.append('time%d' % time_blocks)

    if vol_buckets > 1:
        a = atr_series(bars, atr_len)
        with np.errstate(invalid='ignore', divide='ignore'):
            v = a / close
        parts.append(_quantile_bucket(v, vol_buckets, rank_window))
        sizes.append(vol_buckets)
        names.append('vol%d' % vol_buckets)

    if mom_buckets > 1:
        a = atr_series(bars, atr_len)
        m = np.full(n, np.nan)
        with np.errstate(invalid='ignore', divide='ignore'):
            m[mom_lookback:] = (close[mom_lookback:] - close[:-mom_lookback]) \
                / a[mom_lookback:]
        parts.append(_quantile_bucket(m, mom_buckets, rank_window))
        sizes.append(mom_buckets)
        names.append('mom%d@%d' % (mom_buckets, mom_lookback))

    if not parts:
        return np.zeros(n, dtype=np.int64), 1, 'none'

    cell = np.zeros(n, dtype=np.int64)
    stride = 1
    for p, s in zip(parts, sizes):
        cell += p * stride
        stride *= s
    return cell, stride, ' x '.join(names)


def _quantile_bucket(x, k, window):
    """
    Buckets from a bar's rank within the TRAILING window, so it is causal.

    A bar is placed by where it sits among the last `window` bars, not among all
    of them, which means the bucket a bar gets is the bucket it would have got
    in live use. It also makes the buckets adaptive: "high volatility" is high
    for this era rather than high for the whole history, which is closer to what
    a regime control is meant to mean.

    Non-finite values -- the ATR warm-up at the head of a series -- go to bucket
    0 rather than their own, so that the warm-up does not become a phantom
    regime a pattern could be measured against.
    """
    import pandas as pd
    out = np.zeros(len(x), dtype=np.int64)
    good = np.isfinite(x)
    if good.sum() < k * 10:
        return out
    s = pd.Series(np.where(good, x, np.nan))
    pct = s.rolling(window, min_periods=max(50, k * 10)).rank(pct=True)
    v = pct.to_numpy()
    ok = np.isfinite(v)
    out[ok] = np.clip((v[ok] * k).astype(np.int64), 0, k - 1)
    return out


def base_by_cell(outcome, cell, n_cells, prior=150.0):
    """
    Hold rate per cell, shrunk toward the series rate.

    A three-way cross makes cells small, and a cell holding forty decided bars
    gives a rate with a seven-point standard error -- noise that lands in the
    null and then in every deviation measured against it. Shrinking toward the
    series rate with a pseudo-count trades a little of the confound-removal for
    a lot of stability, and degrades gracefully: a well-populated cell keeps its
    own rate almost exactly, an empty one simply gets the series rate.
    """
    decided = (outcome == TARGET_FIRST) | (outcome == STOP_FIRST)
    hits = outcome == TARGET_FIRST
    overall = hits.sum() / decided.sum() if decided.sum() else np.nan

    n_c = np.bincount(cell[decided], minlength=n_cells).astype(float)
    h_c = np.bincount(cell[decided & hits], minlength=n_cells).astype(float)
    return (h_c + prior * overall) / (n_c + prior), n_c, overall


def time_shift_null(tables, base, cell, bar, dirs, n_bars, n_shifts=400,
                    min_shift=250, seed=0):
    """
    The observed deviation against deviations from the same pattern, moved.

    Every event is displaced by ONE shared offset, wrapped around the series, so
    the events keep their spacing and their clustering -- which is the whole
    point, since that clustering is what makes them non-independent. Shifting
    each event by its own offset would quietly restore independence and hand
    back the inflated significance this is meant to remove.

    `min_shift` keeps the shift clear of small displacements, where a 48-bar
    horizon would leave the shifted windows overlapping the real ones and the
    "null" would still contain the effect.

    Returns (z_shift, p_shift, shifted_deviations). z_shift is the observed
    deviation in units of the shifted spread, and it is the number to trust over
    the binomial z whenever the two disagree.
    """
    rng = np.random.default_rng(seed)

    def deviation(b):
        out = np.array([tables[d][i] for i, d in zip(b, dirs)], dtype=np.int8)
        dec = (out == TARGET_FIRST) | (out == STOP_FIRST)
        if dec.sum() < 30:
            return np.nan
        p0 = np.array([base[d][cell[i]] for i, d in zip(b[dec], dirs[dec])])
        return float((out[dec] == TARGET_FIRST).mean() - p0.mean())

    observed = deviation(bar)
    if not np.isfinite(observed):
        return np.nan, np.nan, np.array([])

    hi = n_bars - min_shift
    if hi <= min_shift:
        return np.nan, np.nan, np.array([])
    shifts = rng.integers(min_shift, hi, size=n_shifts)
    devs = np.array([deviation((bar + s) % n_bars) for s in shifts])
    devs = devs[np.isfinite(devs)]
    if len(devs) < 50:
        return np.nan, np.nan, devs

    sd = devs.std(ddof=1)
    z = (observed - devs.mean()) / sd if sd > 0 else np.nan
    # +1 in numerator and denominator: the observed value is itself one draw
    # from the null being estimated, so a permutation p can never be zero.
    p = (1 + int((np.abs(devs - devs.mean()) >= abs(observed - devs.mean())).sum())) \
        / (1 + len(devs))
    return z, p, devs
