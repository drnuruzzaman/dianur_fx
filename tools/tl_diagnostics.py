#!/usr/bin/env python
"""
tl_diagnostics.py — score the trendline engine itself, not a strategy built on it.

    python tools/tl_diagnostics.py
    python tools/tl_diagnostics.py --tfs 15m,1h,4h --start 2021-01-01

Reports, per symbol and timeframe, what happens when price approaches a CONFIRMED
line versus a parallel placebo line and versus random bars. The number that
matters is `edge_vs_placebo`: if a real line holds no more often than an
arbitrary level 1.5 ATR away, the detector is finding geometry rather than
structure, and no amount of strategy tuning will fix that.
"""

import argparse
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sim.instruments import load
from sim.tl.diagnostics import DiagParams, by_quality, by_touches, run, summarise
from sim.tl.engine import Params


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--symbols', default='XAUUSD.a,USDJPY.a')
    ap.add_argument('--tfs', default='15m,1h,4h')
    ap.add_argument('--start', default='2021-01-01')
    ap.add_argument('--end', default=None)
    ap.add_argument('--tol-atr', type=float, default=None,
                    help='override the engine touch/break tolerance')
    ap.add_argument('--min-swing-atr', type=float, default=None,
                    help='override the minimum pivot swing magnitude')
    ap.add_argument('--move-atr', type=float, default=1.0)
    ap.add_argument('--horizon', type=int, default=48)
    ap.add_argument('--out', default=None)
    args = ap.parse_args()

    kw = {}
    if args.tol_atr is not None:
        kw['tol_atr'] = args.tol_atr
    if args.min_swing_atr is not None:
        kw['min_swing_atr'] = args.min_swing_atr
    params = Params(**kw)
    dp = DiagParams(move_atr=args.move_atr, horizon=args.horizon)

    print('engine params: %s' % {k: getattr(params, k) for k in
                                 ('strength', 'tol_atr', 'min_quality')
                                 if hasattr(params, k)})
    if kw:
        print('overrides: %s' % kw)

    all_ev = []
    rows = []
    for symbol in args.symbols.split(','):
        for tf in args.tfs.split(','):
            bars = load(symbol, tf, args.start, args.end)
            ev, _ = run(bars, tf, params, dp)
            # approaches only: this gate asks whether a line is a special place
            # to arrive at. What happens after it breaks is a different question
            # and a different phase -- see tools/r_conversion.py --phase.
            ev = ev[ev.phase == 'approach'] if 'phase' in ev.columns else ev
            summary = summarise(ev)
            if not len(ev):
                print('  %-10s %-4s no approaches' % (symbol, tf))
                continue
            ev['symbol'] = symbol
            all_ev.append(ev)
            line = summary.loc['line']
            plac = summary.loc['placebo'] if 'placebo' in summary.index else None
            rand = summary.loc['random'] if 'random' in summary.index else None
            rows.append({
                'symbol': symbol, 'tf': tf, 'bars': len(bars),
                'approaches': int(line['approaches']),
                'decided': int(line['decided']),
                'chop%': line['chop%'],
                'line_hold%': line['hold%'],
                'placebo_hold%': plac['hold%'] if plac is not None else None,
                'random_hold%': rand['hold%'] if rand is not None else None,
                'edge': summary.attrs.get('edge_vs_placebo'),
                'edge_se': summary.attrs.get('edge_se'),
                'z': summary.attrs.get('z'),
            })
            print('  %-10s %-4s approaches=%-6d line=%.1f%% placebo=%.1f%% '
                  'edge=%+.1f (z=%s)'
                  % (symbol, tf, int(line['approaches']), line['hold%'],
                     plac['hold%'] if plac is not None else float('nan'),
                     summary.attrs.get('edge_vs_placebo', float('nan')),
                     summary.attrs.get('z')))

    if not rows:
        return
    df = pd.DataFrame(rows)
    print('\n=== does a confirmed line hold more often than a nearby placebo? ===')
    print(df.to_string(index=False))

    ev = pd.concat(all_ev, ignore_index=True)
    print('\n=== pooled, all symbols and timeframes ===')
    print(summarise(ev).to_string())
    pooled = summarise(ev)
    print('edge vs placebo: %+.2f pct pts (se %.2f, z %.2f)'
          % (pooled.attrs.get('edge_vs_placebo', float('nan')),
             pooled.attrs.get('edge_se', float('nan')),
             pooled.attrs.get('z', float('nan'))))

    print('\n=== does quality_score discriminate? (line arm only) ===')
    print(by_quality(ev).to_string(index=False))
    print('\n=== does retest count discriminate? ===')
    print(by_touches(ev).to_string(index=False))

    if args.out:
        ev.to_csv(args.out, index=False)
        print('\nwrote %s (%d events)' % (args.out, len(ev)))


if __name__ == '__main__':
    main()
