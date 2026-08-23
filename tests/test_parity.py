"""
Parity — the Python port against the REAL browser modules.

tools/parity_export.mjs runs js/chart/indicators.js and js/chart/trendlines.js
under node over a fixed slice of real bars and writes the result to
tests/fixtures/expected_*.json. This compares the Python implementations against
that output, value by value.

Why it matters: the chart and the backtest must agree. If they drift, a strategy
can backtest beautifully and behave differently on screen, and you would have no
way to tell which was wrong. Here, drift fails a test.

Regenerate the fixtures whenever the JS changes ON PURPOSE:

    node tools/parity_export.mjs

A diff in the regenerated JSON is exactly the signal you want to see.

    python -m pytest tests/test_parity.py -q
"""

import glob
import json
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sim import indicators as ind
from sim.tl.pivots import find_pivots

HERE = os.path.dirname(os.path.abspath(__file__))
FIX = os.path.join(HERE, 'fixtures')

# Floating point crosses a language boundary here: JSON round-trips doubles
# exactly, but the two runtimes accumulate long recursions (EMA, Wilder) in
# their own order, so an exact == is the wrong test. This is tight enough that
# a genuine formula difference cannot hide inside it.
RTOL = 1e-9
ATOL = 1e-9


def cases():
    out = []
    for path in sorted(glob.glob(os.path.join(FIX, 'expected_*.json'))):
        stem = os.path.basename(path)[len('expected_'):-len('.json')]
        bars_path = os.path.join(FIX, 'bars_%s.csv' % stem)
        if os.path.exists(bars_path):
            out.append((stem, bars_path, path))
    return out


CASES = cases()
IDS = [c[0] for c in CASES]


def load_case(bars_path, expected_path):
    df = pd.read_csv(bars_path, parse_dates=['t']).set_index('t')
    doc = json.load(open(expected_path, encoding='utf-8'))
    assert len(df) == doc['bars'], 'fixture bars and expected output disagree'
    return df, doc


def same(actual, expected, label):
    """Compare a numeric series, treating JS null and numpy NaN as equal."""
    a = np.asarray([np.nan if v is None else float(v) for v in expected], dtype=float)
    b = np.asarray(actual, dtype=float)
    assert len(a) == len(b), f'{label}: length {len(b)} vs {len(a)}'
    both_nan = np.isnan(a) & np.isnan(b)
    only_one = np.isnan(a) ^ np.isnan(b)
    if only_one.any():
        i = int(np.argmax(only_one))
        raise AssertionError(f'{label}: NaN mismatch at {i} '
                             f'(python={b[i]!r} js={a[i]!r})')
    ok = both_nan | np.isclose(a, b, rtol=RTOL, atol=ATOL)
    if not ok.all():
        i = int(np.argmax(~ok))
        raise AssertionError(f'{label}: differs at index {i}: '
                             f'python={b[i]!r} js={a[i]!r} (diff={b[i] - a[i]:.3e})')


@pytest.mark.parametrize('stem,bars_path,expected_path', CASES, ids=IDS)
@pytest.mark.parametrize('strength', [2, 3, 6])
def test_pivots_match_the_js(stem, bars_path, expected_path, strength):
    df, doc = load_case(bars_path, expected_path)
    closed = df.iloc[:-1]                       # the JS excludes the forming bar
    highs, lows = find_pivots(closed['high'], closed['low'], strength)
    exp = doc['pivots'][str(strength)]
    assert [p['i'] for p in highs] == [e[0] for e in exp['highs']], 'pivot high indexes'
    assert [p['i'] for p in lows] == [e[0] for e in exp['lows']], 'pivot low indexes'
    same([p['price'] for p in highs], [e[2] for e in exp['highs']], 'pivot high prices')
    same([p['price'] for p in lows], [e[2] for e in exp['lows']], 'pivot low prices')


@pytest.mark.parametrize('stem,bars_path,expected_path', CASES, ids=IDS)
def test_moving_averages_match_the_js(stem, bars_path, expected_path):
    df, doc = load_case(bars_path, expected_path)
    same(ind.ema(df['close'], 21), doc['studies']['ema'][0]['data'], 'EMA 21')
    same(ind.sma(df['close'], 50), doc['studies']['sma'][0]['data'], 'SMA 50')


@pytest.mark.parametrize('stem,bars_path,expected_path', CASES, ids=IDS)
def test_bollinger_matches_the_js(stem, bars_path, expected_path):
    df, doc = load_case(bars_path, expected_path)
    upper, basis, lower = ind.bollinger(df, 20, 2.0)
    plots = {p['label']: p for p in doc['studies']['bb'] if p['type'] == 'line'}
    same(upper, plots['upper']['data'], 'BB upper')
    same(basis, plots['basis']['data'], 'BB basis')
    same(lower, plots['lower']['data'], 'BB lower')


