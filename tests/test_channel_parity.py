"""
Channel parity — js/chart/channels.js against sim/tl/channels.py.

Channels are the first structure in this project that is DERIVED from other
detected structure rather than from bars directly, which makes drift easier: a
one-line difference in the line population changes which pairs are considered,
and the corridor you see moves without any channel code being wrong. So the
comparison is channel-for-channel on everything a reader or a strategy could
act on -- kind, rails, containment, touches, score, and where the rails
actually sit right now.

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
from sim.tl.channels import ChannelParams, detect
from sim.tl.engine import Params, TrendlineEngine
from sim.tl.mtf import TF_MS

HERE = os.path.dirname(os.path.abspath(__file__))
FIX = os.path.join(HERE, 'fixtures')

# Long recursions (Wilder ATR) accumulate differently across the two runtimes;
# the other parity tests use the same band.
TOL = 1e-9
# quality_score is rounded to 2dp on both sides before comparison
Q_TOL = 0.011


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


def _python_channels(csv, tf):
    df = pd.read_csv(csv, parse_dates=['t']).set_index('t')
    df = df.iloc[:-1]                       # the exporter drops the forming bar
    snaps = TrendlineEngine(tf, TF_MS[tf], Params()).walk(df)
    last = snaps[-1]
    live = [l for l in last.live if l.is_tradeable]
    a = atr_series(df, 14)
    chans = detect(live, df, a, len(df) - 1, tf, ChannelParams())
    return df, chans


@pytest.mark.parametrize('stem,csv,expected', CASES, ids=IDS)
def test_same_channels_found(stem, csv, expected):
    tf = _tf_of(stem)
    _, mine = _python_channels(csv, tf)
    theirs = json.load(open(expected, encoding='utf-8'))['channels']

    assert len(mine) == len(theirs), (
        'python found %d channels, js found %d' % (len(mine), len(theirs)))

    for k, (a, b) in enumerate(zip(mine, theirs)):
        where = '%s channel %d' % (stem, k)
        assert a.kind == b['kind'], where
        assert a.type == b['type'], where
        assert a.direction.value == b['direction'], where
        assert a.lower.id == b['lower_id'], where
        assert a.upper.id == b['upper_id'], where
        assert a.touches_lower == b['touches_lower'], where
        assert a.touches_upper == b['touches_upper'], where
        assert a.bars == b['bars'], where
        assert (a.projected_side or None) == (b['projected_side'] or None), where


@pytest.mark.parametrize('stem,csv,expected', CASES, ids=IDS)
def test_same_geometry_and_score(stem, csv, expected):
    """
    Where the rails SIT is the part a reader acts on. Slope and intercept could
    both differ while the drawn corridor stayed identical, and vice versa, so
    the rails are compared at the current bar as well as by their parameters.
    """
    tf = _tf_of(stem)
    df, mine = _python_channels(csv, tf)
    theirs = json.load(open(expected, encoding='utf-8'))['channels']
    if not mine:
        pytest.skip('no channels on this fixture')

    t_now = int(df.index[-1].value // 1_000_000)

    for k, (a, b) in enumerate(zip(mine, theirs)):
        where = '%s channel %d' % (stem, k)
        assert a.slope == pytest.approx(b['slope'], rel=1e-9, abs=1e-12), where
        assert a.t_start == b['t_start'], where
        assert a.t_end == b['t_end'], where
        assert a.width_atr == pytest.approx(b['width_atr'], rel=1e-9, abs=TOL), where
        assert a.containment == pytest.approx(b['containment'], rel=1e-9, abs=TOL), where
        assert abs(a.quality_score - b['quality_score']) <= Q_TOL, where

        assert a.lower_at(t_now) == pytest.approx(b['lower_now'], rel=1e-9, abs=TOL), where
        assert a.upper_at(t_now) == pytest.approx(b['upper_now'], rel=1e-9, abs=TOL), where
        assert a.median_at(t_now) == pytest.approx(b['median_now'], rel=1e-9, abs=TOL), where


@pytest.mark.parametrize('stem,csv,expected', CASES, ids=IDS)
def test_channel_invariants(stem, csv, expected):
    """
    Properties that must hold whatever the two implementations agree on -- a
    shared bug would pass the parity tests above and fail here.
    """
    tf = _tf_of(stem)
    df, mine = _python_channels(csv, tf)
    if not mine:
        pytest.skip('no channels on this fixture')
    t_now = int(df.index[-1].value // 1_000_000)
    p = ChannelParams()

    for k, c in enumerate(mine):
        where = '%s channel %d' % (stem, k)
        lo, hi = c.lower_at(t_now), c.upper_at(t_now)
        assert hi > lo, '%s: upper rail must sit above lower' % where
        med = c.median_at(t_now)
        assert lo < med < hi, '%s: median must lie inside the corridor' % where
        assert p.min_width_atr <= c.width_atr <= p.max_width_atr, where
        assert c.containment >= p.min_containment, where
        assert min(c.touches_lower, c.touches_upper) >= p.min_touches_each, where
        # position_at is the numeric form of "where in the corridor": it must
        # agree with the rails it is derived from
        assert c.position_at(t_now, lo) == pytest.approx(0.0, abs=1e-9), where
        assert c.position_at(t_now, hi) == pytest.approx(1.0, abs=1e-9), where
        assert c.position_at(t_now, med) == pytest.approx(0.5, abs=1e-9), where


def test_channels_are_deduped():
    """
    Two corridors within dedupe_atr of each other at the current bar are the
    same corridor found from different anchors. This was a real bug: the
    projected pass appended to the caller's list AND returned the channel, so
    every projected channel appeared twice.
    """
    for stem, csv, _ in CASES:
        tf = _tf_of(stem)
        df, chans = _python_channels(csv, tf)
        if len(chans) < 2:
            continue
        t_now = int(df.index[-1].value // 1_000_000)
        a = atr_series(df, 14)[len(df) - 1]
        tol = ChannelParams().dedupe_atr * a
        for i in range(len(chans)):
            for j in range(i + 1, len(chans)):
                same = (abs(chans[i].lower_at(t_now) - chans[j].lower_at(t_now)) <= tol
                        and abs(chans[i].upper_at(t_now) - chans[j].upper_at(t_now)) <= tol)
                assert not same, '%s: channels %d and %d are the same corridor' % (stem, i, j)
