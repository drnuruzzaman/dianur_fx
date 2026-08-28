#!/usr/bin/env python
"""
stack.py -- do the validated cells DIVERSIFY, or are they one bet counted four times?

    python tools/stack.py --symbol XAUUSD.a --start 2021-01-01

XAUUSD passes every gate at 4h N=20, 1h N=79, 15m N=317 and 5m N=950. The
tempting conclusion is "run all four and make four times as much". The reason to
doubt it is in the finding itself: those are the SAME 3.3-day channel at four
resolutions. If they enter and exit together, stacking them multiplies position
size and drawdown without adding a single independent source of return -- the
textbook way to turn a working strategy into a margin call.

So this measures it instead of assuming. Daily realised P/L per cell, then:
  * pairwise correlation of the daily series
  * how often two cells are in the market on the same day, same direction
  * the stacked portfolio's return and drawdown against the best single cell

A stack is only worth running if its return/drawdown beats the best cell alone.
"""
import argparse
import itertools
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sim.core import Config, Simulator
from sim.fx import FX
from sim.instruments import account_currency, load, spec
from sim.strategies import BASELINES, strategy_for_tf


def daily_pnl(symbol, tf, equity, risk_pct, start, end):
    """Realised P/L per calendar day, in account currency."""
    strat = strategy_for_tf(tf)
    bars = load(symbol, tf, start, end)
    sp = spec(symbol, tf)
    cfg = Config(start_equity=equity, apply_swap=False,
                 size_base='start', risk_pct=risk_pct)
    res = Simulator(sp, fx=FX.build(account_currency()), config=cfg).run(
        bars, BASELINES[strat](), symbol, tf)
    t = res.trades
    if not len(t):
        return None, 0
    # size_base='start' so every trade risks the SAME cash -- a correlation
    # measured on a compounding curve would partly be measuring the compounding.
    s = pd.Series(t['net_acct'].to_numpy(float),
                  index=pd.to_datetime(t['exit_time']).dt.normalize())
    return s.groupby(level=0).sum(), len(t)


def dd(curve):
    peak = np.maximum.accumulate(curve)
    return float(np.min(curve - peak))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--symbol', default='XAUUSD.a')
    ap.add_argument('--tfs', default='5m,15m,1h,4h')
    #: CELLS ACROSS INSTRUMENTS, as "SYMBOL:TF" -- overrides --symbol/--tfs.
    #: Same-instrument timeframes are near-duplicates of one bet; the question
    #: worth asking is whether DIFFERENT instruments diversify, and that needs
    #: the symbol to vary too.
    ap.add_argument('--cells', default='',
                    help='comma-separated SYMBOL:TF, e.g. XAUUSD.a:4h,USDJPY.a:15m')
    ap.add_argument('--equity', type=float, default=30_000.0)
    ap.add_argument('--risk', type=float, default=0.5)
    ap.add_argument('--start', default='2021-01-01')
    ap.add_argument('--end', default=None)
    args = ap.parse_args()

    if args.cells:
        cells = [tuple(c.split(':')) for c in args.cells.split(',') if ':' in c]
        label = 'cells across instruments'
    else:
        cells = [(args.symbol, tf) for tf in args.tfs.split(',')]
        label = args.symbol

    series, counts = {}, {}
    for sym, tf in cells:
        name = '%s %s' % (sym.replace('.a', ''), tf)
        s, n = daily_pnl(sym, tf, args.equity, args.risk, args.start, args.end)
        if s is not None:
            series[name], counts[name] = s, n
    if len(series) < 2:
        sys.exit('need at least two cells with trades')

    df = pd.DataFrame(series).fillna(0.0).sort_index()
    print('%s  %s..%s  risk %.2f%% of a FIXED %s %.0f\n'
          % (label, df.index[0].date(), df.index[-1].date(), args.risk,
             account_currency(), args.equity))

    print('  daily P/L correlation between cells')
    w = max(11, max(len(x) for x in df.columns) + 1)
    print(' ' * (w + 2) + ''.join(('%%%ds' % w) % t for t in df.columns))
    c = df.corr()
    for a in df.columns:
        print('  %-*s' % (w, a) + ''.join(('%%%d.2f' % w) % c.loc[a, b]
                                          for b in df.columns))

    print('\n  overlap: share of active days both cells are in the market')
    act = df != 0
    for a, b in itertools.combinations(df.columns, 2):
        both = (act[a] & act[b]).sum()
        either = (act[a] | act[b]).sum()
        print('  %-12s + %-12s  %5.1f%% of active days'
              % (a, b, 100.0 * both / max(1, either)))

    print('\n  %-16s %7s %11s %11s %8s' % ('portfolio', 'trades', 'net', 'max DD', 'net/DD'))
    rows = []
    for tf in df.columns:
        cur = df[tf].cumsum().to_numpy()
        rows.append((tf, counts[tf], cur[-1], dd(cur)))
    stack = df.sum(axis=1).cumsum().to_numpy()
    rows.append(('ALL %d stacked' % len(df.columns), sum(counts.values()),
                 stack[-1], dd(stack)))
    # equal-RISK stack: same total risk budget split across the cells, which is
    # what someone would actually do rather than multiplying exposure
    split = (df / len(df.columns)).sum(axis=1).cumsum().to_numpy()
    rows.append(('ALL, risk split', sum(counts.values()), split[-1], dd(split)))
    for name, n, net, d in rows:
        print('  %-26s %7d %11.0f %11.0f %8.2f'
              % (name, n, net, d, (net / abs(d)) if d else float('nan')))


if __name__ == '__main__':
    main()