@pytest.mark.parametrize('stem,bars_path,expected_path', CASES, ids=IDS)
def test_rsi_matches_the_js(stem, bars_path, expected_path):
    df, doc = load_case(bars_path, expected_path)
    # The plain 'rsi' study was folded into 'rsidiv' (a superset), so this now
    # validates the RSI the chart actually draws — which is also the series
    # divergence detection compares at each pivot.
    line = next(p for p in doc['studies']['rsidiv']
                if p['type'] == 'line' and str(p['label']).startswith('RSI'))
    same(ind.rsi(df, 14), line['data'], 'RSI 14 (rsidiv)')


@pytest.mark.parametrize('stem,bars_path,expected_path', CASES, ids=IDS)
def test_macd_matches_the_js(stem, bars_path, expected_path):
    df, doc = load_case(bars_path, expected_path)
    line, signal, hist = ind.macd(df, 12, 26, 9)
    plots = {p['label']: p for p in doc['studies']['macd']}
    same(line, plots['macd']['data'], 'MACD line')
    same(signal, plots['signal']['data'], 'MACD signal')
    same(hist, plots['hist']['data'], 'MACD histogram')


@pytest.mark.parametrize('stem,bars_path,expected_path', CASES, ids=IDS)
def test_atr_and_stochastic_match_the_js(stem, bars_path, expected_path):
    df, doc = load_case(bars_path, expected_path)
    same(ind.atr(df, 14), doc['studies']['atr'][0]['data'], 'ATR 14')
    k, d = ind.stochastic(df, 14, 3, 3)
    plots = {p['label']: p for p in doc['studies']['stoch']}
    same(k, plots['%K']['data'], 'Stoch %K')
    same(d, plots['%D']['data'], 'Stoch %D')


@pytest.mark.parametrize('stem,bars_path,expected_path', CASES, ids=IDS)
def test_vwap_and_heikin_match_the_js(stem, bars_path, expected_path):
    df, doc = load_case(bars_path, expected_path)
    same(ind.vwap_session(df), doc['studies']['vwap'][0]['data'], 'VWAP')
    ha = ind.heikin_ashi(df)
    exp = np.asarray(doc['heikin'], dtype=float)
    for col, j in (('open', 0), ('high', 1), ('low', 2), ('close', 3)):
        same(ha[col], exp[:, j], f'Heikin {col}')


@pytest.mark.parametrize('stem,bars_path,expected_path', CASES, ids=IDS)
def test_rsi_divergences_match_the_js(stem, bars_path, expected_path):
    """
    The chart must draw exactly what the backtester would trade. A divergence
    visible on screen that sim/divergence.py never found would quietly teach you
    to trust a signal the numbers never tested.
    """
    df, doc = load_case(bars_path, expected_path)
    from sim.divergence import find_divergences
    mine = find_divergences(df)
    theirs = doc['divergences']

    assert len(mine) == len(theirs), (
        f'python found {len(mine)} divergences, the JS found {len(theirs)}')
    for a, b in zip(mine, theirs):
        assert a['kind'] == b['kind']
        assert a['pivot_i'] == b['pivotI'], 'divergence anchored on a different swing'
        assert a['prev_i'] == b['prevI']
        assert a['confirmed_i'] == b['confirmedI'], 'confirmation bar differs'
        assert a['bars_apart'] == b['barsApart']
        same([a['price'], a['prev_price']], [b['price'], b['prevPrice']], 'div prices')
        same([a['rsi'], a['prev_rsi']], [b['rsi'], b['prevRsi']], 'div RSI values')


@pytest.mark.parametrize('stem,bars_path,expected_path', CASES, ids=IDS)
def test_divergence_confirmation_lags_the_swing(stem, bars_path, expected_path):
    """
    A pivot is only visible `strength` bars later. If these were equal, the
    chart would be implying you could have traded the exact swing low.
    """
    df, doc = load_case(bars_path, expected_path)
    assert doc['divergences'], 'fixture has no divergences to check'
    for d in doc['divergences']:
        assert d['confirmedI'] == d['pivotI'] + 3, 'confirmation lag is missing'


@pytest.mark.parametrize('stem,bars_path,expected_path', CASES, ids=IDS)
def test_the_fixture_is_the_real_js_output(stem, bars_path, expected_path):
    """
    Guard the guard: an empty or stale fixture would make every test above pass
    for the wrong reason.
    """
    df, doc = load_case(bars_path, expected_path)
    assert doc['bars'] > 500, 'fixture too small to be meaningful'
    assert any(p['type'] == 'line' and p['data'] for p in doc['studies']['rsidiv']), \
        'fixture has no RSI output'
    assert any(v is not None for v in doc['studies']['macd'][1]['data'])
    assert doc['pivots']['3']['highs'], 'fixture found no pivots'
    first_t = pd.Timestamp(doc['first_t'], unit='ms')
    assert abs((first_t - df.index[0]).total_seconds()) < 1, 'fixture is from other bars'
