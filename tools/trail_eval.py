#!/usr/bin/env python
"""
trail_eval.py -- does a trailing exit beat the Rayo TP1 exit, WITH swap, BY ERA?

    python tools/trail_eval.py                       gold 15m/30m/1h, all arms
    python tools/trail_eval.py --tfs 30m --modes none be

THE QUESTION. On 2026-09-17 a pooled 2017-2026 run, before swap, said two
trailing exits beat the registered TP1 exit on gold:

    be      once TP1 prints, stop to entry, run to TP3   30m/1h best
    chand2  once TP1 prints, trail 2 ATR, no target      15m best

Pooled and swap-free is not this project's adoption bar. `be` holds far longer
than a 0.9R target -- and gold's financing is large -- so swap can take the edge
back; and nine years pooled hides whether it held in every regime. This charges
both and splits by the registered eras.

HOW EACH TRADE IS COUNTED -- the two rules that made earlier Rayo numbers wrong:

  * ONE LIVE ORDER PER CELL, from the ticket stream in time order, a new
    ticket only once the previous one has RESOLVED (not merely expired from a
    24h window). The same cursor tools/scalper.py --backtest uses.
  * NOTHING IS DROPPED FOR BEING SLOW. The horizon is 240h and a trade still
    open there is closed at market and counted. At the old 24h horizon 40-70%
    of 15m/30m/1h trades were silently discarded, which put gold 30m at +0.093
    R when it is +0.028. See memory: rayo-24h-horizon-dropped-unresolved-trades.

Costs are the registry's: spread at the captured spec quote plus 0.02 ATR each
side, and swap per night held from fill to exit with the triple-roll weekday
(tools/scalp_portfolio.Cell). The era a trade belongs to is the era it was
POSTED in.
"""

import argparse
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding='utf-8', errors='replace')
    except (AttributeError, ValueError):
        pass

import tools.scalper as SC                                     # noqa: E402
from tools.scalp_portfolio import Cell                         # noqa: E402

ERAS = [('2017-19', '2017-01-01', '2019-01-01'),
        ('2019-21', '2019-01-01', '2021-01-01'),
        ('2021-23', '2021-01-01', '2023-01-01'),
        ('2023-26', '2023-01-01', '2026-09-14')]

LABEL = {'none': 'TP1 (current)', 'be': 'BE->TP3', 'chand2': '2ATR after TP1',
         'chand2f': '2ATR from fill'}


def ms_of(day):
    import pandas as pd
    return int(pd.Timestamp(day, tz='UTC').value // 10 ** 6)


def run(cell, mode, horizon_ms):
    SC.TRAIL[0] = mode
    SC.EXIT_TP[0] = 1
    out, busy = [], -1
    for t in cell.tk:
        if t['ms'] < busy:
            continue
        r = SC.resolve(t, cell.M, cell.expire_ms, horizon_ms)
        if not r:
            continue
        busy = r.get('end_ms', t['ms'])
        if r['outcome'] == 'expired' or r.get('r') is None:
            continue
        fill = r.get('fill_ms') or t['ms']
        cost = cell.cost_r(t)
        swap = cell.swap_r(t, fill, r['end_ms'])
        out.append({'ms': t['ms'], 'gross': r['r'], 'cost': cost, 'swap': swap,
                    'net': r['r'] - cost + swap,
                    'hold_h': (r['end_ms'] - fill) / 3600000.0,
                    'open': r['outcome'] == 'open'})
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--symbol', default='XAUUSD.a')
    ap.add_argument('--tfs', nargs='+', default=['15m', '30m', '1h'])
    ap.add_argument('--modes', nargs='+', default=['none', 'be', 'chand2'])
    ap.add_argument('--horizon-h', type=float, default=240.0)
    ap.add_argument('--from', dest='start', default='2017-01-01')
    ap.add_argument('--to', dest='end', default='2026-09-14')
    args = ap.parse_args()
    horizon_ms = int(args.horizon_h * 3600 * 1000)
    bounds = [(n, ms_of(a), ms_of(b)) for n, a, b in ERAS]

    print('%s, %s .. %s, horizon %.0fh, stop 5 ATR, spread + slip + SWAP'
          % (args.symbol, args.start, args.end, args.horizon_h))
    for tf in args.tfs:
        sys.stderr.write('loading %s %s ...\n' % (args.symbol, tf))
        cell = Cell(args.symbol, tf, args.start, args.end, None, 5.0, 20, 20,
                    50, 12)
        print()
        print('%s %s' % (args.symbol, tf))
        print('%-16s %6s %6s %8s %8s %8s %8s | %s'
              % ('exit', 'n', 'win%', 'gross', 'swap', 'NET', 'hold h',
                 '  '.join('%8s' % e[0] for e in ERAS)))
        for mode in args.modes:
            sys.stderr.write('  %s ...\n' % mode)
            tr = run(cell, mode, horizon_ms)
            if not tr:
                print('%-16s no trades' % LABEL.get(mode, mode))
                continue
            n = len(tr)
            eras = []
            for _, a, b in bounds:
                xs = [x['net'] for x in tr if a <= x['ms'] < b]
                eras.append(('%+8.4f' % (sum(xs) / len(xs))) if xs else '       -')
            pos = sum(1 for e in eras if e.strip() != '-' and float(e) > 0)
            print('%-16s %6d %5.0f%% %+8.4f %+8.4f %+8.4f %8.1f | %s  %d/4'
                  % (LABEL.get(mode, mode), n,
                     100.0 * sum(1 for x in tr if x['net'] > 0) / n,
                     sum(x['gross'] for x in tr) / n,
                     sum(x['swap'] for x in tr) / n,
                     sum(x['net'] for x in tr) / n,
                     statistics.median(x['hold_h'] for x in tr),
                     '  '.join(eras), pos))
    return 0


if __name__ == '__main__':
    sys.exit(main())
