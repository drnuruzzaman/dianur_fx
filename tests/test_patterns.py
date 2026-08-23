"""
test_patterns.py — the discovery harness.

The harness exists to kill things, so most of what matters here is that it kills
things it should: a random proposer must not survive, and a proposer given a
planted edge must. Those two together are what make a null result from this code
mean anything.
"""

import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sim.patterns.evaluate import evaluate, fold_ids, strata_ids
from sim.patterns.outcomes import (CHOP, STOP_FIRST, TARGET_FIRST, UNDEFINED,
                                   fair_value, triple_barrier)
from sim.patterns.proposer import (LookAheadError, empty_proposals,
                                   validate_proposals)
from sim.stats import benjamini_hochberg, expected_max_z, two_sided_p


def _bars(rows, freq='1h'):
    idx = pd.date_range('2022-01-03', periods=len(rows), freq=freq)
    df = pd.DataFrame(rows, columns=['open', 'high', 'low', 'close'], index=idx)
    df['tick_volume'] = 1
    df['spread'] = 1
    return df


def _walk_bars(n, seed=0, start=100.0, vol=0.5):
    """A driftless random walk, as OHLC bars with real intrabar range."""
    rng = np.random.default_rng(seed)
    close = start + np.cumsum(rng.normal(0, vol, n))
    hi = close + np.abs(rng.normal(0, vol / 2, n))
    lo = close - np.abs(rng.normal(0, vol / 2, n))
    op = np.r_[start, close[:-1]]
    return _bars(np.c_[op, np.maximum.reduce([hi, op, close]),
                       np.minimum.reduce([lo, op, close]), close])


# --------------------------------------------------------------------------
# outcomes
# --------------------------------------------------------------------------

def test_target_and_stop_are_detected_on_the_bar_that_reaches_them():
    df = _bars([(100, 100, 100, 100),
                (100, 104, 99, 103),
                (103, 100.5, 99, 100)])
    # ATR is undefined this early, so feed one explicitly
    atr = np.full(len(df), 2.0)
    out, amb = triple_barrier(df, 1, stop_atr=1.0, target_atr=1.0, horizon=5,
                              atr=atr)
    assert out[0] == TARGET_FIRST          # bar 1 reaches 102 before 98
    assert not amb[0]


def test_ambiguous_bar_goes_to_the_stop_and_is_flagged():
    df = _bars([(100, 100, 100, 100), (100, 110, 90, 100)])
    atr = np.full(len(df), 2.0)
    out, amb = triple_barrier(df, 1, 1.0, 1.0, horizon=5, atr=atr)
    assert out[0] == STOP_FIRST
    assert amb[0]


def test_bars_that_cannot_resolve_are_undefined_not_chop():
    """
    The last `horizon` bars never got their chance. Scoring them as CHOP -- and
    then as a loss -- silently biases the tail of every sample downward.
    """
    df = _walk_bars(200)
    out, _ = triple_barrier(df, 1, 1.0, 1.0, horizon=48)
    # A tail bar may still RESOLVE early -- that is an observed fact and is
    # kept. What must never happen is a tail bar recorded as CHOP, because it
    # never had the full horizon in which to chop.
    assert (out[-48:] != CHOP).all()
    assert out[-1] == UNDEFINED
    assert (out[60:-48] != UNDEFINED).all()


def test_fair_value_matches_the_martingale_identity():
    assert fair_value(1.0, 1.0) == pytest.approx(0.5)
    assert fair_value(1.0, 3.0) == pytest.approx(0.25)
    assert fair_value(2.0, 1.0) == pytest.approx(2 / 3)


