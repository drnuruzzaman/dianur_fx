#!/usr/bin/env python
"""
Which market regime is the gold rule actually paid in?

    python tools/regime_eval.py

WHAT A REGIME IS HERE. `js/chart/regime.js` -- the module the Trend read panel
already draws from, and the one labelling every trade in this study, so there is
no second implementation to drift. Three causal readings combined:

    SLOPE     EMA(21) vs EMA(50), in ATR units so gold and yen mean the same
    BREADTH   where price sits in its own 40-bar range; pinned mid-range is
              sideways however the EMAs look
    ENERGY    ATR now against ATR over 56 bars; a contracting range that is
              still directional is TRANSITION, not TREND

giving TRENDING_UP, TRENDING_DOWN, SIDEWAYS, TRANSITION. Every input at bar i
ends at bar i, and the label is taken at the SIGNAL bar, not the fill -- a label
read off the entry bar would be half a bar of hindsight.

WHY THE CONTROL COLUMN IS NOT OPTIONAL. A regime filter IS an entry gate, and
eleven entry gates have been measured in this project against a random gate held
to the same retention rate; none beat it. Splitting trades by regime and keeping
the best bucket is the same act that produced those eleven, plus a multiplicity
problem: with four regimes, one of them looks good by construction. So each
regime is compared against a RANDOM gate keeping the same number of trades, and
the best regime is additionally compared against a best-bucket permutation null,
which is the distribution of the MAXIMUM and therefore the honest bar.

WHAT WOULD MAKE A REGIME GATE WORTH SHIPPING: positive in that regime, beating
the random gate, clearing the best-bucket null, and doing all three in BOTH
eras. Anything less is a description of the past.

Net of the 8-point spread and quoted per trade, so the numbers here are the same
ones the chart tags are drawn in.
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
SPREAD = 0.08                 # 8 points on gold, the rung the app now quotes
ERAS = [('2016-2020', 2016, 2020), ('2021-2026', 2021, 2026)]
N_PERM = 2000
N_BOOT = 2000
MIN_N = 30
LABEL = {'trending_up': 'TRENDING UP', 'trending_down': 'TRENDING DOWN',
         'sideways': 'SIDEWAYS', 'transition': 'TRANSITION', None: '(unlabelled)'}
ORDER = ['trending_up', 'trending_down', 'sideways', 'transition', None]


def year_of(ms):
    return datetime.datetime.fromtimestamp(ms / 1000, datetime.timezone.utc).year


def net_r(t):
    risk = t.get('risk') or 0.0
    return t['r'] if risk <= 0 else t['r'] - SPREAD / risk


def mean_ci(vals, seed=23, n=N_BOOT):
    if not vals:
        return (float('nan'), float('nan'))
    rnd = random.Random(seed)
    k = len(vals)
    s = sorted(sum(vals[rnd.randrange(k)] for _ in range(k)) / k for _ in range(n))
    return (s[int(0.025 * n)], s[int(0.975 * n)])


def aligned(t):
    """Did the trade go WITH the regime's direction?"""
    if t.get('side') is None or not t.get('regime'):
        return None
    if t['regime'] == 'trending_up':
        return t['side'] > 0
    if t['regime'] == 'trending_down':
        return t['side'] < 0
    return None


def table(trades, title, seed=5):
    vals_all = [net_r(t) for t in trades]
    base = sum(vals_all) / len(vals_all) if vals_all else float('nan')
    print('  %s   (%d trades, %+.4f R each overall)' % (title, len(trades), base))
    print('    %-16s%7s%7s%10s%11s   %s'
          % ('regime', 'n', 'share', 'net R', 'per trade', '95% CI on per-trade'))

    buckets = defaultdict(list)
    for t in trades:
        buckets[t.get('regime')].append(net_r(t))

    scored = {}
    for k in ORDER:
        v = buckets.get(k)
        if not v:
            continue
        m = sum(v) / len(v)
        if len(v) >= MIN_N:
            scored[k] = m
        lo, hi = mean_ci(v, seed=seed + len(v))
        thin = '' if len(v) >= MIN_N else '   (thin)'
        print('    %-16s%7d%6.0f%%%10.1f%11.4f   [%+.4f, %+.4f]%s'
              % (LABEL[k], len(v), 100 * len(v) / len(trades), sum(v), m, lo, hi, thin))
    return buckets, scored


