#!/usr/bin/env python
"""
neighbourhood.py — is a surviving cell a real effect or a lucky coordinate?

    python tools/neighbourhood.py --symbol XAUUSD.a --tf 4h --strategy donchian

A strategy that only works at exactly entry=20, exit=10 has not found anything
about the market; it has found one point where noise happened to line up. A real
effect degrades SMOOTHLY as the parameters move, and does so on both the window
it was discovered in and the window it was validated on.

So this deliberately does NOT search for the best parameters. Reading off the
top row would be exactly the overfitting the whole framework exists to prevent,
and the incumbent's out-of-sample result would be worthless the moment it was
replaced by a better-looking neighbour. What is measured instead:

    * what fraction of the neighbourhood is profitable, in each window
    * whether the incumbent sits on a plateau or on a spike
    * whether the IS and OOS surfaces agree about WHERE the good region is

Each grid point is a single simulation, not a time-shift distribution: 61 runs
per point would cost hours to answer a question about shape. The control test
belongs to the incumbent alone, and it has already had one.
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
from sim.strategies import BASELINES

GRIDS = {
    'donchian': {'entry': [12, 16, 20, 24, 28, 32], 'exit': [6, 8, 10, 13, 16],
                 'atr_mult': [1.5, 2.0, 2.5]},
    'ema_cross': {'fast': [13, 17, 21, 26, 34], 'slow': [40, 50, 60, 75],
                  'atr_mult': [2.0, 2.5, 3.0]},
    'mean_revert': {'ma_len': [20, 35, 50, 75, 100], 'entry_atr': [1.5, 2.0, 2.5, 3.0],
                    'stop_atr': [1.5, 2.0, 3.0]},
}
INCUMBENT = {'donchian': {'entry': 20, 'exit': 10, 'atr_mult': 2.0},
             'ema_cross': {'fast': 21, 'slow': 50, 'atr_mult': 2.5},
             'mean_revert': {'ma_len': 50, 'entry_atr': 2.0, 'stop_atr': 2.0}}


def run_point(bars, sp, cfg, fx, cls, params, symbol, tf):
    res = Simulator(sp, fx=fx, config=cfg).run(bars, cls(**params), symbol, tf)
    t = res.trades
    if not len(t):
        return {'trades': 0, 'avg_R': np.nan, 'pf': np.nan}
    wins = t.loc[t.r_multiple > 0, 'r_multiple'].sum()
    loss = -t.loc[t.r_multiple < 0, 'r_multiple'].sum()
    return {'trades': len(t),
            'avg_R': round(float(t.r_multiple.mean()), 4),
            'pf': round(float(wins / loss) if loss else np.inf, 3)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--symbol', default='XAUUSD.a')
    ap.add_argument('--tf', default='4h')
    ap.add_argument('--strategy', default='donchian', choices=sorted(GRIDS))
    ap.add_argument('--split', default='2021-01-01',
                    help='OOS is everything before this date, IS everything after')
    ap.add_argument('--min-trades', type=int, default=100,
                    help='a grid point with fewer says nothing either way')
    args = ap.parse_args()

    cfg = Config(risk_pct=0.5, apply_swap=False)
    fx = FX.build(account_currency())
    cls = BASELINES[args.strategy]
    sp = spec(args.symbol, args.tf)
    grid = GRIDS[args.strategy]
    keys = list(grid)
    combos = list(itertools.product(*(grid[k] for k in keys)))

    windows = {'OOS': load(args.symbol, args.tf, '2001-01-01', args.split),
               'IS': load(args.symbol, args.tf, args.split, None)}
    print('%s %s %s -- %d grid points x %d windows'
          % (args.symbol, args.tf, args.strategy, len(combos), len(windows)))
    for w, b in windows.items():
        print('  %-4s %6d bars  %s .. %s' % (w, len(b), b.index[0].date(), b.index[-1].date()))

    rows = []
    for n, combo in enumerate(combos, 1):
        params = dict(zip(keys, combo))
        row = dict(params)
        for w, bars in windows.items():
            r = run_point(bars, sp, cfg, fx, cls, params, args.symbol, args.tf)
            row.update({'%s_%s' % (w, k): v for k, v in r.items()})
        rows.append(row)
        print('  %3d/%d %s' % (n, len(combos), params), flush=True)

    df = pd.DataFrame(rows)
    inc = INCUMBENT[args.strategy]
    mask = np.ones(len(df), bool)
    for k, v in inc.items():
        mask &= df[k] == v

    def ok(w):
        return (df['%s_trades' % w] >= args.min_trades) & (df['%s_pf' % w] > 1.0)

    df['both'] = ok('IS') & ok('OOS')
    out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       'runs', 'neighbourhood_%s_%s_%s.csv'
                       % (args.symbol.replace('.', ''), args.tf, args.strategy))
    df.to_csv(out, index=False)

    print('\n=== NEIGHBOURHOOD ===')
    print(df.to_string(index=False))
    print('\nincumbent %s' % inc)
    if mask.any():
        print(df[mask].to_string(index=False))
    n = len(df)
    print('\nprofitable IS  : %d/%d (%.0f%%)' % (ok('IS').sum(), n, 100 * ok('IS').mean()))
    print('profitable OOS : %d/%d (%.0f%%)' % (ok('OOS').sum(), n, 100 * ok('OOS').mean()))
    print('profitable BOTH: %d/%d (%.0f%%)' % (df.both.sum(), n, 100 * df.both.mean()))
    corr = df[['IS_avg_R', 'OOS_avg_R']].corr().iloc[0, 1]
    print('IS vs OOS avg_R correlation across the grid: %+.3f' % corr)
    print('\nwrote %s' % out)


if __name__ == '__main__':
    main()
