#!/usr/bin/env python
"""
A 5m and 15m gold signal that survives its own spread.

    python tools/scalp_eval.py

THE REQUEST. "Build the 5m and 15m gold scalping signal with tighter spread."
A signal cannot produce a tighter spread -- that is bought at the venue -- so the
build is: take the rule that already measures positive GROSS on these frames, and
find out (a) what spread it needs to be positive NET, and (b) whether trading only
certain hours buys enough to matter.

WHY HOURS, AND WHY HERE. The one effect in this project that survived every test
put to it was an hour effect on 4h gold: the 21:00 UTC bucket at +1.019 R against
a best-bucket permutation null at p = 0.004, replicating across both eras. Its
stated weakness was sample -- 29 trades in eight years -- and the README says
plainly that it is a lead, not a result, and needs more observations.

5m and 15m are where those observations are. The same rule takes 2306 trades on
5m and 1537 on 15m against 687 on 4h. Using the fast frames to TEST a lead found
on a slow one is the honest use of high frequency, and it is the only reason this
study is on a scalping timeframe at all.

WHAT IS MEASURED

  1. THE COST LADDER. Net R at 24 points (this broker, captured 2026-08-22),
     18, 12 and 8. Cost is charged PER TRADE as spread / risk, because a trade
     with a tight stop pays a larger fraction of its risk than a wide one, and a
     cell-level average hides exactly the trades a scalper takes most of.

  2. HOUR BUCKETS, net of the ladder's middle rung, with calendar-block
     intervals -- trades inside a trend are not independent observations.

  3. THE BEST-BUCKET PERMUTATION NULL. Hour labels are shuffled and the best
     bucket recorded, so the null is the distribution of the MAXIMUM, which is
     what picking the best hour actually produces. This is the test the 4h
     result passed and it is the one that matters: with 24 buckets, something
     always looks good.

  4. MATCHED CONTROLS. `randEntry` and `randSide` carry the rule's bias, stop
     width, exit and trade count. And a RANDOM HOUR GATE keeping the same number
     of trades as the chosen hours: "trade fewer hours" and "trade THESE hours"
     are different claims.

  5. BOTH ERAS. A result that appears in one is a regime.

GROSS elsewhere in this project means before costs; here nothing is gross -- the
whole point is the net column.
"""

import argparse
import datetime
import glob
import gzip
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NODE = shutil.which('node')
RUNNER = os.path.join(ROOT, 'tools', 'scalp_runner.mjs')

SYMBOL = 'XAUUSD.a'
TFS = ['5m', '15m']
#: Round-trip spread in PRICE units. 0.24 is this broker's captured 24 points;
#: the rest are the raw-spread accounts worth pricing before writing any signal.
LADDER = [('24 pts (this broker)', 0.24), ('18 pts', 0.18),
          ('12 pts (raw)', 0.12), ('8 pts (best case)', 0.08)]
COST_FOR_HOURS = 0.12          # the rung the hour study is judged at
ERAS = [('2016-2020', 2016, 2020), ('2021-2026', 2021, 2026)]
N_BLOCKS = 20
N_BOOT = 2000
N_PERM = 2000
MIN_BUCKET = 30                # an hour with fewer trades is not scored


def load_bars(symbol, tf, year_from):
    rows = []
    for f in sorted(glob.glob(os.path.join(ROOT, 'data', 'bars', symbol, tf, '*.csv.gz'))):
        if int(os.path.basename(f)[:4]) < year_from:
            continue
        with gzip.open(f, 'rt') as fh:
            head = fh.readline().strip().split(',')
            ix = {k: i for i, k in enumerate(head)}
            for line in fh:
                c = line.strip().split(',')
                if len(c) < 5:
                    continue
                rows.append({'t': int(c[ix['ts']]) * 1000,
                             'o': float(c[ix['open']]), 'h': float(c[ix['high']]),
                             'l': float(c[ix['low']]), 'c': float(c[ix['close']]), 'v': 1})
    rows.sort(key=lambda r: r['t'])
    return rows


def year_of(ms):
    return datetime.datetime.fromtimestamp(ms / 1000, datetime.timezone.utc).year


def net_r(t, spread):
    """This trade's R after paying the spread once, entry and exit."""
    risk = t.get('risk') or 0.0
    if risk <= 0:
        return t['r']
    return t['r'] - spread / risk


def blocks(trades, spread, lo_t, hi_t, n=N_BLOCKS):
    if hi_t <= lo_t:
        return [0.0] * n
    width = (hi_t - lo_t) / n
    out = [0.0] * n
    for t in trades:
        b = min(n - 1, max(0, int((t['entryTime'] - lo_t) / width)))
        out[b] += net_r(t, spread)
    return out


