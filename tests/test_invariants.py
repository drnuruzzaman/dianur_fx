"""
Gate 2 — invariants and causality.

Known answers prove the engine is right on cases I thought of. These prove it
stays coherent on cases I did not: random walks, both baselines, real specs.

The causality audit is the important one. Vectorised indicators are computed
over the whole series for speed, which is safe only if each value depends on
bars up to its own index. This recomputes each series on a TRUNCATED history and
demands the value at that index be identical — so any look-ahead inside an
indicator fails a test instead of quietly inflating a backtest.

    python -m pytest tests/test_invariants.py -q
"""

import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sim.core import CENT, Config, Simulator
from sim.strategies import Donchian, EmaCross, MeanRevert

SPECS = {
    'gold_like': {
        'digits': 2, 'point': 0.01, 'contract_size': 100.0, 'tick_size': 0.01,
        'tick_value': 1.39, 'volume_min': 0.01, 'volume_step': 0.01, 'volume_max': 50.0,
        'currency_base': 'XAU', 'currency_profit': 'AUD', 'currency_margin': 'XAU',
        'swap_mode': 1, 'swap_long_points': -82.92, 'swap_short_points': 30.23,
        'swap_rollover_3days_weekday': 2, '_symbol': 'GOLDLIKE', '_tf': '1h',
    },
    'fx_like': {
        'digits': 3, 'point': 0.001, 'contract_size': 100000.0, 'tick_size': 0.001,
        'tick_value': 0.88, 'volume_min': 0.01, 'volume_step': 0.01, 'volume_max': 100.0,
        'currency_base': 'USD', 'currency_profit': 'AUD', 'currency_margin': 'USD',
        'swap_mode': 1, 'swap_long_points': 3.01, 'swap_short_points': -15.70,
        'swap_rollover_3days_weekday': 2, '_symbol': 'FXLIKE', '_tf': '1h',
    },
}


def random_walk(n=4000, start=100.0, vol=0.004, seed=1):
    """Deterministic walk with OHLC that respects high >= max(o,c) >= min(o,c) >= low."""
    rng = np.random.default_rng(seed)
    steps = rng.normal(0, vol, n).cumsum()
    close = start * np.exp(steps)
    open_ = np.concatenate([[start], close[:-1]])
    span = np.abs(rng.normal(0, vol, n)) * close
    high = np.maximum(open_, close) + span
    low = np.minimum(open_, close) - span
    idx = pd.date_range('2015-01-01', periods=n, freq='1h')
    return pd.DataFrame({'open': open_, 'high': high, 'low': low, 'close': close,
                         'tick_volume': rng.integers(50, 500, n).astype(float),
                         'spread': np.full(n, 10.0)}, index=idx)


# Sizing is a function of account size vs contract size: a 100,000-unit contract
# on a synthetic 100.00 price needs a far bigger account than a 100-oz one before
# 0.5% risk clears volume_min. These are coherence tests, so pick an equity that
# lets trades actually happen and assert that none were skipped for size.
EQUITY = {'gold_like': 100_000.0, 'fx_like': 5_000_000.0}

CASES = [(spec_name, strat)
         for spec_name in SPECS
         for strat in (Donchian(), EmaCross(), MeanRevert())]


@pytest.mark.parametrize('spec_name,strategy', CASES,
                         ids=[f'{s}-{st.name}' for s, st in CASES])
def test_books_reconcile_on_random_data(spec_name, strategy):
    df = random_walk(seed=7)
    cfg = Config(risk_pct=0.5, start_equity=EQUITY[spec_name])
    sim = Simulator(SPECS[spec_name], fx=None, config=cfg)
    r = sim.run(df, strategy, spec_name, '1h')

    assert len(r.trades) > 5, 'a baseline should trade on 4000 bars of random walk'
    assert r.stats['skipped_signals'] == 0, 'signals were dropped for size — retune the test'
    t, e = r.trades, r.equity

    # realised P/L must equal the change in cash, exactly
    assert abs(float(t['net_acct'].sum()) - (e['cash'].iloc[-1] - cfg.start_equity)) < CENT

    # every trade is internally consistent
    assert (t['exit_time'] >= t['entry_time']).all()
    assert t['exit_reason'].astype(bool).all()
    assert (t['lots'] >= SPECS[spec_name]['volume_min'] - 1e-12).all()
    assert (t['lots'] <= SPECS[spec_name]['volume_max'] + 1e-12).all()
    assert (t['bars_held'] >= 0).all()

    # a long's stop sits below entry and a short's above — no inverted risk
    longs, shorts = t[t['side'] == 1], t[t['side'] == -1]
    assert (longs['stop_price'] < longs['entry_price']).all()
    assert (shorts['stop_price'] > shorts['entry_price']).all()

    # only one position at a time: no trade may start before the previous ended
    ordered = t.sort_values('entry_time')
    assert (ordered['entry_time'].iloc[1:].to_numpy()
            >= ordered['exit_time'].iloc[:-1].to_numpy()).all(), 'overlapping trades'


@pytest.mark.parametrize('strategy', [Donchian(), EmaCross(), MeanRevert()],
                         ids=lambda s: s.name)
def test_indicators_are_causal(strategy):
    """
    Recompute each precomputed series on history truncated at i, and require the
    value at i to match the full-series value. Anything peeking forward — a
    centred window, a reversed fill, a shift the wrong way — fails here.
    """
    df = random_walk(n=1200, seed=3)
    full = strategy.prepare(df)
    rng = np.random.default_rng(11)
    probes = sorted(rng.choice(range(strategy.warmup + 5, len(df)), size=25, replace=False))

    for i in probes:
        truncated = strategy.prepare(df.iloc[:i + 1])
        for key, arr in full.items():
            a, b = np.asarray(arr, float)[i], np.asarray(truncated[key], float)[i]
            if np.isnan(a) and np.isnan(b):
                continue
            assert a == pytest.approx(b, rel=1e-9, abs=1e-9), (
                f'{strategy.name}.{key} at bar {i} changed when the future was '
                f'removed ({a} vs {b}) — it is reading ahead')


def test_stop_loss_bounds_the_loss_when_there_are_no_gaps():
    """
    On a series with no gaps (each open equals the prior close), a stopped-out
    trade cannot lose more than its planned risk plus one spread. If it does, the
    stop logic or the sizing is wrong.
    """
    df = random_walk(n=3000, seed=5)
    df['open'] = np.concatenate([[df['close'].iloc[0]], df['close'].iloc[:-1].to_numpy()])
    df['high'] = np.maximum(df['high'], np.maximum(df['open'], df['close']))
    df['low'] = np.minimum(df['low'], np.minimum(df['open'], df['close']))

    spec = SPECS['fx_like']
    # slippage off, so this isolates the stop LOGIC. Stop-fill slippage is a
    # separate, deliberate cost — see test_a_stop_slips_and_a_target_does_not.
    sim = Simulator(spec, fx=None, config=Config(risk_pct=0.5, apply_swap=False,
                                                 slippage_atr=0.0, slippage_points=0.0,
                                                 start_equity=EQUITY['fx_like']))
    r = sim.run(df, Donchian(), 'FXLIKE', '1h')
    stopped = r.trades[r.trades['exit_reason'] == 'stop']
    assert len(stopped) > 0
    units = spec['contract_size'] * stopped['lots']
    planned = (stopped['entry_price'] - stopped['stop_price']).abs() * units
    spread_allow = 10.0 * spec['point'] * units
    assert (-stopped['net_acct'] <= planned + spread_allow + 1e-6).all()
