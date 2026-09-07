#!/usr/bin/env python
"""
scaleout_sweep.py -- does taking part off at a fixed R beat capping the whole?

    python tools/scaleout_sweep.py --symbol XAUUSD.a --tf 4h

tools/tp_sweep.py rejected every FULL take-profit that moved the win rate: a
1.5R cap keeps 124% of net R out of sample and 42% in sample, and a variant that
only survives one era is fitted to it. A partial exit is the version of the idea
that keeps half the tail, so it gets tested against the SAME bar:

    keep >=80% of net R in BOTH eras, with PF > 1, or it is not worth having.

Two win rates are reported and the difference matters:

  pos win%   share of POSITIONS that made money. Comparable to the uncapped
             rule, and the honest number.
  fill win%  share of FILLS. A scale-out books a winning fill and then the
             runner books its own, so this is always the flattering one -- it
             is shown because it is what a broker statement will show you, and
             it should not be mistaken for the first column.
"""
import argparse
import csv
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sim.core import Config, Simulator
from sim.fx import FX
from sim.instruments import account_currency, load, spec
from sim.strategies import BASELINES, strategy_for_tf
from sim.strategies.scaleout import WithScaleOut

ERAS = [('OOS 2016-2020', '2016-01-01', '2021-01-01'),
        ('IS  2021-2026', '2021-01-01', None)]


def stats(res):
    t = res.trades
    if not len(t):
        return None
    pos = t.groupby('pos_id')['r_multiple'].sum()
    r = t.r_multiple.to_numpy(float)
    eq = res.equity['equity'].to_numpy(float)
    peak = np.maximum.accumulate(eq)
    dd = float(np.nanmin((eq - peak) / np.where(peak > 0, peak, np.nan)))
    gain = float(pos[pos > 0].sum())
    loss = float(-pos[pos < 0].sum())
    return {'fills': len(t), 'positions': len(pos),
            'pos_win': 100.0 * float((pos > 0).mean()),
            'fill_win': 100.0 * float((r > 0).mean()),
            'net_R': float(r.sum()), 'avg_R': float(pos.mean()),
            'pf': (gain / loss) if loss else float('inf'),
            'maxdd_pct': 100.0 * dd,
            'scaled': int((t.exit_reason == 'scale_out').sum())}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--symbol', default='XAUUSD.a')
    ap.add_argument('--tf', default='4h')
    ap.add_argument('--r-mults', default='1,1.5,2,3')
    ap.add_argument('--fracs', default='0.25,0.5,0.75')
    args = ap.parse_args()

    strat = strategy_for_tf(args.tf)
    sp = spec(args.symbol, args.tf)
    fx = FX.build(account_currency())
    rows, keeps = [], {}
    print('%s %s %s   partial exits vs the uncapped rule\n'
          % (args.symbol, args.tf, strat))

    for era, start, end in ERAS:
        bars = load(args.symbol, args.tf, start, end)
        run = lambda st: Simulator(sp, fx=fx, config=Config(
            start_equity=100_000.0, apply_swap=False)).run(
                bars, st, args.symbol, args.tf)
        base = stats(run(BASELINES[strat]()))
        print('=== %s ===' % era)
        print('  %-18s %6s %6s %8s %8s %8s %7s %8s %7s'
              % ('variant', 'fills', 'posns', 'pos win%', 'fill win%', 'net_R',
                 'pf', 'maxDD%', 'keeps'))
        print('  %-18s %6d %6d %8.1f %8.1f %+8.1f %7.3f %7.1f%%   --'
              % ('none (validated)', base['fills'], base['positions'],
                 base['pos_win'], base['fill_win'], base['net_R'],
                 base['pf'], base['maxdd_pct']))
        for r_mult in [float(x) for x in args.r_mults.split(',')]:
            for frac in [float(x) for x in args.fracs.split(',')]:
                st = stats(run(WithScaleOut(BASELINES[strat](), r_mult, frac)))
                if not st:
                    continue
                keep = 100.0 * st['net_R'] / base['net_R'] if base['net_R'] else 0.0
                name = '%gR x %d%%' % (r_mult, round(frac * 100))
                keeps.setdefault(name, {})[era] = (keep, st['pf'], st['pos_win'])
                rows.append(dict(era=era, variant=name, **st, keeps_pct=round(keep, 1)))
                print('  %-18s %6d %6d %8.1f %8.1f %+8.1f %7.3f %7.1f%% %6.0f%%'
                      % (name, st['fills'], st['positions'], st['pos_win'],
                         st['fill_win'], st['net_R'], st['pf'],
                         st['maxdd_pct'], keep))
        print()

    print('=== verdict: >=80%% of net R in BOTH eras, PF>1, and a higher '
          'position win rate ===')
    base_win = {}
    for era, start, end in ERAS:
        bars = load(args.symbol, args.tf, start, end)
        base_win[era] = stats(Simulator(sp, fx=fx, config=Config(
            start_equity=100_000.0, apply_swap=False)).run(
                bars, BASELINES[strat](), args.symbol, args.tf))['pos_win']
    winners = []
    for name, per in keeps.items():
        if len(per) < len(ERAS):
            continue
        ok = all(k >= 80 and pf > 1 for k, pf, _ in per.values())
        better = all(w > base_win[e] for e, (_, _, w) in per.items())
        if ok and better:
            winners.append((name, per))
    if not winners:
        print('  NONE. No partial exit keeps 80% of net R in both eras while '
              'also raising the win rate.')
        best = max(keeps.items(), key=lambda kv: min(k for k, _, _ in kv[1].values()))
        print('  closest: %s -> %s' % (best[0], ', '.join(
            '%s keeps %.0f%% (win %.1f%%)' % (e, k, w)
            for e, (k, _, w) in best[1].items())))
    for name, per in winners:
        print('  %-12s %s' % (name, ', '.join(
            '%s keeps %.0f%% win %.1f%%' % (e, k, w)
            for e, (k, _, w) in per.items())))
    print('  baseline win rate: ' + ', '.join('%s %.1f%%' % (e, w)
                                              for e, w in base_win.items()))

    out = 'runs/scaleout_%s_%s.csv' % (args.symbol.replace('.', ''), args.tf)
    with open(out, 'w', newline='', encoding='utf-8') as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print('\nwrote %s' % out)


if __name__ == '__main__':
    main()
