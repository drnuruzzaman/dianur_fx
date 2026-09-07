"""
Trendline lifecycle engine: JS against Python.

The chart used to draw lines from a batch scorer while the backtest used the
incremental lifecycle engine — two different algorithms, so the lines on screen
were not the lines being traded. js/chart/tlengine.js is now a port of
sim/tl/engine.py, and this is what keeps them honest.

Every line either implementation ever created is compared: same anchors, same
lifecycle transitions, same touch and violation counts, same quality. A line
present in one and absent from the other fails, as does a line that confirmed in
one and stayed a candidate in the other.

Regenerate the reference after an intentional change to the JS:

    node tools/parity_export.mjs

    python -m pytest tests/test_tl_parity.py -q
"""

import glob
import json
import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sim.tl.engine import Params, TrendlineEngine
from sim.tl.mtf import TF_MS

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fixtures')

# quality is rounded to 2dp on both sides, and Python's round() is banker's
# rounding while JS Math.round is half-up, so the last digit may differ by one.
# Everything discrete (status, counts, anchors, timestamps) must match exactly —
# that is where an algorithmic difference would show.
Q_TOL = 0.011


def cases():
    out = []
    for path in sorted(glob.glob(os.path.join(FIX, 'expected_*.json'))):
        stem = os.path.basename(path)[len('expected_'):-len('.json')]
        bars = os.path.join(FIX, 'bars_%s.csv' % stem)
        if os.path.exists(bars):
            out.append((stem, bars, path))
    return out


CASES = cases()
IDS = [c[0] for c in CASES]


def run_python(bars_path, tf):
    df = pd.read_csv(bars_path, parse_dates=['t']).set_index('t')
    eng = TrendlineEngine(tf, TF_MS[tf], Params())
    snaps = eng.walk(df)
    seen = {}
    for s in snaps:
        for l in s.live:
            seen.setdefault(l.id, l)
    rows = {}
    for l in seen.values():
        key = '%s|%d|%d' % (l.role.value, l.pivot_1['i'], l.pivot_2['i'])
        rows[key] = l
    return df, snaps, rows


def tf_of(stem):
    return '1h' if stem.endswith('_1h') else '15m'


@pytest.mark.parametrize('stem,bars_path,expected_path', CASES, ids=IDS)
def test_the_same_lines_exist(stem, bars_path, expected_path):
    doc = json.load(open(expected_path, encoding='utf-8'))
    _, _, mine = run_python(bars_path, tf_of(stem))
    theirs = {r['k']: r for r in doc['tl_engine']['lines']}

    only_py = sorted(set(mine) - set(theirs))[:5]
    only_js = sorted(set(theirs) - set(mine))[:5]
    assert not only_py, f'lines only Python created: {only_py}'
    assert not only_js, f'lines only the JS created: {only_js}'
    assert len(mine) == len(theirs)


@pytest.mark.parametrize('stem,bars_path,expected_path', CASES, ids=IDS)
def test_lifecycle_and_counts_match(stem, bars_path, expected_path):
    doc = json.load(open(expected_path, encoding='utf-8'))
    _, _, mine = run_python(bars_path, tf_of(stem))
    theirs = {r['k']: r for r in doc['tl_engine']['lines']}

    bad = []
    for key, l in mine.items():
        js = theirs.get(key)
        if js is None:
            continue
        checks = (
            ('status', l.status.value, js['status']),
            ('direction', l.direction.value, js['dir']),
            ('touches', l.touches, js['touches']),
            ('tests', l.tests, js['tests']),
            ('violations', l.violations, js['violations']),
            ('span', l.span_bars, js['span']),
            ('created_at', l.created_at, js['created']),
            ('confirmed_at', l.confirmed_at, js['confirmed']),
            ('broken_at', l.broken_at, js['broken']),
            ('archive_reason', getattr(l, 'archive_reason', ''), js['reason']),
        )
        for name, a, b in checks:
            if a != b:
                bad.append(f'{key} {name}: python={a!r} js={b!r}')
        if abs((l.quality_score or 0) - (js['q'] or 0)) > Q_TOL:
            bad.append(f'{key} quality: python={l.quality_score} js={js["q"]}')
        pq, jq = l.quality_at_break, js['qBreak']
        if (pq is None) != (jq is None) or (pq is not None and abs(pq - jq) > Q_TOL):
            bad.append(f'{key} quality_at_break: python={pq} js={jq}')

    assert not bad, 'lifecycle drift in %d of %d lines:\n  %s' % (
        len(bad), len(mine), '\n  '.join(bad[:12]))


