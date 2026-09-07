"""
Slope-line parity — js/chart/slopelines.js against sim/tl/slope_lines.py.

These lines are a running recursion: each bar's value is the previous value
minus a slope, reset only when a pivot becomes visible. A one-bar disagreement
therefore does not stay local — it propagates until the next reset. So the
comparison is the WHOLE series, every bar, all three slope methods, rather than
a summary at the last bar.

The break bars are compared as index lists because that is what a strategy or an
alert would fire on, and a one-bar drift there is a real bug rather than a
rounding difference.

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

from sim.tl.slope_lines import ATR, LINREG, STDEV, SlopeParams, compute

HERE = os.path.dirname(os.path.abspath(__file__))
FIX = os.path.join(HERE, 'fixtures')

# A long recursion accumulates differently across the two runtimes; the other
# parity tests use the same band and it is far tighter than a tick.
TOL = 1e-9
METHODS = [ATR, STDEV, LINREG]


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
PARAMS = [(s, c, e, m) for (s, c, e) in CASES for m in METHODS]
PARAM_IDS = ['%s-%s' % (s, m) for (s, _c, _e, m) in PARAMS]


def _bars(csv):
    df = pd.read_csv(csv, parse_dates=['t']).set_index('t')
    return df.iloc[:-1]                     # exporter drops the forming bar


def _compare(py, js, label):
    a = np.asarray(py, dtype=float)
    b = np.array([np.nan if v is None else float(v) for v in js], dtype=float)
    assert len(a) == len(b), '%s: length %d vs %d' % (label, len(a), len(b))
    assert np.array_equal(np.isnan(a), np.isnan(b)), (
        '%s: NaN pattern differs — first at bar %s'
        % (label, int(np.argmax(np.isnan(a) != np.isnan(b)))))
    both = ~np.isnan(a)
    if both.any():
        d = np.abs(a[both] - b[both])
        assert d.max() < TOL, ('%s: max diff %.3e at bar %d'
                               % (label, d.max(), int(np.argmax(np.abs(a - b)))))


@pytest.mark.parametrize('stem,csv,expected,method', PARAMS, ids=PARAM_IDS)
def test_lines_match_every_bar(stem, csv, expected, method):
    bars = _bars(csv)
    js = json.load(open(expected, encoding='utf-8'))['slope_lines'][method]
    py = compute(bars, SlopeParams(method=method))
    for key_py, key_js in [('upper', 'upper'), ('lower', 'lower'),
                           ('slope_up', 'slope_up'), ('slope_dn', 'slope_dn')]:
        _compare(py[key_py], js[key_js], '%s/%s/%s' % (stem, method, key_py))


@pytest.mark.parametrize('stem,csv,expected,method', PARAMS, ids=PARAM_IDS)
def test_break_bars_match(stem, csv, expected, method):
    bars = _bars(csv)
    js = json.load(open(expected, encoding='utf-8'))['slope_lines'][method]
    py = compute(bars, SlopeParams(method=method))
    mine_up = [int(i) for i, v in enumerate(py['break_up']) if v]
    mine_dn = [int(i) for i, v in enumerate(py['break_dn']) if v]
    assert mine_up == list(js['break_up']), (
        '%s/%s break_up: python %d, js %d' % (stem, method, len(mine_up),
                                              len(js['break_up'])))
    assert mine_dn == list(js['break_dn']), (
        '%s/%s break_dn: python %d, js %d' % (stem, method, len(mine_dn),
                                              len(js['break_dn'])))


@pytest.mark.parametrize('stem,csv,expected', CASES, ids=IDS)
def test_backpaint_is_look_ahead_and_differs(stem, csv, expected):
    """
    `backpaint` resets the line at the bar the pivot OCCURRED, which is
    `length` bars before anyone could know it. The published script offers it;
    the default here refuses it.

    Both sides must agree on the backpainted variant too -- but it must also be
    DIFFERENT from the default, otherwise the flag is silently doing nothing and
    the causal guarantee is untested.
    """
    bars = _bars(csv)
    js = json.load(open(expected, encoding='utf-8'))['slope_lines']['atr_backpaint']
    bp = compute(bars, SlopeParams(method=ATR, backpaint=True))
    _compare(bp['upper'], js['upper'], '%s/backpaint/upper' % stem)
    _compare(bp['lower'], js['lower'], '%s/backpaint/lower' % stem)

    causal = compute(bars, SlopeParams(method=ATR, backpaint=False))
    a = np.asarray(bp['upper'], float)
    b = np.asarray(causal['upper'], float)
    both = ~np.isnan(a) & ~np.isnan(b)
    assert both.any(), 'nothing comparable'
    assert np.nanmax(np.abs(a[both] - b[both])) > TOL, (
        'backpaint produced an identical series to the causal default — the '
        'look-ahead guard is not actually doing anything')


@pytest.mark.parametrize('stem,csv,expected', CASES, ids=IDS)
def test_lines_always_exist_after_warmup(stem, csv, expected):
    """
    The property that motivated porting this at all: the two-pivot engine draws
    nothing on some series, this one essentially always has a line.
    """
    bars = _bars(csv)
    py = compute(bars, SlopeParams(method=ATR))
    n = len(bars)
    warm = 3 * SlopeParams().length
    cover = np.isfinite(np.asarray(py['upper'], float)[warm:]).mean()
    assert cover > 0.9, ('upper line covers only %.1f%% of bars after warmup'
                         % (100 * cover))


def test_mult_zero_gives_flat_levels():
    """
    mult = 0 is the documented degenerate case: no decay, so the lines are
    horizontal levels holding the last pivot until the next one. Worth pinning —
    it is the natural control to measure the sloped version against.
    """
    csv = os.path.join(FIX, 'bars_XAUUSDa_1h.csv')
    bars = _bars(csv)
    r = compute(bars, SlopeParams(method=ATR, mult=0.0))
    u = np.asarray(r['upper'], float)
    u = u[~np.isnan(u)]
    steps = np.abs(np.diff(u))
    # every change must be a reset to a new pivot, never a drift
    assert (steps[steps > 0] > 1e-9).all()
    assert np.median(steps) == 0.0, 'a flat level should not move between pivots'
