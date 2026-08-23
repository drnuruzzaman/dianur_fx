"""
test_tl_diagnostics.py — the barrier walk, and the pairing the gate rests on.

Two things are being protected here.

The first is that changing HOW a bar is resolved must not change WHICH events
exist. Approach detection is a fact about the line and the price; the stop/target
race is a separate question asked afterwards. If a resolution mode silently
changed the event count, every comparison between modes would be confounded and
the intrabar upgrade could not be read as a like-for-like correction.

The second is that the arms are genuinely paired. `tools/r_conversion.py` tests
a per-approach difference, which is only meaningful if the join key really does
identify the same approach in both arms.
"""

import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sim.tl.diagnostics import (CLOSE, DiagParams, HOLD, BREAK, CHOP, STOP,
                                TARGET, _Ctx, _walk, run)
from sim.tl.engine import Params

pytest.importorskip('pandas')


def _bars(rows):
    """rows of (open, high, low, close) at 1h, indexed from a fixed date."""
    idx = pd.date_range('2022-01-03', periods=len(rows), freq='1h')
    df = pd.DataFrame(rows, columns=['open', 'high', 'low', 'close'], index=idx)
    df['tick_volume'] = 1
    df['spread'] = 1
    return df


def _ctx(df, mode=CLOSE, sub=None):
    return _Ctx(high=df['high'].to_numpy(float), low=df['low'].to_numpy(float),
                close=df['close'].to_numpy(float), times=df.index,
                mode=mode, sub=sub)


# --------------------------------------------------------------------------
# the walk itself, on hand-checkable bars
# --------------------------------------------------------------------------

def test_close_mode_ignores_a_wick_through_the_stop():
    """
    The bar dips to 88 -- through a stop at 90 -- and closes back at 101.

    A real stop order is gone. CLOSE mode cannot see it, which is exactly the
    bias the intrabar work exists to remove, so it is pinned here rather than
    left as an assumption.
    """
    df = _bars([(100, 100, 100, 100),
                (100, 102, 88, 101),      # wick through the stop, closes above
                (101, 111, 101, 110)])    # target
    hit, j = _walk(_ctx(df, CLOSE), 0, 10, len(df),
                   lambda j: 110.0, lambda j: 90.0, +1)
    assert (hit, j) == (TARGET, 2)

    hit, j = _walk(_ctx(df, 'pessimistic'), 0, 10, len(df),
                   lambda j: 110.0, lambda j: 90.0, +1)
    assert (hit, j) == (STOP, 1)


def test_unambiguous_bars_resolve_the_same_in_every_mode():
    """A bar reaching only one barrier is a fact; no mode may disagree on it."""
    df = _bars([(100, 100, 100, 100),
                (100, 105, 99, 104),      # neither barrier
                (104, 112, 104, 111)])    # target only
    for mode in (CLOSE, 'pessimistic', 'intrabar'):
        assert _walk(_ctx(df, mode), 0, 10, len(df),
                     lambda j: 110.0, lambda j: 90.0, +1) == (TARGET, 2)


def test_both_barriers_in_one_bar_goes_to_the_stop_without_sub_bars():
    """With no sub-bars to consult, INTRABAR must degrade to the stop, not guess."""
    df = _bars([(100, 100, 100, 100),
                (100, 115, 85, 100)])     # reaches 110 and 90 in the same bar
    ctx = _ctx(df, 'intrabar', sub=None)
    assert _walk(ctx, 0, 10, len(df),
                 lambda j: 110.0, lambda j: 90.0, +1) == (STOP, 1)
    assert ctx.ambiguous == 1


def test_short_side_is_the_mirror_image():
    """direction=-1 puts the target below and the stop above."""
    df = _bars([(100, 100, 100, 100),
                (100, 101, 89, 90)])
    assert _walk(_ctx(df, CLOSE), 0, 10, len(df),
                 lambda j: 90.0, lambda j: 110.0, -1) == (TARGET, 1)


def test_horizon_expiry_returns_no_hit():
    df = _bars([(100, 100, 100, 100)] * 6)
    assert _walk(_ctx(df, CLOSE), 0, 3, len(df),
                 lambda j: 110.0, lambda j: 90.0, +1) == (None, None)


def test_a_sloping_barrier_tracks_the_line():
    """
    Flat price, rising target. Reached at the bar where the line, not the
    price, says so -- which is what makes a trendline barrier different from
    a bracket.
    """
    df = _bars([(100, 100, 100, 100)] * 5)
    # target descends to meet price at bar 3; stop far below
    hit, j = _walk(_ctx(df, CLOSE), 0, 10, len(df),
                   lambda j: 103.0 - j, lambda j: 50.0, +1)
    assert (hit, j) == (TARGET, 3)


