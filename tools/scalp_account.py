#!/usr/bin/env python
"""
What a $5,000 account does on the 5m and 15m gold signal.

    python tools/scalp_account.py --equity 5000

Reads logs/scalp.json -- the trade ledger tools/scalp_eval.py wrote -- and turns
R multiples into money by SIZING EVERY TRADE AT A FIXED FRACTION of the running
balance, which is what a real account does and what the R multiple was built to
express: risk `f` of equity, and a trade returning `r` R moves equity by
`f * r`.

THREE THINGS THIS REPORTS THAT A HEADLINE RETURN WOULD HIDE.

  DRAWDOWN. The peak-to-trough fall on the way to the final number, and the
  worst run of consecutive losers. This rule wins about a third of its trades,
  so long losing runs are the normal case, not the tail. On a small account the
  drawdown decides whether the final number is reachable by a human.

  THE INTERVAL, from a CALENDAR-BLOCK bootstrap -- resampling whole months, not
  individual trades, because trades inside a trend are not independent. The
  per-trade edge on both frames has an interval that includes zero, so the band
  on the money is wide enough to contain "you lost some", and printing the point
  estimate alone would be dishonest.

  THE SPREAD RUNG. Every figure is quoted at several spreads, because on 5m the
  broker's spread is the difference between a positive and a negative account.

GROSS OF SWAP. Positions on these frames are held hours to days, so overnight
financing is real and is NOT modelled here; it makes every number below
somewhat worse. Sizing ignores the broker's 0.01 lot step, which on a $5,000
account rounds risk by a few percent either way.
"""

import argparse
import datetime
import json
import os
import random
import sys
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEDGER = os.path.join(ROOT, 'logs', 'scalp.json')
LADDER = [('24 pts (this broker)', 0.24), ('18 pts', 0.18),
          ('12 pts (raw)', 0.12), ('8 pts (best case)', 0.08)]
RISKS = [0.005, 0.01, 0.02]
N_BOOT = 2000


def net_r(t, spread):
    risk = t.get('risk') or 0.0
    return t['r'] if risk <= 0 else t['r'] - spread / risk


def walk(rs, equity, f):
    """Fixed-fractional compounding. Returns final, max drawdown, worst streak."""
    peak = bal = equity
    dd = 0.0
    streak = worst = 0
    for r in rs:
        bal *= (1 + f * r)
        if bal <= 0:
            return 0.0, 1.0, max(worst, streak)
        peak = max(peak, bal)
        dd = max(dd, (peak - bal) / peak)
        if r <= 0:
            streak += 1
            worst = max(worst, streak)
        else:
            streak = 0
    return bal, dd, worst


def months(trades):
    out = defaultdict(list)
    for t in trades:
        d = datetime.datetime.fromtimestamp(t['entryTime'] / 1000, datetime.timezone.utc)
        out[(d.year, d.month)].append(t)
    return [out[k] for k in sorted(out)]


def boot_final(blocks, spread, equity, f, seed=17, n=N_BOOT):
    """Resample whole MONTHS with replacement, keeping each month's order."""
    rnd = random.Random(seed)
    k = len(blocks)
    outs = []
    for _ in range(n):
        rs = []
        for _ in range(k):
            rs.extend(net_r(t, spread) for t in blocks[rnd.randrange(k)])
        outs.append(walk(rs, equity, f)[0])
    outs.sort()
    return outs[int(0.05 * n)], outs[int(0.5 * n)], outs[int(0.95 * n)]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--equity', type=float, default=5000.0)
    ap.add_argument('--ledger', default=LEDGER)
    args = ap.parse_args()

    if not os.path.exists(args.ledger):
        print('no ledger at %s -- run tools/scalp_eval.py --json first' % args.ledger,
              file=sys.stderr)
        return 2
    data = json.load(open(args.ledger))

    print('')
    print('A $%s ACCOUNT ON THE GOLD SIGNAL. Fixed-fractional sizing, compounded,'
          % format(round(args.equity), ','))
    print('over the actual trade sequence 2016-2026. GROSS OF SWAP.')

    for cell in sorted(data, key=lambda c: c.split('|')[1]):
        res = data[cell]
        trades = res['runs']['shipped']
        tf = res['tf']
        t0 = min(t['entryTime'] for t in trades) / 1000
        t1 = max(t['entryTime'] for t in trades) / 1000
        years = (t1 - t0) / (365.25 * 86400)
        blocks = months(trades)

        print('')
        print('=' * 78)
        print('XAUUSD %s   %d trades over %.1f years  =  %.0f per year, %.1f per week'
              % (tf, len(trades), years, len(trades) / years,
                 len(trades) / years / 52.0))
        print('=' * 78)

        for label, spread in LADDER:
            rs = [net_r(t, spread) for t in trades]
            print('  %s   (net %+.1f R over the decade)' % (label, sum(rs)))
            print('    %-8s%12s%10s%10s%9s   %s'
                  % ('risk', 'final', 'CAGR', 'max DD', 'worst', '90% band on final'))
            for f in RISKS:
                fin, dd, streak = walk(rs, args.equity, f)
                cagr = ((fin / args.equity) ** (1 / years) - 1) * 100 if fin > 0 else -100
                lo, mid, hi = boot_final(blocks, spread, args.equity, f)
                print('    %-8s%12s%9.1f%%%9.1f%%%9d   %s to %s  (mid %s)'
                      % ('%.1f%%' % (f * 100), '$%s' % format(round(fin), ','),
                         cagr, dd * 100, streak,
                         '$%s' % format(round(lo), ','), '$%s' % format(round(hi), ','),
                         '$%s' % format(round(mid), ',')))
            print('')

    print('=' * 78)
    print('READ THE BAND, NOT THE POINT. The per-trade edge on both frames has a')
    print('confidence interval that includes zero, so the honest statement is the')
    print('90% band -- and its lower end is a loss on every rung. The point')
    print('estimate is the middle of a wide distribution, not a forecast.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
