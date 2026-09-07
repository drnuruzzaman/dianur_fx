#!/usr/bin/env python
"""
zone_r_conversion.py — does the ZONE edge convert into money?

    python tools/zone_r_conversion.py --symbols EURUSD.a,USDJPY.a --tfs 1h,4h

The structural gate found zones holding ~5 percentage points more often than a
parallel placebo band, replicating across three disjoint eras at the same
magnitude. That is a real measurement and it is still not an edge, because a
hold rate is not expectancy. The same three things stand between them as for
trendlines:

  GEOMETRY. An approach fires anywhere within near_atr of the zone edge, so
  entry sits off the edge by ~0.2 ATR on average. A bounce with symmetric 1.0
  ATR barriers therefore risks 1.2 to make 0.8 -- R:R 0.67, not 1:1. At a 57%
  hold rate that is barely positive before a cent of cost.

  THE TRADE-OFF. Tightening the stop improves R:R, lowers the hit rate, and
  inflates friction (a fixed price over a smaller denominator). Neither end is
  obviously right, so the grid is swept rather than argued about.

  FRICTION. friction_R = spread/stop_price + 2*slippage_atr/stop_atr.

The probability is RE-MEASURED at every geometry rather than assumed constant,
and the placebo band is re-measured with the same asymmetry, so the comparison
stays honest once the null stops being 50/50.

The gate passes only if some geometry gives a positive expectancy AFTER
friction AND beats its own placebo there. A positive number the placebo also
achieves is geometry, not structure.

This is a SWEEP, not a backtest -- the same caveat r_conversion.py carries. It
has no next-bar-open fill, no equity-scaled sizing and no account that can run
out of money. The gold breakout passed a sweep like this at t=6.26 and then lost
10% in the real simulator, so treat surviving geometries as hypotheses to
register, not as results.
"""

import argparse
import itertools
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sim.instruments import load, spec
from sim.tl.struct_diagnostics import (StructDiagParams, resolve_approaches,
                                       zone_approaches)
from sim.tl.zones import ZoneParams
from tools.r_conversion import expectancy, friction_r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--symbols', default='EURUSD.a,USDJPY.a')
    ap.add_argument('--tfs', default='1h,4h')
    ap.add_argument('--start', default='2021-01-01')
    ap.add_argument('--end', default=None)
    ap.add_argument('--stops', default='0.4,0.6,0.8,1.0,1.5')
    ap.add_argument('--targets', default='0.8,1.2,1.6,2.0,3.0')
    ap.add_argument('--out', default=None)
    args = ap.parse_args()

    stops = [float(x) for x in args.stops.split(',')]
    targets = [float(x) for x in args.targets.split(',')]
    print('sweeping %d geometries per cell; probability re-measured at each\n'
          % (len(stops) * len(targets)))

    rows = []
    for symbol in args.symbols.split(','):
        for tf in args.tfs.split(','):
            try:
                bars = load(symbol, tf, args.start, args.end)
            except Exception as e:                        # noqa: BLE001
                print('  %s %s skipped: %s' % (symbol, tf, e))
                continue
            sp = spec(symbol, tf)
            print('%s %s  %s..%s  %d bars'
                  % (symbol, tf, bars.index[0].date(), bars.index[-1].date(),
                     len(bars)), flush=True)
            # Detect once; every geometry replays the same approach set.
            aps, close, atr, n_bars = zone_approaches(bars, tf, ZoneParams())
            if not aps:
                print('  no approaches')
                continue
            for stop_atr, target_atr in itertools.product(stops, targets):
                ev = resolve_approaches(
                    aps, close, atr, n_bars,
                    StructDiagParams(stop_atr=stop_atr, target_atr=target_atr))
                if not len(ev):
                    continue
                st = ev[ev.arm == 'structure']
                pl = ev[ev.arm == 'placebo']
                if not len(st) or not len(pl):
                    continue
                dist = float(st.dist_atr.mean())
                atr_price = float(st.atr.mean())
                if target_atr - dist <= 0:
                    continue                  # target inside the entry offset
                fr = friction_r(sp, atr_price, stop_atr, dist)
                p_s = float((st.outcome == 'hold').mean())
                p_p = float((pl.outcome == 'hold').mean())
                e_s, rr = expectancy(p_s, stop_atr, target_atr, dist)
                e_p, _ = expectancy(p_p, stop_atr, target_atr, dist)
                n = len(st)
                se = np.sqrt(p_s * (1 - p_s) / n) * (rr + 1)
                rows.append({
                    'symbol': symbol, 'tf': tf,
                    'stop_atr': stop_atr, 'target_atr': target_atr,
                    'rr': round(rr, 2), 'n': n,
                    'hold_zone': round(100 * p_s, 1),
                    'hold_placebo': round(100 * p_p, 1),
                    'gross_R': round(e_s, 4), 'placebo_R': round(e_p, 4),
                    'friction_R': round(fr, 4),
                    'net_R': round(e_s - fr, 4),
                    'net_vs_placebo': round(e_s - e_p, 4),
                    't': round((e_s - fr) / se, 2) if se else np.nan,
                })
                print('  stop %.1f tgt %.1f  R:R %.2f  hold %.1f%% (plac %.1f%%)'
                      '  gross %+.4f  fric %.4f  NET %+.4f  vs_plac %+.4f'
                      % (stop_atr, target_atr, rr, 100 * p_s, 100 * p_p,
                         e_s, fr, e_s - fr, e_s - e_p), flush=True)

    if not rows:
        print('nothing measured')
        return
    df = pd.DataFrame(rows)
    good = df[(df.net_R > 0) & (df.net_vs_placebo > 0)]
    print('\n=== GATE: economic edge ===')
    print('geometries with positive net R AND beating placebo: %d of %d'
          % (len(good), len(df)))
    if len(good):
        print(good.sort_values('net_R', ascending=False)
              .head(15).to_string(index=False))

    print('\n=== pooled across cells, by geometry ===')
    agg = (df.groupby(['stop_atr', 'target_atr'])
             .agg(cells=('n', 'size'), n=('n', 'sum'), rr=('rr', 'mean'),
                  hold_zone=('hold_zone', 'mean'),
                  hold_placebo=('hold_placebo', 'mean'),
                  friction_R=('friction_R', 'mean'),
                  net_R=('net_R', 'mean'),
                  net_vs_placebo=('net_vs_placebo', 'mean'))
             .reset_index().sort_values('net_R', ascending=False))
    print(agg.round(4).to_string(index=False))

    out = args.out or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        'runs', 'struct', 'zone_r_conversion.csv')
    os.makedirs(os.path.dirname(out), exist_ok=True)
    df.to_csv(out, index=False)
    print('\nwrote %s' % out)


if __name__ == '__main__':
    main()
