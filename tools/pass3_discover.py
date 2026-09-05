#!/usr/bin/env python
"""
PASS 3 of the TP-band study: does any single detector carry information about
what happens after the entry?

    python tools/pass3_discover.py
    python tools/pass3_discover.py --cells XAUUSD.a_1h

STAGE 1 ONLY -- ONE FEATURE AT A TIME. No interactions, no combinations, no
model. If nothing here carries information on its own, a search over pairs and
triples is a search over noise, and the 127-combination problem is not something
to walk into on the strength of hope.

THE THREE QUESTIONS, asked of every feature:

  REACH     Does it change how often price gets to the band at all?
            P(reach | F=1) - P(reach | F=0).

  GAIN      Conditional on reaching, does it change whether banking there beat
            trailing? Mean of (what trailing returned - what banking paid).
            Pass 2 says this is positive nearly everywhere: trailing wins. The
            question is whether any condition turns it negative.

  MFE       Does it change how far the trade runs at all? A feature that
            predicts excursion is interesting even if it says nothing about
            where to take profit.

WHAT COUNTS AS A FINDING, fixed before looking:

  1. The effect clears that cell-era's OWN permutation null (Pass 2's p95, the
     best split obtainable from shuffled features -- 0.31 to 1.45 R depending on
     the cell). This is the multiple-comparison correction, measured rather than
     assumed.
  2. It repeats IN BOTH ERAS of the same cell, with the same sign.
  3. Its calendar-block interval excludes zero.

A feature that manages (1) and (3) in one era and not the other has found a
regime, not a rule. That is the failure mode every study in this project has
run into, so it is the first thing checked rather than the last.

SPLITS. Numeric features split at their median inside the cell-era, which is
balanced by construction -- Pass 2's first null was pinned because it split on
`donch_same_bar`, and this rule enters on a channel break, so that feature is 1
almost everywhere. Sparse features (present on under 60% of trades) are split on
PRESENCE instead: no trendline is a different state from a near one, and
imputing a distance for it would invent structure.

NOTHING HERE IS TRADEABLE. Pass 3 reports which detectors carry information.
Turning that into an exit rule is Pass 4 and needs its own controls.
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
N_PERM = 200
BANDS = ['tp1', 'R1', 'R2']

#: PRE-REGISTERED, and this list is the whole of Stage 1. Written down before
#: any result was looked at; adding a feature after seeing the table would make
#: the permutation null an understatement of the search that actually happened.
FEATURES = [
    'ms_bias_aligned',      # structure agrees with the trade's direction
    'ms_event',             # a BOS or CHoCH printed on the signal bar
    'donch_width_atr',      # how wide the channel was
    'tp1_r',                # how far the first structural level sat
    'levels_ahead',         # how many levels the detectors found ahead
    'sr_resistance_r',      # distance to the next S/R in the way
    'zone_ahead_r',         # distance to the next supply/demand base
    'tl_resistance_r',      # distance to the trendline ahead
    'trail_r',              # where the trail opened
    'atr_pct',              # volatility regime
    'range100_atr',         # compressed or expanded
]


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
    bands = defaultdict(list)
    with open(stem + '.bands.jsonl') as fh:
        for line in fh:
            r = json.loads(line)
            if r['id'] in trades:
                bands[r['band']].append(r)
    return trades, bands


def blocks_of(rows, values, times, lo_t, hi_t, blocks=N_BLOCKS):
    """Sum of `values` per calendar block, and the count per block."""
    width = (hi_t - lo_t) / blocks if hi_t > lo_t else 1
    s = [0.0] * blocks
    n = [0] * blocks
    for v, t in zip(values, times):
        b = min(blocks - 1, max(0, int((t - lo_t) / width)))
        s[b] += v
        n[b] += 1
    return s, n


def block_mean_diff_ci(rows_a, rows_b, key, seed=17, n=N_BOOT):
    """
    Difference in MEAN of `key` between two groups, resampled over calendar
    blocks rather than rows.

    Blocks, because bands inside a trade and trades inside a trend are not
    independent -- resampling rows would shrink this interval by roughly the
    square root of the clustering and make four gold trends look like proof.
    """
    if len(rows_a) < 20 or len(rows_b) < 20:
        return (float('nan'), float('nan'), float('nan'))
    ts = [r['_t'] for r in rows_a + rows_b]
    lo_t, hi_t = min(ts), max(ts)
    sa, na = blocks_of(rows_a, [r[key] for r in rows_a], [r['_t'] for r in rows_a], lo_t, hi_t)
    sb, nb = blocks_of(rows_b, [r[key] for r in rows_b], [r['_t'] for r in rows_b], lo_t, hi_t)
    obs = (sum(sa) / max(1, sum(na))) - (sum(sb) / max(1, sum(nb)))
    rnd = random.Random(seed)
    out = []
    k = len(sa)
    for _ in range(n):
        ta = tb = ca = cb = 0.0
        for _ in range(k):
            j = rnd.randrange(k)
            ta += sa[j]; ca += na[j]
            tb += sb[j]; cb += nb[j]
        if ca > 0 and cb > 0:
            out.append(ta / ca - tb / cb)
    if not out:
        return (obs, float('nan'), float('nan'))
    out.sort()
    return (obs, out[int(0.025 * len(out))], out[int(0.975 * len(out))])


def split_rows(trades_in_era, feature):
    """
    Two groups, and how they were made.

    Sparse features split on PRESENCE; the rest split at the median. `ms_event`
    is categorical and splits on "an event printed here" against "none".
    """
    vals = [t for t in trades_in_era if t.get(feature) is not None]
    cover = len(vals) / max(1, len(trades_in_era))
    if feature == 'ms_event':
        a = {t['id'] for t in trades_in_era if t.get('ms_event') is not None}
        return a, 'event vs none'
    if cover < 0.6:
        a = {t['id'] for t in vals}
        return a, 'present vs absent (%.0f%%)' % (100 * cover)
    if feature in ('ms_bias_aligned', 'levels_ahead'):
        xs = sorted(t[feature] for t in vals)
        med = xs[len(xs) // 2]
        a = {t['id'] for t in vals if t[feature] > med}
        if len(a) < 0.2 * len(vals) or len(a) > 0.8 * len(vals):
            a = {t['id'] for t in vals if t[feature] >= max(xs[0] + 1, med)}
        return a, '> %g' % med
    xs = sorted(t[feature] for t in vals)
    med = xs[len(xs) // 2]
    a = {t['id'] for t in vals if t[feature] > med}
    return a, 'above median'


def perm_p95(trades_era, bands, seed=7, n_perm=N_PERM):
    """This cell-era's own null: the best |gain| split obtainable from shuffled
    features, which is what a real finding has to clear."""
    rnd = random.Random(seed)
    ids = {t['id'] for t in trades_era}
    band_rows = {b: [r for r in bands.get(b, []) if r['id'] in ids and r['y_reached'] == 1]
                 for b in BANDS}
    band_rows = {b: rs for b, rs in band_rows.items() if len(rs) >= 40}
    if not band_rows:
        return None
    pools = {}
    for f in FEATURES:
        a, _ = split_rows(trades_era, f)
        if 0.2 * len(trades_era) < len(a) < 0.8 * len(trades_era):
            pools[f] = a
    if not pools:
        return None
    best = []
    all_ids = [t['id'] for t in trades_era]
    for _ in range(n_perm):
        top = None
        for f, a in pools.items():
            shuffled = set(rnd.sample(all_ids, len(a)))
            for b, rs in band_rows.items():
                g1 = [r for r in rs if r['id'] in shuffled]
                g0 = [r for r in rs if r['id'] not in shuffled]
                if len(g1) < 20 or len(g0) < 20:
                    continue
                m1 = sum(r['y_r_if_trailed'] - r['r_if_taken_here'] for r in g1) / len(g1)
                m0 = sum(r['y_r_if_trailed'] - r['r_if_taken_here'] for r in g0) / len(g0)
                d = abs(m1 - m0)
                top = d if top is None else max(top, d)
        if top is not None:
            best.append(top)
    if not best:
        return None
    best.sort()
    return best[int(0.95 * len(best))]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--events', default=EVENTS)
    ap.add_argument('--cells', nargs='+')
    ap.add_argument('--band', default='tp1', choices=BANDS + ['all'])
    args = ap.parse_args()

    stems = sorted({p[:-len('.trades.jsonl')]
                    for p in glob.glob(os.path.join(args.events, '*.trades.jsonl'))})
    if args.cells:
        stems = [s for s in stems if os.path.basename(s) in args.cells]
    if not stems:
        print('no cells -- run tools/event_dataset.py first', file=sys.stderr)
        return 2

    bands_to_do = BANDS if args.band == 'all' else [args.band]

    print('')
    print('PASS 3 -- STAGE 1. One detector at a time, three questions each:')
    print('does it change how often price REACHES a band, whether banking there')
    print('BEATS TRAILING, and how far the trade RUNS. A finding must clear its')
    print('own cell-era permutation null AND repeat in both eras with the same')
    print('sign. GROSS throughout.')

    # feature -> list of (cell, era, band, gain effect, cleared null, ci excl 0)
    tally = defaultdict(list)

    for stem in stems:
        cell = os.path.basename(stem)
        trades, bands = load(stem)
        for era, y0, y1 in ERAS:
            te = [t for t in trades.values() if y0 <= year_of(t['entry_time']) <= y1]
            if len(te) < 150:
                continue
            null95 = perm_p95(te, bands)
            print('')
            print('%s  %s   (%d trades; null p95 %s)'
                  % (cell, era, len(te),
                     ('%.2f R' % null95) if null95 else 'n/a'))
            print('  %-18s%-22s%7s%9s%9s%9s   %s'
                  % ('feature', 'split', 'band', 'reach d', 'gain d', 'mfe d',
                     '95% CI on gain d'))
            skipped = []
            for f in FEATURES:
                a_ids, how = split_rows(te, f)
                g1t = [t for t in te if t['id'] in a_ids]
                g0t = [t for t in te if t['id'] not in a_ids]
                if len(g1t) < 30 or len(g0t) < 30:
                    # SAY SO RATHER THAN VANISHING. `ms_bias_aligned` dropped
                    # out of the first table with no line explaining it: this
                    # rule enters on a channel break, so structure agrees with
                    # nearly every trade and there is no split to make. That is
                    # a finding about the feature, not a reason to hide it --
                    # and a silently missing row is how Pass 2 shipped a null
                    # that returned nothing for 24 cell-eras.
                    skipped.append('%s (%d/%d)' % (f, len(g1t), len(g0t)))
                    continue
                for t in te:
                    t['_t'] = t['entry_time']
                _, mlo, mhi = 0, 0, 0
                mfe_d, _, _ = block_mean_diff_ci(
                    [t for t in g1t if t['y_mfe_r'] is not None],
                    [t for t in g0t if t['y_mfe_r'] is not None], 'y_mfe_r')
                for band in bands_to_do:
                    rs = [r for r in bands.get(band, []) if r['id'] in {t['id'] for t in te}]
                    if len(rs) < 60:
                        continue
                    for r in rs:
                        r['_t'] = trades[r['id']]['entry_time']
                        r['_gain'] = r['y_r_if_trailed'] - r['r_if_taken_here']
                        r['_reach'] = float(r['y_reached'])
                    r1 = [r for r in rs if r['id'] in a_ids]
                    r0 = [r for r in rs if r['id'] not in a_ids]
                    if len(r1) < 30 or len(r0) < 30:
                        continue
                    reach_d, _, _ = block_mean_diff_ci(r1, r0, '_reach')
                    hit1 = [r for r in r1 if r['y_reached'] == 1]
                    hit0 = [r for r in r0 if r['y_reached'] == 1]
                    gain_d, glo, ghi = block_mean_diff_ci(hit1, hit0, '_gain')
                    cleared = null95 is not None and abs(gain_d) > null95
                    excl = (glo == glo and (glo > 0 or ghi < 0))
                    mark = ('*' if cleared else ' ') + ('!' if excl else ' ')
                    print('  %-18s%-22s%7s%+9.3f%+9.3f%+9.3f   [%+7.3f,%+7.3f] %s'
                          % (f, how, band, reach_d, gain_d, mfe_d, glo, ghi, mark))
                    tally[(f, band)].append((cell, era, gain_d, cleared, excl))
            if skipped:
                print('  (no usable split: %s -- the group sizes are the counts)'
                      % ', '.join(skipped))

    print('')
    print('=' * 78)
    print('WHAT REPEATED. A feature counts only where it cleared the null AND')
    print('its interval excluded zero AND the SAME cell showed the same sign in')
    print('both eras. Anything less is a regime.')
    print('  %-18s%7s%10s%10s%14s' % ('feature', 'band', 'cell-eras', 'passed', 'both eras'))
    for (f, band), rows in sorted(tally.items()):
        passed = [r for r in rows if r[3] and r[4]]
        by_cell = defaultdict(list)
        for cell, era, g, c, e in rows:
            if c and e:
                by_cell[cell].append(g)
        both = sum(1 for cell, gs in by_cell.items()
                   if len(gs) == 2 and gs[0] * gs[1] > 0)
        if not rows:
            continue
        print('  %-18s%7s%10d%10d%14d' % (f, band, len(rows), len(passed), both))
    print('')
    print('`both eras` is the column that matters. A zero there means the')
    print('detector carries no information this study can find -- which is the')
    print('same answer eleven entry gates, three retest rules and every target')
    print('variant have given.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
