"""
test_events.py — the fact layer must be a fact.

Four properties, and the third is the one worth having:

  1. SCHEMA      every row carries every column, always, in order. Downstream
                 code joins on these names; a column that appears only when a
                 particular event type fired is a silent KeyError waiting for
                 the run that matters.

  2. TWO CLOCKS  known_at is exactly one bar interval after occurred_at, on
                 every row. If those two ever collapse into one the whole
                 causality argument collapses with them.

  3. CAUSALITY   rebuild the store on truncated history and demand the events
                 be IDENTICAL to the ones the full history produced. This is
                 the same trick that found the frozen-scalar leak in the
                 feature pipeline, applied one layer down: an event that knows
                 how the market resolved cannot survive having the resolution
                 removed.

  4. LINEAGE     a RETEST hangs off a BREAK of the same line, and comes after
                 it. A REJECTION hangs off a TOUCH or a RETEST. An event store
                 whose parents dangle cannot answer the funnel question.
"""

import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sim.tl.engine import Params
from sim.tl.events import (BREAK, CONFIRMED, EventParams, INVALIDATED,
                           REJECTION, RETEST, SCHEMA, TOUCH, build, cells,
                           funnel)
from sim.tl.mtf import TF_MS

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fixtures')


def bars(name):
    return pd.read_csv(os.path.join(FIX, name), parse_dates=['t']).set_index('t')


CASES = [('bars_XAUUSDa_1h.csv', '1h', 'XAUUSD.a'),
         ('bars_USDJPYa_15m.csv', '15m', 'USDJPY.a')]


@pytest.fixture(scope='module', params=CASES, ids=lambda c: '%s-%s' % (c[2], c[1]))
def store(request):
    name, tf, sym = request.param
    b = bars(name)
    return b, tf, sym, build(b, tf, sym, Params())


def test_schema_is_exact_and_ordered(store):
    _, _, _, ev = store
    assert list(ev.columns) == list(SCHEMA)
    assert len(ev) > 0


def test_no_trading_decision_leaked_into_the_fact_layer(store):
    """
    A fact says what happened. The moment a side, a stop or a size appears
    here, the detector and the strategy have merged again and the store stops
    being replayable against a different hypothesis.
    """
    _, _, _, ev = store
    banned = {'side', 'direction_traded', 'stop', 'target', 'entry', 'size',
              'r_multiple', 'pnl', 'signal'}
    assert not banned & set(ev.columns)


def test_known_at_is_one_bar_after_occurred_at(store):
    _, tf, _, ev = store
    gap = ev.known_at - ev.occurred_at
    assert (gap == pd.Timedelta(milliseconds=TF_MS[tf])).all()


def test_event_ids_are_unique_and_deterministic(store):
    b, tf, sym, ev = store
    assert ev.event_id.is_unique
    again = build(b, tf, sym, Params())
    assert list(again.event_id) == list(ev.event_id)


def test_tolerance_is_recorded_and_changes_the_store(store):
    """
    Every row must name the tolerance that produced it, because two tolerances
    are two different research cells and merging them silently is how a result
    stops being reproducible.
    """
    b, tf, sym, ev = store
    assert (ev.tol_atr == Params().tol_atr).all()
    loose = build(b, tf, sym, Params(tol_atr=0.60))
    assert (loose.tol_atr == 0.60).all()
    assert not set(loose.event_id) & set(ev.event_id)


def test_param_free_flag_matches_the_event_type(store):
    _, _, _, ev = store
    facts = ev[ev.event_type.isin([TOUCH, BREAK, CONFIRMED, INVALIDATED])]
    tuned = ev[ev.event_type.isin([RETEST, REJECTION])]
    assert facts.param_free.all()
    assert not tuned.param_free.any()
    # and a tuned event must say what it was tuned to
    assert ev.loc[ev.event_type == RETEST, 'retest_atr'].notna().all()
    assert ev.loc[ev.event_type == REJECTION, 'rejection_ratio'].notna().all()


def test_lineage_is_intact(store):
    _, _, _, ev = store
    by_id = ev.set_index('event_id')
    kids = ev[ev.parent_event_id.notna()]
    assert len(kids)
    for _, row in kids.iterrows():
        parent = by_id.loc[row.parent_event_id]
        assert parent.trendline_id == row.trendline_id
        assert parent.known_at <= row.known_at
        if row.event_type == RETEST:
            assert parent.event_type == BREAK
        else:
            assert parent.event_type in (TOUCH, CONFIRMED, RETEST)
    assert ev.loc[ev.parent_event_id.notna(), 'parent_event_id'].isin(
        ev.event_id).all()


def test_a_line_is_confirmed_before_it_is_touched_or_broken(store):
    """The lifecycle is not decoration: nothing may act on an unconfirmed line."""
    _, _, _, ev = store
    first_confirm = (ev[ev.event_type == CONFIRMED]
                     .groupby('trendline_id').known_at.min())
    later = ev[ev.event_type.isin([TOUCH, BREAK])]
    assert len(later)
    for line_id, g in later.groupby('trendline_id'):
        assert line_id in first_confirm.index
        assert (g.known_at > first_confirm[line_id]).all()


