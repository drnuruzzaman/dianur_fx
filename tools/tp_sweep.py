#!/usr/bin/env python
"""
tp_sweep.py — what does a take-profit cost the one strategy that works?

    python tools/tp_sweep.py --symbol XAUUSD.a --tf 4h

XAUUSD 4h donchian passed every gate with NO take-profit. Asking it for one is
asking for a different strategy, so the question is how different: capping the
tail at 1R, 2R, ... and reading what happens to expectancy, profit factor and
drawdown, in BOTH eras, against the uncapped original.

Read the columns in this order:

  net_R   the sum, not the mean -- a take-profit trades a higher win rate for
          smaller wins, and the mean can hold while the total collapses.
  pf      profit factor. Below 1.0 the variant loses money whatever else it does.
  win%    what a take-profit is bought WITH. It will rise. That is not good news
          on its own and it is the number that makes capping feel right.
  maxDD%  the one thing a take-profit can legitimately improve.

Both eras are reported because a variant that only survives in one is a variant
fitted to that one. Carry is off by default: the account is swap-free.
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
from sim.metrics import summarise
from sim.strategies import BASELINES
from sim.strategies.target import WithTarget

ERAS = [('OOS 2016-2020', '2016-01-01', '2021-01-01'),
        ('IS  2021-2026', '2021-01-01', None)]


def streak(t):
    """Longest run of consecutive losers -- what the rule actually feels like."""
    best = cur = 0
    for x in (t.r_multiple <= 0).to_numpy():
        cur = cur + 1 if x else 0
        best = max(best, cur)
    return best


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--symbol', default='XAUUSD.a')
    ap.add_argument('--tf', default='4h')
    ap.add_argument('--strategy', default='donchian', choices=sorted(BASELINES))
    ap.add_argument('--r-mults', default='1,1.5,2,3,4,5,6')
    ap.add_argument('--swap', action='store_true', help='charge carry (account is swap-free)')
    args = ap.parse_args()

    fx = FX.build(account_currency())
    sp = spec(args.symbol, args.tf)
    cfg = Config(risk_pct=0.5, apply_swap=args.swap)
    mults = [float(x) for x in args.r_mults.split(',')]

    rows = []
    for era, start, end in ERAS:
        bars = load(args.symbol, args.tf, start, end)
        for label, factory in ([('none (validated)', lambda: BASELINES[args.strategy]())]
                               + [('%gR' % m, (lambda m=m: WithTarget(
                                   BASELINES[args.strategy](), m))) for m in mults]):
            res = Simulator(sp, fx=fx, config=cfg).run(bars, factory(), args.symbol, args.tf)
            t = res.trades
            if not len(t):
                continue
            s = summarise(res, bars)
            ex = s['exit_reasons']
            rows.append({
                'era': era, 'tp': label, 'trades': len(t),
                'win_pct': round(s['win_rate_pct'], 1),
                'avg_R': round(s['avg_R'], 4),
                'net_R': round(float(t.r_multiple.sum()), 1),
                'pf': round(s['profit_factor'], 3),
                'maxDD_pct': round(s['max_drawdown_pct'], 1),
                'worst_streak': streak(t),
                'by_target': ex.get('target', 0) + ex.get('target_gap', 0),
                'by_stop': ex.get('stop', 0) + ex.get('stop_gap', 0),
                'by_signal': ex.get('signal', 0),
            })

    df = pd.DataFrame(rows)
    out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       'runs', 'tp_sweep_%s_%s_%s.csv'
                       % (args.symbol.replace('.', ''), args.tf, args.strategy))
    df.to_csv(out, index=False)

    print('%s %s %s   carry %s\n'
          % (args.symbol, args.tf, args.strategy,
             'charged' if args.swap else 'off (swap-free account)'))
    for era in df.era.unique():
        g = df[df.era == era]
        base = g[g.tp == 'none (validated)'].iloc[0]
        print('=== %s ===' % era)
        print('%-18s %7s %7s %8s %8s %7s %8s %7s   %s'
              % ('take-profit', 'trades', 'win%', 'avg_R', 'net_R', 'pf',
                 'maxDD%', 'streak', 'exits t/s/sig'))
        for _, r in g.iterrows():
            keep = 100 * r.net_R / base.net_R if base.net_R else float('nan')
            mark = '  <- validated' if r.tp == 'none (validated)' else \
                   ('   keeps %3.0f%% of net R' % keep)
            print('%-18s %7d %6.1f%% %+8.4f %8.1f %7.3f %7.1f%% %7d   %d/%d/%d%s'
                  % (r.tp, r.trades, r.win_pct, r.avg_R, r.net_R, r.pf,
                     r.maxDD_pct, r.worst_streak, r.by_target, r.by_stop,
                     r.by_signal, mark))
        print()

    print('=== verdict ===')
    ok = []
    for m in ['%gR' % x for x in mults]:
        sub = df[df.tp == m]
        if len(sub) < len(ERAS):
            continue
        base = df[df.tp == 'none (validated)']
        keeps = [100 * sub[sub.era == e].net_R.iloc[0] / base[base.era == e].net_R.iloc[0]
                 for e, _, _ in ERAS]
        # Keeping net R is necessary but not sufficient. A cap distant
        # enough to be harmless is a cap that almost never fires, and a rule
        # triggering on one trade in ten is complexity, not a take-profit.
        # Without this the sweep recommends 6R, which fires 15 times in 249
        # trades -- a no-op wearing a rule's clothes.
        fires = [100 * r.by_target / r.trades for _, r in sub.iterrows()]
        if all(k >= 80 for k in keeps) and (sub.pf > 1.0).all():
            ok.append((m, keeps, fires))
    if ok:
        print('caps keeping >=80%% of net R in BOTH eras with PF>1:')
        for m, k, f in ok:
            print('  %-5s  keeps %s   fires on %s of trades'
                  % (m, ' / '.join('%.0f%%' % x for x in k),
                     ' / '.join('%.0f%%' % x for x in f)))
        useful = [(m, k, f) for m, k, f in ok if min(f) >= 15]
        if useful:
            print('\nOf those, the caps that actually fire on >=15%% of trades:')
            for m, _k, _f in useful:
                print('  %s' % m)
            print('A partial exit at the lowest of these lands between the capped')
            print('and the uncapped result, so it keeps more than shown above.')
            print('The scale-out engine change is justified.')
        else:
            print('\nBut every surviving cap is nearly a no-op -- it survives')
            print('BECAUSE it rarely triggers. There is no take-profit here that')
            print('both MATTERS and is SAFE, so the scale-out engine change buys')
            print('nothing. Trade the validated rule.')
    else:
        print('NO cap keeps 80% of net R in both eras.')
        print('The tail is the strategy. A take-profit -- partial or full --')
        print('cannot be added without giving up the edge that passed the gates,')
        print('so the scale-out engine change is not worth making.')
    print('\nwrote %s' % out)


if __name__ == '__main__':
    main()
