#!/usr/bin/env python
"""
cell_risk.py -- what each cell COSTS to hold, written to runs/cell_risk.csv.

    python tools/cell_risk.py

The four gates answer "is there an edge here". They say nothing about whether
you can survive holding it, and that turned out to be the difference that
mattered: XAUUSD 5m passed all four with +0.1297 avg R in sample and +0.3906 out
of sample -- and a fixed-risk account trading it hit the ruin gate in August
2023, because the cumulative R curve drew down 174 R before the good half
arrived. The edge was real. The position was not survivable.

So this measures survivability and reports it. It is NOT a fifth gate:
tools/gate_compare.py tested that and the gate selected exactly one cell, which
on this data is identical to a rule saying "only trade 4h" -- a conclusion
reached by other means, dressed as a discovery. These are figures the panel
shows beside the verdict, never a verdict that hides a cell.

    dd_R          worst peak-to-trough of the cumulative R curve
    net_over_dd   net R divided by that drawdown. Scale-free, and the one
                  number that ranked the cells the way holding them would:
                  4h 3.47, everything else under 1.3.
    risk_ceiling  the risk-per-trade at which the historical worst drawdown
                  would have cost DD_TOLERANCE of the account. tolerance / |dd_R|
                  -- no free parameters beyond the tolerance itself.

risk_ceiling is HISTORICAL and therefore optimistic: the worst drawdown so far
is a lower bound on the worst drawdown available. Treat it as a ceiling not to
exceed, not a level to aim at.
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

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'runs', 'cell_risk.csv')

#: How much of the account the historical worst drawdown is allowed to cost.
DD_TOLERANCE = 50.0

ERAS = {'IS': ('2021-01-01', None), 'OOS': ('2016-01-01', '2021-01-01')}
FIRST_REAL = {('XAUUSD.a', '30m'): '2018-01-01', ('XAUUSD.a', '15m'): '2018-01-01',
              ('XAUUSD.a', '5m'): '2018-01-01', ('XAUUSD.a', '1m'): '2018-01-01'}
FIELDS = ['symbol', 'tf', 'strategy', 'era', 'span', 'trades', 'win_pct',
          'avg_R', 'net_R', 'dd_R', 'net_over_dd', 'risk_ceiling_pct',
          'avg_win_R', 'avg_loss_R']


def one(symbol, tf, era, override=None):
    start, end = ERAS[era]
    lo = max(start, FIRST_REAL.get((symbol, tf), start))
    try:
        bars = load(symbol, tf, lo, end)
    except Exception:                                        # noqa: BLE001
        return None
    if bars is None or len(bars) < 500:
        return None
    # MEASURE BEFORE ADOPTING. A timeframe outside HORIZON_TFS still runs the
    # base rule on the live chart, and it must keep doing so until there is a
    # record for the horizon-matched length -- so the override measures the
    # candidate without moving what the chart draws.
    strat = (override or {}).get(tf) or strategy_for_tf(tf)
    cfg = Config(start_equity=1_000_000.0, apply_swap=False,
                 size_base='equity', risk_pct=0.5)
    res = Simulator(spec(symbol, tf), fx=FX.build(account_currency()),
                    config=cfg).run(bars, BASELINES[strat](), symbol, tf)
    t = res.trades
    if not len(t):
        return None
    r = t.r_multiple.to_numpy(float)
    cum = np.cumsum(r)
    dd = float((cum - np.maximum.accumulate(cum)).min())
    wins = r > 0
    return {
        'symbol': symbol, 'tf': tf, 'strategy': strat, 'era': era,
        'span': '%s..%s' % (bars.index[0].date(), bars.index[-1].date()),
        'trades': len(r),
        'win_pct': round(100.0 * float(wins.mean()), 1),
        'avg_R': round(float(r.mean()), 4),
        'net_R': round(float(cum[-1]), 1),
        'dd_R': round(dd, 1),
        'net_over_dd': round(float(cum[-1] / abs(dd)), 2) if dd else '',
        # a losing cell has no survivable risk level; leave it blank rather
        # than print a number that implies one exists
        'risk_ceiling_pct': (round(DD_TOLERANCE / abs(dd), 2)
                             if dd and cum[-1] > 0 else ''),
        'avg_win_R': round(float(r[wins].mean()), 2) if wins.any() else '',
        'avg_loss_R': round(float(r[~wins].mean()), 2) if (~wins).any() else '',
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--symbols',
                    default='XAUUSD.a,USDJPY.a,EURUSD.a,GBPUSD.a,AUDUSD.a')
    ap.add_argument('--tfs', default='5m,15m,30m,1h,4h,1d')
    ap.add_argument('--override', default='',
                    help='tf=strategy pairs, e.g. 1m=donchian_n4752')
    ap.add_argument('--out', default=OUT)
    args = ap.parse_args()
    override = dict(kv.split('=', 1) for kv in args.override.split(',') if '=' in kv)

    rows = []
    for sym in args.symbols.split(','):
        for tf in args.tfs.split(','):
            for era in ERAS:
                r = one(sym, tf, era, override)
                if r:
                    rows.append(r)
                    print('  %-10s %-4s %-4s %5d trades  win %5.1f%%  '
                          'avgR %+.4f  dd %8.1fR  net/dd %6s  ceiling %5s%%'
                          % (sym, tf, era, r['trades'], r['win_pct'],
                             r['avg_R'], r['dd_R'], r['net_over_dd'],
                             r['risk_ceiling_pct']))
    with open(args.out, 'w', newline='', encoding='utf-8') as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    print('\nwrote %s  (%d rows, drawdown tolerance %.0f%%)'
          % (args.out, len(rows), DD_TOLERANCE))


if __name__ == '__main__':
    main()