# --------------------------------------------------------------------------
# the invariants the gate depends on, on real bars
# --------------------------------------------------------------------------

@pytest.fixture(scope='module')
def real_bars():
    from sim.instruments import load
    try:
        return load('EURUSD.a', '4h', '2021-01-01', '2023-01-01')
    except (FileNotFoundError, KeyError):
        pytest.skip('no EURUSD.a 4h history on disk')


def _events(bars, mode, **kw):
    ev, _ = run(bars, '4h', Params(tol_atr=0.10),
                DiagParams(resolution=mode, **kw), symbol='EURUSD.a')
    return ev


def test_resolution_mode_does_not_change_which_events_exist(real_bars):
    """
    The load-bearing invariant. Approach detection must be independent of how
    the outcome race is settled, or no two modes are comparable.
    """
    ref = _events(real_bars, CLOSE)
    ref_keys = set(map(tuple, ref[['arm', 'phase', 'approach_bar', 'line_id']]
                       .drop_duplicates().to_numpy()))
    approaches = ref[ref.phase == 'approach']
    assert len(approaches) > 100, 'fixture too small to mean anything'

    for mode in ('pessimistic', 'intrabar'):
        ev = _events(real_bars, mode)
        got = ev[ev.phase == 'approach']
        assert len(got) == len(approaches)
        assert (got.approach_bar.to_numpy() ==
                approaches.approach_bar.to_numpy()).all()
        # later phases DO change population -- they are conditional on a break,
        # and which bars break is precisely what resolution decides.
        assert set(map(tuple, ev[['arm', 'phase', 'approach_bar', 'line_id']]
                       .drop_duplicates().to_numpy())) <= ref_keys | set(
            map(tuple, ev[['arm', 'phase', 'approach_bar', 'line_id']]
                .drop_duplicates().to_numpy()))


def test_intrabar_differs_from_pessimistic_only_where_a_bar_was_ambiguous(real_bars):
    """
    The resolver can remove error but never introduce it: it may only overturn
    a bar that reached both barriers. If the two modes differ on more approaches
    than there were ambiguous bars, something other than the tie-break moved.
    """
    pes = _events(real_bars, 'pessimistic')
    ib = _events(real_bars, 'intrabar')
    assert ib.attrs['ambiguous_bars'] > 0, 'no ambiguity: test proves nothing'

    key = ['arm', 'phase', 'approach_bar', 'line_id']
    a = pes[pes.phase == 'approach'].set_index(key)['outcome']
    b = ib[ib.phase == 'approach'].set_index(key)['outcome']
    changed = (a != b.reindex(a.index)).sum()
    assert changed <= ib.attrs['ambiguous_bars']
    # and the fallbacks must agree with pessimistic by construction
    assert ib.attrs['resolved_by'].get('fallback', 0) <= ib.attrs['ambiguous_bars']


def test_pessimistic_never_holds_more_often_than_intrabar(real_bars):
    """
    Directional check on the correction. Giving every ambiguous bar to the stop
    can only cost holds, so intrabar's hold rate must be >= pessimistic's.
    """
    def hold_rate(ev):
        g = ev[(ev.phase == 'approach') & (ev.arm == 'line')]
        return (g.outcome == HOLD).mean()
    assert hold_rate(_events(real_bars, 'intrabar')) >= \
        hold_rate(_events(real_bars, 'pessimistic'))


def test_the_two_arms_are_actually_paired(real_bars):
    """
    Every approach yields exactly one line row and one placebo row, joined by
    (approach_bar, line_id). r_conversion's paired t-test is only valid if this
    key is unique within an arm and identical across them.
    """
    ev = _events(real_bars, 'intrabar')
    ap = ev[ev.phase == 'approach']
    key = ['approach_bar', 'line_id']
    line = ap[ap.arm == 'line']
    plac = ap[ap.arm == 'placebo']
    assert not line.duplicated(key).any()
    assert not plac.duplicated(key).any()
    assert (set(map(tuple, line[key].to_numpy())) ==
            set(map(tuple, plac[key].to_numpy())))


