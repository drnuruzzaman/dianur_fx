#!/usr/bin/env python
"""
Why wait for the bar to close, if price already hit the exit?

    python tools/exittouch_eval.py

THE QUESTION, and the first half of the answer is that most of it already does
not wait. The STOP is checked on the bar's RANGE in js/chart/rules.js -- a
touch fills at the stop, a gap fills at the open, no close is involved. What
waits is the CHANNEL exit and the structural trail: both fire on a close
through the level, and both then fill at the next open.

WHY IT WAITS TODAY. The 10-bar exit channel sits at the edge of recent range by
construction, so price wicks through it constantly without the trend being
over. On XAUUSD the low pierces it 13.89% of 4h bars but the close finishes
through it only 6.49% -- 2.1x, and the same 2.1x on 1h and 15m. Requiring the
close is the filter that separates "the move ended" from "one bar poked at it",
and it is worth roughly half the exits.

WHAT IS COMPARED, same entries and the same trail throughout:

  closeExit    the shipped configuration -- close through the level, fill at
               the next open. BASELINE.
  touchExit    the same level, checked on the bar's range and filled at the
               level. Note this gets BOTH halves of the wait back: it acts
               intrabar and it skips the next-open fill.
  randExit     THE CONTROL. Closes at random, at the rate `touchExit` closed
               above and beyond the baseline, with the same trail and the same
               re-entry. "Leave sooner" and "leave when price touches the
               channel" are different claims; without this row they cannot be
               told apart.

WHERE THE CODE LIVES. `p.exitTouch` is a flag on the SHARED walker, off in
everything that ships. A private walker inside this script was the obvious
alternative and is precisely how the ATR divergence got in -- three copies of
the lifecycle, each subtly its own.

GROSS. `touchExit` closes and re-opens more often than the baseline, so it owes
more spread than anything here charges it, and its numbers are the friendly
version.
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
RUNNER = os.path.join(ROOT, 'tools', 'exittouch_runner.mjs')

DEFAULT_SYMBOLS = ['XAUUSD.a', 'USDJPY.a']
DEFAULT_TFS = ['4h', '1h', '30m', '15m', '5m']
BASELINE = 'closeExit'          # what ships today

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
    print('INTRABAR EXIT vs WAITING FOR THE CLOSE. Same rule, same level, same')
    print('trail -- only the moment of acting on it changes. `randExit` is the')
    print('control: the same extra churn at no level at all. GROSS.')
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
            print('%s   (%d bars; touching out added %.2f extra exits per 100 '
                  'bars held)'
                  % (res['cell'].replace('|', ' '), res['bars'],
                     100 * res['touchRate']))
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
    print('Not waiting for the close is worth doing if `touchExit` beats BOTH')
    print('`closeExit` -- leaving intrabar helped at all -- and `randExit` --')
    print('the LEVEL mattered, not the shorter hold. In both eras.')

    if args.json:
        with open(args.json, 'w') as fh:
            json.dump(everything, fh)
        print('')
        print('raw results -> ' + args.json)
    return 0


if __name__ == '__main__':
    sys.exit(main())