@pytest.mark.parametrize('cut', [600, 1000, 1400])
def test_events_are_identical_when_the_future_is_removed(store, cut):
    """
    The causality gate. Rebuild on history truncated at `cut` and demand every
    event the truncated run produced be byte-identical to the full run's.

    The forward-looking window is excluded rather than excused: a BREAK inside
    the last `retest_bars` of the truncated series has not been given its
    chance to be retested yet, so comparing there would fail for a reason that
    is not a leak.
    """
    b, tf, sym, full = store
    if len(b) <= cut + 50:
        pytest.skip('fixture too short for this cut')
    ep = EventParams()
    partial = build(b.iloc[:cut + 1], tf, sym, Params(), ep)
    edge = b.index[cut - ep.retest_bars]

    a = full[full.known_at <= edge].reset_index(drop=True)
    c = partial[partial.known_at <= edge].reset_index(drop=True)
    assert len(c) > 0
    pd.testing.assert_frame_equal(a, c, check_dtype=False)


def test_funnel_counts_reconcile_with_the_store(store):
    _, _, _, ev = store
    f = funnel(ev)
    assert len(f) == 1
    row = f.iloc[0]
    assert row['break'] == int((ev.event_type == BREAK).sum())
    assert row['touch'] == int((ev.event_type == TOUCH).sum())
    # a break can be retested at most once, by construction
    assert row['break_retested'] <= row['break']
    assert row['break_retested'] == int((ev.event_type == RETEST).sum())


def test_cells_reports_testability_against_the_floor(store):
    _, _, _, ev = store
    c = cells(ev, floor=200)
    assert set(c.columns) >= {'instrument', 'timeframe', 'tol_atr',
                              'event_type', 'n', 'testable'}
    assert c.n.sum() == len(ev)
    assert (c.testable == (c.n >= 200)).all()


# --------------------------------------------------------------------------- #
# the diagnostic CSV: can the gate decision be recomputed from the file alone?  #
# --------------------------------------------------------------------------- #
from sim.tl.diagnostics import (CHOP, DiagParams, HOLD, PAIRED_SCHEMA,
                                paired, run as diag_run)


@pytest.fixture(scope='module')
def diag():
    b = bars('bars_XAUUSDa_1h.csv')
    ev, _ = diag_run(b, '1h', Params(), DiagParams(), instrument='XAUUSD.a')
    return ev, paired(ev)


def test_paired_rows_carry_everything_needed_to_reproduce_the_gate(diag):
    _, pr = diag
    assert list(pr.columns) == list(PAIRED_SCHEMA)
    approach = pr[pr.phase == 'approach']
    assert len(approach)
    for col in ('instrument', 'timeframe', 'trendline_id', 'tolerance',
                'occurred_at', 'known_at', 'distance_atr', 'real_result',
                'placebo_result', 'effect_pp', 'z_score'):
        assert approach[col].notna().all(), col


def test_the_approach_phase_is_genuinely_paired(diag):
    """Every approach must produce exactly one outcome in each arm."""
    _, pr = diag
    a = pr[pr.phase == 'approach']
    assert a.is_paired.all()
    assert a.real_result.notna().all()
    assert a.placebo_result.notna().all()
    assert (a.n_pairs > 0).all()


def test_post_break_phases_are_not_reported_as_paired(diag):
    """
    A breakout row exists only for the arm whose approach broke, and the line
    can hold where its placebo breaks. Reporting those as pairs would apply
    McNemar's much tighter error to arms that were never paired.
    """
    _, pr = diag
    post = pr[pr.phase.isin(['breakout', 'retest'])]
    if not len(post):
        pytest.skip('no post-break phases in this fixture')
    assert not post.is_paired.any()
    assert (post.n_pairs == 0).all()
    # at least one row where exactly one arm is present -- the whole reason
    # these phases cannot be paired
    assert ((post.real_result.isna()) | (post.placebo_result.isna())).any()


def test_effect_recomputes_from_the_stored_rows(diag):
    """
    The point of persisting rows: a reader recomputes the headline from them
    and gets the same number, without rerunning the engine.
    """
    _, pr = diag
    a = pr[pr.phase == 'approach']
    d = a[(a.real_result != CHOP) & (a.placebo_result != CHOP)]
    effect = 100 * ((d.real_result == HOLD).mean()
                    - (d.placebo_result == HOLD).mean())
    assert effect == pytest.approx(a.effect_pp.iloc[0], abs=1e-3)
    assert len(d) == a.n_pairs.iloc[0]


def test_both_clocks_survive_into_the_diagnostic_rows(diag):
    _, pr = diag
    a = pr[pr.phase == 'approach']
    assert (a.known_at > a.occurred_at).all()
    resolved = a[a.resolved_at.notna()]
    # an outcome cannot resolve before the approach was even knowable
    assert (resolved.resolved_at >= resolved.known_at).all()


def test_tolerance_is_on_every_diagnostic_row(diag):
    ev, pr = diag
    assert (ev.tol_atr == Params().tol_atr).all()
    assert (pr.tolerance == Params().tol_atr).all()
