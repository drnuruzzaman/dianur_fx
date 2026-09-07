#!/usr/bin/env python
"""
gate_compare.py -- four gates vs five, judged on the trades you would have taken.

    python tools/gate_compare.py

A selection rule is only better if the cells it picks do better AFTERWARDS. So
both policies are applied to the IN-SAMPLE record, and then the OUT-OF-SAMPLE
trades of whatever each picked are pooled and scored. Nothing in the OOS window
is allowed to influence the pick -- that is the whole point.

  gate4  the four existing gates, in sample: >=200 trades, beat the 95th
         percentile of 60 time-shifted controls, avg_R>0 and PF>1, avg_R>=0.05
  gate5  gate4 AND stable in sample (>=3 of 4 equal-count blocks positive, and
         no block worse than -0.15 R)

Reported per policy: cells picked, then the POOLED out-of-sample trades --
win rate, average R, total R, and the worst drawdown of the pooled sequence.
Pooled, because that is what an account holding the selected cells experiences;
a per-cell average hides that one cell can contribute ten times the trades.
"""
import argparse
import csv
import glob
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sim.core import Config, Simulator
from sim.fx import FX
from sim.instruments import account_currency, load, spec
from sim.strategies import BASELINES, strategy_for_tf

GATES = ('gate_sample', 'gate_beats_control', 'gate_profitable', 'gate_effect')
IS = ('2021-01-01', None)
OOS = ('2016-01-01', '2021-01-01')
FIRST_REAL = {('XAUUSD.a', '30m'): '2018-01-01', ('XAUUSD.a', '15m'): '2018-01-01',
              ('XAUUSD.a', '5m'): '2018-01-01', ('XAUUSD.a', '1m'): '2018-01-01'}


def truthy(v):
    return str(v).strip().lower() in ('true', '1', 'yes')


def is_gate4():
    """Which cells cleared the four gates IN SAMPLE, from the run files."""
    out = {}
    for path in glob.glob('runs/horizon_matrix*.csv'):
        for r in csv.DictReader(open(path)):
            if r.get('era') != 'IS':
                continue
            if r['strategy'] != strategy_for_tf(r['tf']) and not (
                    r['strategy'] == 'donchian_n20' and strategy_for_tf(r['tf']) == 'donchian'):
                continue
            out[(r['symbol'], r['tf'])] = all(truthy(r[g]) for g in GATES)
    return out


def trades(symbol, tf, start, end):
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
    return res.trades if len(res.trades) else None


def stable(r, q_pos=3, q_worst=-0.15):
    if r is None or len(r) < 20:
        return False
    b = [float(x.mean()) for x in np.array_split(r, 4)]
    return sum(1 for m in b if m > 0) >= q_pos and min(b) >= q_worst


def score(name, picked, oos):
    """Pooled out-of-sample result of holding every picked cell."""
    if not picked:
        print('  %-8s (no cells selected)' % name)
        return
    R, when = [], []
    for cell in picked:
        t = oos.get(cell)
        if t is None:
            continue
        R.append(t.r_multiple.to_numpy(float))
        when.append(t.exit_time.to_numpy())
    if not R:
        print('  %-8s (picked cells have no out-of-sample trades)' % name)
        return
    order = np.argsort(np.concatenate(when))
    r = np.concatenate(R)[order]
    cum = np.cumsum(r)
    dd = float((cum - np.maximum.accumulate(cum)).min())
    wins = r > 0
    print('  %-8s %2d cells %6d trades   win %5.1f%%   avgR %+.4f   '
          'net %+8.1fR   worst DD %7.1fR   avg win %+.2fR / avg loss %+.2fR'
          % (name, len(picked), len(r), 100.0 * wins.mean(), r.mean(), cum[-1],
             dd, r[wins].mean() if wins.any() else 0.0,
             r[~wins].mean() if (~wins).any() else 0.0))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--symbols',
                    default='XAUUSD.a,USDJPY.a,EURUSD.a,GBPUSD.a,AUDUSD.a')
    ap.add_argument('--tfs', default='5m,15m,30m,1h,4h')
    args = ap.parse_args()

    g4 = is_gate4()
    picked4, picked5, oos, rows = [], [], {}, []
    for sym in args.symbols.split(','):
        for tf in args.tfs.split(','):
            cell = (sym, tf)
            ti = trades(sym, tf, *IS)
            to = trades(sym, tf, *OOS)
            if ti is None or to is None:
                continue
            oos[cell] = to
            ri = ti.r_multiple.to_numpy(float)
            p4 = bool(g4.get(cell))
            p5 = p4 and stable(ri)
            if p4:
                picked4.append(cell)
            if p5:
                picked5.append(cell)
            rows.append((sym, tf, len(ri), float(ri.mean()), p4, p5,
                         100.0 * float((to.r_multiple > 0).mean()),
                         float(to.r_multiple.mean()), len(to)))

    print('%-10s %-4s %7s %9s %6s %6s | %8s %9s %8s'
          % ('symbol', 'tf', 'IS trd', 'IS avgR', 'gate4', 'gate5',
             'OOS trd', 'OOS avgR', 'OOS win%'))
    for sym, tf, n, a, p4, p5, w, oa, on in rows:
        print('%-10s %-4s %7d %+9.4f %6s %6s | %8d %+9.4f %7.1f%%'
              % (sym, tf, n, a, 'PASS' if p4 else '.', 'PASS' if p5 else '.',
                 on, oa, w))

    print('\n=== OUT-OF-SAMPLE RESULT OF EACH POLICY (pooled, chronological) ===')
    score('gate4', picked4, oos)
    score('gate5', picked5, oos)
    print('\n  gate4 picked:', ', '.join('%s %s' % c for c in picked4) or '(none)')
    print('  gate5 picked:', ', '.join('%s %s' % c for c in picked5) or '(none)')
    dropped = [c for c in picked4 if c not in picked5]
    if dropped:
        print('  gate5 DROPPED:', ', '.join('%s %s' % c for c in dropped))
        score('dropped', dropped, oos)


if __name__ == '__main__':
    main()
