"""
stats.py — the small statistical pieces the research tools share.

Kept in one place because the alternative is two implementations drifting
apart, and a multiple-comparison correction that differs between two tools is
worse than not having one.
"""

import math
from statistics import NormalDist

import numpy as np

_N = NormalDist()


def two_sided_p(z):
    """
    Two-sided normal tail. Exact via erfc, so no scipy dependency.

    Sample sizes here run from hundreds to hundreds of thousands, where the t
    and normal tails agree far past any decimal that changes a decision.
    """
    if z is None or not np.isfinite(z):
        return np.nan
    return math.erfc(abs(float(z)) / math.sqrt(2))


def benjamini_hochberg(pvals, alpha=0.05):
    """
    Which p-values survive BH at `alpha`, as a bool array.

    A NaN is a test that could not be RUN, not one that was run and failed, so
    it leaves the family rather than inflating it. NaNs never survive.
    """
    p = np.asarray(pvals, dtype=float)
    out = np.zeros(len(p), dtype=bool)
    idx = np.flatnonzero(np.isfinite(p))
    if not len(idx):
        return out
    order = idx[np.argsort(p[idx])]
    m = len(order)
    passed = p[order] <= alpha * (np.arange(1, m + 1) / m)
    if passed.any():
        out[order[:np.flatnonzero(passed)[-1] + 1]] = True
    return out


def expected_max_z(n_trials):
    """
    The z you should EXPECT the best of `n_trials` worthless candidates to show.

    This is the number that matters in a discovery sweep and that a per-test
    p-value cannot give you. Search ten thousand patterns that are all pure
    noise and the best will still score near z = 4; reporting it as a discovery
    is the False Strategy Theorem in action.

    Bailey and Lopez de Prado's approximation to the expected maximum of N
    independent standard normals:

        E[max] ~ (1 - g) * Phi^-1(1 - 1/N)  +  g * Phi^-1(1 - 1/(N e))

    with g the Euler-Mascheroni constant. Independence is optimistic here --
    overlapping patterns are correlated, which lowers the true expected maximum
    -- so this is a CONSERVATIVE bar to clear, which is the right direction for
    a threshold to be wrong in.
    """
    n = int(n_trials)
    if n < 1:
        return np.nan
    if n == 1:
        # E[max of one standard normal] is just E[N(0,1)] = 0. Returning nan
        # here would make every downstream "did it beat the noise expectation"
        # comparison quietly False for a single-hypothesis run.
        return 0.0
    g = 0.5772156649015329
    return ((1 - g) * _N.inv_cdf(1 - 1.0 / n)
            + g * _N.inv_cdf(1 - 1.0 / (n * math.e)))
