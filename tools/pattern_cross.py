#!/usr/bin/env python
"""
pattern_cross.py — does a motif appear in more than one market?

    python tools/pattern_cross.py --tf 1h

The walk-forward asked whether a pattern survives new TIME. This asks whether it
survives a new MARKET, which is a harder question and a cheaper one: a shape
that reflects something real about how order flow resolves should not care very
much which pair it is drawn on, while a shape that is an artifact of one
instrument's microstructure or one era's trend has no reason to transfer.

It is also the only honest way to buy power here. Adding instruments multiplies
observations without re-using the same bars, which is exactly what the
walk-forward could not do -- it kept slicing one series thinner.

LEAVE ONE INSTRUMENT OUT

For each instrument in turn: pool the OTHERS, select cells there, and test the
selection on the one held out. Selection never touches the held-out market, so
its result is genuinely out of sample even though every bar of it is in the past.

A cell must be present in at least `--min-symbols` of the training instruments
and agree in SIGN across all of them before it can be selected at all. That
requirement does most of the work: an artifact has no reason to point the same
way on gold and on yen.

POOLING

Deviations are combined weighted by decided-event count; the z values are
combined by Stouffer weighted by sqrt(n). Both assume the instruments are
independent, and they are not -- EURUSD, AUDUSD and USDJPY all contain the
dollar, so a dollar move shows up in all three. That inflates the pooled z, in
the same direction and for the same reason the binomial z was inflated before
the time-shift null. Treat the pooled z as an upper bound and the SIGN
AGREEMENT as the number that means something.
"""

import argparse
import itertools
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sim.instruments import available, load, spec
from sim.patterns.evaluate import evaluate
from sim.patterns.sax import SaxMotifs
from sim.stats import expected_max_z, two_sided_p