def test_a_random_walk_lands_near_but_not_on_fair_value():
    """
    The analytic null is close on a driftless walk, and NOT exact. Discrete bars
    overshoot barriers, ATR-scaled barriers move with volatility, and the
    horizon truncates the slowest paths -- each worth a percentage point or two,
    in a direction that depends on the geometry.

    That residual is precisely why `evaluate` scores a pattern against the
    empirical `base` rate rather than against `fair`: every one of those
    distortions applies to the pattern's own bars and cancels in the difference,
    where subtracting `fair` would leave the whole bias in the answer. `fair` is
    reported only so the size of the gap stays visible.
    """
    df = _walk_bars(60000, seed=3)
    gaps = []
    for stop_atr, target_atr in ((1.0, 1.0), (1.0, 2.0), (2.0, 1.0)):
        out, _ = triple_barrier(df, 1, stop_atr, target_atr, horizon=200)
        dec = (out == TARGET_FIRST) | (out == STOP_FIRST)
        got = (out == TARGET_FIRST).sum() / dec.sum()
        assert got == pytest.approx(fair_value(stop_atr, target_atr), abs=0.06)
        gaps.append(abs(got - fair_value(stop_atr, target_atr)))
    assert max(gaps) > 0.005, (
        'no measurable gap at all would mean the walk generator is not '
        'producing realistic intrabar ranges, and this test proves nothing')


def test_direction_mirrors():
    df = _walk_bars(20000, seed=5)
    up, _ = triple_barrier(df, 1, 1.0, 1.0, horizon=100)
    dn, _ = triple_barrier(df, -1, 1.0, 1.0, horizon=100)
    u = (up == TARGET_FIRST).sum() / ((up == TARGET_FIRST) | (up == STOP_FIRST)).sum()
    d = (dn == TARGET_FIRST).sum() / ((dn == TARGET_FIRST) | (dn == STOP_FIRST)).sum()
    assert u + d == pytest.approx(1.0, abs=0.05)


# --------------------------------------------------------------------------
# the proposer contract
# --------------------------------------------------------------------------

def test_validate_rejects_known_at_that_does_not_match_its_bar():
    df = _walk_bars(100)
    p = pd.DataFrame({'pattern_id': ['x'], 'bar': [10],
                      'occurred_at': [df.index[10]],
                      'known_at': [df.index[11]],       # one bar early
                      'direction': [1]})
    with pytest.raises(LookAheadError):
        validate_proposals(p, df)


def test_validate_rejects_structure_claimed_before_it_happened():
    df = _walk_bars(100)
    p = pd.DataFrame({'pattern_id': ['x'], 'bar': [10],
                      'occurred_at': [df.index[20]],    # after known_at
                      'known_at': [df.index[10]], 'direction': [1]})
    with pytest.raises(LookAheadError):
        validate_proposals(p, df)


def test_validate_rejects_a_bad_direction():
    df = _walk_bars(100)
    p = pd.DataFrame({'pattern_id': ['x'], 'bar': [10],
                      'occurred_at': [df.index[10]], 'known_at': [df.index[10]],
                      'direction': [0]})
    with pytest.raises(LookAheadError):
        validate_proposals(p, df)


def test_validate_accepts_a_well_formed_table():
    df = _walk_bars(100)
    p = pd.DataFrame({'pattern_id': ['x'] * 3, 'bar': [10, 20, 30],
                      'occurred_at': df.index[[8, 18, 28]],
                      'known_at': df.index[[10, 20, 30]], 'direction': [1, -1, 1]})
    assert len(validate_proposals(p, df)) == 3
    assert len(validate_proposals(empty_proposals(), df)) == 0


# --------------------------------------------------------------------------
# folds, strata
# --------------------------------------------------------------------------

def test_folds_embargo_the_end_of_each_block():
    ids = fold_ids(1000, 4, horizon=50)
    assert (ids[200:250] == -1).all()          # tail of fold 0
    assert (ids[0:200] == 0).all()
    assert set(np.unique(ids)) == {-1, 0, 1, 2, 3}


def test_strata_partition_without_gaps():
    ids = strata_ids(1000, 20)
    assert (ids >= 0).all()
    assert len(np.unique(ids)) == 20


# --------------------------------------------------------------------------
# the two that matter
# --------------------------------------------------------------------------

def test_a_random_proposer_does_not_survive():
    """
    The harness's reason to exist. Bars chosen at random carry no information,
    and if this ever starts producing survivors the null is broken and every
    result from it is worthless.
    """
    df = _walk_bars(40000, seed=11)
    rng = np.random.default_rng(2)
    bars_i = rng.choice(np.arange(100, len(df) - 300), size=6000, replace=False)
    p = pd.DataFrame({
        'pattern_id': rng.choice(['r%d' % k for k in range(10)], len(bars_i)),
        'bar': bars_i, 'occurred_at': df.index[bars_i],
        'known_at': df.index[bars_i],
        'direction': rng.choice([1, -1], len(bars_i))})
    res = evaluate(df, p, 'SYN', '1h', [(1.0, 1.0), (1.0, 2.0)],
                   horizon=200, min_events=100, n_strata=10)
    assert len(res)
    assert not res['beats_expected_max'].any(), \
        res[['pattern_id', 'z', 'dev_pp']].head().to_string()


