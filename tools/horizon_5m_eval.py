#!/usr/bin/env python
"""
Is 3.3 days the wrong horizon for a 5m chart?

    python tools/horizon_5m_eval.py
    python tools/horizon_5m_eval.py --symbols XAUUSD.a --from-year 2016

THE COMPLAINT THAT PROMPTED THIS. A 5m gold chart is visibly full of moves, and
the rule sits flat through nearly all of them. That is not a detection failure:
`paramsForTf` keeps the channel fixed at 3.3 DAYS, which on 5m is N=950, so the
channel was 349 points wide while the whole preceding day spanned 105. The rule
is a multi-day breakout sampled every five minutes.

The design reason is real -- N=20 on 15m is a five-hour channel measured at
-0.0756 R over 4,142 trades, while the 3.3-day version of the same rule on 15m
measured +0.1762 R -- but it answers "is the literal 20 wrong", not "is 3.3 days
right HERE". This asks the second question, over durations between the two.

WHAT IS COMPARED, all as durations, all on 5m bars:

  native 20/10        the literal parameters -- 100 minutes. Not a duration
                      anyone chose; it is what the chart looks like it wants.
  2h 4h 8h 12h 24h    candidate horizons.
  48h
  79.2h (3.3d)        THE BASELINE -- what ships, and what every other row is
                      compared against.

HOW THIS IS KEPT HONEST. The same design as tools/entry_filter_eval.py and
tools/exit_trail_eval.py, because those are the studies that killed eleven entry
gates and left the structural trail undemonstrated.

  TWO ERAS, AND IT MUST SURVIVE BOTH. 2016-2020 and 2021-2026 are different
  markets. A horizon that wins in one and loses in the other is a bet on the
  regime, not a better rule.

  NET R OVER THE SAME CALENDAR IS THE HEADLINE. A shorter channel takes far more
  trades, so average R per trade is not comparable across rows -- the row with
  the fewest trades wins that metric by construction. Trades are bucketed into
  CALENDAR BLOCKS and the bootstrap resamples blocks, paired against the
  baseline's blocks over the same window.

  THE BEST OF EIGHT ROWS IS NOT A FINDING. With eight candidates the best one in
  any single era is expected to look good by chance. So the report also does the
  only thing that is not selected on itself: pick the winner in the EARLY era,
  then read its LATE-era result, and vice versa. A horizon that survives that in
  both directions is worth a second look; one that does not is noise with a
  name.

  GROSS. No spread, slippage or swap -- and this is the study where that matters
  most, because the short-horizon rows take 10-100x the trades. On XAUUSD 5m the
  spread alone is 18 points, roughly 0.07 R on a 2-ATR stop, and 2,000 trades of
  that is 140 R the gross numbers below do not pay. Read every short-horizon row
  as an overstatement.
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

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NODE = shutil.which('node')
RUNNER = os.path.join(ROOT, 'tools', 'horizon_5m_runner.mjs')

DEFAULT_SYMBOLS = ['XAUUSD.a', 'EURUSD.a', 'USDJPY.a', 'GBPUSD.a']
TF = '5m'
BASELINE = '79.2h (3.3d)'

#: The project's existing era split.
ERAS = [('2016-2020', 2016, 2020), ('2021-2026', 2021, 2026)]

N_BLOCKS = 20

ORDER = ['native 20/10', '2h', '4h', '8h', '12h', '24h', '48h', BASELINE]


def load_bars(symbol, tf, year_from):
    files = sorted(glob.glob(os.path.join(ROOT, 'data', 'bars', symbol, tf, '*.csv.gz')))
    rows = []
    for f in files:
        year = int(os.path.basename(f)[:4])
        if year < year_from:
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
                             'l': float(c[ix['low']]), 'c': float(c[ix['close']]),
                             'v': 1})
    rows.sort(key=lambda r: r['t'])
    return rows


def year_of(ms):
    return datetime.datetime.fromtimestamp(ms / 1000, datetime.timezone.utc).year


def block_totals(trades, lo_t, hi_t, blocks=N_BLOCKS):
    """Net R per contiguous block of CALENDAR TIME, by entry."""
    if hi_t <= lo_t:
        return [0.0] * blocks
    width = (hi_t - lo_t) / blocks
    out = [0.0] * blocks
    for t in trades:
        b = min(blocks - 1, max(0, int((t['entryTime'] - lo_t) / width)))
        out[b] += t['r']
    return out


def paired_block_ci(a, b, n=2000, seed=17):
    k = min(len(a), len(b))
    if not k:
        return (float('nan'), float('nan'))
    diffs = [a[j] - b[j] for j in range(k)]
    rnd = random.Random(seed)
    sums = []
    for _ in range(n):
        sums.append(sum(diffs[rnd.randrange(k)] for _ in range(k)))
    sums.sort()
    return (sums[int(0.025 * n)], sums[int(0.975 * n)])


def summarise(trades):
    rs = [t['r'] for t in trades]
    if not rs:
        return {'n': 0, 'win': float('nan'), 'avgR': float('nan'),
                'netR': 0.0, 'bars': float('nan')}
    wins = [r for r in rs if r > 0]
    return {'n': len(rs), 'win': 100.0 * len(wins) / len(rs),
            'avgR': sum(rs) / len(rs), 'netR': sum(rs),
            'bars': sum(t['bars'] for t in trades) / len(trades)}


def era_trades(trades, y0, y1):
    return [t for t in trades if y0 <= year_of(t['entryTime']) <= y1]


def run_cell(symbol, year_from):
    bars = load_bars(symbol, TF, year_from)
    if len(bars) < 20000:
        print('  ! %s: only %d bars, skipped' % (symbol, len(bars)), file=sys.stderr)
        return None
    tmp = tempfile.mkdtemp(prefix='horizon5m_')
    try:
        bp = os.path.join(tmp, 'bars.json')
        with open(bp, 'w') as fh:
            json.dump(bars, fh)
        cp = os.path.join(tmp, 'cfg.json')
        with open(cp, 'w') as fh:
            json.dump({'barsPath': bp, 'tf': TF, 'cell': symbol + '|' + TF}, fh)
        out = subprocess.run([NODE, '--max-old-space-size=6144', RUNNER, cp],
                             cwd=ROOT, capture_output=True, text=True, timeout=21600)
        if out.returncode != 0:
            print('  ! node failed: ' + out.stderr[-1200:], file=sys.stderr)
            return None
        sys.stderr.write(out.stderr)
        return json.loads(out.stdout.strip().splitlines()[-1])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--symbols', nargs='+', default=DEFAULT_SYMBOLS)
    ap.add_argument('--from-year', type=int, default=2016)
    ap.add_argument('--json', help='also write the raw per-cell results here')
    args = ap.parse_args()

    if NODE is None:
        print('node is not installed', file=sys.stderr)
        return 2

    everything = {}
    pooled = {e[0]: {} for e in ERAS}

    print('')
    print('5m HORIZON SWEEP. GROSS of spread, slippage and swap -- which flatters')
    print('the short-horizon rows most, since they take the most trades.')
    print('NET R over the same calendar is the comparison; avg R per trade is')
    print('shown but does not decide, because the rows do not take the same trades.')
    print('')

    for symbol in args.symbols:
        res = run_cell(symbol, args.from_year)
        if not res:
            continue
        cell = symbol + ' ' + TF
        everything[cell] = res
        print('%s   (%d bars; shipped horizon N=%s/%s)'
              % (cell, res['bars'], res['shipped']['entry'], res['shipped']['exit']))
        hdr = '  %-14s%7s%7s%6s%7s   ' % ('horizon', 'N', 'n', 'win%', 'held')
        print(hdr + '   '.join('%-30s' % e[0] for e in ERAS))

        # THE BASELINE'S BLOCKS FIRST, because every other row is paired against
        # them and dict order is the print order, not the dependency order.
        blocks = {}
        for era, y0, y1 in ERAS:
            base = era_trades(res['runs'][BASELINE], y0, y1)
            if not base:
                continue
            lo_t = min(t['entryTime'] for t in base)
            hi_t = max(t['entryTime'] for t in base)
            blocks.setdefault(BASELINE, {})[era] = block_totals(base, lo_t, hi_t)
        for name in ORDER:
            tr_all = res['runs'].get(name)
            if tr_all is None:
                continue
            s_all = summarise(tr_all)
            cells = []
            for era, y0, y1 in ERAS:
                tr = era_trades(tr_all, y0, y1)
                base = era_trades(res['runs'][BASELINE], y0, y1)
                if not base:
                    cells.append('%-30s' % '-')
                    continue
                lo_t = min(t['entryTime'] for t in base)
                hi_t = max(t['entryTime'] for t in base)
                bt = (blocks[BASELINE][era] if name == BASELINE
                      else block_totals(tr, lo_t, hi_t))
                blocks.setdefault(name, {})[era] = bt
                pooled[era].setdefault(name, []).extend(bt)
                net = summarise(tr)['netR']
                if name == BASELINE:
                    cells.append('%+9.1f%21s' % (net, 'baseline'))
                else:
                    lo, hi = paired_block_ci(bt, blocks[BASELINE][era])
                    cells.append('%+9.1f  %+7.1f [%+5.1f,%+5.1f]'
                                 % (net, net - summarise(base)['netR'], lo, hi))
            print('  %-14s%7d%7d%6.1f%7.0f   '
                  % (name, res['params'][name]['entry'], s_all['n'],
                     s_all['win'], s_all['bars'])
                  + '   '.join(cells))
        print('')

    if not pooled[ERAS[0][0]]:
        return 0

    print('POOLED ACROSS EVERY CELL, PER ERA')
    print('  %-14s' % 'horizon' + '   '.join('%-30s' % e[0] for e in ERAS)
          + '     verdict')
    for name in ORDER:
        cells, wins, losses = [], 0, 0
        for era, _, _ in ERAS:
            bt = pooled[era].get(name, [])
            bb = pooled[era][BASELINE]
            tot = sum(bt)
            if name == BASELINE:
                cells.append('%+9.1f%21s' % (tot, 'baseline'))
                continue
            lo, hi = paired_block_ci(bt, bb)
            if lo > 0:
                wins += 1
            elif hi < 0:
                losses += 1
            cells.append('%+9.1f  %+7.1f [%+5.1f,%+5.1f]'
                         % (tot, tot - sum(bb), lo, hi))
        if name == BASELINE:
            verdict = ''
        elif wins == len(ERAS):
            verdict = 'BETTER IN BOTH ERAS'
        elif losses == len(ERAS):
            verdict = 'WORSE IN BOTH ERAS'
        else:
            verdict = 'not demonstrated'
        print('  %-14s' % name + '   '.join(cells) + '     ' + verdict)

    # THE ONLY NUMBER NOT SELECTED ON ITSELF.
    print('')
    print('PICKED IN ONE ERA, READ IN THE OTHER. Eight rows means the best of')
    print('them looks good by chance; this is what that winner then did on data')
    print('it was not chosen on.')
    for pick_era, _, _ in ERAS:
        other = [e[0] for e in ERAS if e[0] != pick_era][0]
        cands = [n for n in ORDER if n != BASELINE]
        best = max(cands, key=lambda n: sum(pooled[pick_era].get(n, [])))
        gain_pick = sum(pooled[pick_era][best]) - sum(pooled[pick_era][BASELINE])
        gain_other = sum(pooled[other].get(best, [])) - sum(pooled[other][BASELINE])
        lo, hi = paired_block_ci(pooled[other].get(best, []), pooled[other][BASELINE])
        print('  best in %s: %-14s  %+7.1f R there  ->  %+7.1f R in %s [%+.1f, %+.1f]'
              % (pick_era, best, gain_pick, gain_other, other, lo, hi))

    print('')
    print('A horizon worth shipping would beat the baseline in BOTH eras and')
    print('survive being picked in one and read in the other. Anything else is a')
    print('parameter that fit the sample it was chosen on.')

    if args.json:
        with open(args.json, 'w') as fh:
            json.dump(everything, fh)
        print('')
        print('raw results -> ' + args.json)
    return 0


if __name__ == '__main__':
    sys.exit(main())
