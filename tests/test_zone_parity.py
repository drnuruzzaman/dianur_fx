"""
Zone parity — js/chart/zones.js against sim/tl/zones.py.

A zone is a band, so the numbers that matter are its EDGES: a one-tick
difference in `low` moves where a stop would sit. The pivot set feeding it comes
from `findPivots`, which is already parity-tested, so a divergence here is a
clustering or scoring divergence and the comparison is written to say which.

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
from sim.tl.zones import RESISTANCE, SUPPORT, ZoneParams, detect

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


def _python_zones(csv, tf):
    df = pd.read_csv(csv, parse_dates=['t']).set_index('t')
    df = df.iloc[:-1]                       # exporter drops the forming bar
    a = atr_series(df, 14)
    return df, detect(df, len(df) - 1, tf, a, ZoneParams())


def _tf_of(stem):
    return '1h' if stem.endswith('_1h') else '15m'


@pytest.mark.parametrize('stem,csv,expected', CASES, ids=IDS)
def test_same_zones_found(stem, csv, expected):
    tf = _tf_of(stem)
    _, mine = _python_zones(csv, tf)
    theirs = json.load(open(expected, encoding='utf-8'))['zones']

    assert len(mine) == len(theirs), (
        'python found %d zones, js found %d' % (len(mine), len(theirs)))

    for k, (a, b) in enumerate(zip(mine, theirs)):
        where = '%s zone %d' % (stem, k)
        # edges first: this is where a stop would go
        assert a.low == pytest.approx(b['low'], rel=1e-9, abs=TOL), where + ' low'
        assert a.high == pytest.approx(b['high'], rel=1e-9, abs=TOL), where + ' high'
        assert a.mid == pytest.approx(b['mid'], rel=1e-9, abs=TOL), where + ' mid'
        assert a.touches == b['touches'], where
        assert a.from_highs == b['from_highs'], where
        assert a.from_lows == b['from_lows'], where
        assert a.first_i == b['first_i'] and a.last_i == b['last_i'], where
        assert a.first_t == b['first_t'] and a.last_t == b['last_t'], where
        assert a.width_atr == pytest.approx(b['width_atr'], rel=1e-9, abs=TOL), where
        # reaction feeds 17 of the 100 strength points, so a divergence here
        # would surface as a strength mismatch WITHOUT saying which term drifted
        if b.get('reaction_atr') is not None and a.reaction_atr == a.reaction_atr:
            assert a.reaction_atr == pytest.approx(
                b['reaction_atr'], rel=1e-9, abs=TOL), where + ' reaction'
        assert abs(a.strength - b['strength']) <= S_TOL, (
            '%s strength: python=%s js=%s' % (where, a.strength, b['strength']))


@pytest.mark.parametrize('stem,csv,expected', CASES, ids=IDS)
def test_same_levels_and_role(stem, csv, expected):
    """
    The pivot prices a zone was built FROM, and the role it reports at the
    current price. Role is derived rather than stored, so it is the one field
    that can diverge while every stored number agrees.
    """
    tf = _tf_of(stem)
    df, mine = _python_zones(csv, tf)
    theirs = json.load(open(expected, encoding='utf-8'))['zones']
    if not mine:
        pytest.skip('no zones on this fixture')
    last_close = float(df['close'].iloc[-1])

    for k, (a, b) in enumerate(zip(mine, theirs)):
        where = '%s zone %d' % (stem, k)
        assert len(a.levels) == len(b['levels']), where + ' level count'
        for x, y in zip(a.levels, b['levels']):
            assert x == pytest.approx(y, rel=1e-9, abs=TOL), where + ' level'
        assert a.role_at(last_close) == b['role_now'], where + ' role'


@pytest.mark.parametrize('stem,csv,expected', CASES, ids=IDS)
def test_zone_invariants(stem, csv, expected):
    """Properties a shared bug would still have to satisfy."""
    tf = _tf_of(stem)
    df, mine = _python_zones(csv, tf)
    if not mine:
        pytest.skip('no zones on this fixture')
    p = ZoneParams()
    a = atr_series(df, 14)[len(df) - 1]
    last_close = float(df['close'].iloc[-1])

    for k, z in enumerate(mine):
        where = '%s zone %d' % (stem, k)
        assert z.high >= z.low, where
        assert z.low <= z.mid <= z.high, where
        assert z.width_atr <= p.max_width_atr, where
        assert z.touches >= p.min_touches, where
        assert z.from_highs + z.from_lows == z.touches, where
        assert z.strength >= p.min_strength, where
        assert z.distance_atr(last_close, a) <= p.max_distance_atr + 1e-9, where
        # every forming level must lie inside the band it produced
        for lv in z.levels:
            assert z.low - TOL <= lv <= z.high + TOL, where + ' level outside band'
        # the role flip is the whole point of deriving role from price
        assert z.role_at(z.low - 10 * a) == RESISTANCE, where
        assert z.role_at(z.high + 10 * a) == SUPPORT, where


def test_zones_do_not_overlap():
    """
    Clustering is agglomerative on sorted prices, so two zones sharing a price
    would mean the same level was assigned twice -- a real clustering bug rather
    than a cosmetic one.
    """
    for stem, csv, _ in CASES:
        _, zones = _python_zones(csv, _tf_of(stem))
        ordered = sorted(zones, key=lambda z: z.low)
        for i in range(len(ordered) - 1):
            assert ordered[i].high < ordered[i + 1].low + TOL, (
                '%s: zones %s and %s overlap' % (stem, ordered[i].id, ordered[i + 1].id))