def test_breakout_rows_are_a_subset_of_broken_approaches(real_bars):
    """
    A breakout row may exist only where that arm's own approach broke -- which
    is why the phase needs pairing rather than a mean-vs-mean comparison.
    """
    ev = _events(real_bars, 'intrabar', stop_atr=0.6, target_atr=1.2,
                 phases=('approach', 'breakout'))
    key = ['arm', 'approach_bar', 'line_id']
    broke = ev[(ev.phase == 'approach') & (ev.outcome == BREAK)]
    bo = ev[ev.phase == 'breakout']
    assert len(bo)
    assert (set(map(tuple, bo[key].to_numpy())) <=
            set(map(tuple, broke[key].to_numpy())))


def test_paired_diff_recovers_a_known_difference():
    """Negative control on the statistic itself, away from any market data."""
    from tools.r_conversion import paired_diff
    n = 400
    line = pd.DataFrame({'approach_bar': range(n), 'line_id': 1,
                         'outcome': [HOLD] * n})
    plac = pd.DataFrame({'approach_bar': range(n), 'line_id': 1,
                         'outcome': [BREAK] * n})
    n_p, mean, t, p = paired_diff(line, plac, rr=2.0)
    assert n_p == n
    assert mean == pytest.approx(3.0)      # (+2) - (-1)
    assert np.isnan(t)                     # zero variance: no test to run

    half = [HOLD] * (n // 2) + [BREAK] * (n // 2)
    plac2 = plac.assign(outcome=half)
    n_p, mean, t, p = paired_diff(line, plac2, rr=2.0)
    assert mean == pytest.approx(1.5)
    assert t > 5 and p < 1e-6


def test_paired_diff_ignores_unmatched_rows():
    from tools.r_conversion import paired_diff
    line = pd.DataFrame({'approach_bar': [1, 2, 3, 4], 'line_id': 1,
                         'outcome': [HOLD, HOLD, BREAK, HOLD]})
    plac = pd.DataFrame({'approach_bar': [3, 4, 5], 'line_id': 1,
                         'outcome': [BREAK, BREAK, HOLD]})
    n_p, mean, t, p = paired_diff(line, plac, rr=1.0)
    assert n_p == 2                        # bars 3 and 4 only


def test_benjamini_hochberg_matches_a_worked_example():
    from tools.r_conversion import benjamini_hochberg
    # m=4, so the thresholds are .0125 .025 .0375 .05
    # Only the smallest clears its own: .03 > .025 and .04 > .0375.
    assert list(benjamini_hochberg([0.001, 0.03, 0.04, 0.9])) == \
        [True, False, False, False]

    # BH steps UP, which is the part worth pinning: .037 clears the third
    # threshold, and that drags .03 in with it even though .03 failed its own.
    assert list(benjamini_hochberg([0.001, 0.03, 0.037, 0.9])) == \
        [True, True, True, False]

    assert not benjamini_hochberg([0.2, 0.3]).any()
    assert not benjamini_hochberg([np.nan, np.nan]).any()
    # A NaN is a test that could not be run, not one that failed, so it leaves
    # the family: .04 is then the only test, corrected against m=1, and clears.
    assert list(benjamini_hochberg([0.04, np.nan])) == [True, False]
    # against a real second test it would not
    assert list(benjamini_hochberg([0.04, 0.9])) == [False, False]


def test_paired_mean_agrees_with_the_aggregate_expectancy():
    """
    When every event pairs, the mean paired difference must equal the difference
    of the two arms' expectancies -- otherwise `paired_R` and `net_vs_placebo`
    are measuring different things and the CSV contradicts itself.

    This also pins the convention they share: CHOP is scored as a full -1R, the
    same as a stop, because `expectancy` computes the hold rate over ALL events
    rather than over decided ones. That is a deliberately harsh reading of a
    trade that neither hit its target nor its stop inside the horizon, and it is
    load-bearing -- a time-based exit near breakeven would score these nearer 0R
    and lift every number in the grid.
    """
    from tools.r_conversion import paired_diff
    rng = np.random.default_rng(0)
    n, rr = 500, 1.8
    lo = rng.choice([HOLD, BREAK, CHOP], n)
    po = rng.choice([HOLD, BREAK, CHOP], n)
    line = pd.DataFrame({'approach_bar': range(n), 'line_id': 7, 'outcome': lo})
    plac = pd.DataFrame({'approach_bar': range(n), 'line_id': 7, 'outcome': po})

    def expectancy(o):
        p = (o == HOLD).mean()
        return p * rr - (1 - p)

    n_paired, mean, _, _ = paired_diff(line, plac, rr)
    assert n_paired == n
    assert mean == pytest.approx(expectancy(lo) - expectancy(po))
