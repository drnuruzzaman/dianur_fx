#!/usr/bin/env python
"""
PASS 2 of the TP-band study: the baselines every later claim is measured against.

    python tools/pass2_baselines.py
    python tools/pass2_baselines.py --cells XAUUSD.a_1h

Pass 1 wrote the trades down. This establishes what "no edge" looks like in that
data, so Pass 3 has something to beat rather than something to beat by eye.

WHAT IT REPORTS, per cell and per era:

  1. THE TRADE BASELINES. Net R for the shipped configuration and for its two
     matched controls, with the paired difference and a calendar-block bootstrap
     interval. This re-derives, from the dataset rather than from a walk, the
     result every earlier study reached -- and if it disagrees with
     logs/horizon_eval.txt then the dataset is wrong, which is the point of
     computing it here.

  2. THE UNCONDITIONAL BAND TABLE. For every candidate band: how often price
     reached it, what banking there would have paid, what the trade actually
     returned by trailing, and the difference. THIS IS THE NUMBER PASS 3 HAS TO
     BEAT. tools/partial_tp_eval.py already showed that taking half at TP1 loses
     46-213 R per era per cell; this shows the same thing band by band, so a
     later "condition X makes TP1 worth taking" can be read against the base
     rate for TP1 rather than against zero.

  3. THE PERMUTATION NULL. Every feature is shuffled across trades within the
     era, destroying the link to outcomes while keeping its marginal
     distribution, and the best band-conditional edge is recomputed. Repeated,
     this gives the distribution of the best result obtainable from features
     that cannot possibly work -- which is the correction for looking at many
     conditions, computed rather than assumed.

WHY THE BOOTSTRAP IS OVER CALENDAR BLOCKS AND NOT OVER ROWS. Three bands per
trade and a thousand trades per cell are not 3,000 independent observations:
bands within a trade share one outcome, and trades within a trend share one
market. Resampling rows would shrink every interval by roughly the square root of
that clustering and turn four gold trends into overwhelming evidence. Blocks are
contiguous stretches of calendar time and are resampled whole.
"""

import argparse
import glob
import json
import math
import os
import random
import sys
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EVENTS = os.path.join(ROOT, 'data', 'research', 'events')

ERAS = [('2016-2020', 2016, 2020), ('2021-2026', 2021, 2026)]
N_BLOCKS = 20
N_BOOT = 2000
N_PERM = 200

#: Bands worth a row of their own in the report. The rest are still in the data.
REPORT_BANDS = ['R0.5', 'R1', 'R2', 'R3', 'tp1', 'tp2', 'tp3',
                'sr', 'zone_ahead', 'donchian', 'trendline']


def year_of(ms):
    import datetime
    return datetime.datetime.fromtimestamp(ms / 1000, datetime.timezone.utc).year


def load_summary(cell_stem):
    """The unsampled truth for a cell, when Pass 1 wrote one.

    Cells above the row cap have features for every k-th trade only, so any
    TOTAL computed from the rows is 1/k of the real one. The summary carries net
    R and trade counts from every trade the walk took, and is used to check the
    scaled estimate rather than to replace it -- if the two disagree badly, the
    scaling assumption is wrong and the report says so.
    """
    path = cell_stem + '.summary.json'
    if not os.path.exists(path):
        return None
    with open(path) as fh:
        return json.load(fh)


def load(cell_stem):
    """Feature rows for a cell, or nothing when it was built --summary-only.

    A cell whose walk is too big to feature every trade can be rebuilt with
    exact per-era calendar blocks and NO rows at all. That cell still has a
    trade table -- net R and both controls come from the summary -- but no
    bands, no MFE and no permutation null, because those need the features.
    Returning empties rather than raising is what lets one report hold both
    kinds of cell.
    """
    if not os.path.exists(cell_stem + '.trades.jsonl'):
        return {}, defaultdict(list)
    trades = {}
    with open(cell_stem + '.trades.jsonl') as fh:
        for line in fh:
            r = json.loads(line)
            trades[r['id']] = r
    bands = defaultdict(list)
    with open(cell_stem + '.bands.jsonl') as fh:
        for line in fh:
            r = json.loads(line)
            bands[r['band']].append(r)
    return trades, bands


