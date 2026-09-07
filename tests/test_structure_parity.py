"""
Structure and regime parity — the Trend read panel against the Python engine.

The panel in the top right of the page states a bias per timeframe. That claim
is only worth showing if it is the same claim sim/tl/structure.py and
sim/tl/regime.py would make, because those are what any backtest reads. This
compares them bar by bar over the same real slices the other parity tests use.

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

from sim.tl import regime as reg
from sim.tl import structure as st

HERE = os.path.dirname(os.path.abspath(__file__))
FIX = os.path.join(HERE, 'fixtures')

# Long recursions (EMA, Wilder ATR) accumulate differently across the two
# runtimes; the other parity tests use the same band.
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


def _load(csv):
    df = pd.read_csv(csv, parse_dates=['t']).set_index('t')
    # the exporter drops the last (forming) bar
    return df.iloc[:-1]


@pytest.mark.parametrize('stem,csv,expected', CASES,
                         ids=[c[0] for c in CASES])
def test_structure_parity(stem, csv, expected):
    bars = _load(csv)
    doc = json.load(open(expected))
    js = doc['structure']
    py = st.classify(bars)

    assert len(js['bias']) == len(bars), 'exported length must match the slice'

    for key_js, key_py in [('high_label', 'high_label'), ('low_label', 'low_label'),
                           ('bias', 'bias')]:
        a = list(js[key_js])
        b = list(py[key_py])
        mismatch = [(i, a[i], b[i]) for i in range(len(b)) if a[i] != b[i]]
        assert not mismatch, '%s: %d mismatches, first %s' % (
            key_js, len(mismatch), mismatch[:5])

    for key in ('last_high', 'last_low'):
        a = np.array([np.nan if v is None else v for v in js[key]], dtype=float)
        b = np.asarray(py[key], dtype=float)
        both = ~np.isnan(a) & ~np.isnan(b)
        assert np.array_equal(np.isnan(a), np.isnan(b)), '%s NaN pattern' % key
        assert np.max(np.abs(a[both] - b[both])) < TOL, key


@pytest.mark.parametrize('stem,csv,expected', CASES,
                         ids=[c[0] for c in CASES])
def test_regime_parity(stem, csv, expected):
    bars = _load(csv)
    doc = json.load(open(expected))
    js = doc['regime']
    regime, direction, feats = reg.compute(bars)

    for name, py_vals in [('regime', regime), ('direction', direction)]:
        a = list(js[name])
        b = list(py_vals)
        mismatch = [(i, a[i], b[i]) for i in range(len(b)) if a[i] != b[i]]
        assert not mismatch, '%s: %d mismatches, first %s' % (
            name, len(mismatch), mismatch[:5])

    for js_key, py_key in [('range_pos', 'range_pos'), ('energy', 'energy'),
                           ('ema_sep_atr', 'ema_sep_atr')]:
        a = np.array([np.nan if v is None else v for v in js[js_key]], dtype=float)
        b = np.asarray(feats[py_key], dtype=float)
        assert np.array_equal(np.isnan(a), np.isnan(b)), '%s NaN pattern' % js_key
        both = ~np.isnan(a) & ~np.isnan(b)
        assert np.max(np.abs(a[both] - b[both])) < TOL, js_key


# --------------------------------------------------------------------------- #
# One clock, enforced.
#
# The engine reads broker server time as tz-naive on purpose (see
# sim/tl/clockguard.py). A tz-aware frame slipping in would not raise anywhere
# downstream -- MTF alignment would simply serve the wrong context bar -- so the
# guard has to be a test, not a convention.
# --------------------------------------------------------------------------- #

from sim.tl.clockguard import TimezoneMixError
from sim.tl.engine import Params, TrendlineEngine
from sim.tl.mtf import TF_MS


def _bars():
    csv = os.path.join(FIX, 'bars_XAUUSDa_1h.csv')
    return pd.read_csv(csv, parse_dates=['t']).set_index('t')


@pytest.mark.parametrize('call', [
    lambda b: TrendlineEngine('1h', TF_MS['1h'], Params()).walk(b),
    lambda b: st.classify(b),
    lambda b: reg.compute(b),
], ids=['engine.walk', 'structure.classify', 'regime.compute'])
def test_tz_aware_index_is_rejected(call):
    naive = _bars()
    call(naive)                                  # the supported convention works

    aware = naive.copy()
    aware.index = aware.index.tz_localize('UTC')
    with pytest.raises(TimezoneMixError):
        call(aware)


def test_guard_does_not_silently_convert():
    """A guard that stripped the tz would reintroduce the ambiguity it exists
    to prevent, so rejection must be the only behaviour."""
    aware = _bars()
    aware.index = aware.index.tz_localize('Etc/GMT-3')
    with pytest.raises(TimezoneMixError):
        st.classify(aware)
    assert aware.index.tz is not None, 'the guard must not mutate the caller'
