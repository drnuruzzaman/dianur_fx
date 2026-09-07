"""
Supply/demand parity — js/chart/supplydemand.js against sim/tl/supply_demand.py.

Zone EDGES are what a reader acts on, so they are compared exactly. The bar
indices matter just as much and are easier to get subtly wrong: the base is
found by walking BACKWARDS from the impulse start while bars stay quiet, and an
off-by-one there moves the whole band without changing anything else about the
zone.

`confirmed_i` is checked explicitly because it is the causality guarantee: a
zone is only knowable once its impulse has FINISHED, never at the base it was
built from. A port that confirmed at the base would look right and be reading
the future.

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

from sim.indicators import atr as atr_series
from sim.tl.supply_demand import DEMAND, SUPPLY, SDParams, detect

HERE = os.path.dirname(os.path.abspath(__file__))
FIX = os.path.join(HERE, 'fixtures')
TOL = 1e-9
S_TOL = 0.011          # strength is rounded to 2dp on both sides


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


def _tf_of(stem):
    return '1h' if stem.endswith('_1h') else '15m'


def _python(csv, tf):
    df = pd.read_csv(csv, parse_dates=['t']).set_index('t')
    df = df.iloc[:-1]                   # exporter drops the forming bar
    return df, detect(df, tf, atr_series(df, 14), SDParams())


@pytest.mark.parametrize('stem,csv,expected', CASES, ids=IDS)
def test_same_zones(stem, csv, expected):
    tf = _tf_of(stem)
    _, mine = _python(csv, tf)
    theirs = json.load(open(expected, encoding='utf-8'))['sd_zones']

    assert len(mine) == len(theirs), (
        'python found %d zones, js found %d' % (len(mine), len(theirs)))

    for k, (a, b) in enumerate(zip(mine, theirs)):
        w = '%s zone %d' % (stem, k)
        assert a.kind == b['kind'], w
        assert a.low == pytest.approx(b['low'], rel=1e-9, abs=TOL), w + ' low'
        assert a.high == pytest.approx(b['high'], rel=1e-9, abs=TOL), w + ' high'
        assert a.mid == pytest.approx(b['mid'], rel=1e-9, abs=TOL), w + ' mid'
        # the backwards base walk is the easiest thing to get off by one
        assert a.base_i0 == b['base_i0'], w + ' base start'
        assert a.base_i1 == b['base_i1'], w + ' base end'
        assert a.base_bars == b['base_bars'], w
        assert a.impulse_i1 == b['impulse_i1'], w
        assert a.t_base == b['t_base'] and a.t_confirmed == b['t_confirmed'], w
        assert a.impulse_atr == pytest.approx(b['impulse_atr'], rel=1e-9, abs=TOL), w
        assert a.width_atr == pytest.approx(b['width_atr'], rel=1e-9, abs=TOL), w
        assert a.touches == b['touches'], w
        assert a.fresh == b['fresh'], w
        assert abs(a.strength - b['strength']) <= S_TOL, w + ' strength'


@pytest.mark.parametrize('stem,csv,expected', CASES, ids=IDS)
def test_confirmed_after_the_impulse_not_the_base(stem, csv, expected):
    """
    The causality guarantee. A zone becomes knowable when its impulse ENDS; a
    port that confirmed at the base would be reading the future by however many
    bars the impulse took, and every other field would still look correct.
    """
    tf = _tf_of(stem)
    _, mine = _python(csv, tf)
    theirs = json.load(open(expected, encoding='utf-8'))['sd_zones']
    if not mine:
        pytest.skip('no zones on this fixture')
    for k, (a, b) in enumerate(zip(mine, theirs)):
        w = '%s zone %d' % (stem, k)
        assert a.confirmed_i == b['confirmed_i'], w
        assert a.confirmed_i == a.impulse_i1, w + ': confirmed away from the impulse end'
        assert a.confirmed_i > a.base_i1, (
            '%s: confirmed at bar %d but its base ends at %d — that is the future'
            % (w, a.confirmed_i, a.base_i1))


@pytest.mark.parametrize('stem,csv,expected', CASES, ids=IDS)
def test_zone_invariants(stem, csv, expected):
    """Properties a shared bug in both ports would still have to satisfy."""
    tf = _tf_of(stem)
    df, mine = _python(csv, tf)
    if not mine:
        pytest.skip('no zones on this fixture')
    p = SDParams()
    a_end = atr_series(df, 14)[len(df) - 1]
    last = float(df['close'].iloc[-1])

    for k, z in enumerate(mine):
        w = '%s zone %d' % (stem, k)
        assert z.high >= z.low, w
        assert z.low <= z.mid <= z.high, w
        assert 0 < z.width_atr <= p.max_width_atr, w
        assert p.min_base_bars <= z.base_bars <= p.max_base_bars, w
        assert z.impulse_atr >= p.impulse_atr, w + ': impulse below its own bar'
        assert not z.broken, w + ': a broken zone should have been dropped'
        assert z.fresh == (z.touches == 0), w
        assert z.distance_atr(last, a_end) <= p.max_distance_atr + 1e-9, w
        assert z.kind in (DEMAND, SUPPLY), w

    # deduped: two bases at the same price are one zone
    for i in range(len(mine)):
        for j in range(i + 1, len(mine)):
            assert abs(mine[i].mid - mine[j].mid) > 0.5 * a_end, (
                '%s: zones %d and %d are the same base' % (stem, i, j))


def test_impulse_origin_differs_from_pivot_clustering():
    """
    The two detectors are supposed to find DIFFERENT things — an impulse origin
    can be a level price visited once and ran from, which pivot clustering
    cannot see. If they returned the same bands, porting this separately bought
    nothing.
    """
    from sim.tl.zones import ZoneParams
    from sim.tl.zones import detect as detect_pivot

    csv = os.path.join(FIX, 'bars_XAUUSDa_1h.csv')
    df = pd.read_csv(csv, parse_dates=['t']).set_index('t').iloc[:-1]
    a = atr_series(df, 14)
    sd = detect(df, '1h', a, SDParams())
    pv = detect_pivot(df, len(df) - 1, '1h', a, ZoneParams())
    if not sd or not pv:
        pytest.skip('one detector found nothing on this fixture')

    tol = 0.5 * a[len(df) - 1]
    shared = sum(1 for z in sd if any(abs(z.mid - q.mid) <= tol for q in pv))
    assert shared < len(sd), (
        'every impulse-origin zone coincides with a pivot-cluster zone — the '
        'two detectors are not finding different structure')
