"""
Look-ahead protection, verified end to end.

The spec asks for this to be a property of the ENGINE, not a test — and it is:
BarView raises on future access, pivots carry `confirmed_i`, and MTF alignment is
computed from bar CLOSE times with a strict guard. These tests exist to prove
those mechanisms actually work, and to fail loudly if a future refactor removes
one of them.

The important test is `test_features_are_identical_when_the_future_is_removed`:
it rebuilds the whole multi-timeframe feature pipeline on TRUNCATED history and
demands that the row at bar k is bit-identical to the row at bar k built from the
full history. If any part of the pipeline — pivots, lines, lifecycle, quality,
regime, or MTF projection — peeked forward, that row would change.

    python -m pytest tests/test_lookahead.py -q
"""

import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sim.tl import build
from sim.tl.mtf import TF_MS, LookAheadViolation, align_index
from sim.tl.pivots import find_pivots, pivots_confirmed_by

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fixtures')


def synth(n, tf, start='2024-01-01', seed=5):
    """A deterministic series; exact prices do not matter for these properties."""
    rng = np.random.default_rng(seed)
    step = pd.Timedelta(milliseconds=TF_MS[tf])
    idx = pd.date_range(start, periods=n, freq=step)
    close = 100 * np.exp(rng.normal(0, 0.002, n).cumsum())
    open_ = np.concatenate([[close[0]], close[:-1]])
    span = np.abs(rng.normal(0, 0.002, n)) * close
    return pd.DataFrame({
        'open': open_, 'high': np.maximum(open_, close) + span,
        'low': np.minimum(open_, close) - span, 'close': close,
        'tick_volume': rng.integers(10, 100, n).astype(float),
        'spread': np.full(n, 10.0),
    }, index=idx)


# --------------------------------------------------------------------------- #
def test_alignment_never_exposes_an_unclosed_higher_timeframe_bar():
    ex = synth(2000, '15m').index
    htf = synth(200, '4h').index
    pos = align_index(ex, '15m', htf, '4h')

    ex_close = ex.astype('int64') // 10**6 + TF_MS['15m']
    htf_close = htf.astype('int64') // 10**6 + TF_MS['4h']
    used = pos >= 0
    assert (htf_close[pos[used]] <= ex_close[used]).all(), \
        'an aligned 4H bar had not closed when the 15M bar closed'


def test_alignment_lags_by_the_expected_amount():
    """
    The honest cost of the guard: inside a 4H candle the 4H context is the
    PREVIOUS candle's. If the lag were zero, the guard would not be working.
    """
    ex = synth(2000, '15m').index
    htf = synth(200, '4h').index
    pos = align_index(ex, '15m', htf, '4h')
    changes = np.diff(pos)
    gaps = np.diff(np.where(changes != 0)[0])
    assert len(gaps) > 5
    # a 4H bar spans 16 15M bars, so the context updates every 16 bars
    assert np.median(gaps) == pytest.approx(TF_MS['4h'] / TF_MS['15m'], rel=0.2)


def test_the_naive_open_time_alignment_is_caught():
    """
    The classic bug is aligning on OPEN times, which hands a 15M bar the 4H
    candle it is still inside. Simulating that mistake must trip the guard.
    """
    ex = synth(500, '15m').index
    htf = synth(50, '4h').index
    ex_ms = ex.astype('int64') // 10**6
    htf_ms = htf.astype('int64') // 10**6
    naive = np.searchsorted(htf_ms, ex_ms, side='right') - 1     # open-time match

    correct = align_index(ex, '15m', htf, '4h')
    assert (naive >= correct).all()
    assert (naive > correct).any(), 'the naive alignment must differ somewhere'

    # and it does what the guard exists to prevent: reads an unclosed bar
    htf_close = htf_ms + TF_MS['4h']
    ex_close = ex_ms + TF_MS['15m']
    live = naive >= 0
    assert (htf_close[naive[live]] > ex_close[live]).any(), \
        'the naive alignment should expose at least one unclosed bar'


def test_pivots_are_not_visible_before_they_confirm():
    df = synth(600, '15m')
    highs, lows = find_pivots(df['high'], df['low'], 3)
    assert highs and lows
    for p in highs + lows:
        assert p['confirmed_i'] == p['i'] + 3
    # nothing confirmed by its own bar
    at = pivots_confirmed_by(highs, 100)
    assert all(p['i'] <= 97 for p in at), 'a pivot leaked before its confirmation bar'


@pytest.mark.parametrize('cut', [700, 900, 1100])
def test_features_are_identical_when_the_future_is_removed(cut):
    """
    The end-to-end proof. Build features from the full history, then rebuild from
    history truncated at `cut`, and compare the final row. Any forward-looking
    step anywhere in the pipeline would change it.
    """
    bars = {'15m': synth(1300, '15m', seed=11)}
    bars['1h'] = _resample(bars['15m'], '1h')
    bars['4h'] = _resample(bars['15m'], '4h')

    full, _, _ = build(bars, '15m', ('1h', '4h'))
    ts = bars['15m'].index[cut]

    cut_bars = {'15m': bars['15m'].iloc[:cut + 1]}
    end = cut_bars['15m'].index[-1]
    cut_bars['1h'] = bars['1h'][bars['1h'].index <= end]
    cut_bars['4h'] = bars['4h'][bars['4h'].index <= end]
    partial, _, _ = build(cut_bars, '15m', ('1h', '4h'))

    a = full.loc[ts]
    b = partial.loc[ts]

    # line ids are sequence numbers and legitimately differ; everything a
    # strategy actually reads must not
    numeric = [c for c in full.columns
               if not c.endswith('_trendline_id') and full[c].dtype != object]
    for col in numeric:
        x, y = float(a[col]) if a[col] is not None else np.nan, \
               float(b[col]) if b[col] is not None else np.nan
        if np.isnan(x) and np.isnan(y):
            continue
        assert x == pytest.approx(y, rel=1e-9, abs=1e-9), (
            f'{col} at {ts} changed when the future was removed '
            f'({y} with truncation vs {x} with full history) — something is '
            f'reading ahead')

    for col in ('15m_regime', '4h_regime', '15m_trend_direction', '4h_trend_direction'):
        assert a[col] == b[col], f'{col} changed when the future was removed'


def _resample(df, rule):
    out = df.resample(rule, label='left', closed='left').agg(
        {'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last',
         'tick_volume': 'sum', 'spread': 'mean'}).dropna(subset=['open'])
    return out
