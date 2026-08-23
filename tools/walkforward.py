#!/usr/bin/env python
"""
walkforward.py — does the edge hold across eras, or live in a few rallies?

    python tools/walkforward.py --symbol XAUUSD.a --tf 4h --strategy donchian

The neighbourhood test showed XAUUSD 4h donchian is profitable at all 90
parameter settings in both halves of history. That rules out a lucky
coordinate. It does not rule out a lucky ASSET or a lucky ERA -- Donchian is
trend-following and gold trended hard in both halves, so "gold trends and this
catches trends" would produce the same result while generalising to nothing.

Three things are measured, and the third is the point:

  1. FIXED   the incumbent parameters run on consecutive windows. An edge that
             is real should show up in most eras, not in two of eight.

  2. ADAPTIVE a true walk-forward: choose the best parameters on each training
             window, trade them on the window that follows, never overlapping.
             The neighbourhood found the IS/OOS parameter correlation to be
             NEGATIVE, so this is expected to LOSE to fixed parameters -- and if
             it does, that is a result worth having in writing rather than a
             disappointment.

  3. TREND    each window's return is set against how far the instrument
             actually travelled in it. If profit only appears when the market
             ran, the strategy has no edge -- it has beta to trends, which is a
             different and much more fragile thing to own.
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
from tools.neighbourhood import GRIDS, INCUMBENT


def measure(bars, sp, cfg, fx, cls, params, symbol, tf):
    res = Simulator(sp, fx=fx, config=cfg).run(bars, cls(**params), symbol, tf)
    t = res.trades
    if not len(t):
        return {'trades': 0, 'avg_R': np.nan, 'pf': np.nan, 'net_R': 0.0}
    wins = t.loc[t.r_multiple > 0, 'r_multiple'].sum()
    loss = -t.loc[t.r_multiple < 0, 'r_multiple'].sum()
    return {'trades': len(t),
            'avg_R': round(float(t.r_multiple.mean()), 4),
            'pf': round(float(wins / loss) if loss else np.inf, 3),
            'net_R': round(float(t.r_multiple.sum()), 2)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--symbol', default='XAUUSD.a')
    ap.add_argument('--tf', default='4h')
    ap.add_argument('--strategy', default='donchian', choices=sorted(GRIDS))
    ap.add_argument('--test-years', type=int, default=2)
    ap.add_argument('--train-years', type=int, default=6,
                    help='training span for the ADAPTIVE arm only')
    ap.add_argument('--first-year', type=int, default=2007)
    ap.add_argument('--last-year', type=int, default=2026)
    args = ap.parse_args()

    cfg = Config(risk_pct=0.5, apply_swap=False)
    fx = FX.build(account_currency())
    cls = BASELINES[args.strategy]
    sp = spec(args.symbol, args.tf)
    inc = INCUMBENT[args.strategy]
    grid = GRIDS[args.strategy]
    keys = list(grid)
    combos = [dict(zip(keys, c)) for c in itertools.product(*(grid[k] for k in keys))]

    edges = list(range(args.first_year, args.last_year + 1, args.test_years))
    print('%s %s %s -- incumbent %s' % (args.symbol, args.tf, args.strategy, inc))
    print('%d test windows of %dy; ADAPTIVE trains on the preceding %dy of %d grid points\n'
          % (len(edges), args.test_years, args.train_years, len(combos)))

    rows = []
    for y0 in edges:
        y1 = y0 + args.test_years
        test = load(args.symbol, args.tf, '%d-01-01' % y0, '%d-01-01' % y1)
        if len(test) < 200:
            continue
        row = {'window': '%d-%d' % (y0, y1 - 1), 'bars': len(test)}

        # how far did the market itself travel? the trend-beta control
        move = float(test.close.iloc[-1] / test.close.iloc[0] - 1) * 100
        row['market_pct'] = round(move, 1)

        f = measure(test, sp, cfg, fx, cls, inc, args.symbol, args.tf)
        row.update({'fx_' + k: v for k, v in f.items()})

        # ADAPTIVE: fit on the years immediately before this window, trade here
        train = load(args.symbol, args.tf, '%d-01-01' % (y0 - args.train_years),
                     '%d-01-01' % y0)
        if len(train) >= 200:
            scored = [(measure(train, sp, cfg, fx, cls, p, args.symbol, args.tf), p)
                      for p in combos]
            scored = [(m, p) for m, p in scored if m['trades'] >= 30]
            if scored:
                best = max(scored, key=lambda mp: mp[0]['avg_R'])[1]
                a = measure(test, sp, cfg, fx, cls, best, args.symbol, args.tf)
                row.update({'ad_' + k: v for k, v in a.items()})
                row['ad_params'] = ','.join('%s=%s' % (k, best[k]) for k in keys)
        rows.append(row)
        print('  %-9s market %+6.1f%%   fixed avgR %+7.4f pf %5.3f n=%-4d   '
              'adaptive avgR %+7.4f pf %5.3f n=%-4d  %s'
              % (row['window'], row['market_pct'], row.get('fx_avg_R', np.nan),
                 row.get('fx_pf', np.nan), row.get('fx_trades', 0),
                 row.get('ad_avg_R', np.nan), row.get('ad_pf', np.nan),
                 row.get('ad_trades', 0), row.get('ad_params', '-')), flush=True)

    df = pd.DataFrame(rows)
    out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       'runs', 'walkforward_%s_%s_%s.csv'
                       % (args.symbol.replace('.', ''), args.tf, args.strategy))
    df.to_csv(out, index=False)

    print('\n=== WALK-FORWARD ===')
    print(df.to_string(index=False))
    n = len(df)
    for arm, pre in (('FIXED', 'fx'), ('ADAPTIVE', 'ad')):
        c = df['%s_avg_R' % pre].dropna()
        if not len(c):
            continue
        win = int((c > 0).sum())
        print('\n%-8s windows profitable by avg_R : %d/%d' % (arm, win, len(c)))
        print('%-8s pooled net_R                 : %+.1f over %d trades'
              % (arm, df['%s_net_R' % pre].sum(), int(df['%s_trades' % pre].sum())))
        print('%-8s per-window avg_R  worst %+.4f  median %+.4f  best %+.4f'
              % (arm, c.min(), c.median(), c.max()))

    ok = df.dropna(subset=['fx_avg_R'])
    if len(ok) > 2:
        r_signed = ok[['fx_avg_R', 'market_pct']].corr().iloc[0, 1]
        r_abs = np.corrcoef(ok.fx_avg_R, ok.market_pct.abs())[0, 1]
        print('\nTREND CHECK  (does profit only appear when the market ran?)')
        print('  corr(avg_R, market return)      %+.3f' % r_signed)
        print('  corr(avg_R, |market return|)    %+.3f' % r_abs)
        quiet = ok[ok.market_pct.abs() < ok.market_pct.abs().median()]
        print('  quiet windows (below-median move): %d, avg_R %+.4f, %d/%d profitable'
              % (len(quiet), quiet.fx_avg_R.mean(),
                 int((quiet.fx_avg_R > 0).sum()), len(quiet)))
    print('\nwrote %s' % out)


if __name__ == '__main__':
    main()
