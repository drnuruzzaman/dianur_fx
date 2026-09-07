"""
Segment parity — js/chart/segments.js against sim/tl/segments.py.

Segmentation is the one structure here whose output is a PARTITION, so the
failure mode is different from the others: a single bar assigned to the wrong
episode shifts every boundary after it. The comparison is therefore exact on
indices, and the invariant test checks that the partition is actually a
partition -- contiguous, non-overlapping, gapless.

Regenerate the fixtures after an intentional JS change:

    node tools/parity_export.mjs
"""

import glob
import json
import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sim.tl.segments import SegmentParams, build

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


def _python_segments(csv):
    df = pd.read_csv(csv, parse_dates=['t']).set_index('t')
    df = df.iloc[:-1]                       # exporter drops the forming bar
    return df, build(df)


@pytest.mark.parametrize('stem,csv,expected', CASES, ids=IDS)
def test_same_segments(stem, csv, expected):
    _, mine = _python_segments(csv)
    theirs = json.load(open(expected, encoding='utf-8'))['segments']

    assert len(mine) == len(theirs), (
        'python found %d segments, js found %d' % (len(mine), len(theirs)))

    for k, (a, b) in enumerate(zip(mine, theirs)):
        where = '%s segment %d' % (stem, k)
        assert a.kind == b['kind'], where
        assert a.label == b['label'], where
        # boundaries: one bar out here shifts everything downstream
        assert a.i0 == b['i0'], where + ' i0'
        assert a.i1 == b['i1'], where + ' i1'
        assert a.t0 == b['t0'] and a.t1 == b['t1'], where + ' times'
        assert a.bars == b['bars'], where
        assert a.closed == b['closed'], where
        assert a.high == pytest.approx(b['high'], rel=1e-9, abs=TOL), where
        assert a.low == pytest.approx(b['low'], rel=1e-9, abs=TOL), where
        assert a.ret_atr == pytest.approx(b['ret_atr'], rel=1e-6, abs=1e-6), where


@pytest.mark.parametrize('stem,csv,expected', CASES, ids=IDS)
def test_segments_are_a_partition(stem, csv, expected):
    """
    Contiguous, non-overlapping, gapless, and long enough. A shared bug in the
    absorb/fuse logic would pass the parity test above and fail here.
    """
    df, segs = _python_segments(csv)
    if not segs:
        pytest.skip('no segments')
    p = SegmentParams()

    for k, s in enumerate(segs):
        where = '%s segment %d' % (stem, k)
        assert s.i1 >= s.i0, where
        assert s.bars == s.i1 - s.i0 + 1, where + ' bars must match its span'
        assert s.low <= s.high, where
        if k:
            prev = segs[k - 1]
            assert s.i0 == prev.i1 + 1, (
                '%s: gap or overlap after segment %d' % (stem, k - 1))
            assert s.kind != prev.kind, (
                '%s: adjacent segments %d and %d share a kind and should have '
                'been fused' % (stem, k - 1, k))

    # only the final segment is open
    assert all(s.closed for s in segs[:-1]), stem
    assert segs[-1].closed is False, stem
    # the last segment must reach the last bar of the frame it was built on
    assert segs[-1].i1 == len(df) - 1, stem


def test_short_runs_are_absorbed():
    """
    min_bars is the guard against confetti. The only segment allowed to be
    shorter is the last one, which is still forming.
    """
    for stem, csv, _ in CASES:
        _, segs = _python_segments(csv)
        p = SegmentParams()
        for s in segs[:-1]:
            assert s.bars >= p.min_bars, (
                '%s: segment of %d bars survived a %d-bar minimum'
                % (stem, s.bars, p.min_bars))