@pytest.mark.parametrize('stem,bars_path,expected_path', CASES, ids=IDS)
def test_the_offered_lines_match_at_the_last_bar(stem, bars_path, expected_path):
    """
    What a consumer actually reads: the best support and resistance, and their
    prices. If these drift, the chart and the strategy disagree about the level
    even when the populations match.
    """
    doc = json.load(open(expected_path, encoding='utf-8'))
    _, snaps, _ = run_python(bars_path, tf_of(stem))
    last = snaps[-1]
    js = doc['tl_engine']['final']

    def close(a, b, label):
        a = float('nan') if a is None else float(a)
        b = float('nan') if b is None else float(b)
        if pd.isna(a) and pd.isna(b):
            return
        assert a == pytest.approx(b, rel=1e-9, abs=1e-9), \
            f'{label}: python={a} js={b}'

    def close_q(a, b, label):
        """
        Quality needs the same NaN handling the prices already get. "No line is
        offered on this side" is a legitimate and increasingly common answer --
        min_quality is 90, so a side with nothing above the bar reports NaN in
        Python and null in JS. `(x or 0)` turned that agreement into `nan <= tol`,
        which is False, and the two engines were failing a test by agreeing.
        """
        a = float('nan') if a is None else float(a)
        b = float('nan') if b is None else float(b)
        assert pd.isna(a) == pd.isna(b),             f'{label}: one side offers a line and the other does not (py={a}, js={b})'
        if pd.isna(a):
            return
        assert abs(a - b) <= Q_TOL, f'{label}: python={a} js={b}'

    close(last.support_px, js['supportPx'], 'support price')
    close(last.resistance_px, js['resistancePx'], 'resistance price')
    close_q(last.support_q, js['supportQ'], 'support quality')
    close_q(last.resistance_q, js['resistanceQ'], 'resistance quality')
    assert last.live_count == js['liveCount']


@pytest.mark.parametrize('stem,bars_path,expected_path', CASES, ids=IDS)
def test_break_events_happen_on_the_same_bars(stem, bars_path, expected_path):
    """A breakout strategy trades these bars, so a one-bar drift is a real bug."""
    doc = json.load(open(expected_path, encoding='utf-8'))
    _, snaps, _ = run_python(bars_path, tf_of(stem))
    mine = [[s.i, len(s.breaks)] for s in snaps if s.breaks]
    theirs = [list(x) for x in doc['tl_engine']['breakBars']]
    assert mine == theirs, (
        f'break bars differ: python has {len(mine)}, js has {len(theirs)}; '
        f'first mismatch around {next((a for a, b in zip(mine, theirs) if a != b), None)}')


@pytest.mark.parametrize('stem,bars_path,expected_path', CASES, ids=IDS)
def test_the_reference_is_not_empty(stem, bars_path, expected_path):
    """Guard the guard: an empty export would make everything above pass."""
    doc = json.load(open(expected_path, encoding='utf-8'))
    tl = doc['tl_engine']
    assert len(tl['lines']) > 200, 'suspiciously few lines in the reference'
    assert any(l['status'] in ('CONFIRMED', 'ACTIVE') for l in tl['lines'])
    assert any(l['confirmed'] for l in tl['lines'])
    assert tl['breakBars'], 'reference contains no break events'


