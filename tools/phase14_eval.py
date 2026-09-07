#!/usr/bin/env python
"""
Phase 14 — does conditioning move the TP curve, or does it only look like it?

    python tools/phase14_eval.py logs/phase14_15m.jsonl

THE LADDER, as specified: P(2R | BULL), then P(2R | BULL + PULLBACK), then
+ SWEEP, then + NORMAL_VOL. Each step is narrower and each step is more likely
to produce an impressive number for no reason, so three things are reported
beside every cell and none of them is optional.

  n AND THE RESOLVABLE EFFECT. The 95% half-width on a proportion of size n.
  A cell whose interval is wider than the effect being hunted has not measured
  anything, however extreme its point estimate. This is the column that ends
  most of the ladder.

  A NULL THAT GROWS WITH THE SEARCH. The permutation shuffles the labels and
  takes the BEST cell at that depth, so the bar rises as the search widens --
  which is the correct direction, and the opposite of what "just use finer
  buckets" assumes. A cell must clear the 95th percentile of the best-cell
  distribution at ITS OWN depth.

  BOTH ERAS. A conditional edge that appears in one era is a regime.

WHY THE BAR IS SET HERE AND NOT LOWER. Eleven entry gates, three retest rules,
a session filter and a four-state regime gate have each cleared a naive test in
this project and failed a multiplicity-corrected one. On 5m the regime gate
scored p = 0.049 against a random gate and p = 0.254 against the best-bucket
null: same data, opposite conclusion. Every number below is reported against
the corrected bar for that reason.

Baselines printed first, because a conditional probability means nothing without
the unconditional one beside it.
"""

import argparse
import collections
import datetime
import json
import math
import random
import sys

N_PERM = 2000
BANDS = ['hit_0_5R', 'hit_0_75R', 'hit_1R', 'hit_1_5R', 'hit_2R', 'hit_3R', 'hit_5R']
NICE = {'hit_0_5R': '0.5R', 'hit_0_75R': '0.75R', 'hit_1R': '1R',
        'hit_1_5R': '1.5R', 'hit_2R': '2R', 'hit_3R': '3R', 'hit_5R': '5R'}
ERAS = [('2016-2020', 2016, 2020), ('2021-2026', 2021, 2026)]
MIN_N = 30


def year_of(ms):
    return datetime.datetime.fromtimestamp(ms / 1000, datetime.timezone.utc).year


def half_width(p, n):
    """95% half-width on a proportion -- what this cell can actually resolve."""
    if n <= 0:
        return float('nan')
    return 1.96 * math.sqrt(max(p * (1 - p), 1e-9) / n)


def load(path):
    rows = []
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def sweep_side(r):
    """A sweep counts only when it is the side the trade is fading INTO."""
    s = r.get('sweep')
    if not s:
        return 'none'
    return 'sweep_high' if 'high' in s else 'sweep_low'


#: The ladder, each level adding one condition. Values are (label, predicate).
def ladder(r):
    d = r.get('direction')
    steps = [('%s' % d, d in ('bull', 'bear'))]
    steps.append(('%s+%s' % (d, r.get('phase')), steps[0][1]))
    steps.append(('%s+%s+%s' % (d, r.get('phase'), sweep_side(r)), steps[0][1]))
    steps.append(('%s+%s+%s+%s' % (d, r.get('phase'), sweep_side(r),
                                   r.get('volatility')), steps[0][1]))
    return [s[0] for s in steps if steps[0][1]]


def best_cell_null(labels, values, sizes, seed, n_perm=N_PERM):
    """Distribution of the BEST cell mean when the labels carry no information."""
    rnd = random.Random(seed)
    pool = list(values)
    out = []
    for _ in range(n_perm):
        rnd.shuffle(pool)
        i, best = 0, -1.0
        for k in sizes:
            if k <= 0:
                continue
            m = sum(pool[i:i + k]) / k
            if m > best:
                best = m
            i += k
        out.append(best)
    out.sort()
    return out


