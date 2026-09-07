#!/usr/bin/env python
"""
PHASES 9, 10, 11, 14 -- queries over data/research/approaches.jsonl.

    python tools/approach_phases.py [--horizon 20] [--tf 15m]

Every phase here is a GROUPING of the same table. Nothing recomputes a zone, a
pivot or an outcome: the definitions were fixed in tools/approach_dataset.mjs
before any of these numbers were read, which is the only reason a flat result
below can be trusted as flat rather than as a threshold that was never tuned.

WHAT "BASE" MEANS IN EVERY TABLE. The rejection rate over ALL approaches in the
slice being cut. A bucket is interesting when it differs from that, not when it
is above 50: rejections outnumber breakouts roughly 3:2 in this data, so 60% is
the null result, not a win.

PHASE 11 IS PARTIAL, AND THE FILE SAYS SO. The plan asks for four groups --
neither / zone only / liquidity only / zone+liquidity. This table has one row
per ZONE APPROACH, so groups A (neither) and C (liquidity only) do not exist in
it: there is no row where no zone was present. B and D are answerable and are
reported; A and C need a second sample drawn from bars with no zone nearby, and
that is a change to the dataset builder rather than a query.
"""

import argparse
import collections
import json
import math
import os
import statistics
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, 'data', 'research', 'approaches.jsonl')


def load(path, tf=None, pivot=None):
    rows = []
    with open(path, encoding='utf-8') as fh:
        for line in fh:
            r = json.loads(line)
            if tf and r['timeframe'] != tf:
                continue
            if pivot and r['pivot_definition'] != pivot:
                continue
            rows.append(r)
    return rows


def summarise(rows, hz, label='y_first_%d'):
    """The block of numbers every phase below prints.

    DEFAULTS TO THE WIDTH-FREE LABEL. `y_outcome_*` measures a breakout from
    the far edge, so a wider band makes one mechanically rarer; every geometry
    feature inherited that. `y_first_*` is a symmetric barrier race from the
    approach bar and cannot be tilted by the band's size.
    """
    if not rows:
        return None
    key = label % hz
    raw = collections.Counter(r.get(key) for r in rows)
    # map either vocabulary onto the same three columns
    oc = collections.Counter()
    oc['rejection'] = raw.get('toward', 0) + raw.get('rejection', 0)
    oc['breakout'] = raw.get('through', 0) + raw.get('breakout', 0)
    oc['failed_breakout'] = raw.get('failed_breakout', 0)
    n = len(rows)
    mfe = [r['y_mfe_atr_%d' % hz] for r in rows if r.get('y_mfe_atr_%d' % hz) is not None]
    mae = [r['y_mae_atr_%d' % hz] for r in rows if r.get('y_mae_atr_%d' % hz) is not None]
    p = lambda x: 100.0 * sum(1 for v in mfe if v >= x) / len(mfe) if mfe else float('nan')
    return {
        'n': n,
        'rej': 100.0 * oc['rejection'] / n,
        'brk': 100.0 * oc['breakout'] / n,
        'fbo': 100.0 * oc['failed_breakout'] / n,
        'mfe': statistics.median(mfe) if mfe else float('nan'),
        'mae': statistics.median(mae) if mae else float('nan'),
        'p05': p(0.5), 'p1': p(1.0), 'p2': p(2.0),
    }


HDR = ('%-22s %7s %7s %7s %7s  %6s %6s  %6s %6s %6s'
       % ('bucket', 'n', 'rej%', 'brk%', 'fbo%', 'MFE', 'MAE', 'P.5', 'P1', 'P2'))


def line(label, s):
    if not s:
        return
    print('%-22s %7d %7.1f %7.1f %7.1f  %6.2f %6.2f  %6.1f %6.1f %6.1f'
          % (label[:22], s['n'], s['rej'], s['brk'], s['fbo'],
             s['mfe'], s['mae'], s['p05'], s['p1'], s['p2']))


LABEL = 'y_first_%d'


def buckets(rows, key, edges, hz, fmt='%g-%g'):
    out = []
    for a, b in zip(edges, edges[1:]):
        sel = [r for r in rows if r.get(key) is not None and a <= r[key] < b]
        out.append((fmt % (a, b), summarise(sel, hz, LABEL)))
    return out