def step_of(rows):
    """The sampling step Pass 1 used for these rows -- 1 when it featured every
    trade. Mixed steps inside one group would make a total meaningless, so that
    is reported rather than averaged over."""
    steps = {r.get('sample_step', 1) for r in rows}
    if len(steps) > 1:
        raise ValueError('mixed sample_step in one group: %s' % sorted(steps))
    return steps.pop() if steps else 1


def blocks_of(rows, key, lo_t, hi_t, blocks=N_BLOCKS, scale=1):
    """Sum `key` per contiguous block of calendar time, scaled back up to the
    full trade list when Pass 1 sampled."""
    if hi_t <= lo_t:
        return [0.0] * blocks
    width = (hi_t - lo_t) / blocks
    out = [0.0] * blocks
    for r in rows:
        b = min(blocks - 1, max(0, int((r['entry_time'] - lo_t) / width)))
        out[b] += r[key] * scale
    return out


def block_ci(a, b, n=N_BOOT, seed=17):
    """Paired percentile bootstrap over blocks."""
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


def era_rows(rows, y0, y1, source=None):
    out = []
    for r in rows:
        if source is not None and r.get('source') != source:
            continue
        if y0 <= year_of(r['entry_time']) <= y1:
            out.append(r)
    return out


def trade_baselines(trades, era, y0, y1, summ=None):
    """
    Net R per source, and each control's paired difference from the rule.

    EXACT WHERE EXACT EXISTS. When Pass 1 wrote calendar blocks from every trade,
    they are used and nothing is scaled. Scaling a sampled total does not
    reconstruct a heavy-tailed one: on USDJPY 1m every-11th sampling produced
    +305.1 R against an actual -1470.9, the wrong sign, because a few large
    trades either make the sample or do not. The band statistics below stay on
    the sampled rows, where they are means and the sampling cancels.
    """
    if summ:
        blocks = {}
        for src in ('shipped', 'randEntry', 'randSide'):
            rec = summ['summary'].get(src, {}).get(era)
            if not rec or 'blocks' not in rec:
                blocks = None
                break
            blocks[src] = rec
        if blocks:
            out = {}
            base = blocks['shipped']['blocks']
            for src, rec in blocks.items():
                r = {'n': rec['n'], 'net': sum(rec['blocks']),
                     'win': rec['win'] if rec['win'] is not None else float('nan'),
                     'mfe': float('nan'), 'exact': True}
                if src != 'shipped':
                    r['delta'] = sum(rec['blocks']) - sum(base)
                    r['ci'] = block_ci(rec['blocks'], base)
                out[src] = r
            # MFE is not in the summary -- it needs the feature rows, so it comes
            # from the sample and is a mean, which sampling leaves alone.
            for src in out:
                rows = era_rows(list(trades.values()), y0, y1, src)
                vals = [r['y_mfe_r'] for r in rows if r.get('y_mfe_r') is not None]
                out[src]['mfe'] = sum(vals) / len(vals) if vals else float('nan')
            return out
    
    all_rows = list(trades.values())
    ship = era_rows(all_rows, y0, y1, 'shipped')
    if not ship:
        return None
    lo_t = min(r['entry_time'] for r in ship)
    hi_t = max(r['entry_time'] for r in ship)
    out = {}
    # SCALED BY THE SAMPLING STEP. A cell that featured every 7th trade holds a
    # seventh of the R, and putting that beside an unsampled cell would read as
    # a collapse in performance rather than as a smaller sample.
    base_blocks = blocks_of(ship, 'y_r', lo_t, hi_t, scale=step_of(ship))
    for src in ('shipped', 'randEntry', 'randSide'):
        rows = era_rows(all_rows, y0, y1, src)
        bl = blocks_of(rows, 'y_r', lo_t, hi_t, scale=step_of(rows))
        rec = {'n': len(rows), 'net': sum(bl),
               'win': 100.0 * sum(1 for r in rows if r['y_r'] > 0) / len(rows) if rows else float('nan'),
               'mfe': sum(r['y_mfe_r'] for r in rows if r['y_mfe_r'] is not None) / max(1, len(rows))}
        if src != 'shipped':
            # BOTH ARE control MINUS shipped, which is the convention every
            # other tool here prints. They disagreed in the first draft -- a
            # +69.5 delta beside a [-131.6, -9.2] interval -- and a sign error
            # that only shows up when the two happen to differ is exactly the
            # kind that survives a skim.
            rec['delta'] = sum(bl) - sum(base_blocks)
            rec['ci'] = block_ci(bl, base_blocks)
        out[src] = rec
    return out


