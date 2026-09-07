#!/usr/bin/env python
"""
struct_diagnostics.py — the placebo test for zones and channel rails.

    python tools/struct_diagnostics.py --tfs 1h,4h --start 2021-01-01

Same question, same method and same barriers as tools/tl_diagnostics.py, so the
answers are directly comparable with the trendline result: when price approaches
a detected structure, does what happens next differ from what happens at the
same shape shifted 1.5 ATR sideways?
"""

import argparse
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sim.instruments import load
from sim.tl.struct_diagnostics import (StructDiagParams, paired, run_channels,
                                       run_zones, summarise)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--symbols', default='EURUSD.a,USDJPY.a,XAUUSD.a')
    ap.add_argument('--tfs', default='1h,4h')
    ap.add_argument('--start', default='2021-01-01')
    ap.add_argument('--end', default=None)
    ap.add_argument('--what', default='zones,channels')
    ap.add_argument('--out', default=None)
    args = ap.parse_args()

    kinds = args.what.split(',')
    all_ev = []
    rows = []
    for symbol in args.symbols.split(','):
        for tf in args.tfs.split(','):
            try:
                bars = load(symbol, tf, args.start, args.end)
            except Exception as e:                       # noqa: BLE001
                print('  %-10s %-3s skipped: %s' % (symbol, tf, e))
                continue
            for kind in kinds:
                fn = run_zones if kind.startswith('zone') else run_channels
                ev = fn(bars, tf)
                if not len(ev):
                    print('  %-10s %-3s %-8s no approaches' % (symbol, tf, kind))
                    continue
                ev['symbol'] = symbol
                all_ev.append(ev)
                s = summarise(ev)
                p = paired(ev)
                rows.append({
                    'symbol': symbol, 'tf': tf, 'kind': kind,
                    'approaches': int(s.loc['structure', 'approaches']),
                    'structure%': s.loc['structure', 'hold%'],
                    'placebo%': s.loc['placebo', 'hold%'],
                    'edge': s.attrs.get('edge_vs_placebo'),
                    'paired_edge': float(p['edge'].iloc[0]) if len(p) else None,
                    'z': float(p['z'].iloc[0]) if len(p) else None,
                })
                print('  %-10s %-3s %-8s n=%-6d struct=%.1f%% placebo=%.1f%% '
                      'edge=%+.2f (paired z=%s)'
                      % (symbol, tf, kind, int(s.loc['structure', 'approaches']),
                         s.loc['structure', 'hold%'], s.loc['placebo', 'hold%'],
                         rows[-1]['paired_edge'], rows[-1]['z']))

    if not rows:
        print('nothing measured')
        return
    print('\n=== per cell ===')
    print(pd.DataFrame(rows).to_string(index=False))

    ev = pd.concat(all_ev, ignore_index=True)
    for kind in kinds:
        sub = ev[ev['kind'] == ('zone' if kind.startswith('zone') else 'channel')]
        if not len(sub):
            continue
        print('\n=== POOLED: %s ===' % kind)
        print(paired(sub).to_string(index=False))
        if 'strength' in sub.columns:
            print('-- by strength --')
            print(paired(sub, by='strength', bins=[0, 50, 65, 80, 101]).to_string(index=False))
        if kind.startswith('chan'):
            for col in ('channel_kind', 'rail'):
                if col in sub.columns:
                    print('-- by %s --' % col)
                    print(paired(sub, by=col).to_string(index=False))

    if args.out:
        ev.to_csv(args.out, index=False)
        print('\nwrote %s (%d rows)' % (args.out, len(ev)))


if __name__ == '__main__':
    main()
