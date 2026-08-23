#!/usr/bin/env python
"""
pattern_walkforward.py — do the survivors survive on data they were not chosen on?

    python tools/pattern_walkforward.py --symbol EURUSD.a --tf 1h

Expanding-window walk-forward. History is cut into segments; each segment is
tested using only patterns selected on the segments BEFORE it. Selection and
testing never share a bar.

WHY THE OUT-OF-SAMPLE PASS IS A DIFFERENT KIND OF TEST

In the discovery pass the multiplicity is enormous -- every word, every
direction, every geometry -- so the deflated bar is high and most things die
against it. In the out-of-sample pass there is no search at all: the hypotheses
were fixed before the test data was touched, so the family is however many
survived, usually a handful, and the bar drops accordingly. That is not a
loophole. It is the entire reason out-of-sample evidence is worth more per
observation than in-sample evidence, and it only holds if the selection really
was blind, which is what the segment boundaries enforce.

WHAT COUNTS AS SURVIVING

A candidate has to keep its SIGN. A pattern selected long that comes back
negative out of sample has not "underperformed", it has been refuted -- and
because the discovery pass scores both directions, the sign is a genuine
prediction rather than a coin flip fitted after the fact.

Sign agreement across folds is reported as a fraction, and it is the headline.
With a handful of candidates over a few folds, individual out-of-sample z values
are noisy; whether the signs hold is the thing that generalises.
"""

import argparse
import itertools
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sim.instruments import load, spec
from sim.patterns.evaluate import evaluate
from sim.patterns.sax import SaxMotifs
from sim.patterns.trendline import TrendlineApproach

PROPOSERS = {'trendline': TrendlineApproach, 'sax': SaxMotifs}


