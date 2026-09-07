#!/usr/bin/env python
"""
Does a structure retest pick entries better than chance, on the frames the
Donchian rule sits out?

    python tools/smc_eval.py
    python tools/smc_eval.py --tfs 5m --symbols XAUUSD.a

THE IDEA UNDER TEST, defined in js/chart/smcretest.js: while market structure is
bullish, buy the first bar that trades into the newest live demand base and
closes back above it, stop under the base; short the mirror; exit on an opposite
CHoCH or the stop. It is assembled from detectors the charts already draw --
BOS/CHoCH, supply and demand -- because a shorter Donchian channel is measured
to lose on these frames (-3,616 R on 5m, -1,230 R on 15m, both eras,
logs/horizon_5m_eval.txt and logs/horizon_holdout.txt).

WHAT IS COMPARED.

  smc          the rule.
  smcTrail     the rule with the structural trailing exit added.
  randEntry    THE CONTROL. Identical in every respect except WHICH bar it
               enters on: same bias, same opportunity set (a live zone on the
               correct side), same stop width in ATR, same exits. It fires at
               random with the probability that reproduces the rule's own trade
               count. If the rule cannot beat this, the zone is not picking
               entries -- it is only deciding how often to trade.
  randSide     the same, with the SIDE randomised too. If randEntry matches the
               rule but randSide does not, then structure's direction carries
               the result and the zone carries none of it.
  donchian     the shipped rule on the same bars, for scale.

WHY THE CONTROL IS THE ROW THAT DECIDES. Eleven entry gates died in
tools/entry_filter_eval.py against exactly this construction, and the structural
trail came out "not demonstrated" against a matched ATR trail in
tools/exit_trail_eval.py. Beating the Donchian rule on a 5m chart proves
nothing: the Donchian rule is deliberately dormant there. Beating a coin flip
that trades at the same rate, at the same stop distance, in the same direction,
on the same bars, is the whole claim.

MATCHED BEFORE ANYTHING IS SCORED. The rule runs first; its trade count sets the
control's firing rate and its MEDIAN stop-in-ATR sets the control's stop. Both
are read off the rule's geometry, never off its returns.

HOW ELSE THIS IS KEPT HONEST -- the design the other studies use.

  TWO ERAS, AND IT MUST SURVIVE BOTH. 2016-2020 and 2021-2026.

  NET R OVER THE SAME CALENDAR, paired on calendar blocks, bootstrap over
  blocks. The rows do not take the same trades, so avg R per trade is not
  comparable across them.

  GROSS. No spread, slippage or swap. On XAUUSD 5m the spread alone is 0.122 R
  per trade in 2016-2020 and 0.061 R in 2021-2026 -- so a row taking 2,000
  trades owes roughly 180 R that nothing below pays. Both the rule and its
  controls trade at the SAME rate, so the comparison between them is unaffected;
  the comparison with `donchian`, which trades far less, is not.
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
RUNNER = os.path.join(ROOT, 'tools', 'smc_runner.mjs')

DEFAULT_SYMBOLS = ['XAUUSD.a', 'EURUSD.a', 'USDJPY.a', 'GBPUSD.a']
DEFAULT_TFS = ['5m', '15m']
BASELINE = 'randEntry'          # the control, not the rule

ERAS = [('2016-2020', 2016, 2020), ('2021-2026', 2021, 2026)]
N_BLOCKS = 20
MIN_BARS = 20000


def load_bars(symbol, tf, year_from):
    files = sorted(glob.glob(os.path.join(ROOT, 'data', 'bars', symbol, tf, '*.csv.gz')))
    rows = []
    for f in files:
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
                             'l': float(c[ix['low']]), 'c': float(c[ix['close']]),
                             'v': 1})
    rows.sort(key=lambda r: r['t'])
    return rows


def year_of(ms):
    return datetime.datetime.fromtimestamp(ms / 1000, datetime.timezone.utc).year


def era_trades(trades, y0, y1):
    return [t for t in trades if y0 <= year_of(t['entryTime']) <= y1]


def block_totals(trades, lo_t, hi_t, blocks=N_BLOCKS):
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


def run_cell(symbol, tf, year_from, heap):
    bars = load_bars(symbol, tf, year_from)
    if len(bars) < MIN_BARS:
        print('  ! %s %s: only %d bars, skipped' % (symbol, tf, len(bars)), file=sys.stderr)
        return None
    tmp = tempfile.mkdtemp(prefix='smc_')
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
            print('  ! node failed on %s %s: %s' % (symbol, tf, out.stderr[-1500:]),
                  file=sys.stderr)
            return None
        sys.stderr.write(out.stderr)
        return json.loads(out.stdout.strip().splitlines()[-1])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--symbols', nargs='+', default=DEFAULT_SYMBOLS)
    ap.add_argument('--tfs', nargs='+', default=DEFAULT_TFS)
    ap.add_argument('--from-year', type=int, default=2016)
    ap.add_argument('--heap', type=int, default=6144)
    ap.add_argument('--json')
    args = ap.parse_args()

    if NODE is None:
        print('node is not installed', file=sys.stderr)
        return 2

    everything = {}
    print('')
    print('STRUCTURE RETEST vs A MATCHED COIN FLIP. Every row is compared with')
    print('`randEntry`: same bias, same opportunity bars, same stop width, same')
    print('exits, entering at random at the rule\'s own trade rate. GROSS.')
    print('')

    for tf in args.tfs:
        pooled = {e[0]: {} for e in ERAS}
        print('=' * 78)
        print('TIMEFRAME %s' % tf)
        print('=' * 78)
        any_cell = False
        for symbol in args.symbols:
            res = run_cell(symbol, tf, args.from_year, args.heap)
            if not res:
                continue
            any_cell = True
            everything[res['cell']] = res
            print('%s   (%d bars; control fires on %.2f%% of %d opportunity bars, '
                  'stop %.2f ATR)'
                  % (res['cell'].replace('|', ' '), res['bars'],
                     100 * res['matchedRate'], res['opportunityBars'], res['riskAtr']))
            print('  %-12s%7s%6s%6s   ' % ('run', 'n', 'win%', 'held')
                  + '   '.join('%-30s' % e[0] for e in ERAS))

            blocks = {}
            for era, y0, y1 in ERAS:
                base = era_trades(res['runs'][BASELINE], y0, y1)
                if not base:
                    continue
                lo_t = min(t['entryTime'] for t in base)
                hi_t = max(t['entryTime'] for t in base)
                blocks.setdefault(BASELINE, {})[era] = block_totals(base, lo_t, hi_t)

            for name in res['order']:
                s_all = summarise(res['runs'][name])
                cells = []
                for era, y0, y1 in ERAS:
                    base = era_trades(res['runs'][BASELINE], y0, y1)
                    if not base:
                        cells.append('%-30s' % '-')
                        continue
                    lo_t = min(t['entryTime'] for t in base)
                    hi_t = max(t['entryTime'] for t in base)
                    bt = (blocks[BASELINE][era] if name == BASELINE
                          else block_totals(era_trades(res['runs'][name], y0, y1),
                                            lo_t, hi_t))
                    pooled[era].setdefault(name, []).extend(bt)
                    net = sum(bt)
                    if name == BASELINE:
                        cells.append('%+9.1f%21s' % (net, 'CONTROL'))
                    else:
                        lo, hi = paired_block_ci(bt, blocks[BASELINE][era])
                        cells.append('%+9.1f  %+7.1f [%+5.1f,%+5.1f]'
                                     % (net, net - sum(blocks[BASELINE][era]), lo, hi))
                print('  %-12s%7d%6.1f%6.0f   '
                      % (name, s_all['n'], s_all['win'], s_all['bars'])
                      + '   '.join(cells))
            print('')

        if not any_cell:
            print('  (no cell had enough history)\n')
            continue

        print('POOLED ACROSS CELLS -- %s' % tf)
        print('  %-12s' % 'run' + '   '.join('%-30s' % e[0] for e in ERAS)
              + '     verdict')
        order = ['smc', 'smcTrail', 'randEntry', 'randSide', 'donchian']
        for name in order:
            cells, wins, losses = [], 0, 0
            for era, _, _ in ERAS:
                bt = pooled[era].get(name, [])
                bb = pooled[era].get(BASELINE, [])
                if not bb:
                    # An era with no control trades has nothing to pair
                    # against -- happens when --from-year skips one entirely.
                    cells.append('%-30s' % '-')
                    continue
                tot = sum(bt)
                if name == BASELINE:
                    cells.append('%+9.1f%21s' % (tot, 'CONTROL'))
                    continue
                lo, hi = paired_block_ci(bt, bb)
                if lo > 0:
                    wins += 1
                elif hi < 0:
                    losses += 1
                cells.append('%+9.1f  %+7.1f [%+5.1f,%+5.1f]'
                             % (tot, tot - sum(bb), lo, hi))
            verdict = ('' if name == BASELINE
                       else 'BEATS THE COIN FLIP IN BOTH ERAS' if wins == len(ERAS)
                       else 'WORSE IN BOTH ERAS' if losses == len(ERAS)
                       else 'not demonstrated')
            print('  %-12s' % name + '   '.join(cells) + '     ' + verdict)
        print('')

    print('=' * 78)
    print('A structure entry worth having beats `randEntry` in BOTH eras. Losing')
    print('to it means the zone chose nothing: the trades came from the bias and')
    print('the trade rate, both of which a coin flip had too.')

    if args.json:
        with open(args.json, 'w') as fh:
            json.dump(everything, fh)
        print('')
        print('raw results -> ' + args.json)
    return 0


if __name__ == '__main__':
    sys.exit(main())