def paired_ci(a, b, seed=17, n=N_BOOT):
    k = min(len(a), len(b))
    if not k:
        return (float('nan'), float('nan'))
    d = [a[j] - b[j] for j in range(k)]
    rnd = random.Random(seed)
    s = sorted(sum(d[rnd.randrange(k)] for _ in range(k)) for _ in range(n))
    return (s[int(0.025 * n)], s[int(0.975 * n)])


def mean_ci(vals, seed=23, n=N_BOOT):
    if not vals:
        return (float('nan'), float('nan'))
    rnd = random.Random(seed)
    k = len(vals)
    s = sorted(sum(vals[rnd.randrange(k)] for _ in range(k)) / k for _ in range(n))
    return (s[int(0.025 * n)], s[int(0.975 * n)])


def era_of(t, y0, y1):
    return y0 <= year_of(t['entryTime']) <= y1


def run_cell(tf, year_from, heap):
    bars = load_bars(SYMBOL, tf, year_from)
    tmp = tempfile.mkdtemp(prefix='scalp_')
    try:
        bp = os.path.join(tmp, 'bars.json')
        with open(bp, 'w') as fh:
            json.dump(bars, fh)
        cp = os.path.join(tmp, 'cfg.json')
        with open(cp, 'w') as fh:
            json.dump({'barsPath': bp, 'tf': tf, 'cell': SYMBOL + '|' + tf}, fh)
        out = subprocess.run([NODE, '--max-old-space-size=%d' % heap, RUNNER, cp],
                             cwd=ROOT, capture_output=True, text=True, timeout=43200)
        if out.returncode != 0:
            print('  ! node failed on %s: %s' % (tf, out.stderr[-1500:]), file=sys.stderr)
            return None
        sys.stderr.write(out.stderr)
        return json.loads(out.stdout.strip().splitlines()[-1])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def cost_ladder(res):
    print('  THE COST LADDER -- what spread this signal needs to be worth trading')
    print('  %-22s%8s%10s%11s%11s   %s'
          % ('spread', 'cost/R', 'net R', 'per trade', 'win%', 'verdict'))
    ship = res['runs']['shipped']
    n = len(ship)
    for label, spread in LADDER:
        vals = [net_r(t, spread) for t in ship]
        cost = sum((spread / t['risk']) for t in ship if t.get('risk')) / n
        tot = sum(vals)
        win = 100.0 * sum(1 for v in vals if v > 0) / n
        lo, hi = mean_ci(vals)
        verdict = ('positive, interval clears zero' if lo > 0
                   else 'NEGATIVE' if hi < 0 else 'indistinguishable from zero')
        print('  %-22s%8.3f%10.1f%11.4f%11.1f   %s'
              % (label, cost, tot, tot / n, win, verdict))
    print('    per-trade interval at the raw rung: [%+.4f, %+.4f] R'
          % mean_ci([net_r(t, COST_FOR_HOURS) for t in ship]))


def controls(res, spread):
    print('')
    print('  AGAINST MATCHED CONTROLS at %.2f spread (same bias, stop, exit, count)'
          % spread)
    print('  %-12s%8s%10s%11s   %s' % ('source', 'n', 'net R', 'per trade', 'vs shipped'))
    for era, y0, y1 in ERAS:
        ship = [t for t in res['runs']['shipped'] if era_of(t, y0, y1)]
        if len(ship) < 60:
            continue
        lo_t = min(t['entryTime'] for t in ship)
        hi_t = max(t['entryTime'] for t in ship)
        base = blocks(ship, spread, lo_t, hi_t)
        print('   %s' % era)
        for src in ('shipped', 'randEntry', 'randSide'):
            rows = [t for t in res['runs'][src] if era_of(t, y0, y1)]
            if not rows:
                continue
            bl = blocks(rows, spread, lo_t, hi_t)
            tail = 'BASELINE'
            if src != 'shipped':
                lo, hi = paired_ci(bl, base)
                tail = '%+8.1f [%+7.1f,%+7.1f]' % (sum(bl) - sum(base), lo, hi)
            print('   %-12s%8d%10.1f%11.4f   %s'
                  % (src, len(rows), sum(bl), sum(bl) / len(rows), tail))


