#!/usr/bin/env python
"""
stability.py -- DOES A STABILITY GATE EARN ITS PLACE? Measured, not assumed.

    python tools/stability.py

A fifth gate is a cost: every gate rejects cells, and a gate that rejects at
random makes the survivors look better without making them better. So this does
not add the gate. It measures whether the gate would have HELPED, by the only
test that matters:

    does IN-SAMPLE stability predict OUT-OF-SAMPLE performance?

If cells that were steady in sample go on to do better out of sample than cells
that were lumpy in sample, the metric carries information the existing four
gates do not, and it is worth gating on. If the two groups do the same out of
sample, it is noise dressed as prudence and must not be added.

THE METRIC IS FIXED BEFORE LOOKING. Split the in-sample trade sequence into four
equal-COUNT blocks (not equal-time: equal time gives the quiet blocks almost no
trades and their means are then noise). For each cell:

    q_pos    how many of the four blocks have a positive mean R
    q_worst  the worst block's mean R
    dd_R     worst peak-to-trough of the cumulative R curve

Chosen because they are what actually went wrong: XAUUSD 5m passed all four
existing gates with +0.1297 avg R while its first 379 trades averaged -0.4266
and a fixed-risk account hit the ruin gate before the good half arrived.
"""
import argparse
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sim.core import Config, Simulator
from sim.fx import FX
from sim.instruments import account_currency, load, spec
from sim.strategies import BASELINES, strategy_for_tf

BLOCKS = 4
IS = ('2021-01-01', None)
OOS = ('2016-01-01', '2021-01-01')
#: gold intraday is thin before 2018 -- same rule tools/horizon_sweep.py uses
FIRST_REAL = {('XAUUSD.a', '30m'): '2018-01-01', ('XAUUSD.a', '15m'): '2018-01-01',
              ('XAUUSD.a', '5m'): '2018-01-01', ('XAUUSD.a', '1m'): '2018-01-01'}


def rs(symbol, tf, start, end):
    """The R sequence for one cell, or None."""
    lo = max(start, FIRST_REAL.get((symbol, tf), start))
    try:
        bars = load(symbol, tf, lo, end)
    except Exception:                                        # noqa: BLE001
        return None
    if bars is None or len(bars) < 500:
        return None
    cfg = Config(start_equity=1_000_000.0, apply_swap=False,
                 size_base='equity', risk_pct=0.5)
    res = Simulator(spec(symbol, tf), fx=FX.build(account_currency()),
                    config=cfg).run(bars, BASELINES[strategy_for_tf(tf)](),
                                    symbol, tf)
    r = res.trades.r_multiple.to_numpy(float)
    return r if len(r) else None


def stats(r):
    if r is None or len(r) < BLOCKS * 5:
        return None
    blocks = np.array_split(r, BLOCKS)
    means = [float(b.mean()) for b in blocks]
    cum = np.cumsum(r)
    return {'trades': len(r), 'avg_R': float(r.mean()),
            'q_pos': sum(1 for m in means if m > 0), 'q_worst': min(means),
            'blocks': means,
            'dd_R': float((cum - np.maximum.accumulate(cum)).min())}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--symbols',
                    default='XAUUSD.a,USDJPY.a,EURUSD.a,GBPUSD.a,AUDUSD.a')
    ap.add_argument('--tfs', default='5m,15m,30m,1h,4h')
    ap.add_argument('--q-pos', type=int, default=3, help='blocks that must be +ve')
    ap.add_argument('--q-worst', type=float, default=-0.15)
    args = ap.parse_args()

    rows = []
    print('%-10s %-4s %7s %8s  %-34s %6s %8s %8s | %7s %8s'
          % ('symbol', 'tf', 'trades', 'IS avgR', 'IS blocks (R per quarter)',
             'q_pos', 'q_worst', 'dd_R', 'OOS trd', 'OOS avgR'))
    for sym in args.symbols.split(','):
        for tf in args.tfs.split(','):
            i = stats(rs(sym, tf, *IS))
            o = stats(rs(sym, tf, *OOS))
            if not i or not o:
                continue
            rows.append((sym, tf, i, o))
            print('%-10s %-4s %7d %+8.4f  %-34s %6d %+8.4f %8.1f | %7d %+8.4f'
                  % (sym, tf, i['trades'], i['avg_R'],
                     ' '.join('%+.2f' % m for m in i['blocks']),
                     i['q_pos'], i['q_worst'], i['dd_R'],
                     o['trades'], o['avg_R']))

    stable = [r for r in rows if r[2]['q_pos'] >= args.q_pos
              and r[2]['q_worst'] >= args.q_worst]
    lumpy = [r for r in rows if r not in stable]
    print('\n=== DOES IT PREDICT? gate = q_pos>=%d and q_worst>=%.2f ==='
          % (args.q_pos, args.q_worst))

    def summarise(name, group):
        if not group:
            print('  %-22s (none)' % name)
            return
        ois = np.array([g[3]['avg_R'] for g in group])
        iis = np.array([g[2]['avg_R'] for g in group])
        print('  %-22s n=%2d   IS avgR %+.4f   OOS avgR %+.4f   OOS +ve %d/%d'
              % (name, len(group), iis.mean(), ois.mean(),
                 int((ois > 0).sum()), len(ois)))
    summarise('stable in sample', stable)
    summarise('lumpy in sample', lumpy)

    # THE HONEST COMPARISON. A gate is only useful among cells that would have
    # been ACCEPTED anyway -- rejecting things the existing gates already reject
    # costs nothing and proves nothing.
    print('\n  among cells with a POSITIVE in-sample avg R '
          '(what the current gates would pass):')
    pos = [r for r in rows if r[2]['avg_R'] > 0]
    summarise('  stable', [r for r in pos if r in stable])
    summarise('  lumpy', [r for r in pos if r not in stable])

    if len(rows) > 3:
        x = np.array([r[2]['q_worst'] for r in rows])
        y = np.array([r[3]['avg_R'] for r in rows])
        print('\n  corr(IS worst-quarter, OOS avgR) = %+.3f  over %d cells'
              % (float(np.corrcoef(x, y)[0, 1]), len(rows)))
        x2 = np.array([r[2]['dd_R'] for r in rows])
        print('  corr(IS dd_R,           OOS avgR) = %+.3f'
              % float(np.corrcoef(x2, y)[0, 1]))


if __name__ == '__main__':
    main()
