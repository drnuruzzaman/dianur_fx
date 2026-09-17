#!/usr/bin/env python
"""
rayo_fade_eval.py -- is FADE mode worth trading? BREAK vs FADE, same harness.

    python tools/rayo_fade_eval.py                    gold 5m..4h
    python tools/rayo_fade_eval.py --tfs 15m 30m

RESEARCH ONLY. Touches no config and no live tool.

WHY. FADE rests a LIMIT at the opposite 20-bar swing in the trend's direction
(uptrend: buy the pullback to the swing low). It was measured once, at the
original 5m / 1.5 ATR / TP3 settings, at -0.1272 R net, and never since -- not
at the 5 ATR stop, not with the 24h horizon bug fixed, not with fixed 12. A
FADE trade looked good on a 15m chart on 2026-09-17 and the question was how to
put it live; this is the measurement that has to come first.

SAME RULES AS THE REGISTRY: tools/scalp_register.measure's harness (one trade
at a time, 1m resolution, ties to the stop, a trade open at the horizon closed
at market and counted), stop 5 ATR, exit TP1, 12-bar expiry. Two cost columns:
net (captured spread + 0.02 ATR a side + swap) and zero (nothing charged).

THE BAR, STATED BEFORE THE RUN. FADE is a live candidate on a frame only if its
median-era net R is > 0 at captured costs AND at least 3 of 4 eras are > 0 AND
it beats BREAK's median-era net R on that frame.
"""

import argparse
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd                                            # noqa: E402

from sim.instruments import load                               # noqa: E402
from sim.strategies.rayo import tickets                        # noqa: E402
from tools import scalp_register as R                          # noqa: E402
from tools.scalp_portfolio import Cell, run_arm                # noqa: E402


def measure(symbol, tf, mode, start, end, zero):
    c = Cell(symbol, tf, start, end, None, 5.0, 20, 20, 50, 12)
    if mode != 'break':
        c.tk = tickets(load(symbol, tf, start, end), mode, 20, 20, 50, 5.0, None)
        for t in c.tk:
            t['symbol'], t['tf'] = symbol, tf
    if zero:
        c.cost_r = lambda t: 0.0
        c.swap_r = lambda t, a, b: 0.0
    h = R.HORIZON_H.get(tf, 240)
    tr = run_arm([c], None, horizon_ms=h * 3600 * 1000, slots=1)
    if not tr:
        return None
    eras = []
    for _, a, b in R.ERAS:
        lo, hi = (int(pd.Timestamp(x).value // 10 ** 6) for x in (a, b))
        sel = [t['net'] for t in tr if lo <= t['ms'] < hi]
        eras.append(round(float(sum(sel)) / len(sel), 4) if sel else None)
    vals = [e for e in eras if e is not None]
    years = (pd.Timestamp(end) - pd.Timestamp(start)).days / 365.25
    nets = [t['net'] for t in tr]
    return {'n': len(tr), 'win': 100.0 * sum(1 for t in tr if t['gross'] > 0) / len(tr),
            'med': round(float(statistics.median(vals)), 4), 'ryr': sum(nets) / years,
            'dd': R.drawdown_r(nets), 'eras': eras}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--symbol', default='XAUUSD.a')
    ap.add_argument('--tfs', nargs='+', default=['5m', '15m', '30m', '1h', '4h'])
    ap.add_argument('--from', dest='start', default='2017-01-01')
    ap.add_argument('--to', dest='end', default='2026-09-14')
    args = ap.parse_args()
    print('%s %s..%s  stop 5 ATR, TP1, fixed 12, one trade at a time, 1m resolution, '
          'no 24h drop' % (args.symbol, args.start, args.end))
    print('%-5s %-6s %-5s %6s %5s %8s %7s %6s | %8s %8s %8s %8s | %s'
          % ('tf', 'mode', 'cost', 'n', 'win%', 'median', 'R/yr', 'dd R',
             *[e[0] for e in R.ERAS], 'verdict'))
    for tf in args.tfs:
        res = {}
        for mode in ('break', 'fade'):
            for zero in (False, True):
                sys.stderr.write('%s %s %s\n' % (tf, mode, 'zero' if zero else 'net'))
                res[(mode, zero)] = m = measure(args.symbol, tf, mode, args.start,
                                                args.end, zero)
                if m is None:
                    print('%-5s %-6s %-5s  no trades' % (tf, mode, 'zero' if zero else 'net'))
                    continue
                verdict = ''
                if mode == 'fade' and not zero and res.get(('break', False)):
                    up = sum(1 for e in m['eras'] if e is not None and e > 0)
                    ok = m['med'] > 0 and up >= 3 and m['med'] > res[('break', False)]['med']
                    verdict = 'CANDIDATE' if ok else 'no (%d/4 eras > 0)' % up
                print('%-5s %-6s %-5s %6d %4.0f%% %+8.4f %+7.1f %6.1f | %s | %s'
                      % (tf, mode, 'zero' if zero else 'net', m['n'], m['win'], m['med'],
                         m['ryr'], m['dd'],
                         ' '.join(('%+8.4f' % e) if e is not None else '       -'
                                  for e in m['eras']), verdict))
                sys.stdout.flush()
    return 0


if __name__ == '__main__':
    sys.exit(main())
