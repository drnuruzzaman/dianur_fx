"""
Donchian correctness, proved BEFORE profitability is discussed.

A correctness gate, not an edge gate. Everything here would still have to pass
if the strategy lost money, and none of it says whether it makes any.

Four things:

  1. THE DEFINITION IS UNAMBIGUOUS.  Upper(t) = max(High[t-N .. t-1]), which
     EXCLUDES the current bar. A channel containing the bar being decided on can
     make a breakout arithmetically impossible -- High[t] can never exceed a
     maximum it is part of -- so this is not a stylistic choice.

  2. THE WORKED EXAMPLE.  N=5, highs 101 104 103 106 105 108: Upper[6] is
     max(101,104,103,106,105) = 106, and High[6] = 108 > 106 is exactly one
     breakout. Hardcoded, so a refactor that changes the window by one bar
     fails here rather than in a p-value six months later.

  3. NO LOOK-AHEAD.  The channel at bar i must be computable from bars < i, and
     recomputing it on a series TRUNCATED at i must give the identical value.

  4. AGAINST AN INDEPENDENT REFERENCE.  A deliberately naive brute-force
     Donchian, written from the definition rather than from the implementation,
     compared bar by bar on real data: upper, lower, and both breakout flags.

The two triggers -- Close[t] > Upper[t] and High[t] > Upper[t] -- are separate
hypotheses and are checked separately. Mixing them (entering on the touch while
measuring the channel on closes) is how a backtest ends up trading a rule nobody
could have followed.

    python -m pytest tests/test_donchian_correctness.py -q
"""

import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sim.core import BarView, Config, Simulator
from sim.strategies import BASELINES
from sim.strategies.donchian import Donchian


# --------------------------------------------------------------------------- #
# an independent reference, written from the DEFINITION                       #
# --------------------------------------------------------------------------- #
def reference_channel(high, low, n):
    """
    Brute force, one bar at a time, no pandas.

    Deliberately not vectorised and deliberately not sharing a line of code with
    the implementation: a reference that borrows the same rolling call would
    agree with a bug as readily as with correctness.
    """
    upper = [float('nan')] * len(high)
    lower = [float('nan')] * len(low)
    for t in range(len(high)):
        if t < n:                      # not enough history BEFORE t
            continue
        window_hi = high[t - n:t]      # t-n .. t-1, current bar excluded
        window_lo = low[t - n:t]
        upper[t] = max(window_hi)
        lower[t] = min(window_lo)
    return upper, lower


def reference_breaks(high, low, close, n, trigger):
    up, dn = reference_channel(high, low, n)
    long_break, short_break = [], []
    for t in range(len(high)):
        u, l = up[t], dn[t]
        px_up = high[t] if trigger == 'high' else close[t]
        px_dn = low[t] if trigger == 'high' else close[t]
        long_break.append(bool(u == u and px_up > u))     # u == u filters NaN
        short_break.append(bool(l == l and px_dn < l))
    return up, dn, long_break, short_break


def frame(high, low=None, close=None):
    n = len(high)
    low = low if low is not None else [h - 1 for h in high]
    close = close if close is not None else [h - 0.5 for h in high]
    return pd.DataFrame(
        {'open': close, 'high': high, 'low': low, 'close': close},
        index=pd.date_range('2024-01-01', periods=n, freq='4h'))


# --------------------------------------------------------------------------- #
# 1 + 2. the definition, and the worked example                               #
# --------------------------------------------------------------------------- #
def test_the_worked_example():
    """N=5, highs 101 104 103 106 105 108 -> Upper[6] = 106, one breakout."""
    highs = [101.0, 104.0, 103.0, 106.0, 105.0, 108.0]
    up, dn, longs, shorts = reference_breaks(
        highs, [h - 3 for h in highs], [h - 0.5 for h in highs], 5, 'high')
    # index 5 is the sixth bar
    assert up[5] == 106.0, 'Upper at the 6th bar must exclude that bar'
    assert highs[5] == 108.0 and 108.0 > 106.0
    assert sum(longs) == 1, 'exactly one breakout, got %d' % sum(longs)
    assert longs[5] is True


def test_the_channel_excludes_the_current_bar():
    """
    Including it makes a High breakout impossible: High[t] cannot exceed a
    maximum it is a member of. A rule that can never fire is worse than a wrong
    one, because the backtest reports zero trades and looks merely unlucky.
    """
    highs = [100.0, 101.0, 102.0, 103.0, 110.0]
    up_excl, _ = reference_channel(highs, [h - 1 for h in highs], 4)
    assert up_excl[4] == 103.0 and highs[4] > up_excl[4]

    inclusive = max(highs[1:5])           # what including the bar would give
    assert inclusive == 110.0
    assert not highs[4] > inclusive, 'inclusive window can never be broken'


