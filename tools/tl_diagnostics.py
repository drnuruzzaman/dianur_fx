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

BOTH REGISTERED TOLERANCES, ALWAYS, AND THE RAW ROWS ARE KEPT

Tolerance is not a nuisance parameter to be marginalised over -- it defines the
detector, so 0.32 and 0.10 are two different research cells and a result at one
says nothing about the other. They are run together and reported separately,
never pooled.

The per-approach rows are written to disk on every run, not just a summary. A
summary is a claim; the rows are the evidence, and everything needed to
recompute the gate decision without rerunning anything is on them: instrument,
timeframe, trendline id, tolerance, both clocks, the distance at the approach,
the real result, its paired placebo result, the effect and the z. What cannot
be audited should not be believed, including by the person who produced it.
"""

import argparse
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sim.instruments import load
from sim.tl.diagnostics import (DiagParams, by_quality, by_touches, paired,
                                run, summarise)
from sim.tl.engine import Params

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--symbols', default='XAUUSD.a,USDJPY.a')
    ap.add_argument('--tfs', default='15m,1h,4h')
    ap.add_argument('--start', default='2021-01-01')
    ap.add_argument('--end', default=None)
    ap.add_argument('--tols', default='0.32,0.10',
                    help='engine tolerances to run, kept as separate cells')
    ap.add_argument('--tol-atr', type=float, default=None,
                    help='shorthand for a single --tols value')
    ap.add_argument('--min-swing-atr', type=float, default=None,
                    help='override the minimum pivot swing magnitude')
    ap.add_argument('--move-atr', type=float, default=1.0)
    ap.add_argument('--horizon', type=int, default=48)
    ap.add_argument('--out', default=None)
    args = ap.parse_args()

    tols = ([args.tol_atr] if args.tol_atr is not None
            else [float(x) for x in args.tols.split(',')])
    dp = DiagParams(move_atr=args.move_atr, horizon=args.horizon)

    all_ev = []
    rows = []
    cells = [(tol, sym, tf) for tol in tols
             for sym in args.symbols.split(',')
             for tf in args.tfs.split(',')]
    last_tol = None
    for tol, symbol, tf in cells:
        if tol != last_tol:
            print('\n--- tolerance %.2f ---' % tol)
            last_tol = tol
        kw = {'tol_atr': tol}
        if args.min_swing_atr is not None:
            kw['min_swing_atr'] = args.min_swing_atr
        bars = load(symbol, tf, args.start, args.end)
        ev, _ = run(bars, tf, Params(**kw), dp, instrument=symbol)
        # approaches only: this gate asks whether a line is a special place to
        # arrive at. What happens after it breaks is a different question and a
        # different phase -- see tools/r_conversion.py --phase.
        ev = ev[ev.phase == 'approach'] if 'phase' in ev.columns else ev
        if not len(ev):
            print('  %-10s %-4s no approaches' % (symbol, tf))
            continue
        summary = summarise(ev)
        ev['symbol'] = symbol
        all_ev.append(ev)
        line = summary.loc['line']
        plac = summary.loc['placebo'] if 'placebo' in summary.index else None
        rand = summary.loc['random'] if 'random' in summary.index else None
        rows.append({
            'symbol': symbol, 'tf': tf, 'tol_atr': tol, 'bars': len(bars),
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
                 summary.attrs.get('z')), flush=True)

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

    # the raw rows, always -- see the module docstring
    out = args.out or os.path.join(ROOT, 'runs', 'diagnostics',
                                   'tl_diagnostics.csv')
    os.makedirs(os.path.dirname(out), exist_ok=True)
    ev.to_csv(out.replace('.csv', '_raw.csv'), index=False)
    pr = paired(ev)
    pr.to_csv(out, index=False)
    print('\n=== PER-CELL EFFECT (paired where the phase permits pairing) ===')
    cols = ['instrument', 'timeframe', 'tolerance', 'phase', 'is_paired',
            'effect_pp', 'effect_se_pp', 'z_score', 'n_real', 'n_pairs']
    print(pr[cols].drop_duplicates().to_string(index=False))
    print('\nwrote %s (%d approaches) and %s (%d rows)'
          % (out, len(pr), out.replace('.csv', '_raw.csv'), len(ev)))


if __name__ == '__main__':
    main()
