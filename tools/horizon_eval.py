#!/usr/bin/env python
"""
Is the shipped horizon the right one, on every timeframe?

    python tools/horizon_eval.py
    python tools/horizon_eval.py --tfs 1h 4h --symbols XAUUSD.a

THE QUESTION. `paramsForTf` fixes the channel at 3.3 DAYS and lets the bar count
follow the timeframe: N=20 on 4h, 317 on 15m, 950 on 5m. That map is why a 5m
chart full of visible moves produces almost no signals -- the channel there was
349 points wide while the whole preceding day spanned 105. The duration was
chosen because N=20 on 15m (a five-hour channel) measured -0.0756 R over 4,142
trades while the 3.3-day version measured +0.1762 R. That settles "is the literal
20 wrong". It does not settle "is 3.3 days right on THIS frame", which is what
this asks, on all of them at once.

WHAT IS COMPARED. A ladder in MULTIPLES of what ships -- 0.1x, 0.25x, 0.5x, 1x,
2x, 4x -- plus the literal `native 20/10`. Multiples rather than absolute hours
because an hour ladder cannot span the frames: 2h is a 5-bar channel on 4h and a
24-bar one on 5m, and nothing under a month registers on 1w. Every row prints its
bar count beside its multiple, so the abstraction is never load-bearing.

On 4h and above the shipped rule IS 20/10, so those two rows are the same run and
the report prints it once, as the baseline.

HOW THIS IS KEPT HONEST -- the design from tools/entry_filter_eval.py and
tools/exit_trail_eval.py, the two studies that killed eleven entry gates and left
the structural trail undemonstrated.

  TWO ERAS, AND IT MUST SURVIVE BOTH. 2016-2020 and 2021-2026 are different
  markets. A horizon that wins in one and loses in the other is a bet on the
  regime, not a better rule.

  NET R OVER THE SAME CALENDAR IS THE HEADLINE. A shorter channel takes far more
  trades, so avg R per trade is not comparable across rows -- the row with the
  fewest trades wins that metric by construction. Trades are bucketed into
  CALENDAR BLOCKS; the bootstrap resamples blocks, paired against the baseline's
  blocks over the same window.

  THE BEST OF SEVEN ROWS IS NOT A FINDING. With seven candidates per cell the
  best one in a single era is expected to look good by chance, so the report also
  does the one thing not selected on itself: pick the winner in one era, read it
  in the other, both directions.

  GROSS. No spread, slippage or swap, which flatters the short-horizon rows most
  because they take the most trades. On XAUUSD 5m the 18-point spread is 0.122 R
  per trade in 2016-2020 and 0.061 R in 2021-2026; a row taking 20,000 trades
  owes thousands of R that nothing below pays.
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
RUNNER = os.path.join(ROOT, 'tools', 'horizon_runner.mjs')

DEFAULT_SYMBOLS = ['XAUUSD.a', 'EURUSD.a', 'USDJPY.a', 'GBPUSD.a']
DEFAULT_TFS = ['5m', '15m', '30m', '1h', '4h', '1d', '1w']

#: Roughly how many bars a timeframe has per 24h, for printing a multiple as a
#: duration. Mirrors BARS_PER_DAY in js/chart/donchian.js, extended to the two
#: frames the horizon map does not cover.
PER_DAY = {'1m': 1440, '5m': 288, '15m': 96, '30m': 48,
           '1h': 24, '4h': 6, '1d': 1, '1w': 1 / 7}

#: The project's existing era split.
ERAS = [('2016-2020', 2016, 2020), ('2021-2026', 2021, 2026)]

N_BLOCKS = 20

#: A cell needs enough bars that 20 calendar blocks mean something.
MIN_BARS = {'1m': 200000, '5m': 20000, '15m': 20000, '30m': 10000,
            '1h': 5000, '4h': 2000, '1d': 800, '1w': 300}


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
        return {'n': 0, 'win': float('nan'), 'netR': 0.0, 'bars': float('nan')}
    wins = [r for r in rs if r > 0]
    return {'n': len(rs), 'win': 100.0 * len(wins) / len(rs), 'netR': sum(rs),
            'bars': sum(t['bars'] for t in trades) / len(trades)}


def era_trades(trades, y0, y1):
    return [t for t in trades if y0 <= year_of(t['entryTime']) <= y1]


def duration(tf, n):
    """A bar count as the duration it actually is, for the row label."""
    days = n / PER_DAY[tf]
    if days < 1:
        return '%.0fh' % (days * 24)
    if days < 14:
        return '%.1fd' % days
    return '%.0fw' % (days / 7)


def run_cell(symbol, tf, year_from, heap):
    bars = load_bars(symbol, tf, year_from)
    if len(bars) < MIN_BARS.get(tf, 5000):
        print('  ! %s %s: only %d bars, skipped' % (symbol, tf, len(bars)),
              file=sys.stderr)
        return None
    tmp = tempfile.mkdtemp(prefix='horizon_')
    try:
        bp = os.path.join(tmp, 'bars.json')
        with open(bp, 'w') as fh:
            json.dump(bars, fh)
        cp = os.path.join(tmp, 'cfg.json')
        with open(cp, 'w') as fh:
            json.dump({'barsPath': bp, 'tf': tf, 'cell': symbol + '|' + tf}, fh)
        out = subprocess.run([NODE, '--max-old-space-size=%d' % heap, RUNNER, cp],
                             cwd=ROOT, capture_output=True, text=True, timeout=43200)
        if out.returncode != 0:
            print('  ! node failed on %s %s: %s'
                  % (symbol, tf, out.stderr[-1200:]), file=sys.stderr)
            return None
        sys.stderr.write(out.stderr)
        return json.loads(out.stdout.strip().splitlines()[-1])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def report_cell(res, pooled_tf):
    """One table per cell, and the blocks folded into the timeframe's pool."""
    base_key = res['baselineKey']
    tf = res['tf']
    print('%s   (%d bars; ships N=%s/%s = %s)'
          % (res['cell'].replace('|', ' '), res['bars'],
             res['shipped']['entry'], res['shipped']['exit'],
             duration(tf, res['shipped']['entry'])))
    print('  %-16s%7s%9s%7s%6s%7s   ' % ('horizon', 'N', 'duration', 'n', 'win%', 'held')
          + '   '.join('%-30s' % e[0] for e in ERAS))

    blocks = {}
    for era, y0, y1 in ERAS:
        base = era_trades(res['runs'][base_key], y0, y1)
        if not base:
            continue
        lo_t = min(t['entryTime'] for t in base)
        hi_t = max(t['entryTime'] for t in base)
        blocks.setdefault(base_key, {})[era] = block_totals(base, lo_t, hi_t)

    for name in res['order']:
        tr_all = res['runs'][name]
        s_all = summarise(tr_all)
        cells = []
        for era, y0, y1 in ERAS:
            base = era_trades(res['runs'][base_key], y0, y1)
            if not base:
                cells.append('%-30s' % '-')
                continue
            lo_t = min(t['entryTime'] for t in base)
            hi_t = max(t['entryTime'] for t in base)
            bt = (blocks[base_key][era] if name == base_key
                  else block_totals(era_trades(tr_all, y0, y1), lo_t, hi_t))
            blocks.setdefault(name, {})[era] = bt
            pooled_tf[era].setdefault(name, []).extend(bt)
            net = sum(bt)
            if name == base_key:
                cells.append('%+9.1f%21s' % (net, 'SHIPPED'))
            else:
                lo, hi = paired_block_ci(bt, blocks[base_key][era])
                cells.append('%+9.1f  %+7.1f [%+5.1f,%+5.1f]'
                             % (net, net - sum(blocks[base_key][era]), lo, hi))
        p = res['params'][name]
        print('  %-16s%7d%9s%7d%6.1f%7.0f   '
              % (name, p['entry'], duration(tf, p['entry']), s_all['n'],
                 s_all['win'], s_all['bars'])
              + '   '.join(cells))
    print('')
    return base_key


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--symbols', nargs='+', default=DEFAULT_SYMBOLS)
    ap.add_argument('--tfs', nargs='+', default=DEFAULT_TFS)
    ap.add_argument('--from-year', type=int, default=2016)
    ap.add_argument('--heap', type=int, default=6144,
                    help='node --max-old-space-size, MB (1m needs more)')
    ap.add_argument('--json', help='also write the raw per-cell results here')
    args = ap.parse_args()

    if NODE is None:
        print('node is not installed', file=sys.stderr)
        return 2

    everything = {}
    per_tf = {}

    print('')
    print('HORIZON SWEEP, EVERY TIMEFRAME. Rows are multiples of the SHIPPED')
    print('channel duration, plus the literal 20/10. GROSS of spread, slippage')
    print('and swap, which flatters the short rows most. NET R over the same')
    print('calendar decides; avg R per trade is not comparable across rows.')
    print('')

    for tf in args.tfs:
        pooled = {e[0]: {} for e in ERAS}
        base_keys = set()
        seen_any = False
        print('=' * 78)
        print('TIMEFRAME %s' % tf)
        print('=' * 78)
        for symbol in args.symbols:
            res = run_cell(symbol, tf, args.from_year, args.heap)
            if not res:
                continue
            seen_any = True
            everything[res['cell']] = res
            base_keys.add(report_cell(res, pooled))
        if not seen_any:
            print('  (no cell had enough history)\n')
            continue
        per_tf[tf] = (pooled, base_keys)

        base_key = sorted(base_keys)[0] if len(base_keys) == 1 else None
        if base_key is None:
            print('  ! cells disagreed on the baseline row; pooling skipped\n')
            continue

        print('POOLED ACROSS CELLS -- %s' % tf)
        print('  %-16s' % 'horizon' + '   '.join('%-30s' % e[0] for e in ERAS)
              + '     verdict')
        order = [k for k in dict.fromkeys(
            sum([everything[c]['order'] for c in everything
                 if everything[c]['tf'] == tf], []))]
        for name in order:
            cells, wins, losses = [], 0, 0
            for era, _, _ in ERAS:
                bt = pooled[era].get(name, [])
                bb = pooled[era][base_key]
                tot = sum(bt)
                if name == base_key:
                    cells.append('%+9.1f%21s' % (tot, 'SHIPPED'))
                    continue
                lo, hi = paired_block_ci(bt, bb)
                if lo > 0:
                    wins += 1
                elif hi < 0:
                    losses += 1
                cells.append('%+9.1f  %+7.1f [%+5.1f,%+5.1f]'
                             % (tot, tot - sum(bb), lo, hi))
            if name == base_key:
                verdict = ''
            elif wins == len(ERAS):
                verdict = 'BETTER IN BOTH ERAS'
            elif losses == len(ERAS):
                verdict = 'WORSE IN BOTH ERAS'
            else:
                verdict = 'not demonstrated'
            print('  %-16s' % name + '   '.join(cells) + '     ' + verdict)

        print('')
        print('  PICKED IN ONE ERA, READ IN THE OTHER (%s)' % tf)
        for pick_era, _, _ in ERAS:
            other = [e[0] for e in ERAS if e[0] != pick_era][0]
            cands = [n for n in order if n != base_key and pooled[pick_era].get(n)]
            if not cands:
                continue
            best = max(cands, key=lambda n: sum(pooled[pick_era][n]))
            gp = sum(pooled[pick_era][best]) - sum(pooled[pick_era][base_key])
            go = sum(pooled[other].get(best, [])) - sum(pooled[other][base_key])
            lo, hi = paired_block_ci(pooled[other].get(best, []), pooled[other][base_key])
            print('    best in %s: %-14s %+7.1f R there  ->  %+7.1f R in %s '
                  '[%+.1f, %+.1f]' % (pick_era, best, gp, go, other, lo, hi))
        print('')

    print('=' * 78)
    print('A horizon worth shipping would beat the baseline in BOTH eras and')
    print('survive being picked in one era and read in the other. Anything else')
    print('is a parameter that fit the sample it was chosen on -- and every')
    print('number here is gross, so the rows taking more trades owe the most.')

    if args.json:
        with open(args.json, 'w') as fh:
            json.dump(everything, fh)
        print('')
        print('raw results -> ' + args.json)
    return 0


if __name__ == '__main__':
    sys.exit(main())