def hour_study(res, spread, seed=11):
    ship = res['runs']['shipped']
    by_h = defaultdict(list)
    for t in ship:
        by_h[t['hour']].append(net_r(t, spread))
    scored = {h: v for h, v in by_h.items() if len(v) >= MIN_BUCKET}
    if not scored:
        print('\n  no hour bucket reached %d trades' % MIN_BUCKET)
        return None

    means = {h: sum(v) / len(v) for h, v in scored.items()}
    best_h = max(means, key=means.get)

    print('')
    print('  BY ENTRY HOUR (UTC), net at %.2f spread. Buckets under %d trades are'
          % (spread, MIN_BUCKET))
    print('  not scored -- the 4h study\'s weakness was sample, not significance.')
    print('  %-6s%8s%11s%11s   %s' % ('hour', 'n', 'net R', 'per trade', '95% CI on per-trade'))
    for h in sorted(scored):
        v = scored[h]
        lo, hi = mean_ci(v, seed=seed + h)
        mark = '  <-- best' if h == best_h else ''
        print('  %-6d%8d%11.1f%11.4f   [%+.4f, %+.4f]%s'
              % (h, len(v), sum(v), means[h], lo, hi, mark))

    # THE BEST-BUCKET NULL. Shuffle the hour labels, take the best bucket each
    # time, and compare the observed best against that distribution -- the null
    # of the MAXIMUM, which is what choosing the best hour actually samples.
    vals = [net_r(t, spread) for t in ship]
    sizes = [len(scored[h]) for h in sorted(scored)]
    rnd = random.Random(seed)
    nulls = []
    for _ in range(N_PERM):
        rnd.shuffle(vals)
        i, best = 0, -1e18
        for k in sizes:
            m = sum(vals[i:i + k]) / k
            if m > best:
                best = m
            i += k
        nulls.append(best)
    nulls.sort()
    p = sum(1 for x in nulls if x >= means[best_h]) / len(nulls)
    print('    best-bucket null over %d shuffles: mean %+.4f, 95th pct %+.4f'
          % (N_PERM, sum(nulls) / len(nulls), nulls[int(0.95 * len(nulls))]))
    print('    observed best (hour %d) %+.4f  ->  p = %.3f  %s'
          % (best_h, means[best_h], p,
             'CLEARS the null' if p < 0.05 else 'does NOT clear the null'))

    # ERA REPLICATION of that hour.
    for era, y0, y1 in ERAS:
        v = [net_r(t, spread) for t in ship
             if t['hour'] == best_h and era_of(t, y0, y1)]
        if not v:
            continue
        lo, hi = mean_ci(v, seed=seed + 99)
        print('    hour %d in %s: n=%d  %+.4f R/trade  [%+.4f, %+.4f]'
              % (best_h, era, len(v), sum(v) / len(v), lo, hi))
    return best_h


def hour_gate(res, spread, best_h, seed=7):
    """Trading only the best hour, against trading as few trades at random."""
    ship = res['runs']['shipped']
    kept = [t for t in ship if t['hour'] == best_h]
    if len(kept) < MIN_BUCKET:
        return
    print('')
    print('  THE GATE vs A RANDOM GATE OF THE SAME SIZE. "Trade fewer" and "trade')
    print('  THESE hours" are different claims and only this row separates them.')
    keep_n = len(kept)
    rnd = random.Random(seed)
    idx = list(range(len(ship)))
    draws = []
    for _ in range(N_PERM):
        pick = rnd.sample(idx, keep_n)
        draws.append(sum(net_r(ship[i], spread) for i in pick) / keep_n)
    draws.sort()
    obs = sum(net_r(t, spread) for t in kept) / keep_n
    p = sum(1 for x in draws if x >= obs) / len(draws)
    print('   hour %-2d gate      n=%-6d %+.4f R/trade' % (best_h, keep_n, obs))
    print('   random same-size  n=%-6d %+.4f R/trade   95th pct %+.4f   p = %.3f'
          % (keep_n, sum(draws) / len(draws), draws[int(0.95 * len(draws))], p))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--tfs', nargs='+', default=TFS)
    ap.add_argument('--from-year', type=int, default=2016)
    ap.add_argument('--heap', type=int, default=6144)
    ap.add_argument('--json')
    args = ap.parse_args()

    if NODE is None:
        print('node is not installed', file=sys.stderr)
        return 2

    print('')
    print('A 5m/15m GOLD SIGNAL, NET OF ITS OWN SPREAD. The rule is the shipped')
    print('Donchian horizon rule with the structural trail -- unchanged, because')
    print('every variant tried on it has lost. What is on trial is the COST it')
    print('needs and whether an HOUR filter buys anything a random one does not.')

    everything = {}
    for tf in args.tfs:
        res = run_cell(tf, args.from_year, args.heap)
        if not res:
            continue
        everything[res['cell']] = res
        print('')
        print('=' * 78)
        print('%s %s   (%d bars, %d trades)'
              % (SYMBOL, tf, res['bars'], len(res['runs']['shipped'])))
        print('=' * 78)
        cost_ladder(res)
        controls(res, COST_FOR_HOURS)
        best_h = hour_study(res, COST_FOR_HOURS)
        if best_h is not None:
            hour_gate(res, COST_FOR_HOURS, best_h)

    print('')
    print('=' * 78)
    print('A signal worth trading here is positive NET at a spread you can')
    print('actually get, beats both matched controls, and -- if it uses an hour')
    print('filter -- beats a random gate keeping the same number of trades.')

    if args.json:
        with open(args.json, 'w') as fh:
            json.dump(everything, fh)
        print('')
        print('raw results -> ' + args.json)
    return 0


if __name__ == '__main__':
    sys.exit(main())
