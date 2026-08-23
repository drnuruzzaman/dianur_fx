#!/usr/bin/env python
"""
pattern_eval.py — run a proposer through the discovery harness.

    python tools/pattern_eval.py --symbol EURUSD.a --tf 4h
    python tools/pattern_eval.py --symbol XAUUSD.a --tf 4h --force-direction 1

Scores every (pattern, geometry) against a direction- and era-matched null, and
reports what the best of that many worthless candidates would have scored
anyway. Read `beats_expected_max` before anything else; a p-value in a sweep
this wide is decoration.

--force-direction is the diagnostic that separates a LEVEL effect from a
DIRECTIONAL one. If a proposer's patterns all improve when forced long and all
worsen when forced short, it has found drift, not structure -- which is exactly
what the trendline proposer turned out to be doing on EURUSD 4h.
"""

import argparse
import itertools
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sim.instruments import load, spec
from sim.intrabar import INTRABAR, PESSIMISTIC
from sim.patterns.evaluate import evaluate, summarise
from sim.patterns.sax import SaxMotifs
from sim.patterns.trendline import TrendlineApproach

PROPOSERS = {'trendline': TrendlineApproach, 'sax': SaxMotifs}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--symbol', default='EURUSD.a')
    ap.add_argument('--tf', default='4h')
    ap.add_argument('--proposer', default='trendline', choices=list(PROPOSERS))
    ap.add_argument('--start', default='2018-01-01')
    ap.add_argument('--end', default=None)
    ap.add_argument('--stops', default='0.4,0.6,0.8,1.0,1.5')
    ap.add_argument('--targets', default='0.8,1.2,1.6,2.0,3.0')
    ap.add_argument('--horizon', type=int, default=48)
    ap.add_argument('--min-events', type=int, default=200)
    ap.add_argument('--folds', type=int, default=4)
    ap.add_argument('--strata', type=int, default=20,
                    help='time blocks defining the null. 1 disables the era '
                         'match, which is how a trend gets scored as an edge.')
    ap.add_argument('--resolution', default=PESSIMISTIC,
                    choices=[PESSIMISTIC, INTRABAR])
    ap.add_argument('--force-direction', type=int, default=0, choices=[-1, 0, 1],
                    help='override every proposal to long (1) or short (-1)')
    ap.add_argument('--n-shifts', type=int, default=400)
    ap.add_argument('--window', type=int, default=6,
                    help='sax: bars per window')
    ap.add_argument('--path-letters', type=int, default=4)
    ap.add_argument('--body-letters', type=int, default=3)
    ap.add_argument('--body-bars', type=int, default=2)
    ap.add_argument('--min-count', type=int, default=200,
                    help='sax: words rarer than this are not scored (but ARE '
                         'counted as considered)')
    ap.add_argument('--out', default=None)
    args = ap.parse_args()

    bars = load(args.symbol, args.tf, args.start, args.end)
    if args.proposer == 'sax':
        proposer = SaxMotifs(window=args.window, path_letters=args.path_letters,
                             body_letters=args.body_letters,
                             body_bars=args.body_bars,
                             min_count=args.min_count)
    else:
        proposer = PROPOSERS[args.proposer]()
    props = proposer.run(bars, args.symbol, args.tf)
    if args.force_direction:
        props = props.copy()
        props['direction'] = args.force_direction

    geoms = list(itertools.product(
        [float(x) for x in args.stops.split(',')],
        [float(x) for x in args.targets.split(',')]))

    print('%s %s  %s..%s  %d bars' % (args.symbol, args.tf, bars.index[0].date(),
                                      bars.index[-1].date(), len(bars)))
    print('proposer=%s %s' % (proposer.name, proposer.params()))
    if hasattr(proposer, 'hypothesis_space'):
        print('enumerable word space: %d' % proposer.hypothesis_space())
    print('%d proposals over %d pattern ids; %d geometries; %d strata; '
          'resolution=%s%s\n'
          % (len(props), props['pattern_id'].nunique(), len(geoms),
             args.strata, args.resolution,
             '; FORCED direction=%+d' % args.force_direction
             if args.force_direction else ''))

    df = evaluate(bars, props, args.symbol, args.tf, geoms,
                  horizon=args.horizon, resolution=args.resolution,
                  min_events=args.min_events, n_folds=args.folds,
                  n_strata=args.strata, n_shifts=args.n_shifts,
                  spec=spec(args.symbol, args.tf))
    if not len(df):
        print(summarise(df))
        return

    cols = ['pattern_id', 'stop_atr', 'target_atr', 'rr', 'n_decided',
            'hold_pct', 'base_pct', 'dev_pp', 'edge_R', 'gross_R',
            'friction_R', 'net_R', 'z', 'z_shift', 'folds_agree',
            'survives_bh', 'beats_expected_max']
    print(df[cols].head(15).to_string(index=False))
    print('\n' + summarise(df))

    out = args.out or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'runs',
        'patterns_%s_%s_%s%s.csv' % (args.symbol.replace('.', ''), args.tf,
                                     proposer.name,
                                     '_dir%+d' % args.force_direction
                                     if args.force_direction else ''))
    df.to_csv(out, index=False)
    print('\nwrote %s' % out)


if __name__ == '__main__':
    main()
