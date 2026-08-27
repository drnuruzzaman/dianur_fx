#!/usr/bin/env python
"""
edge_matrix.py — WHERE the edge is, described rather than judged.

    python tools/edge_matrix.py

A shift from strategy development to edge characterisation. Every experiment
before this one asked "can the rule be improved?" and the answer was no, seven
times. This asks a different question: what IS this thing, and where does it
live? The output is a description, not a verdict.

PASS/FAIL IS DELIBERATELY NOT THE OUTPUT. A matrix of ticks and crosses hides
the two things worth seeing -- how BIG the edge is, and how OFTEN it pays. A
cell returning +0.18 R over 42 trades a year is a different animal from one
returning +0.02 R over 900, and both would read PASS. So every cell carries
expectancy, profit factor, trade count, trades per year, max drawdown and the
out-of-sample expectancy beside it.

NO TIME-SHIFT CONTROLS HERE, and that is a scope decision rather than an
oversight. The control asks "is this better than the same rule on a shuffled
schedule?", which is a gating question, and gating is what tools/stage1.py is
for -- it runs 61 simulations per cell to answer it. This runs one per cell and
is therefore ~60x cheaper, which is what makes a 5 x 4 x 2 matrix affordable.
Read a strong cell here as "worth gating", never as "gated".

THE ERAS are the project's standing convention, so these numbers can be laid
beside every stage1 run already in runs/:

    IS    2021-01-01 onward
    OOS   2016-01-01 .. 2020-12-31   -- an earlier, sealed period

XAUUSD intraday does not exist before 2018 (the pre-2018 files hold daily bars
mislabelled at the requested timeframe -- 4h at 17% density is one bar per day),
so its OOS window is genuinely shorter and is printed rather than hidden.
"""
import argparse
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sim.core import Config, Simulator
from sim.fx import FX
from sim.instruments import account_currency, load, spec
from sim.strategies import BASELINES

SYMBOLS = ('EURUSD.a', 'GBPUSD.a', 'AUDUSD.a', 'USDJPY.a', 'XAUUSD.a')
TFS = ('15m', '1h', '4h', '1d')
IS_START, OOS_START, OOS_END = '2021-01-01', '2016-01-01', '2020-12-31'
#: the project's sample floor, for annotation only -- nothing is gated here
MIN_TRADES = 200


def one(symbol, tf, strategy, start, end, fx):
    """One cell, one simulation. Returns None when the cell has no data."""
    try:
        bars = load(symbol, tf, start, end)
    except Exception:                                     # noqa: BLE001
        return None
    if len(bars) < 300:
        return None
    sp = spec(symbol, tf)
    res = Simulator(sp, fx=fx, config=Config(risk_pct=0.5, apply_swap=False)).run(
        bars, BASELINES[strategy](), symbol, tf)
    t = res.trades
    if t.empty:
        return {'trades': 0}
    eq = res.equity['equity'].to_numpy(float)
    peak = np.maximum.accumulate(eq)
    dd = 100.0 * float(np.nanmin((eq - peak) / np.where(peak > 0, peak, np.nan)))
    gain = t.loc[t.net_acct > 0, 'net_acct'].sum()
    loss = -t.loc[t.net_acct < 0, 'net_acct'].sum()
    years = max((bars.index[-1] - bars.index[0]).days / 365.25, 1e-9)
    return {
        'trades': len(t),
        'avg_R': float(t.r_multiple.mean()),
        'pf': float(gain / loss) if loss > 0 else float('inf'),
        'per_year': len(t) / years,
        'maxdd_pct': dd,
        'win_pct': 100.0 * float((t.r_multiple > 0).mean()),
        'span': '%s..%s' % (bars.index[0].date(), bars.index[-1].date()),
        'years': years,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--strategy', default='donchian')
    ap.add_argument('--symbols', default=','.join(SYMBOLS))
    ap.add_argument('--tfs', default=','.join(TFS))
    args = ap.parse_args()

    fx = FX.build(account_currency())
    syms = [s for s in args.symbols.split(',') if s]
    tfs = [t for t in args.tfs.split(',') if t]
    rows = []

    for sym in syms:
        for tf in tfs:
            a = one(sym, tf, args.strategy, IS_START, None, fx)
            b = one(sym, tf, args.strategy, OOS_START, OOS_END, fx)
            if a is None and b is None:
                continue
            rows.append({
                'symbol': sym, 'tf': tf,
                'is_trades': (a or {}).get('trades', 0),
                'is_avg_R': (a or {}).get('avg_R', np.nan),
                'is_pf': (a or {}).get('pf', np.nan),
                'per_year': (a or {}).get('per_year', np.nan),
                'is_maxdd': (a or {}).get('maxdd_pct', np.nan),
                'is_win': (a or {}).get('win_pct', np.nan),
                'oos_trades': (b or {}).get('trades', 0),
                'oos_avg_R': (b or {}).get('avg_R', np.nan),
                'oos_pf': (b or {}).get('pf', np.nan),
                'is_span': (a or {}).get('span', '-'),
                'oos_span': (b or {}).get('span', '-'),
            })
            r = rows[-1]
            print('  %-9s %-4s  IS %5d %+7.4f pf %5.2f  %5.1f/yr  dd %6.1f%%   '
                  'OOS %5d %+7.4f pf %5.2f'
                  % (sym, tf, r['is_trades'], r['is_avg_R'], r['is_pf'],
                     r['per_year'], r['is_maxdd'], r['oos_trades'],
                     r['oos_avg_R'], r['oos_pf']))

    df = pd.DataFrame(rows)
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out = os.path.join(root, 'runs', 'edge_matrix_%s.csv' % args.strategy)
    df.to_csv(out, index=False)

    # ---- the matrix, as a shape rather than a table ----------------------
    def grid(title, col, fmt):
        print('\n%s' % title)
        print('  %-10s' % '' + ''.join('%12s' % t for t in tfs))
        for sym in syms:
            cells = []
            for tf in tfs:
                m = df[(df.symbol == sym) & (df.tf == tf)]
                cells.append('%12s' % ('-' if m.empty or not np.isfinite(m[col].iloc[0])
                                       else fmt % m[col].iloc[0]))
            print('  %-10s' % sym.replace('.a', '') + ''.join(cells))

    grid('IN-SAMPLE EXPECTANCY (R per trade)', 'is_avg_R', '%+.4f')
    grid('OUT-OF-SAMPLE EXPECTANCY (R per trade)', 'oos_avg_R', '%+.4f')
    grid('TRADES PER YEAR (in sample)', 'per_year', '%.1f')
    grid('MAX DRAWDOWN (in sample, %)', 'is_maxdd', '%.1f')

    both = df[(df.is_avg_R > 0) & (df.oos_avg_R > 0)]
    print('\npositive in BOTH eras: %d of %d cells' % (len(both), len(df)))
    if len(both):
        print(both[['symbol', 'tf', 'is_trades', 'is_avg_R',
                    'oos_trades', 'oos_avg_R']].to_string(index=False))
    print('\nCells under the %d-trade floor are not measurements; expectancy on a '
          'small\nsample is not distinguishable from zero. Gating needs '
          'tools/stage1.py.' % MIN_TRADES)
    print('wrote %s' % os.path.relpath(out, root))


if __name__ == '__main__':
    main()
