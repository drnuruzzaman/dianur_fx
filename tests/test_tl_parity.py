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

    close(last.support_px, js['supportPx'], 'support price')
    close(last.resistance_px, js['resistancePx'], 'resistance price')
    assert abs((last.support_q or 0) - (js['supportQ'] or 0)) <= Q_TOL
    assert abs((last.resistance_q or 0) - (js['resistanceQ'] or 0)) <= Q_TOL
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