def gate_tests(trades, buckets, scored, seed=7):
    """Each scored regime against a random gate of the same size; then the best
    regime against the distribution of the MAXIMUM over regimes."""
    if not scored:
        print('    no regime reached %d trades' % MIN_N)
        return
    vals_all = [net_r(t) for t in trades]
    rnd = random.Random(seed)

    print('')
    print('    %-16s%9s%14s%12s   %s'
          % ('regime gate', 'kept', 'gate R/trade', 'random gate', 'p'))
    for k in sorted(scored, key=lambda x: -scored[x]):
        keep = len(buckets[k])
        draws = sorted(sum(rnd.choice(vals_all) for _ in range(keep)) / keep
                       for _ in range(N_PERM))
        p = sum(1 for x in draws if x >= scored[k]) / len(draws)
        print('    %-16s%9d%14.4f%12.4f   %.3f%s'
              % (LABEL[k], keep, scored[k], sum(draws) / len(draws), p,
                 '  <-- beats a random gate' if p < 0.05 else ''))

    # BEST-BUCKET NULL: shuffle the regime labels, keep the bucket sizes, take
    # the best each time. This is what choosing the best regime actually samples.
    sizes = [len(buckets[k]) for k in scored]
    best_k = max(scored, key=scored.get)
    pool = list(vals_all)
    nulls = []
    for _ in range(N_PERM):
        rnd.shuffle(pool)
        i, best = 0, -1e18
        for sz in sizes:
            m = sum(pool[i:i + sz]) / sz
            if m > best:
                best = m
            i += sz
        nulls.append(best)
    nulls.sort()
    p = sum(1 for x in nulls if x >= scored[best_k]) / len(nulls)
    print('    best-bucket null over %d shuffles: mean %+.4f, 95th pct %+.4f'
          % (N_PERM, sum(nulls) / len(nulls), nulls[int(0.95 * len(nulls))]))
    print('    best regime %s at %+.4f  ->  p = %.3f  %s'
          % (LABEL[best_k], scored[best_k], p,
             'CLEARS the null' if p < 0.05 else 'does NOT clear the null'))
    return best_k


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--ledger', default=LEDGER)
    args = ap.parse_args()

    if not os.path.exists(args.ledger):
        print('no ledger -- run tools/scalp_eval.py --json %s first' % args.ledger,
              file=sys.stderr)
        return 2
    data = json.load(open(args.ledger))

    print('')
    print('WHICH REGIME IS THE RULE PAID IN? Labels from js/chart/regime.js at')
    print('the SIGNAL bar. Net of 8 points of spread, per trade.')

    for cell in sorted(data, key=lambda c: c.split('|')[1]):
        res = data[cell]
        trades = res['runs']['shipped']
        if not trades or trades[0].get('regime') is None and 'regime' not in trades[0]:
            print('\n  %s has no regime labels -- re-run tools/scalp_eval.py'
                  % cell, file=sys.stderr)
            continue
        print('')
        print('=' * 78)
        print('XAUUSD %s' % res['tf'])
        print('=' * 78)

        buckets, scored = table(trades, 'ALL YEARS')
        best = gate_tests(trades, buckets, scored)

        print('')
        print('    DOES IT HOLD IN BOTH ERAS?')
        for era, y0, y1 in ERAS:
            rows = [t for t in trades if y0 <= year_of(t['entryTime']) <= y1]
            if not rows:
                continue
            b = defaultdict(list)
            for t in rows:
                b[t.get('regime')].append(net_r(t))
            bits = []
            for k in ORDER:
                if k in scored and b.get(k):
                    bits.append('%s n=%d %+.4f' % (LABEL[k].split()[0][:5], len(b[k]),
                                                   sum(b[k]) / len(b[k])))
            print('    %-11s %s' % (era, '   '.join(bits)))

        # ALIGNMENT, the other thing a regime is supposed to buy: trading WITH
        # the trend rather than against it.
        with_r = [net_r(t) for t in trades if aligned(t) is True]
        against = [net_r(t) for t in trades if aligned(t) is False]
        if with_r and against:
            lo1, hi1 = mean_ci(with_r, seed=31)
            lo2, hi2 = mean_ci(against, seed=37)
            print('')
            print('    TRADING WITH vs AGAINST THE REGIME (trending bars only)')
            print('    %-16s%7d%11.4f   [%+.4f, %+.4f]'
                  % ('with', len(with_r), sum(with_r) / len(with_r), lo1, hi1))
            print('    %-16s%7d%11.4f   [%+.4f, %+.4f]'
                  % ('against', len(against), sum(against) / len(against), lo2, hi2))

    print('')
    print('=' * 78)
    print('A regime gate is worth shipping only if it beats a random gate of the')
    print('same size, clears the best-bucket null, and does both in BOTH eras.')
    print('Eleven entry gates have failed the first of those already.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
