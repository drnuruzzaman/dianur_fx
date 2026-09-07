"""
Market-structure parity — js/chart/marketstructure.js against
sim/tl/market_structure.py.

BOS and CHoCH are the same break wearing different labels: which one fires
depends on the BIAS the break arrives in. So this is a state machine, and the
failure mode is not a wrong value on one bar — it is a bias that diverges once
and then mislabels every event after it. The whole event list is compared in
order, and the per-bar bias array with it.

Regenerate the fixtures after an intentional JS change:

    node tools/parity_export.mjs
"""

import glob
import json
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sim.tl.market_structure import BOS, CHOCH, MSParams, detect

HERE = os.path.dirname(os.path.abspath(__file__))
FIX = os.path.join(HERE, 'fixtures')
TOL = 1e-9


def _cases():
    out = []
    for path in sorted(glob.glob(os.path.join(FIX, 'expected_*.json'))):
        stem = os.path.basename(path)[len('expected_'):-len('.json')]
        csv = os.path.join(FIX, 'bars_%s.csv' % stem)
        if os.path.exists(csv):
            out.append((stem, csv, path))
    return out


CASES = _cases()
IDS = [c[0] for c in CASES]


def _python(csv):
    df = pd.read_csv(csv, parse_dates=['t']).set_index('t').iloc[:-1]
    return df, detect(df, MSParams())


@pytest.mark.parametrize('stem,csv,expected', CASES, ids=IDS)
def test_same_events(stem, csv, expected):
    _, (events, _arr) = _python(csv)
    theirs = json.load(open(expected, encoding='utf-8'))['market_structure']['events']

    assert len(events) == len(theirs), (
        'python %d events, js %d' % (len(events), len(theirs)))

    for k, (a, b) in enumerate(zip(events, theirs)):
        w = '%s event %d (bar %d)' % (stem, k, a.i)
        assert a.kind == b['kind'], w + ': BOS/CHoCH label differs'
        assert a.direction == b['direction'], w
        assert a.i == b['i'] and a.t == b['t'], w + ': bar'
        assert a.level == pytest.approx(b['level'], rel=1e-9, abs=TOL), w
        assert a.level_i == b['level_i'], w + ': broken swing bar'
        assert a.bias_before == b['bias_before'], w
        assert a.bias_after == b['bias_after'], w
        assert a.close == pytest.approx(b['close'], rel=1e-9, abs=TOL), w


@pytest.mark.parametrize('stem,csv,expected', CASES, ids=IDS)
def test_same_per_bar_state(stem, csv, expected):
    """
    The bias array is where a divergence shows up EARLIEST — before it starts
    mislabelling events.
    """
    _, (_events, arr) = _python(csv)
    js = json.load(open(expected, encoding='utf-8'))['market_structure']

    for key in ('bias', 'event', 'event_dir'):
        a = list(arr[key])
        b = list(js[key])
        assert len(a) == len(b), '%s/%s length' % (stem, key)
        bad = [(i, a[i], b[i]) for i in range(len(a)) if a[i] != b[i]]
        assert not bad, '%s/%s: %d mismatches, first %s' % (stem, key, len(bad), bad[:3])

    for key in ('swing_high', 'swing_low'):
        a = np.asarray(arr[key], dtype=float)
        b = np.array([np.nan if v is None else float(v) for v in js[key]], dtype=float)
        assert np.array_equal(np.isnan(a), np.isnan(b)), (
            '%s/%s: NaN pattern differs — a consumed level on one side only'
            % (stem, key))
        both = ~np.isnan(a)
        if both.any():
            assert np.max(np.abs(a[both] - b[both])) < TOL, '%s/%s' % (stem, key)


@pytest.mark.parametrize('stem,csv,expected', CASES, ids=IDS)
def test_state_machine_invariants(stem, csv, expected):
    """
    Properties a shared bug in both ports would still have to satisfy. These are
    the definition of BOS/CHoCH, not an implementation detail.
    """
    _, (events, arr) = _python(csv)
    if not events:
        pytest.skip('no events')

    for k, e in enumerate(events):
        w = '%s event %d (bar %d)' % (stem, k, e.i)
        # a CHoCH flips the bias; a BOS never does
        flipped = e.bias_before != e.bias_after
        assert (e.kind == CHOCH) == flipped, (
            w + ': %s with bias %s -> %s' % (e.kind, e.bias_before, e.bias_after))
        # the bias after an event always matches its direction
        assert e.bias_after == e.direction, w
        # a bullish break must close ABOVE the level it broke, and vice versa
        if e.direction == 'bullish':
            assert e.close > e.level, w + ': bullish break closed below its level'
        else:
            assert e.close < e.level, w + ': bearish break closed above its level'
        # the broken swing must PRE-date the break
        assert e.level_i < e.i, w + ': broke a swing from the future'
        if k:
            assert e.i > events[k - 1].i, w + ': events out of order'

    # a level is consumed when broken: it must be NaN on the break bar
    for e in events:
        side = 'swing_high' if e.direction == 'bullish' else 'swing_low'
        assert np.isnan(arr[side][e.i]), (
            '%s bar %d: %s survived its own break' % (stem, e.i, side))


def test_first_event_is_a_choch():
    """
    Bias starts neutral, so the first break can never be a continuation of a
    trend that was never established.
    """
    for stem, csv, _ in CASES:
        _, (events, _) = _python(csv)
        if events:
            assert events[0].kind == CHOCH, (
                '%s: first event is a %s from a neutral bias' % (stem, events[0].kind))
