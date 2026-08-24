#!/usr/bin/env python
"""
gold_breakout_wf.py — take the gold 4h breakout result the rest of the way.

    python tools/gold_breakout_wf.py

WHY THIS EXISTS SEPARATELY FROM sim/run_tl.py

`run_tl` hardcodes a 15m execution timeframe. The only result in this project
that has survived a split-sample test is on gold 4h, so it cannot be run there.

WHAT IT ADDS OVER tools/r_conversion.py

r_conversion measures R-multiples on a barrier approximation: entry at the break
bar's close, fixed ATR barriers, friction estimated as a formula. That is the
right tool for sweeping 25 geometries cheaply, and it is NOT a backtest. It has
no next-bar-open fill, no equity-scaled sizing, no swap, no gap handling, no
stop-before-target resolution, and no account that can run out of money.

This runs the actual simulator over the actual strategy, so the number at the
end is one an account could have made:

  * a signal on the close of bar i fills at the OPEN of bar i+1
  * a bar containing both stop and target resolves as the STOP
  * size comes from a risk fraction of live equity, rounded down to volume_step
  * every instrument number is read from data/instruments.json
  * invariants are asserted every bar

GEOMETRY IS FROZEN, NOT SWEPT. stop_atr=0.4 and risk_reward=4.0 come from the
split-sample table and are not re-optimised here. Sweeping again would re-earn
the selection bias the split was designed to remove -- the whole point is to ask
what ONE pre-registered configuration does on a span it was not chosen on.

Swap is reported both ways. Holding gold overnight costs ~83 points per lot and
MT5 exposes only today's rate, so a multi-year overnight backtest is partly a
statement about assumed carry. The pair is the answer, not either number.
"""

import argparse
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sim.core import Config, Simulator
from sim.fx import FX
from sim.instruments import account_currency, load, spec
from sim.metrics import summarise
from sim.strategies import FEATURE_STRATEGIES
from sim.tl import build

EXEC_TF = '4h'
CONTEXT = ('1d',)          # 4h is the execution frame, so context is D1 only

# Pre-registered from the split-sample table. Do not sweep these here.
STOP_ATR = 0.4
RISK_REWARD = 4.0


def run_span(symbol, start, end, no_swap, label, min_quality):
    bars = {tf: load(symbol, tf, start, end) for tf in (EXEC_TF, *CONTEXT)}
    feat, states, lines = build(bars, EXEC_TF, CONTEXT)

    strat = FEATURE_STRATEGIES['tl_breakout'](
        feat, confluence_mode='off', exec_tf=EXEC_TF,
        stop_atr=STOP_ATR, risk_reward=RISK_REWARD, min_quality=min_quality)

    cfg = Config(risk_pct=0.5, apply_swap=not no_swap)
    sim = Simulator(spec(symbol, EXEC_TF), fx=FX.build(account_currency()), config=cfg)
    result = sim.run(bars[EXEC_TF], strat, symbol, EXEC_TF)
    st = summarise(result, bars[EXEC_TF])
    st['span'] = label
    st['swap'] = 'off' if no_swap else 'on'
    st['bars'] = len(bars[EXEC_TF])
    st['lines'] = len(lines)
    return st, result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--symbol', default='XAUUSD.a')
    ap.add_argument('--min-quality', type=float, default=90.0)
    args = ap.parse_args()

    spans = [('IS 2016-2020', '2016-01-01', '2020-12-31'),
             ('OOS 2021-2026', '2021-01-01', None)]

    rows = []
    for label, start, end in spans:
        for no_swap in (True, False):
            try:
                st, _ = run_span(args.symbol, start, end, no_swap, label,
                                 args.min_quality)
            except Exception as e:                       # noqa: BLE001
                print('  %-14s swap=%-3s FAILED: %s'
                      % (label, 'off' if no_swap else 'on', e))
                continue
            rows.append(st)
            print('  %-14s swap=%-3s trades=%-5s net=%-11s ret=%-8s pf=%-6s '
                  'win=%-6s maxdd=%s'
                  % (label, 'off' if no_swap else 'on',
                     st.get('trades'), st.get('net_acct'), st.get('return_pct'),
                     st.get('pf'), st.get('win_pct'), st.get('maxdd_pct')))

    if not rows:
        print('no spans completed')
        return
    df = pd.DataFrame(rows)
    cols = [c for c in ('span', 'swap', 'trades', 'net_acct', 'return_pct',
                        'win_pct', 'avg_R', 'pf', 'maxdd_pct', 'sharpe',
                        'bars', 'lines') if c in df.columns]
    print('\n=== gold 4h breakout, geometry FROZEN at stop %.1f / rr %.1f ==='
          % (STOP_ATR, RISK_REWARD))
    print(df[cols].to_string(index=False))
    out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       'runs', 'oos', 'gold4h_breakout_walkforward.csv')
    os.makedirs(os.path.dirname(out), exist_ok=True)
    df.to_csv(out, index=False)
    print('\nwrote %s' % out)


if __name__ == '__main__':
    main()