#: What "the next band" means, per family. The dataset's own `y_reached_next`
#: walks the band LIST, so tp3's successor was whatever structural band happened
#: to be recorded after it -- an ordering artefact, not a ladder.
NEXT_IN_FAMILY = {'R0.5': 'R1', 'R1': 'R2', 'R2': 'R3', 'R3': 'R4',
                  'tp1': 'tp2', 'tp2': 'tp3'}


def next_rate(band, reached, bands, trades, y0, y1):
    """Share of trades that, having reached this band, went on to the next one
    in the SAME ladder. `None` where the band has no successor."""
    nxt = NEXT_IN_FAMILY.get(band)
    if not nxt:
        return None
    hit = {r['id'] for r in bands.get(nxt, []) if r['y_reached'] == 1}
    return 100.0 * sum(1 for r in reached if r['id'] in hit) / max(1, len(reached))


def band_table(trades, bands, era, y0, y1):
    """
    The unconditional decision at every band.

    `banked` is what taking the whole position there would pay; `trailed` is what
    the trade actually returned. Their difference, over the trades that REACHED
    the band, is the base rate a conditional rule has to beat -- positive means
    banking there was right on average.
    """
    rows = []
    for band in REPORT_BANDS:
        rs = [r for r in bands.get(band, [])
              if trades[r['id']].get('source') == 'shipped'
              and y0 <= year_of(trades[r['id']]['entry_time']) <= y1]
        if len(rs) < 30:
            continue
        for r in rs:
            r['entry_time'] = trades[r['id']]['entry_time']
        reached = [r for r in rs if r['y_reached'] == 1]
        if len(reached) < 20:
            rows.append({'band': band, 'n': len(rs), 'reach': 100.0 * len(reached) / len(rs),
                         'thin': True})
            continue
        lo_t = min(r['entry_time'] for r in reached)
        hi_t = max(r['entry_time'] for r in reached)
        for r in reached:
            r['_gain'] = r['y_r_if_trailed'] - r['r_if_taken_here']
        gain_bl = blocks_of(reached, '_gain', lo_t, hi_t, scale=step_of(reached))
        zero_bl = [0.0] * len(gain_bl)
        lo, hi = block_ci(gain_bl, zero_bl)
        rows.append({
            'band': band, 'n': len(rs), 'reach': 100.0 * len(reached) / len(rs),
            'dist': sum(r['dist_r'] for r in rs) / len(rs),
            'banked': sum(r['r_if_taken_here'] for r in reached) / len(reached),
            'trailed': sum(r['y_r_if_trailed'] for r in reached) / len(reached),
            'gain': sum(gain_bl), 'ci': (lo, hi),
            'next': next_rate(band, reached, bands, trades, y0, y1),
            'thin': False,
        })
    return rows


