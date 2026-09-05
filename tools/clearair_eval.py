#!/usr/bin/env python
"""
Should a trade END when it runs out of structure ahead of it?

    python tools/clearair_eval.py

THE REQUEST BEHIND IT. "Once a trade is in clear air the chart should be clear
of TP bands, SL and entry, and look for a new opportunity." The bands already
clear themselves -- no levels means no band and no tags -- and the entry and stop
stay because the POSITION is still open and the stop is the only thing that ends
it. Hiding a live stop is the one omission on this chart that can cost money.

So the question that actually decides it is whether clear air should CLOSE the
trade. If it should, the chart clears by itself and the rule is free to look for
the next setup; if it should not, the entry and stop are exactly what a reader
still needs.

WHAT CLEAR AIR MEANS HERE. js/ui/rulepanel.js calls it when every level chosen at
the SIGNAL BAR has been reached -- so in the Pass 1 dataset it is precisely "tp3
reached", and no new walk is needed. Trades where the detectors found fewer than
three levels are counted as clear once their last one is reached.

WHAT IS COMPARED, on the same trades, changing only the exit:

  trailOnly    the shipped configuration. THE BASELINE.
  exitClear    out at the last structural level, the moment clear air begins.
  exitTp2      out one level earlier, for shape.
  halfClear    half out at the last level, the rest still trailing.

GROSS, and the split rows close more positions than the baseline, so they owe
more spread than anything here charges them. Intervals are the calendar-block
bootstrap every other study in this project uses: bands inside a trade and
trades inside a trend are not independent observations.
"""

import argparse
import glob
import json
import os
import random
import sys
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EVENTS = os.path.join(ROOT, 'data', 'research', 'events')
ERAS = [('2016-2020', 2016, 2020), ('2021-2026', 2021, 2026)]
N_BLOCKS = 20
N_BOOT = 2000


def year_of(ms):
    import datetime
    return datetime.datetime.fromtimestamp(ms / 1000, datetime.timezone.utc).year


def load(stem):
    trades = {}
    with open(stem + '.trades.jsonl') as fh:
        for line in fh:
            r = json.loads(line)
            if r['source'] == 'shipped':
                trades[r['id']] = r
    bands = defaultdict(dict)
    with open(stem + '.bands.jsonl') as fh:
        for line in fh:
            r = json.loads(line)
            if r['id'] in trades:
                bands[r['band']][r['id']] = r
    return trades, bands


def blocks(rows, key, lo_t, hi_t, scale=1, n=N_BLOCKS):
    width = (hi_t - lo_t) / n if hi_t > lo_t else 1
    out = [0.0] * n
    for r in rows:
        b = min(n - 1, max(0, int((r['entry_time'] - lo_t) / width)))
        out[b] += r[key] * scale
    return out


def paired_ci(a, b, seed=17, n=N_BOOT):
    k = min(len(a), len(b))
    if not k:
        return (float('nan'), float('nan'))
    d = [a[j] - b[j] for j in range(k)]
    rnd = random.Random(seed)
    s = [sum(d[rnd.randrange(k)] for _ in range(k)) for _ in range(n)]
    s.sort()
    return (s[int(0.025 * n)], s[int(0.975 * n)])


