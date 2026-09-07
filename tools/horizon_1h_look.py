#!/usr/bin/env python
"""
The 1h cell, read out of runs already on disk -- no new walks.

    python tools/horizon_1h_look.py

WHY THIS EXISTS. tools/horizon_eval.py left 1h as the weakest case for the
shipped 3.3-day horizon anywhere: pooled over four symbols it is +68.5 R in
2016-2020 and -59.8 R in 2021-2026, and the literal 20/10 leads it in both. That
is either (a) 3.3 days being wrong on this frame, or (b) the pool containing
instruments this rule does not work on in the first place. Those have different
consequences and the pooled row cannot tell them apart.

WHAT IS ASKED, in this order, so the answer to each is fixed before the next:

  1. PER CELL. Which symbols make the 1h pool negative?

  2. RE-POOLED ON A PRE-EXISTING SELECTION. tools/entry_filter_eval.py already
     found -- on its own data, before any of this -- that XAUUSD and USDJPY are
     positive in both eras while EURUSD is negative in both and GBPUSD flips.
     Restricting to the two positive cells is therefore NOT a choice made on
     this sweep's numbers; it is a prior applied to them. Any restriction chosen
     by looking at the table above would be worthless, and the difference is the
     whole reason this is stated as a rule rather than a result.

  3. DOES "SHORTER IS BETTER" APPEAR ANYWHERE ELSE? On USDJPY 1h the short
     channels beat the shipped one with intervals excluding zero. Five variants
     per cell means one of them looking good is unremarkable, so the question is
     whether the same variant does it on other instruments -- including the
     three hold-out symbols that fed none of this.
"""

import argparse
import json
import os
import random
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAIN = os.path.join(ROOT, 'runs', 'horizon_eval.json')
HOLD = os.path.join(ROOT, 'runs', 'horizon_holdout.json')

ERAS = [('2016-2020', 2016, 2020), ('2021-2026', 2021, 2026)]
N_BLOCKS = 20

#: From tools/entry_filter_eval.py -- an INDEPENDENT study, quoted not derived.
POSITIVE_CELLS = ['XAUUSD.a', 'USDJPY.a']


def year_of(ms):
    import datetime
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


def cells_for(blob, tf):
    return {k: v for k, v in blob.items() if v['tf'] == tf}


def era_blocks(res, name, era):
    """Blocks for one row, over the BASELINE's calendar window in that era."""
    y0, y1 = [(a, b) for e, a, b in ERAS if e == era][0]
    base = era_trades(res['runs'][res['baselineKey']], y0, y1)
    if not base:
        return None
    lo_t = min(t['entryTime'] for t in base)
    hi_t = max(t['entryTime'] for t in base)
    return block_totals(era_trades(res['runs'][name], y0, y1), lo_t, hi_t)


