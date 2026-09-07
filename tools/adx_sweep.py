#!/usr/bin/env python
"""
adx_sweep.py -- does an ADX trend filter improve the Donchian cells?

    python tools/adx_sweep.py

The filter is pre-committed at ADX >= 20 / 25 / 30 on the horizon-matched rule
for each timeframe, both eras, against the unfiltered cell. See
sim/strategies/adxfilter.py for why this filter and not another, and for the two
reasons it is expected to struggle.

THE BAR, fixed before the run and the same one every other change was held to:

    a filter earns its place only if it RAISES avg R in BOTH eras while keeping
    at least 200 trades in both. Improving one era is what a filter fitted to
    that era looks like, and improving avg R by discarding trades until only a
    lucky handful remain is not an improvement at all.

Trade count is printed beside every row because it is the thing that usually
kills a filter here: the last one cut 363 trades to 167 and stopped being
measurable.
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
from sim.strategies.adxfilter import WithADX

ERAS = [('OOS 2016-2020', '2016-01-01', '2021-01-01'),
        ('IS  2021-2026', '2021-01-01', None)]
FIRST_REAL = {('XAUUSD.a', '30m'): '2018-01-01', ('XAUUSD.a', '15m'): '2018-01-01',
              ('XAUUSD.a', '5m'): '2018-01-01', ('XAUUSD.a', '1m'): '2018-01-01'}
THRESHOLDS = (20.0, 25.0, 30.0)
FLOOR = 200


def run(symbol, tf, start, end, min_adx):
    lo = max(start, FIRST_REAL.get((symbol, tf), start))
    try:
        bars = load(symbol, tf, lo, end)
    except Exception:                                        # noqa: BLE001
        return None
    if bars is None or len(bars) < 500:
        return None
    inner = BASELINES[strategy_for_tf(tf)]()
    st = inner if min_adx is None else WithADX(inner, min_adx)
    res = Simulator(spec(symbol, tf), fx=FX.build(account_currency()),
                    config=Config(start_equity=1_000_000.0, apply_swap=False,
                                  size_base='equity', risk_pct=0.5)).run(
        bars, st, symbol, tf)
    t = res.trades
    if not len(t):
        return {'trades': 0, 'avg_R': float('nan'), 'net_R': 0.0,
                'win': float('nan'), 'pf': float('nan'), 'dd_R': 0.0}
    r = t.r_multiple.to_numpy(float)
    cum = np.cumsum(r)
    gain = float(r[r > 0].sum())
    loss = float(-r[r < 0].sum())
    return {'trades': len(r), 'avg_R': float(r.mean()), 'net_R': float(cum[-1]),
            'win': 100.0 * float((r > 0).mean()),
            'pf': (gain / loss) if loss else float('inf'),
            'dd_R': float((cum - np.maximum.accumulate(cum)).min())}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--symbol', default='XAUUSD.a')
    ap.add_argument('--tfs', default='5m,15m,30m,1h,4h')
    args = ap.parse_args()

    rows, better = [], {}
    for tf in args.tfs.split(','):
        print('\n=== %s %s (%s) ===' % (args.symbol, tf, strategy_for_tf(tf)))
        print('  %-14s %-16s %7s %7s %9s %8s %7s %9s'
              % ('era', 'filter', 'trades', 'win%', 'avg_R', 'net_R', 'pf', 'dd_R'))
        for era, start, end in ERAS:
            base = run(args.symbol, tf, start, end, None)
            if not base:
                continue
            print('  %-14s %-16s %7d %7.1f %+9.4f %+8.1f %7.3f %9.1f'
                  % (era, 'none', base['trades'], base['win'], base['avg_R'],
                     base['net_R'], base['pf'], base['dd_R']))
            for thr in THRESHOLDS:
                st = run(args.symbol, tf, start, end, thr)
                if not st or not st['trades']:
                    continue
                kept = 100.0 * st['trades'] / base['trades']
                flag = ''
                if st['trades'] < FLOOR:
                    flag = '  under the %d-trade floor' % FLOOR
                elif st['avg_R'] > base['avg_R']:
                    flag = '  avg_R up'
                    better.setdefault((tf, thr), set()).add(era)
                print('  %-14s %-16s %7d %7.1f %+9.4f %+8.1f %7.3f %9.1f'
                      '   keeps %3.0f%%%s'
                      % (era, 'ADX >= %g' % thr, st['trades'], st['win'],
                         st['avg_R'], st['net_R'], st['pf'], st['dd_R'],
                         kept, flag))
                rows.append(dict(symbol=args.symbol, tf=tf, era=era,
                                 min_adx=thr, base_avg_R=round(base['avg_R'], 4),
                                 base_trades=base['trades'], kept_pct=round(kept, 1),
                                 **{k: (round(v, 4) if isinstance(v, float) else v)
                                    for k, v in st.items()}))

    print('\n=== verdict: avg_R up in BOTH eras AND >=%d trades in both ===' % FLOOR)
    won = []
    for (tf, thr), eras in better.items():
        if len(eras) < len(ERAS):
            continue
        counts = [r['trades'] for r in rows if r['tf'] == tf and r['min_adx'] == thr]
        if counts and min(counts) >= FLOOR:
            won.append((tf, thr))
    if won:
        for tf, thr in won:
            print('  %s  ADX >= %g' % (tf, thr))
    else:
        print('  NONE.')
        part = [(tf, thr) for (tf, thr), e in better.items() if len(e) == len(ERAS)]
        if part:
            print('  avg_R up in both eras but under the trade floor: '
                  + ', '.join('%s ADX>=%g' % x for x in part))

    if rows:
        out = 'runs/adx_%s.csv' % args.symbol.replace('.', '')
        with open(out, 'w', newline='', encoding='utf-8') as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
        print('\nwrote %s' % out)


if __name__ == '__main__':
    main()