@pytest.mark.parametrize('n', [3, 5, 20])
def test_implementation_channel_matches_the_reference(n):
    rng = np.random.default_rng(7)
    px = 2000 + np.cumsum(rng.normal(0, 5, 400))
    df = frame(list(px + 4), list(px - 4), list(px))
    got = Donchian(entry=n, exit=n).prepare(df)
    want_up, want_lo = reference_channel(list(df.high), list(df.low), n)
    for i in range(len(df)):
        a, b = got['hi'][i], want_up[i]
        assert (np.isnan(a) and np.isnan(b)) or a == pytest.approx(b, abs=1e-12), (
            'upper at %d: impl %r reference %r' % (i, a, b))
        a, b = got['lo'][i], want_lo[i]
        assert (np.isnan(a) and np.isnan(b)) or a == pytest.approx(b, abs=1e-12), (
            'lower at %d: impl %r reference %r' % (i, a, b))


# --------------------------------------------------------------------------- #
# 3. no look-ahead                                                            #
# --------------------------------------------------------------------------- #
def test_channel_is_unchanged_by_truncating_the_future():
    """
    The value at bar i must not depend on bars after i. Recomputed on a series
    that STOPS at i, it has to be identical -- which is the same audit
    tests/test_invariants.py runs over every indicator, applied here to the
    channel specifically because this is the series the entry reads.
    """
    rng = np.random.default_rng(3)
    px = 2000 + np.cumsum(rng.normal(0, 5, 260))
    df = frame(list(px + 3), list(px - 3), list(px))
    full = Donchian(entry=20, exit=10).prepare(df)
    for i in (40, 100, 199, 259):
        cut = Donchian(entry=20, exit=10).prepare(df.iloc[:i + 1])
        for key in ('hi', 'lo', 'exit_hi', 'exit_lo'):
            a, b = full[key][i], cut[key][i]
            assert (np.isnan(a) and np.isnan(b)) or a == pytest.approx(b, abs=1e-12), (
                '%s at %d changed when the future was removed: %r vs %r'
                % (key, i, a, b))


# --------------------------------------------------------------------------- #
# 4. the signal, against the reference, on real bars                          #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize('trigger', ['close', 'high'])
def test_breakout_flags_match_the_reference_on_real_bars(trigger):
    try:
        from sim.instruments import load
        df = load('XAUUSD.a', '4h', '2020-01-01', '2022-01-01')
    except Exception:                                     # noqa: BLE001
        pytest.skip('no bars on disk')
    df = df.iloc[:3000]
    n = 20
    strat = Donchian(entry=n, exit=10, trigger=trigger)
    series = {k: np.asarray(v, float) for k, v in strat.prepare(df).items()}
    arrays = (df['open'].to_numpy(float), df['high'].to_numpy(float),
              df['low'].to_numpy(float), df['close'].to_numpy(float),
              np.zeros(len(df)), np.zeros(len(df)), df.index.to_numpy())

    _u, _l, want_long, want_short = reference_breaks(
        list(df.high), list(df.low), list(df.close), n, trigger)

    checked = 0
    for i in range(strat.warmup, len(df)):
        intent = strat.on_bar(BarView(arrays, series, i), None)
        got_long = intent is not None and intent.side == 1
        got_short = intent is not None and intent.side == -1
        # the reference has no notion of "long wins the tie"; the implementation
        # checks long first, so only compare where the reference is unambiguous
        if want_long[i] and want_short[i]:
            continue
        assert got_long == want_long[i], 'long break at bar %d' % i
        assert got_short == want_short[i], 'short break at bar %d' % i
        checked += 1
    assert checked > 2000, 'only %d bars compared' % checked


def test_the_two_triggers_are_genuinely_different():
    """
    If 'high' and 'close' produced the same signals the split would be
    decoration. The touch must fire at least as often, and strictly more often
    on real data.
    """
    try:
        from sim.instruments import load
        df = load('XAUUSD.a', '4h', '2020-01-01', '2022-01-01')
    except Exception:                                     # noqa: BLE001
        pytest.skip('no bars on disk')

    counts = {}
    for trigger in ('close', 'high'):
        _u, _l, longs, shorts = reference_breaks(
            list(df.high), list(df.low), list(df.close), 20, trigger)
        counts[trigger] = sum(longs) + sum(shorts)
    assert counts['high'] > counts['close'], (
        'the intrabar touch fired %d times and the close %d -- the touch cannot '
        'be rarer' % (counts['high'], counts['close']))


def test_both_triggers_run_end_to_end():
    """A registered strategy that cannot complete a run is not a hypothesis."""
    try:
        from sim.instruments import load, spec
        df = load('XAUUSD.a', '4h', '2020-01-01', '2022-01-01')
        sp = spec('XAUUSD.a', '4h')
    except Exception:                                     # noqa: BLE001
        pytest.skip('no bars or spec on disk')
    for key in ('donchian', 'donchian_high'):
        res = Simulator(sp, fx=None, config=Config(risk_pct=0.5, apply_swap=False)).run(
            df, BASELINES[key](), 'XAUUSD.a', '4h')
        assert len(res.trades) > 20, '%s produced %d trades' % (key, len(res.trades))
        assert res.trades.target_price.isna().all(), (
            '%s emitted a target; these baselines have no take-profit' % key)