def report_depth(rows, depth, band, seed=11):
    vals = [r[band] for r in rows]
    base = sum(vals) / len(vals)
    cells = collections.defaultdict(list)
    for r in rows:
        keys = ladder(r)
        if len(keys) > depth:
            cells[keys[depth]].append(r[band])
    scored = {k: v for k, v in cells.items() if len(v) >= MIN_N}
    if not scored:
        print('    depth %d: no cell reached %d trades' % (depth + 1, MIN_N))
        return None

    means = {k: sum(v) / len(v) for k, v in scored.items()}
    best = max(means, key=means.get)
    sizes = [len(scored[k]) for k in scored]
    nulls = best_cell_null(list(scored), vals, sizes, seed + depth)
    p = sum(1 for x in nulls if x >= means[best]) / len(nulls)

    print('    depth %d  (%d cells scored of %d, %d trades each on average)'
          % (depth + 1, len(scored), len(cells),
             sum(sizes) / len(sizes)))
    for k in sorted(means, key=means.get, reverse=True)[:4]:
        n = len(scored[k])
        hw = half_width(means[k], n)
        lift = means[k] - base
        flag = '  resolvable' if abs(lift) > hw else '  LIFT IS INSIDE THE NOISE'
        print('      %-38s n=%-5d P=%5.1f%%  lift %+5.1f pp  +/-%4.1f pp%s'
              % (k, n, 100 * means[k], 100 * lift, 100 * hw, flag))
    print('      best-cell null at this depth: 95th pct %5.1f%%   best %5.1f%%  ->  p = %.3f  %s'
          % (100 * nulls[int(0.95 * len(nulls))], 100 * means[best], p,
             'CLEARS' if p < 0.05 else 'does NOT clear'))
    return best, p


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('ledger')
    ap.add_argument('--band', default='hit_2R', choices=BANDS)
    ap.add_argument('--all-bands', action='store_true')
    ap.add_argument('--from-year', type=int)
    ap.add_argument('--to-year', type=int)
    args = ap.parse_args()

    rows = load(args.ledger)
    if args.from_year or args.to_year:
        lo = args.from_year or 1900
        hi = args.to_year or 2999
        rows = [r for r in rows if lo <= year_of(r['entry_time']) <= hi]
    if not rows:
        print('no rows in %s' % args.ledger, file=sys.stderr)
        return 2
    tf = rows[0]['tf']

    print('')
    print('PHASE 14 -- CONDITIONAL TP CURVE, %s  (%d trades)' % (tf, len(rows)))
    print('Features read at the SIGNAL bar; labels after the fill. Ties inside a')
    print('bar go to the stop.')
    if args.from_year or args.to_year:
        print('')
        print('  ! WINDOW RESTRICTED to %s-%s. The both-eras test is therefore NOT'
              % (args.from_year or 'start', args.to_year or 'end'))
        print('  ! available, and it is the check that has killed every previous')
        print('  ! lead in this project. Read every cell below as in-sample.')

    print('')
    print('  UNCONDITIONAL CURVE -- the baseline every cell below has to beat')
    print('  %-8s%9s%12s' % ('target', 'P(hit)', '+/- 95%'))
    for b in BANDS:
        v = [r[b] for r in rows]
        m = sum(v) / len(v)
        print('  %-8s%8.1f%%%11.1f pp' % (NICE[b], 100 * m, 100 * half_width(m, len(v))))

    bands = BANDS if args.all_bands else [args.band]
    for band in bands:
        print('')
        print('=' * 78)
        print('LADDER FOR %s' % NICE[band])
        print('=' * 78)
        for depth in range(4):
            report_depth(rows, depth, band)

        if args.from_year or args.to_year:
            continue
        print('')
        print('    BOTH ERAS, at depth 2 (the deepest with usable samples):')
        for era, y0, y1 in ERAS:
            sub = [r for r in rows if y0 <= year_of(r['entry_time']) <= y1]
            if len(sub) < 100:
                continue
            cells = collections.defaultdict(list)
            for r in sub:
                k = ladder(r)
                if len(k) > 1:
                    cells[k[1]].append(r[band])
            scored = {k: v for k, v in cells.items() if len(v) >= MIN_N}
            if not scored:
                print('      %-11s no cell reached %d trades' % (era, MIN_N))
                continue
            bits = ['%s %.0f%% (n=%d)' % (k, 100 * sum(v) / len(v), len(v))
                    for k, v in sorted(scored.items(),
                                       key=lambda kv: -sum(kv[1]) / len(kv[1]))[:3]]
            print('      %-11s %s' % (era, '   '.join(bits)))

    print('')
    print('=' * 78)
    print('A conditional edge worth building on is resolvable at its own sample')
    print('size, clears the best-cell null at its own depth, and holds in both')
    print('eras. A cell that only satisfies the first is a description.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