KEY = ['pattern_id', 'direction', 'stop_atr', 'target_atr']


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--symbols', default='EURUSD.a,USDJPY.a,XAUUSD.a,AUDUSD.a')
    ap.add_argument('--tf', default='1h')
    ap.add_argument('--start', default='2007-01-01')
    ap.add_argument('--end', default=None)
    ap.add_argument('--stops', default='0.5,1.0')
    ap.add_argument('--targets', default='1.0,2.0,3.0')
    ap.add_argument('--window', type=int, default=6)
    ap.add_argument('--min-count', type=int, default=150)
    ap.add_argument('--path-letters', type=int, default=4)
    ap.add_argument('--body-letters', type=int, default=3)
    ap.add_argument('--body-bars', type=int, default=2)
    ap.add_argument('--min-events', type=int, default=150)
    ap.add_argument('--n-shifts', type=int, default=200)
    ap.add_argument('--strata', type=int, default=10)
    ap.add_argument('--min-symbols', type=int, default=2,
                    help='training instruments a cell must appear in')
    ap.add_argument('--out', default=None)
    args = ap.parse_args()

    symbols = [s for s in args.symbols.split(',') if args.tf in available(s)]
    missing = [s for s in args.symbols.split(',') if s not in symbols]
    if missing:
        print('skipping (no %s history): %s\n' % (args.tf, ', '.join(missing)))
    if len(symbols) < 3:
        print('need at least 3 instruments to leave one out; have %d' % len(symbols))
        return

    geoms = list(itertools.product([float(x) for x in args.stops.split(',')],
                                   [float(x) for x in args.targets.split(',')]))
    per = {}
    for sym in symbols:
        bars = load(sym, args.tf, args.start, args.end)
        props = SaxMotifs(window=args.window, min_count=args.min_count,
                          path_letters=args.path_letters,
                          body_letters=args.body_letters,
                          body_bars=args.body_bars).run(bars, sym, args.tf)
        df = evaluate(bars, props, sym, args.tf, geoms,
                      min_events=args.min_events, n_strata=args.strata,
                      n_shifts=args.n_shifts, spec=spec(sym, args.tf))
        per[sym] = df
        print('%-10s %6d bars  %3d words  %4d cells scored (%d hypotheses)'
              % (sym, len(bars), props['pattern_id'].nunique(), len(df),
                 df.attrs['n_hypotheses']))

    # ---- how much do the instruments agree at all? ----
    allc = pd.concat([d.assign(symbol=s) for s, d in per.items()],
                     ignore_index=True)
    shared = allc.groupby(KEY).agg(
        n_sym=('symbol', 'nunique'),
        signs=('dev_pp', lambda x: len(set(np.sign(x)))),
        mean_dev=('dev_pp', 'mean')).reset_index()
    both = shared[shared.n_sym >= 2]
    print('\ncells present in >=2 instruments: %d of %d'
          % (len(both), len(shared)))
    if len(both):
        agree = int((both.signs == 1).sum())
        # Under a null each instrument's sign is a coin flip, so k instruments
        # agreeing happens by chance with probability 2^-(k-1).
        exp = float(sum(0.5 ** (r.n_sym - 1) for r in both.itertuples()))
        print('  agreeing in sign across all of them: %d (chance would give '
              '~%.0f)' % (agree, exp))

    # ---- leave one instrument out ----
    print('\n=== leave one instrument out ===')
    rows = []
    for held in symbols:
        train = [s for s in symbols if s != held]
        t = allc[allc.symbol.isin(train)]
        g = t.groupby(KEY).agg(
            n_sym=('symbol', 'nunique'),
            n_dec=('n_decided', 'sum'),
            dev=('dev_pp', lambda x: np.average(x)),
            signs=('dev_pp', lambda x: len(set(np.sign(x)))),
            net=('net_R', 'mean'),
            z_st=('z_shift', lambda x: np.nansum(x) / np.sqrt(np.isfinite(x).sum())
                  if np.isfinite(x).any() else np.nan)).reset_index()

        cand = g[(g.n_sym >= args.min_symbols) & (g.signs == 1) & (g.net > 0)]
        bar = expected_max_z(max(2, len(g)))
        cand = cand[cand.z_st.abs() > bar]
        print('%s held out: %d pooled cells, bar |z|=%.2f -> %d selected'
              % (held, len(g), bar, len(cand)))
        if not len(cand):
            continue
        h = per[held].merge(cand[KEY + ['dev', 'z_st', 'net']], on=KEY)
        if not h.empty:
            h['held'] = held
            h['sign_held'] = np.sign(h['dev_pp']) == np.sign(h['dev'])
            rows.append(h)
            for r in h.itertuples():
                print('    %-12s %-5s %.1f/%.1f  train %+6.2f pp (z %+5.2f) -> '
                      '%s %+6.2f pp (n=%4d, z %+5.2f, net %+.4f)  %s'
                      % (r.pattern_id, 'long' if r.direction > 0 else 'short',
                         r.stop_atr, r.target_atr, r.dev, r.z_st, held,
                         r.dev_pp, r.n_decided, r.z_shift, r.net_R,
                         'sign held' if r.sign_held else 'REFUTED'))
        else:
            print('    none of the selection occurs often enough in %s' % held)

    print('\n=== verdict ===')
    if not rows:
        print('no cell was ever selected on a pool of instruments and testable '
              'on the one held out.\nThat is itself the finding: nothing this '
              'proposer produces is consistent enough across markets to be\n'
              'worth carrying to a held-out one.')
        return
    m = pd.concat(rows, ignore_index=True)
    held_n, tot = int(m.sign_held.sum()), len(m)
    z = (held_n - tot / 2) / np.sqrt(tot / 4)
    print('%d cross-instrument tests, %d kept their sign (%.0f%%), '
          '%d positive net R.' % (tot, held_n, 100 * held_n / tot,
                                  int((m.net_R > 0).sum())))
    print('mean deviation: %+.2f pp on the training pool -> %+.2f pp on the '
          'held-out market' % (m['dev'].mean(), m['dev_pp'].mean()))
    print('sign agreement vs a coin flip: z = %+.2f, p = %.3f'
          % (z, two_sided_p(z)))

    out = args.out or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'runs',
        'cross_instrument_%s_sax.csv' % args.tf)
    m.to_csv(out, index=False)
    print('wrote %s' % out)


if __name__ == '__main__':
    main()