def slice_bars(bars, props, lo, hi, warmup):
    """
    Bars [lo, hi) plus a warm-up head, with proposals reindexed onto the slice.

    The warm-up exists because ATR, the trailing ranks behind the strata, and
    the outcome tables all need history before the first scored bar; without it
    the head of every fold is measured with half-formed inputs. Proposals are
    still restricted to bars at or after `lo`, so the warm-up informs the
    measurement without ever being tested on.
    """
    start = max(0, lo - warmup)
    sub = bars.iloc[start:hi]
    sel = props[(props['bar'] >= lo) & (props['bar'] < hi)].copy()
    sel['bar'] = sel['bar'].to_numpy() - start
    return sub, sel


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--symbol', default='EURUSD.a')
    ap.add_argument('--tf', default='1h')
    ap.add_argument('--proposer', default='sax', choices=list(PROPOSERS))
    ap.add_argument('--start', default='2005-01-01')
    ap.add_argument('--end', default=None)
    ap.add_argument('--stops', default='0.5,1.0')
    ap.add_argument('--targets', default='1.0,2.0,3.0')
    ap.add_argument('--segments', type=int, default=5)
    ap.add_argument('--horizon', type=int, default=48)
    ap.add_argument('--min-events', type=int, default=150)
    ap.add_argument('--min-count', type=int, default=150)
    ap.add_argument('--window', type=int, default=6)
    ap.add_argument('--n-shifts', type=int, default=200)
    ap.add_argument('--strata', type=int, default=10)
    ap.add_argument('--warmup', type=int, default=2500)
    ap.add_argument('--out', default=None)
    args = ap.parse_args()

    bars = load(args.symbol, args.tf, args.start, args.end)
    sp = spec(args.symbol, args.tf)
    proposer = (SaxMotifs(window=args.window, min_count=args.min_count)
                if args.proposer == 'sax' else TrendlineApproach())
    # Proposals are built once on the whole series. That is safe here and only
    # here: every input to a word -- the ATR divisor, the trailing ranks -- looks
    # backwards only, so a bar's word is identical whether or not the future
    # exists. tests/test_patterns.py asserts exactly that for the strata.
    props = proposer.run(bars, args.symbol, args.tf)
    geoms = list(itertools.product([float(x) for x in args.stops.split(',')],
                                   [float(x) for x in args.targets.split(',')]))

    n = len(bars)
    edges = np.linspace(0, n, args.segments + 1).astype(int)
    print('%s %s  %s..%s  %d bars, %d segments'
          % (args.symbol, args.tf, bars.index[0].date(), bars.index[-1].date(),
             n, args.segments))
    print('proposer=%s  %d proposals, %d words, %d geometries\n'
          % (proposer.name, len(props), props['pattern_id'].nunique(),
             len(geoms)))

    oos = []
    for k in range(1, args.segments):
        tr_bars, tr_props = slice_bars(bars, props, 0, edges[k], 0)
        te_bars, te_props = slice_bars(bars, props, edges[k], edges[k + 1],
                                       args.warmup)
        if not len(tr_props) or not len(te_props):
            continue

        tr = evaluate(tr_bars, tr_props, args.symbol, args.tf, geoms,
                      horizon=args.horizon, min_events=args.min_events,
                      n_strata=args.strata, n_shifts=args.n_shifts, spec=sp)
        if not len(tr):
            print('fold %d: nothing testable in train' % k)
            continue

        # SELECTION -- in-sample only. Statistically distinguishable after
        # deflation, economically positive after costs, and consistent across
        # the training eras.
        sel = tr[tr['beats_expected_max'] & (tr['net_R'] > 0)
                 & (tr['fold_frac'].isin([0.0, 1.0]))]
        print('fold %d  train %s..%s (%d rows, %d hypotheses) -> %d selected'
              % (k, tr_bars.index[0].date(), tr_bars.index[-1].date(),
                 len(tr), tr.attrs['n_hypotheses'], len(sel)))
        if not len(sel):
            continue

        only = [(r.pattern_id, int(r.direction), r.stop_atr, r.target_atr)
                for r in sel.itertuples()]
        te = evaluate(te_bars, te_props, args.symbol, args.tf, geoms,
                      horizon=args.horizon, min_events=30,
                      n_strata=max(2, args.strata // 2),
                      n_shifts=args.n_shifts, spec=sp, only=only)
        if not len(te):
            print('    nothing from the selection recurred often enough to test')
            continue

        m = te.merge(sel[['pattern_id', 'direction', 'stop_atr', 'target_atr',
                          'dev_pp', 'net_R']],
                     on=['pattern_id', 'direction', 'stop_atr', 'target_atr'],
                     suffixes=('_oos', '_is'))
        m['fold'] = k
        m['test_from'] = te_bars.index[args.warmup].date() \
            if len(te_bars) > args.warmup else te_bars.index[0].date()
        m['sign_held'] = np.sign(m['dev_pp_oos']) == np.sign(m['dev_pp_is'])
        oos.append(m)
        for r in m.itertuples():
            print('    %-12s %-5s %.1f/%.1f  IS %+6.2f pp -> OOS %+6.2f pp '
                  '(n=%4d, z_shift %+5.2f, net %+.4f)  %s'
                  % (r.pattern_id, 'long' if r.direction > 0 else 'short',
                     r.stop_atr, r.target_atr, r.dev_pp_is, r.dev_pp_oos,
                     r.n_decided, r.z_shift, r.net_R_oos,
                     'sign held' if r.sign_held else 'REFUTED'))

    print('\n=== out-of-sample ===')
    if not oos:
        print('nothing was ever selected, so nothing was ever tested. '
              'That is a result: the discovery pass found no candidate that '
              'cleared its own bar on training data.')
        return
    allm = pd.concat(oos, ignore_index=True)
    held = int(allm['sign_held'].sum())
    tot = len(allm)
    pays = int((allm['net_R_oos'] > 0).sum())
    print('%d candidate-folds tested, %d kept their sign (%.0f%%), '
          '%d also positive net R out of sample.'
          % (tot, held, 100 * held / tot, pays))
    print('mean OOS deviation %+.2f pp (in sample %+.2f pp -- the gap is the '
          'selection premium)'
          % (allm['dev_pp_oos'].mean(), allm['dev_pp_is'].mean()))
    # Under a null the sign is a coin flip, so this is the headline test.
    from sim.stats import two_sided_p
    z = (held - tot / 2) / np.sqrt(tot / 4) if tot else np.nan
    print('sign agreement against a coin flip: z = %+.2f, p = %.3f'
          % (z, two_sided_p(z)))
    if held / tot < 0.6 or allm['dev_pp_oos'].mean() * \
            np.sign(allm['dev_pp_is'].mean()) <= 0:
        print('\nVERDICT: the candidates do not generalise. In-sample survival '
              'was selection, not signal.')
    else:
        print('\nVERDICT: signs largely hold. Still a small sample -- the next '
              'test is another instrument, not another fold.')

    out = args.out or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'runs',
        'walkforward_%s_%s_%s.csv' % (args.symbol.replace('.', ''), args.tf,
                                      proposer.name))
    allm.to_csv(out, index=False)
    print('wrote %s' % out)


if __name__ == '__main__':
    main()