def main():
    if not (os.path.exists(MAIN) and os.path.exists(HOLD)):
        print('run tools/horizon_eval.py with --json first', file=sys.stderr)
        return 2
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--tf', default='1h',
                    help='which timeframe to dissect (default 1h, the one this '
                         'was written for)')
    args = ap.parse_args()
    tf = args.tf
    main_blob = json.load(open(MAIN))
    hold_blob = json.load(open(HOLD))
    cells = cells_for(main_blob, tf)
    hold = cells_for(hold_blob, tf)
    if not cells:
        print('no %s cells in runs/horizon_eval.json' % tf, file=sys.stderr)
        return 2

    print('')
    print('1. THE SHIPPED HORIZON ON %s, PER CELL. Net R by era, gross.' % tf)
    print('   %-12s%12s%12s   %s' % ('symbol', ERAS[0][0], ERAS[1][0], 'both eras'))
    for sym in sorted(cells) + sorted(hold):
        res = cells.get(sym) or hold[sym]
        nets = []
        for era, y0, y1 in ERAS:
            tr = era_trades(res['runs'][res['baselineKey']], y0, y1)
            nets.append(sum(t['r'] for t in tr))
        tag = 'positive' if min(nets) > 0 else ('negative' if max(nets) < 0 else 'flips')
        mark = '   (hold-out)' if sym in hold else ''
        print('   %-12s%+12.1f%+12.1f   %s%s'
              % (sym.split('|')[0], nets[0], nets[1], tag, mark))

    print('')
    print('2. RE-POOLED ON THE PRIOR SELECTION (XAUUSD + USDJPY, from')
    print('   tools/entry_filter_eval.py -- not chosen on the table above).')
    keep = [k for k in cells if k.split('|')[0] in POSITIVE_CELLS]
    drop = [k for k in cells if k.split('|')[0] not in POSITIVE_CELLS]
    for label, group in (('kept  (XAUUSD, USDJPY)', keep),
                         ('dropped (EURUSD, GBPUSD)', drop)):
        line = '   %-26s' % label
        for era, _, _ in ERAS:
            tot = 0.0
            for k in group:
                bt = era_blocks(cells[k], cells[k]['baselineKey'], era)
                tot += sum(bt or [])
            line += '%+12.1f' % tot
        print(line)

    print('')
    print('   And the ladder, on the kept cells only:')
    order = cells[keep[0]]['order']
    base_key = cells[keep[0]]['baselineKey']
    print('   %-16s' % 'horizon' + '   '.join('%-28s' % e[0] for e in ERAS))
    pooled = {e[0]: {} for e in ERAS}
    for name in order:
        for era, _, _ in ERAS:
            acc = []
            for k in keep:
                bt = era_blocks(cells[k], name, era)
                if bt:
                    acc.extend(bt)
            pooled[era][name] = acc
    for name in order:
        cellstr = []
        for era, _, _ in ERAS:
            tot = sum(pooled[era][name])
            if name == base_key:
                cellstr.append('%+9.1f%19s' % (tot, 'SHIPPED'))
            else:
                lo, hi = paired_block_ci(pooled[era][name], pooled[era][base_key])
                cellstr.append('%+9.1f  %+7.1f [%+5.1f,%+5.1f]'
                               % (tot, tot - sum(pooled[era][base_key]), lo, hi))
        print('   %-16s' % name + '   '.join(cellstr))

    print('')
    print('3. DOES "SHORTER IS BETTER" REPEAT? Gain over the shipped horizon for')
    print('   the two shortest rows, every %s cell including the hold-outs.' % tf)
    print('   %-12s%30s%30s' % ('symbol', 'native 20/10', '0.1x shipped'))
    for sym in sorted(cells) + sorted(hold):
        res = cells.get(sym) or hold[sym]
        bits = []
        for name in ('native 20/10', '0.1x shipped'):
            if name not in res['runs']:
                bits.append('%30s' % '-')
                continue
            per = []
            for era, _, _ in ERAS:
                bt = era_blocks(res, name, era)
                bb = era_blocks(res, res['baselineKey'], era)
                if bt is None or bb is None:
                    per.append('    -   ')
                    continue
                lo, hi = paired_block_ci(bt, bb)
                sig = '*' if (lo > 0 or hi < 0) else ' '
                per.append('%+8.1f%s' % (sum(bt) - sum(bb), sig))
            bits.append('%30s' % ('  '.join(per)))
        print('   %-12s%s' % (sym.split('|')[0], ''.join(bits))
              + ('   (hold-out)' if sym in hold else ''))
    print('')
    print('   * = the paired interval excludes zero. With five variants per cell')
    print('     and two eras, isolated stars are expected; a horizon worth')
    print('     changing to would star in the same direction across cells.')

    print('')
    print('4. THE SAME NUMBERS TALLIED BY ERA, which is what they are really')
    print('   about. If a shorter channel were better on 1h it would be better')
    print('   in both; if it is a regime, it lands in one.')
    for name in ('native 20/10', '0.1x shipped', '0.5x shipped'):
        row = '   %-14s' % name
        for era, _, _ in ERAS:
            gains = []
            for sym in sorted(cells) + sorted(hold):
                res = cells.get(sym) or hold[sym]
                if name not in res['runs']:
                    continue
                bt = era_blocks(res, name, era)
                bb = era_blocks(res, res['baselineKey'], era)
                if bt is None or bb is None:
                    continue
                gains.append(sum(bt) - sum(bb))
            up = sum(1 for g in gains if g > 0)
            row += '   %s: %d/%d cells better, %+8.1f R total' % (
                era, up, len(gains), sum(gains))
        print(row)
    return 0


if __name__ == '__main__':
    sys.exit(main())
