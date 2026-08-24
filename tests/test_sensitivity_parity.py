"""
Sensitivity parity — js/chart/sensitivity.js against sim/tl/sensitivity.py.

Two things are compared, because a divergence in either would move the lines on
the chart away from the lines a backtest sees:

  1. the CALIBRATION itself -- regime, window, prominence bars, thresholds
  2. the ENGINE'S OUTPUT when driven by it -- the offered lines and break bars

The second matters on its own. Both sides could agree on a prominence bar of
2.83 and still disagree about which pivots clear it, or about which side the bar
applies to (a swing HIGH feeds resistance, a swing LOW feeds support -- getting
that backwards is silent and still looks plausible).

Percentile is the subtle one: numpy's default is linear interpolation, so the JS
port implements that rather than a nearest-rank shortcut. A method mismatch
would show up here as a small, stable offset in the prominence bars.

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

from sim.tl.engine import Params, TrendlineEngine
from sim.tl.mtf import TF_MS
from sim.tl.sensitivity import SensitivityParams, calibrate

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


def _bars(csv):
    df = pd.read_csv(csv, parse_dates=['t']).set_index('t')
    return df.iloc[:-1]                      # exporter drops the forming bar


def _tf_of(stem):
    return '1h' if stem.endswith('_1h') else '15m'


@pytest.mark.parametrize('stem,csv,expected', CASES, ids=IDS)
def test_calibration_matches(stem, csv, expected):
    bars = _bars(csv)
    tf = _tf_of(stem)
    doc = json.load(open(expected, encoding='utf-8'))['sensitivity']
    mine = calibrate(bars, tf, stem, params=SensitivityParams(),
                     upto=doc['cut'])
    js = doc['causal']

    assert mine.vol_regime == js['vol_regime'], 'volatility regime'
    assert mine.atr_pct == pytest.approx(js['atr_pct'], rel=1e-9, abs=TOL)
    assert mine.support.strength == js['strength']
    assert mine.resistance.strength == js['strength']
    assert mine.n_pivots == js['n_pivots']

    assert mine.support.min_prominence_atr == pytest.approx(
        js['prom_sup'], rel=1e-9, abs=TOL), 'support prominence bar'
    assert mine.resistance.min_prominence_atr == pytest.approx(
        js['prom_res'], rel=1e-9, abs=TOL), 'resistance prominence bar'
    assert mine.support.tol_atr == pytest.approx(js['tol_sup'], abs=TOL)
    assert mine.resistance.tol_atr == pytest.approx(js['tol_res'], abs=TOL)
    assert mine.support.min_quality == pytest.approx(js['q_sup'], abs=TOL)
    assert mine.resistance.min_quality == pytest.approx(js['q_res'], abs=TOL)


@pytest.mark.parametrize('stem,csv,expected', CASES, ids=IDS)
def test_full_history_calibration_matches(stem, csv, expected):
    """
    The causal fixture may sit below `min_pivots` and report a prominence bar of
    zero, which would let a percentile bug through unnoticed. Calibrating on the
    whole slice guarantees a non-trivial bar and puts the percentile arithmetic
    itself under test.
    """
    bars = _bars(csv)
    tf = _tf_of(stem)
    js = json.load(open(expected, encoding='utf-8'))['sensitivity']['full']
    mine = calibrate(bars, tf, stem, params=SensitivityParams())

    assert mine.vol_regime == js['vol_regime']
    assert mine.support.strength == js['strength']
    assert mine.support.min_prominence_atr == pytest.approx(
        js['prom_sup'], rel=1e-9, abs=TOL)
    assert mine.resistance.min_prominence_atr == pytest.approx(
        js['prom_res'], rel=1e-9, abs=TOL)
    assert mine.support.min_prominence_atr > 0, (
        'this fixture should have enough pivots to produce a real bar; a zero '
        'here means the percentile arithmetic is never actually compared')


@pytest.mark.parametrize('stem,csv,expected', CASES, ids=IDS)
def test_calibrated_engine_output_matches(stem, csv, expected):
    """
    Agreeing on the thresholds is not enough -- the two engines must also
    CONSUME them identically.
    """
    bars = _bars(csv)
    tf = _tf_of(stem)
    js = json.load(open(expected, encoding='utf-8'))['sensitivity']['walk']
    sens = calibrate(bars, tf, stem, params=SensitivityParams())
    snaps = TrendlineEngine(tf, TF_MS[tf], Params(), sensitivity=sens).walk(bars)
    last = snaps[-1]

    assert last.live_count == js['live_count'], 'live population'

    def _same(a, b, label):
        a = float('nan') if a is None else float(a)
        b = float('nan') if b is None else float(b)
        assert pd.isna(a) == pd.isna(b), '%s: one side offers a line, one does not' % label
        if not pd.isna(a):
            assert a == pytest.approx(b, rel=1e-9, abs=1e-9), label

    _same(last.support_px, js['support_px'], 'support price')
    _same(last.resistance_px, js['resistance_px'], 'resistance price')
    _same(last.support_q, js['support_q'], 'support quality')
    _same(last.resistance_q, js['resistance_q'], 'resistance quality')

    mine = [[s.i, len(s.breaks)] for s in snaps if s.breaks]
    theirs = [list(x) for x in js['break_bars']]
    assert mine == theirs, (
        'break bars differ under a calibrated engine: python %d, js %d'
        % (len(mine), len(theirs)))


def test_asymmetry_is_off_by_default():
    """
    The per-side asymmetry contributed +1.10 pp in one era, -0.85 in the next
    and -0.13 in the third, and was the only variant that turned a positive era
    negative. It stays available and stays OFF; a default that quietly re-enables
    it would put an unreplicated finding into every chart.
    """
    p = SensitivityParams()
    assert p.resistance_quality_bonus == 0.0
    assert p.resistance_tol_scale == 1.0

    csv = os.path.join(FIX, 'bars_XAUUSDa_1h.csv')
    s = calibrate(_bars(csv), '1h', 'x', params=p)
    assert s.support.tol_atr == s.resistance.tol_atr
    assert s.support.min_quality == s.resistance.min_quality