def last_level(bands, tid):
    """The furthest level this trade was given, and whether price reached it."""
    for band in ('tp3', 'tp2', 'tp1'):
        row = bands.get(band, {}).get(tid)
        if row:
            return row
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--events', default=EVENTS)
    args = ap.parse_args()

    stems = sorted({p[:-len('.trades.jsonl')]
                    for p in glob.glob(os.path.join(args.events, '*.trades.jsonl'))})
    if not stems:
        print('no cells -- run tools/event_dataset.py first', file=sys.stderr)
        return 2

    print('')
    print('SHOULD CLEAR AIR CLOSE THE TRADE? Same trades throughout; only the')
    print('exit changes. `exitClear` leaves at the last structural level -- the')
    print('moment js/ui/rulepanel.js would call the trade clear. GROSS.')

    pooled = {e[0]: defaultdict(list) for e in ERAS}
    for stem in stems:
        cell = os.path.basename(stem)
        trades, bands = load(stem)
        step = max((t.get('sample_step', 1) for t in trades.values()), default=1)
        print('')
        print('%s%s' % (cell, '' if step == 1 else '   (every %dth trade; scaled)' % step))
        print('  %-12s%8s%9s%9s   %s' % ('exit', 'n', 'reached', 'net R', 'vs trailOnly'))

        for era, y0, y1 in ERAS:
            rows = [t for t in trades.values() if y0 <= year_of(t['entry_time']) <= y1]
            if len(rows) < 60:
                continue
            lo_t = min(r['entry_time'] for r in rows)
            hi_t = max(r['entry_time'] for r in rows)

            variants = {'trailOnly': [], 'exitClear': [], 'exitTp2': [], 'halfClear': []}
            reached_n = 0
            for t in rows:
                full = t['y_r']
                variants['trailOnly'].append({**t, 'v': full})
                last = last_level(bands, t['id'])
                tp2 = bands.get('tp2', {}).get(t['id'])
                if last and last['y_reached'] == 1:
                    reached_n += 1
                    banked = last['r_if_taken_here']
                    variants['exitClear'].append({**t, 'v': banked})
                    variants['halfClear'].append({**t, 'v': 0.5 * banked + 0.5 * full})
                else:
                    variants['exitClear'].append({**t, 'v': full})
                    variants['halfClear'].append({**t, 'v': full})
                if tp2 and tp2['y_reached'] == 1:
                    variants['exitTp2'].append({**t, 'v': tp2['r_if_taken_here']})
                else:
                    variants['exitTp2'].append({**t, 'v': full})

            base = blocks(variants['trailOnly'], 'v', lo_t, hi_t, scale=step)
            line_era = era
            for name in ('trailOnly', 'exitClear', 'exitTp2', 'halfClear'):
                bl = blocks(variants[name], 'v', lo_t, hi_t, scale=step)
                pooled[era][name].extend(bl)
                if name == 'trailOnly':
                    tail = 'BASELINE'
                else:
                    lo, hi = paired_ci(bl, base)
                    tail = '%+8.1f [%+7.1f,%+7.1f]' % (sum(bl) - sum(base), lo, hi)
                print('  %-12s%8d%9s%9.1f   %s'
                      % ('%s %s' % (name, line_era if name == 'trailOnly' else ''),
                         len(rows),
                         ('%d' % reached_n) if name == 'exitClear' else '',
                         sum(bl), tail))
                line_era = ''

    print('')
    print('=' * 78)
    print('POOLED ACROSS EVERY CELL')
    print('  %-12s' % 'exit' + '   '.join('%-32s' % e[0] for e in ERAS) + '   verdict')
    for name in ('trailOnly', 'exitClear', 'exitTp2', 'halfClear'):
        cells, wins, losses = [], 0, 0
        for era, _, _ in ERAS:
            bl = pooled[era][name]
            base = pooled[era]['trailOnly']
            if name == 'trailOnly':
                cells.append('%+10.1f%22s' % (sum(bl), 'BASELINE'))
                continue
            lo, hi = paired_ci(bl, base)
            if lo > 0:
                wins += 1
            elif hi < 0:
                losses += 1
            cells.append('%+10.1f %+8.1f [%+6.1f,%+6.1f]'
                         % (sum(bl), sum(bl) - sum(base), lo, hi))
        verdict = ('' if name == 'trailOnly'
                   else 'BETTER IN BOTH ERAS' if wins == 2
                   else 'WORSE IN BOTH ERAS' if losses == 2
                   else 'not demonstrated')
        print('  %-12s' % name + '   '.join(cells) + '   ' + verdict)

    print('')
    print('If `exitClear` is not better in both eras, clear air is not a reason')
    print('to close -- and the entry and stop stay on the chart, because the')
    print('trade they belong to is still live.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