def quantile_edges(rows, key, k=5):
    vals = sorted(r[key] for r in rows if r.get(key) is not None)
    if len(vals) < k * 20:
        return None
    return [vals[0]] + [vals[int(len(vals) * i / k)] for i in range(1, k)] + [vals[-1] + 1e-9]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--horizon', type=int, default=20)
    ap.add_argument('--tf', default=None, help='restrict to one timeframe')
    ap.add_argument('--pivot', default='fixed', help='fixed | zigzag | all')
    ap.add_argument('--label', default='y_first_%d',
                    help='y_first_%%d (width-free, default) or y_outcome_%%d')
    args = ap.parse_args()

    rows = load(SRC, args.tf, None if args.pivot == 'all' else args.pivot)
    if not rows:
        print('no rows -- run tools/approach_dataset.mjs first', file=sys.stderr)
        return 2
    hz = args.horizon
    global LABEL
    LABEL = args.label
    base = summarise(rows, hz, LABEL)
    print('approaches %d   horizon %d bars   pivots=%s   tf=%s'
          % (len(rows), hz, args.pivot, args.tf or 'all'))
    print('label=%s   MFE/MAE medians in ATR; P.5/P1/P2 = P(MFE >= n ATR).' % LABEL)
    print('')
    print(HDR)
    print('-' * len(HDR))
    line('ALL (the base rate)', base)

    # ---- PHASE 9: the old strength score ----------------------------------
    print('')
    print('PHASE 9  strength, the number printed on the chart')
    print('-' * len(HDR))
    for lab, s in buckets(rows, 'strength', [0, 20, 40, 60, 80, 101], hz, '%d-%d'):
        line(lab, s)

    # ---- PHASE 10: individual zone features -------------------------------
    print('')
    print('PHASE 10  one feature at a time, quintiles of its own distribution')
    for key in ('zone_width_atr', 'touch_count', 'touch_density', 'reaction_atr',
                'zone_age_bars', 'zone_recency_bars', 'zone_span_bars',
                'distance_from_price_atr', 'break_count', 'bars_since_break',
                'htf_zone_dist_atr'):
        edges = quantile_edges(rows, key)
        if not edges:
            continue
        print('-' * len(HDR))
        print(key)
        for lab, s in buckets(rows, key, edges, hz, '%.3g-%.3g'):
            line('  ' + lab, s)

    # ---- HIGHER-FRAME CONFLUENCE -----------------------------------------
    print('')
    print('HTF  is this level also a level on the higher frame?')
    print('     (5m->1h, 15m->4h, 1h->4h, 4h->1d; the HTF zones are read from')
    print('      the last HTF bar that had CLOSED at the approach)')
    print('-' * len(HDR))
    line('no HTF zone here', summarise([r for r in rows if r.get('htf_zone_present') == 0], hz, LABEL))
    line('HTF zone contains it', summarise([r for r in rows if r.get('htf_zone_present') == 1], hz, LABEL))
    line('  and bands overlap', summarise([r for r in rows if r.get('htf_zone_overlap') == 1], hz, LABEL))
    line('  no band overlap', summarise([r for r in rows if r.get('htf_zone_overlap') == 0], hz, LABEL))

    # ---- PHASE 11: zone vs liquidity (B and D only) -----------------------
    print('')
    print('PHASE 11  zone approaches split by whether liquidity sits with them')
    print('          (groups A/C do not exist in this table -- see the docstring)')
    print('-' * len(HDR))
    line('B  zone only', summarise([r for r in rows if not r.get('liq_present')], hz, LABEL))
    line('D  zone + liquidity', summarise([r for r in rows if r.get('liq_present')], hz, LABEL))
    edges = quantile_edges(rows, 'liq_nearest_atr')
    if edges:
        print('   distance to nearest liquidity level, in ATR')
        for lab, s in buckets(rows, 'liq_nearest_atr', edges, hz, '%.2f-%.2f'):
            line('  ' + lab, s)

    # ---- PHASE 14: volatility state ---------------------------------------
    print('')
    print('PHASE 14  by volatility state at the last release (prior_vol_ratio)')
    print('-' * len(HDR))
    named = [('LOW    <1.2', lambda v: v < 1.2),
             ('NORMAL 1.2-2', lambda v: 1.2 <= v < 2.0),
             ('HIGH   2-3', lambda v: 2.0 <= v < 3.0),
             ('EXTREME >=3', lambda v: v >= 3.0)]
    for lab, test in named:
        sel = [r for r in rows if r.get('prior_vol_ratio') is not None
               and test(r['prior_vol_ratio'])]
        line(lab, summarise(sel, hz, LABEL))
    print('   and by the chart\'s own regime, for comparison')
    for reg in sorted({r['regime'] for r in rows if r.get('regime')}):
        line('  ' + reg, summarise([r for r in rows if r.get('regime') == reg], hz, LABEL))
    print('')
    print('A bucket matters when it differs from the ALL row, not when it beats 50.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