def permutation_null(trades, bands, y0, y1, n_perm=N_PERM, seed=7):
    """
    THE BEST EDGE OBTAINABLE FROM FEATURES THAT CANNOT WORK.

    Feature values are shuffled across trades inside the era -- marginals kept,
    the link to outcomes destroyed -- and the best band x condition split is
    recomputed. The 95th percentile of that is how good a "discovery" has to be
    before it means anything, and it is measured rather than assumed.

    SPLITS ARE AT THE MEDIAN, and the first version was not. It used the two
    binary features to hand, `ms_bias_aligned` and `donch_same_bar` -- and this
    rule ENTERS on a channel break, so `donch_same_bar` is 1 on nearly every
    trade. Neither feature split anything, every subset was either under 20 rows
    or over 80% of them, and the function returned None for all 24 cell-eras
    without saying so. A median split is balanced by construction, which is what
    a null distribution needs.

    LABELS ARE NOT ELIGIBLE. Only `feats` may be shuffled here; anything with a
    `y_` prefix is an outcome and conditioning on it would manufacture an edge
    from nothing.
    """
    feats = ['tp1_r', 'atr_pct', 'range100_atr', 'donch_width_atr',
             'zone_behind_age', 'sr_resistance_r', 'trail_r']
    rnd = random.Random(seed)
    ship = [r for r in trades.values() if r['source'] == 'shipped'
            and y0 <= year_of(r['entry_time']) <= y1]
    if len(ship) < 100:
        return None
    ids = {s['id'] for s in ship}
    band_rows = {b: [r for r in bands.get(b, []) if r['id'] in ids and r['y_reached'] == 1]
                 for b in ('tp1', 'R1', 'R2')}
    band_rows = {b: rs for b, rs in band_rows.items() if len(rs) >= 40}
    if not band_rows:
        return None

    # Binarise each feature at its median over the trades that have it.
    binary = {}
    for f in feats:
        vals = sorted(r[f] for r in ship if r.get(f) is not None)
        if len(vals) < 0.5 * len(ship):
            continue                      # too sparse to split on
        med = vals[len(vals) // 2]
        binary[f] = {r['id']: (1 if (r.get(f) is not None and r[f] > med) else 0)
                     for r in ship if r.get(f) is not None}
    if not binary:
        return None

    best = []
    for _ in range(n_perm):
        shuffled = {}
        for f, mapping in binary.items():
            keys = list(mapping.keys())
            vals = list(mapping.values())
            rnd.shuffle(vals)
            shuffled[f] = dict(zip(keys, vals))
        top = None
        for b, rs in band_rows.items():
            for f, mapping in shuffled.items():
                for v in (0, 1):
                    sub = [r for r in rs if mapping.get(r['id']) == v]
                    if len(sub) < 20 or len(sub) > 0.8 * len(rs):
                        continue
                    g = sum(r['y_r_if_trailed'] - r['r_if_taken_here'] for r in sub) / len(sub)
                    top = abs(g) if top is None else max(top, abs(g))
        if top is not None:
            best.append(top)
    if not best:
        return None
    best.sort()
    return {'n': len(best), 'p50': best[len(best) // 2],
            'p95': best[int(0.95 * len(best))],
            'feats': len(binary), 'bands': len(band_rows)}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--events', default=EVENTS)
    ap.add_argument('--cells', nargs='+', help='stems, e.g. XAUUSD.a_1h')
    args = ap.parse_args()

    # BOTH KINDS OF CELL. Discovering by rows alone silently dropped the two 1m
    # cells the moment they were rebuilt --summary-only: they had exact totals
    # sitting on disk and vanished from the report instead of appearing in it.
    stems = sorted({p[:-len('.trades.jsonl')]
                    for p in glob.glob(os.path.join(args.events, '*.trades.jsonl'))}
                   | {p[:-len('.summary.json')]
                      for p in glob.glob(os.path.join(args.events, '*.summary.json'))})
    if args.cells:
        stems = [s for s in stems if os.path.basename(s) in args.cells]
    if not stems:
        print('no cells found in %s -- run tools/event_dataset.py first' % args.events,
              file=sys.stderr)
        return 2

    print('')
    print('PASS 2 -- BASELINES. What "no edge" looks like in the Pass 1 data, so')
    print('Pass 3 has a number to beat. Every interval is a bootstrap over')
    print('CALENDAR BLOCKS: bands inside a trade and trades inside a trend are')
    print('not independent observations. GROSS throughout.')

    for stem in stems:
        cell = os.path.basename(stem)
        trades, bands = load(stem)
        summ = load_summary(stem)
        step = max((r.get('sample_step', 1) for r in trades.values()), default=1)
        print('')
        print('=' * 78)
        if not trades:
            print('%s   (summary only: exact totals, no feature rows)' % cell)
        else:
            print('%s   (%d trade rows, %d band rows%s)'
                  % (cell, len(trades), sum(len(v) for v in bands.values()),
                     '' if step == 1 else
                     '; every %dth trade featured, totals scaled' % step))
        print('=' * 78)

        for era, y0, y1 in ERAS:
            tb = trade_baselines(trades, era, y0, y1, summ)
            if not tb:
                print('  %-10s (no trades)' % era)
                continue
            print('  %s' % era)
            print('    %-12s%7s%8s%9s%9s   %s'
                  % ('source', 'n', 'win%', 'net R', 'mean MFE', 'vs shipped'))
            if tb['shipped'].get('exact') and not trades:
                print('    net R and intervals from EVERY trade (%d shipped); '
                      'no feature rows, so no MFE, bands or null here'
                      % tb['shipped']['n'])
            elif tb['shipped'].get('exact'):
                print('    net R and intervals from EVERY trade (%d shipped); '
                      'MFE from the %s sample'
                      % (tb['shipped']['n'], 'every %dth' % step if step > 1 else 'full'))
            elif step > 1:
                print('    ! totals are SCALED from every %dth trade and are not '
                      'reliable here -- re-run Pass 1 with --summary-only' % step)
            for src in ('shipped', 'randEntry', 'randSide'):
                r = tb[src]
                tail = ''
                if 'delta' in r:
                    tail = '%+8.1f [%+7.1f,%+7.1f]' % (r['delta'], r['ci'][0], r['ci'][1])
                print('    %-12s%7d%8.1f%9.1f%9.2f   %s'
                      % (src, r['n'], r['win'], r['net'], r['mfe'], tail))

            rows = band_table(trades, bands, era, y0, y1)
            if rows:
                print('')
                print('    %-12s%7s%8s%8s%9s%9s%10s%8s   %s'
                      % ('band', 'n', 'reach%', 'dist R', 'banked', 'trailed',
                         'gain R', 'next%', '95% CI on gain'))
                for r in rows:
                    if r['thin']:
                        print('    %-12s%7d%8.1f   (too few reached to score)'
                              % (r['band'], r['n'], r['reach']))
                        continue
                    nxt = '%8.0f' % r['next'] if r['next'] is not None else '%8s' % '-'
                    print('    %-12s%7d%8.1f%8.2f%9.2f%9.2f%10.1f%s   [%+7.1f,%+7.1f]'
                          % (r['band'], r['n'], r['reach'], r['dist'], r['banked'],
                             r['trailed'], r['gain'], nxt, r['ci'][0], r['ci'][1]))

            perm = permutation_null(trades, bands, y0, y1)
            if perm:
                print('')
                print('    permutation null over %d shuffles (%d features x %d bands, '
                      'median splits): median best |gain/trade| %.3f R, 95th pct %.3f R'
                      % (perm['n'], perm['feats'], perm['bands'], perm['p50'], perm['p95']))
                print('    -> a conditional split has to clear the 95th percentile '
                      'before it is worth a second look.')

    print('')
    print('=' * 78)
    print('READ `gain R` FIRST. It is what the trade returned by trailing MINUS')
    print('what banking at the band would have paid, over the trades that got')
    print('there. Positive means letting it run was right on average, which is')
    print('the base rate a conditional TP has to overturn -- not zero.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
