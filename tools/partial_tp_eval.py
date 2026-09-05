#!/usr/bin/env python
"""
Does taking half off at the first structural level beat letting it all ride?

    python tools/partial_tp_eval.py
    python tools/partial_tp_eval.py --tfs 4h --symbols XAUUSD.a

THE QUESTION. The rule has no take-profit and two measurements say it should not
(tools/tp_sweep.py: a 1R cap turned +43.7 net R into -2.1; logs/tp_struct_eval.txt:
twelve cells, every target variant below the plain trailing exit). Both tested
capping the WHOLE position. This tests the version that survives that argument on
its face -- take half at the first level, let the rest run on the trail -- which
keeps the tail the rule is paid from while banking something at the price the
panel already draws.

WHAT IS COMPARED. Every row is the SAME trade list, taken from the shipped
configuration (validated rule + structural trail). Only the exit of the first
half changes:

  trailOnly     the shipped configuration -- nothing taken early. THE BASELINE.
  halfTp1       half out at TP1, the first structural level ahead, chosen at the
                signal bar exactly as js/ui/rulepanel.js chooses it.
  halfMatched   THE CONTROL. Half out at the same DISTANCE as TP1 sat -- the
                median, in ATR, over this cell's own trades -- but at no
                particular level. If halfTp1 cannot beat this, the structure
                chose nothing and the result is just "take half off early".
  halfOneR      half out at 1R, for scale. This is the shape tp_sweep measured
                on the whole position.

HOW THE SPLIT IS COMPUTED. Two halves closed independently are arithmetically
two positions with the same entry and stop, so half B IS the shipped trade and
only half A is reconstructed -- see tools/partial_tp_runner.mjs. No target
machinery was put back into js/chart/rules.js, and ties within a bar are given
to the stop.

WHAT IT DOES NOT MODEL: moving the stop to break-even after the first half. That
is a different rule and needs its own measurement.

HOW THIS IS KEPT HONEST -- the design the other studies use. Two eras and it must
survive both. Net R over the same calendar, paired on calendar blocks, bootstrap
over blocks. GROSS: the split rows close twice as many positions as the baseline,
so they owe more spread than anything below charges them.
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
RUNNER = os.path.join(ROOT, 'tools', 'partial_tp_runner.mjs')

DEFAULT_SYMBOLS = ['XAUUSD.a', 'EURUSD.a', 'USDJPY.a', 'GBPUSD.a']
DEFAULT_TFS = ['4h', '1h', '15m', '5m']
BASELINE = 'trailOnly'          # what ships today

ERAS = [('2016-2020', 2016, 2020), ('2021-2026', 2021, 2026)]
N_BLOCKS = 20
#: Enough history for 20 calendar blocks to mean something. Per timeframe,
#: because the retest studies' flat 20,000 was a 5m number and it silently
#: skipped every 4h and 1h cell -- the frames this rule actually trades.
MIN_BARS = {'5m': 20000, '15m': 20000, '30m': 10000,
            '1h': 5000, '4h': 2000, '1d': 800, '1w': 300}


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
    if len(bars) < MIN_BARS.get(tf, 5000):
        print('  ! %s %s: only %d bars, skipped' % (symbol, tf, len(bars)), file=sys.stderr)
        return None
    tmp = tempfile.mkdtemp(prefix='smc_')
    try:
        bp = os.path.join(tmp, 'bars.json')
        with open(bp, 'w') as fh:
            json.dump(bars, fh)
        cp = os.path.join(tmp, 'cfg.json')
        with open(cp, 'w') as fh:
            json.dump({'barsPath': bp, 'tf': tf,
                       'cell': symbol + '|' + tf}, fh)
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
    print('HALF OFF AT A LEVEL vs LETTING IT RIDE. Same trades throughout --')
    print('only the exit of the first half changes. `halfMatched` is the')
    print('control: half off at the same distance, at no level. GROSS.')
    print('')

    for tf in args.tfs:
        pooled = {e[0]: {} for e in ERAS}
        last_ok = None
        print('=' * 78)
        print('TIMEFRAME %s' % tf)
        print('=' * 78)
        any_cell = False
        for symbol in args.symbols:
            res = run_cell(symbol, tf, args.from_year, args.heap)
            if not res:
                continue
            any_cell = True
            last_ok = res
            everything[res['cell']] = res
            print('%s   (%d bars; TP1 known for %d trades, reached on %d; '
                  'median TP1 %.2f ATR out)'
                  % (res['cell'].replace('|', ' '), res['bars'],
                     res['tradesWithTp1'], res['tp1Hits'], res['medDistAtr']))
            print('  %-15s%7s%6s%6s   ' % ('run', 'n', 'win%', 'held')
                  + '   '.join('%-30s' % e[0] for e in ERAS))

            # EVERY CONTROL'S BLOCKS FIRST. A trailed row pairs with the
            # trailed control and a plain row with the plain one -- see
            # `baselineFor` in the runner, and the note there for what
            # comparing across the two did to the first S/R hold-out.
            base_of = res.get('baselineFor', {})
            blocks = {}
            for ctrl in sorted(set(list(base_of.values()) + [BASELINE])):
                for era, y0, y1 in ERAS:
                    base = era_trades(res['runs'][ctrl], y0, y1)
                    if not base:
                        continue
                    lo_t = min(t['entryTime'] for t in base)
                    hi_t = max(t['entryTime'] for t in base)
                    blocks.setdefault(ctrl, {})[era] = block_totals(base, lo_t, hi_t)

            for name in res['order']:
                s_all = summarise(res['runs'][name])
                cells = []
                ctrl = base_of.get(name, BASELINE)
                for era, y0, y1 in ERAS:
                    base = era_trades(res['runs'][ctrl], y0, y1)
                    if not base:
                        cells.append('%-30s' % '-')
                        continue
                    lo_t = min(t['entryTime'] for t in base)
                    hi_t = max(t['entryTime'] for t in base)
                    bt = (blocks[ctrl][era] if name == ctrl
                          else block_totals(era_trades(res['runs'][name], y0, y1),
                                            lo_t, hi_t))
                    pooled[era].setdefault(name, []).extend(bt)
                    net = sum(bt)
                    if name == ctrl:
                        cells.append('%+9.1f%21s' % (net, 'CONTROL'))
                    else:
                        lo, hi = paired_block_ci(bt, blocks[ctrl][era])
                        cells.append('%+9.1f  %+7.1f [%+5.1f,%+5.1f]'
                                     % (net, net - sum(blocks[ctrl][era]), lo, hi))
                print('  %-15s%7d%6.1f%6.0f   '
                      % (name, s_all['n'], s_all['win'], s_all['bars'])
                      + '   '.join(cells))
            print('')

        if not any_cell:
            print('  (no cell had enough history)\n')
            continue

        print('POOLED ACROSS CELLS -- %s' % tf)
        print('  %-15s' % 'run' + '   '.join('%-30s' % e[0] for e in ERAS)
              + '     verdict')
        # From the last cell that SUCCEEDED, not from `res` -- which is None
        # when the final symbol was skipped or its walk failed, and which took
        # the whole pooled table down with it once already.
        order = last_ok['order']
        base_of = last_ok.get('baselineFor', {})
        for name in order:
            cells, wins, losses = [], 0, 0
            ctrl = base_of.get(name, BASELINE)
            for era, _, _ in ERAS:
                bt = pooled[era].get(name, [])
                bb = pooled[era].get(ctrl, [])
                if not bb:
                    # An era with no control trades has nothing to pair
                    # against -- happens when --from-year skips one entirely.
                    cells.append('%-30s' % '-')
                    continue
                tot = sum(bt)
                if name == ctrl:
                    cells.append('%+9.1f%21s' % (tot, 'CONTROL'))
                    continue
                lo, hi = paired_block_ci(bt, bb)
                if lo > 0:
                    wins += 1
                elif hi < 0:
                    losses += 1
                cells.append('%+9.1f  %+7.1f [%+5.1f,%+5.1f]'
                             % (tot, tot - sum(bb), lo, hi))
            verdict = ('' if name == ctrl
                       else 'BEATS THE COIN FLIP IN BOTH ERAS' if wins == len(ERAS)
                       else 'WORSE IN BOTH ERAS' if losses == len(ERAS)
                       else 'not demonstrated')
            print('  %-15s' % name + '   '.join(cells) + '     ' + verdict)
        print('')

    print('=' * 78)
    print('A partial target worth taking beats BOTH rows above it: `trailOnly`,')
    print('which says banking early helped at all, and `halfMatched`, which says')
    print('the LEVEL mattered rather than the distance. In both eras.')

    if args.json:
        with open(args.json, 'w') as fh:
            json.dump(everything, fh)
        print('')
        print('raw results -> ' + args.json)
    return 0


if __name__ == '__main__':
    sys.exit(main())
