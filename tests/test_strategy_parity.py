"""
Every JS strategy in the registry must reproduce its Python twin.

js/chart/strategies.js is what the Strategy Replay reads, and each entry claims a
validation record measured by the Python engine. That claim is only transferable
if the JS runs the same rule -- a panel quoting levels the backtest never traded
is worse than no panel, because it looks authoritative.

Compared: entry bar, side, STOP PRICE to 1e-9, exit bar, exit reason. Entry
prices differ on purpose (the engine pays spread and slippage, the panel quotes
the raw rule) and the engine skips signals it cannot size, so only entries both
took are lined up.

The private-ATR bug is why this exists. Three strategy files each carried their
own Wilder seeding; they converged but differed by up to 0.86 price units during
warmup, so a JS panel reading the parity-tested `atrSeries` would have quoted a
different stop from the run that passed the gates.

    python -m pytest tests/test_strategy_parity.py -q
"""

import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from parity_helpers import NODE, compare, js_trades, load_cell, python_trades

pytestmark = pytest.mark.skipif(NODE is None, reason='node not on PATH')

#: strategy -> the cells its record covers, plus one it does not
#: their stop is moved after entry (break-even, trailing), so its final value
#: is a function of the fill price rather than of the signal
MANAGED_STOP = {'turtle_ea'}

CASES = [
    ('donchian', 'XAUUSD.a', '4h', '2018-01-01', '2022-01-01'),
    ('donchian', 'EURUSD.a', '1h', '2020-01-01', '2021-01-01'),
    ('donchian_ema200', 'XAUUSD.a', '4h', '2018-01-01', '2022-01-01'),
    ('turtle_ea', 'XAUUSD.a', '4h', '2018-01-01', '2022-01-01'),
    ('turtle_ea', 'XAUUSD.a', '1h', '2020-01-01', '2021-01-01'),
    ('ema_cross', 'XAUUSD.a', '4h', '2018-01-01', '2022-01-01'),
    ('ema_cross', 'USDJPY.a', '1h', '2020-01-01', '2021-01-01'),
]


@pytest.mark.parametrize('strategy,symbol,tf,start,end', CASES,
                         ids=[f'{s}_{y}_{t}' for s, y, t, _a, _b in CASES])
def test_js_matches_the_engine(strategy, symbol, tf, start, end, tmp_path):
    bars = load_cell(symbol, tf, start, end)
    if len(bars) > 4000:
        bars = bars.iloc[:4000]
    want = python_trades(bars, symbol, tf, strategy)
    got = js_trades(bars, strategy, tmp_path)
    assert want, 'the cell produced no Python trades to compare against'
    # Strategies that MOVE the stop after entry derive it from the fill, and
    # the two sides fill differently on purpose -- see compare()'s docstring.
    # Everything the rule decides is still compared.
    compare(want, got, f'{strategy} {symbol} {tf}',
            check_stop=strategy not in MANAGED_STOP)


def test_the_registry_declares_a_status_for_every_strategy():
    """
    A dropdown that lists a failed rule beside a validated one as equals erases
    the only finding this project has. Status and cells are required fields.
    """
    import json
    import subprocess

    from parity_helpers import ROOT

    url = (os.path.join(ROOT, 'js', 'chart', 'strategies.js')
           .replace(os.sep, '/').replace('C:', 'file:///C:'))
    script = ('const m = await import("%s");'
              'console.log(JSON.stringify(m.STRATEGIES.map(s => ({'
              ' key: s.key, label: s.label, status: s.status, cells: s.cells,'
              ' hasSummary: !!s.summary, hasNotes: !!s.notes }))));' % url)
    res = subprocess.run([NODE, '--input-type=module', '-e', script],
                         cwd=ROOT, capture_output=True, text=True, timeout=120)
    if res.returncode != 0:
        pytest.fail('node failed: %s' % res.stderr[-1200:])
    rows = json.loads(res.stdout)
    assert rows, 'the registry is empty'
    for r in rows:
        assert r['status'] in ('validated', 'failed', 'untested'), r
        assert r['cells'], '%s declares no cells' % r['key']
        assert r['hasSummary'], '%s has no plain-language summary' % r['key']
        assert r['hasNotes'], '%s has no validation notes' % r['key']


def test_no_registered_strategy_emits_a_take_profit():
    """
    Every rule here exits on a signal or a stop. tools/tp_sweep.py measured that
    capping the validated one at 1R turns +43.7 net R into -2.1, so a target
    appearing in the panel would mean it is showing something never validated.
    """
    from sim.core import BarView
    from sim.strategies import BASELINES

    rng = np.random.default_rng(11)
    n = 1200
    px = 2000 + np.cumsum(rng.normal(0, 4, n))
    bars = pd.DataFrame({'open': px, 'high': px + 3, 'low': px - 3, 'close': px},
                        index=pd.date_range('2024-01-01', periods=n, freq='4h'))
    for name in ('donchian', 'ema_cross'):
        strat = BASELINES[name]()
        series = {k: np.asarray(v, float) for k, v in strat.prepare(bars).items()}
        arrays = (bars['open'].to_numpy(float), bars['high'].to_numpy(float),
                  bars['low'].to_numpy(float), bars['close'].to_numpy(float),
                  np.zeros(n), np.zeros(n), bars.index.to_numpy())
        seen = 0
        # from warmup, not from 0: ema_cross reads series(..., 1) and BarView
        # correctly refuses a negative index, which is the look-ahead guard
        # doing its job rather than a bug to work around.
        for i in range(strat.warmup, n):
            intent = strat.on_bar(BarView(arrays, series, i), None)
            if intent is not None and intent.side != 0:
                seen += 1
                assert intent.target is None, '%s set a target at bar %d' % (name, i)
        assert seen > 5, '%s produced no entries to check' % name


def test_there_is_exactly_one_atr_in_the_project():
    """The bug the parity suite guards: a stop the chart cannot reproduce."""
    from sim.indicators import atr as canonical
    from sim.strategies.donchian import atr as from_donchian
    from sim.strategies.mean_revert import atr as from_mean_revert

    assert from_donchian is canonical, 'donchian re-declared ATR'
    assert from_mean_revert is canonical, 'mean_revert re-declared ATR'
