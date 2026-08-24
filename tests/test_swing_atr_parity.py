"""
ATR swing-threshold parity — the size filter, in both languages.

`Swing Threshold = ATR(n) x sensitivity` is a second, independent way of
deciding what counts as a swing. The fractal window asks "is this the highest of
N bars" -- a question about SHAPE. This asks "did price actually travel" -- a
question about SIZE, which is what makes XAUUSD and EURUSD comparable at all.

Both live behind `atr_sensitivity` (Python) / `atrSensitivity` (JS) and default
to 0, so every measurement in this project was made without them. They still
need the same guarantee everything else here has: the chart and the backtest
must select the same swings, or the structure you looked at is not the structure
that was tested.

Regenerate the fixtures after an intentional JS change:

    node tools/parity_export.mjs
"""

import glob
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sim.tl.swings import SwingState
from sim.tl.swings import detect as detect_swings

HERE = os.path.dirname(os.path.abspath(__file__))
FIX = os.path.join(HERE, 'fixtures')
SENSITIVITIES = (0, 0.5, 1, 1.5, 2.5)


def _cases():
    out = []
    for path in sorted(glob.glob(os.path.join(FIX, 'expected_*.json'))):
        stem = os.path.basename(path)[len('expected_'):-len('.json')]
        csv = os.path.join(FIX, 'bars_%s.csv' % stem)
        if os.path.exists(csv) and 'swings_atr' in json.load(open(path)):
            out.append((stem, csv, path))
    return out


CASES = _cases()


def _load(csv):
    import pandas as pd
    df = pd.read_csv(csv, parse_dates=['t']).set_index('t')
    return df.iloc[:-1]              # the exporter drops the forming bar


def _confirmed(bars, sens):
    return [s.i for s in detect_swings(bars, strength=3, atr_sensitivity=sens)
            if s.state is SwingState.CONFIRMED]


@pytest.mark.parametrize('stem,csv,expected', CASES, ids=[c[0] for c in CASES])
def test_atr_swing_threshold_parity(stem, csv, expected):
    """
    The same swings must survive in both languages, at every sensitivity.

    Checked across a ladder rather than at one value: a size filter agrees
    trivially at 0, where it does nothing, and can diverge the moment it starts
    removing anything.
    """
    bars = _load(csv)
    js = json.load(open(expected))['swings_atr']
    for sens in SENSITIVITIES:
        py = _confirmed(bars, sens)
        got = js[str(sens)]
        first = next((k for k in range(min(len(got), len(py)))
                      if got[k] != py[k]), 'length only')
        assert got == py, (
            'sensitivity %s: JS kept %d, Python %d; first divergence at %s'
            % (sens, len(got), len(py), first))


@pytest.mark.parametrize('stem,csv,expected', CASES, ids=[c[0] for c in CASES])
def test_filter_actually_removes_something(stem, csv, expected):
    """
    Negative control: the ladder must BITE.

    Without this the parity test above would pass on a filter that ignored its
    own parameter, since every sensitivity would return the same list and the
    two languages would agree on all of them.
    """
    bars = _load(csv)
    counts = [len(_confirmed(bars, s)) for s in SENSITIVITIES]
    assert counts == sorted(counts, reverse=True), \
        'raising the threshold must never ADD swings: %s' % counts
    assert counts[-1] < counts[0] * 0.9, \
        'sensitivity 2.5 removed almost nothing: %s' % counts


def test_atr_threshold_is_causal():
    """
    The filter must compare against the last KEPT swing, never the next one.

    The natural implementation -- drop a swing because the FOLLOWING swing sits
    too close -- needs a bar that has not printed, and is the classic ZigZag
    repaint. This checks the property directly: truncating the data must not
    change which earlier swings survive.
    """
    if not CASES:
        pytest.skip('no fixtures')
    bars = _load(CASES[0][1])
    full = _confirmed(bars, 1.5)
    part = _confirmed(bars.iloc[:len(bars) // 2], 1.5)
    assert part, 'the truncated run found nothing to compare'
    assert part == [i for i in full if i <= max(part)], \
        'the ATR filter changed its mind about the past when more data arrived'