def test_a_planted_edge_is_found():
    """
    The other half: a harness that survives the negative control by being too
    conservative to detect anything is equally useless. Bars are marked where
    the next 200 bars actually did drift up, so the proposal carries a genuine
    forward edge, and it must be recovered.
    """
    df = _walk_bars(40000, seed=13)
    close = df['close'].to_numpy()
    fwd = close[200:] - close[:-200]
    cand = np.flatnonzero(fwd > np.quantile(fwd, 0.80)) 
    cand = cand[(cand > 100)]
    p = pd.DataFrame({'pattern_id': 'planted', 'bar': cand,
                      'occurred_at': df.index[cand], 'known_at': df.index[cand],
                      'direction': 1})
    res = evaluate(df, p, 'SYN', '1h', [(1.0, 1.0), (1.0, 2.0), (2.0, 1.0)],
                   horizon=200, min_events=100, n_strata=10)
    assert len(res) == 3
    assert res.iloc[0]['dev_pp'] > 5
    assert res.iloc[0]['beats_expected_max']
    assert res.iloc[0]['survives_bh']


def test_the_null_is_direction_and_era_matched():
    """
    A proposer that fires only in an up-trending stretch and only goes long has
    found the trend, not a pattern. The stratified null must price that in.
    """
    n = 20000
    rng = np.random.default_rng(7)
    step = rng.normal(0, 0.5, n)
    step[:n // 2] += 0.12                       # first half drifts up hard
    close = 100 + np.cumsum(step)
    hi = close + np.abs(rng.normal(0, 0.25, n))
    lo = close - np.abs(rng.normal(0, 0.25, n))
    op = np.r_[100.0, close[:-1]]
    df = _bars(np.c_[op, np.maximum.reduce([hi, op, close]),
                     np.minimum.reduce([lo, op, close]), close])
    bars_i = rng.choice(np.arange(100, n // 2 - 300), size=3000, replace=False)
    p = pd.DataFrame({'pattern_id': 'trend_rider', 'bar': bars_i,
                      'occurred_at': df.index[bars_i],
                      'known_at': df.index[bars_i], 'direction': 1})

    def run(n_strata):
        return evaluate(df, p, 'SYN', '1h', [(1.0, 1.0)], horizon=200,
                        min_events=100, n_strata=n_strata).iloc[0]

    # With one stratum the null is the whole-series rate and the trend reads as
    # a large, wildly significant edge -- which is the failure mode this whole
    # mechanism exists to prevent.
    fooled = run(1)
    assert fooled['dev_pp'] > 5
    assert fooled['z'] > 5

    # Stratified, the same events are measured against what their OWN era did,
    # and the edge disappears entirely.
    for n_strata in (20, 60):
        clean = run(n_strata)
        assert abs(clean['dev_pp']) < 1.5, clean.to_dict()
        assert abs(clean['z']) < 2, clean.to_dict()
        assert not clean['survives_bh']
        assert clean['base_flat_pct'] < clean['base_pct']


def test_hypothesis_count_includes_patterns_dropped_for_sample_size():
    df = _walk_bars(20000, seed=17)
    rng = np.random.default_rng(4)
    bars_i = rng.choice(np.arange(100, len(df) - 300), 400, replace=False)
    p = pd.DataFrame({'pattern_id': rng.choice(['a', 'b', 'c'], len(bars_i)),
                      'bar': bars_i, 'occurred_at': df.index[bars_i],
                      'known_at': df.index[bars_i], 'direction': 1})
    res = evaluate(df, p, 'SYN', '1h', [(1.0, 1.0)], horizon=200,
                   min_events=10_000, n_strata=5)      # floor nothing can clear
    assert len(res) == 0
    assert res.attrs['n_hypotheses'] == 3