# --------------------------------------------------------------------------- #
# The RECLAIM lifecycle.
#
# BROKEN used to be terminal, which buried lines the market was still using: on
# gold H1 a rising support with five touches sat one point under price, marked
# BROKEN because price had left it 29 bars earlier, and seven of the ten bars
# since had closed back above it.
#
# Reclaim is OFF by default, so every test above exercises the original
# lifecycle. This one turns it on -- otherwise the new code path would ship
# unported and untested, which is exactly how minTouches and breakConfirmBars
# came to exist in Python only.
# --------------------------------------------------------------------------- #

def test_reclaim_lifecycle_matches():
    from sim.tl.engine import Params, TrendlineEngine
    from sim.tl.mtf import TF_MS as PY_TF_MS

    for stem, bars_path, expected_path in CASES:
        df = pd.read_csv(bars_path, parse_dates=['t']).set_index('t')
        js = json.load(open(expected_path, encoding='utf-8'))['reclaim']
        tf = tf_of(stem)
        snaps = TrendlineEngine(tf, PY_TF_MS[tf],
                                Params(reclaim_confirm_bars=3,
                                       break_confirm_bars=2,
                                       min_touches=3)).walk(df)
        last = snaps[-1]
        assert last.live_count == js['live_count'], '%s: live count' % stem
        assert sum(1 for l in last.live if l.is_tradeable) == js['tradeable'], (
            '%s: tradeable count' % stem)

        # keyed by anchors: snap.live ordering is not part of the contract
        mine = sorted(
            ({'k': '%s|%d|%d' % (l.role.value, l.pivot_1['i'], l.pivot_2['i']),
              'status': l.status.value, 'reclaims': l.reclaims,
              'violations': l.violations, 'quality': l.quality_score}
             for l in last.live), key=lambda d: d['k'])
        theirs = sorted(js['byline'], key=lambda d: d['k'])
        assert [d['k'] for d in mine] == [d['k'] for d in theirs], (
            '%s: different line populations' % stem)
        for a, b in zip(mine, theirs):
            assert a['status'] == b['status'], (
                '%s %s: python %s, js %s' % (stem, a['k'], a['status'], b['status']))
            assert a['reclaims'] == b['reclaims'], '%s %s: reclaims' % (stem, a['k'])
            assert a['violations'] == b['violations'], '%s %s: violations' % (stem, a['k'])
            assert abs(a['quality'] - b['quality']) <= 0.011, (
                '%s %s: quality' % (stem, a['k']))


def test_reclaim_is_on_and_actually_changes_the_outcome():
    """
    A parity test that compared two implementations both doing nothing would
    pass and prove nothing. This asserts the feature is live AND that it moves
    the result, so the compared path is real.

    Reclaim ships ON: pooled edge over everything the engine offers went
    +0.21 -> +1.02 pp (1999-2010), -1.43 -> -0.40 (2011-2020),
    +1.19 -> +0.90 (2021-2026). The reclaimed slice itself, gated at
    reclaim_min_quality, runs +4.76 / +3.10 / +4.75 pp.
    """
    from sim.tl.engine import Params, TrendlineEngine
    from sim.tl.mtf import TF_MS as PY_TF_MS

    assert Params().reclaim_confirm_bars == 3, 'reclaim ships on'
    assert Params().reclaim_min_quality == 70.0, (
        'the quality floor is measured, not incidental — see engine.Params')

    stem, bars_path, expected_path = CASES[0]
    df = pd.read_csv(bars_path, parse_dates=['t']).set_index('t')
    tf = tf_of(stem)
    off = TrendlineEngine(tf, PY_TF_MS[tf],
                          Params(reclaim_confirm_bars=0)).walk(df)[-1]
    on = TrendlineEngine(tf, PY_TF_MS[tf], Params()).walk(df)[-1]
    n_off = sum(1 for l in off.live if l.is_tradeable)
    n_on = sum(1 for l in on.live if l.is_tradeable)
    assert n_on > n_off, (
        'reclaim produced no additional tradeable lines (%d vs %d) — the path '
        'under test is not being exercised' % (n_on, n_off))
    assert any(l.status.value == 'RECLAIMED' for l in on.live)
    assert all(l.violations > 0 for l in on.live if l.status.value == 'RECLAIMED'), \
        'a reclaimed line must keep its violation: the scar is the point'
