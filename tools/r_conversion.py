#!/usr/bin/env python
"""
r_conversion.py — gate 2.2. Does the structural edge convert into money?

    python tools/r_conversion.py --symbol EURUSD.a --tf 1h --tol 0.10

The structural gate found that a confirmed EURUSD line holds 3.1 percentage
points more often than a parallel placebo (z = 3.09). That is a real
measurement and it is not yet an edge, because a hold rate is not expectancy.
Three things stand between them:

  GEOMETRY. An approach fires anywhere within near_atr of the line, so entry
  sits off the line by 0.20 ATR on average. A bounce with symmetric 1.0 ATR
  barriers therefore risks 1.20 to make 0.80 -- R:R 0.67, not 1:1. At a 55.2%
  hold rate that is NEGATIVE before a cent of cost.

  THE TRADE-OFF. Tightening the stop improves R:R but lowers the hit rate and
  inflates friction, which is a fixed price divided by a smaller denominator.
  Widening it does the reverse. Neither end is obviously right, so the grid is
  swept rather than argued about.

  FRICTION. Spread and slippage in R terms scale with 1/stop_distance, so the
  same broker costs a different number of R at every geometry:

      friction_R = spread_price / stop_price  +  2 * slippage_atr / stop_atr

Crucially the probability is RE-MEASURED at each geometry rather than assumed
constant -- a hold rate measured with symmetric barriers says nothing about a
1:3 bracket. The placebo arm is re-measured with the same asymmetry, so the
comparison stays honest even though the null is no longer 50/50.

The gate passes only if some geometry gives the line a positive expectancy
AFTER friction, AND beats its own placebo there. A positive number that the
placebo also achieves is geometry, not structure.

THE COMPARISON IS PAIRED. Every approach produces a line arm and a placebo arm
from the SAME bar, the same slope and the same ATR, differing only in where the
level sits. Treating those two as independent samples throws that away and
inflates the standard error, because the pair shares whatever the market was
doing that week. `paired_t` therefore tests the per-approach DIFFERENCE against
zero, over the approaches where both arms produced an event. Friction is a
function of the stop distance alone, so it is identical in both arms and
cancels from the difference exactly -- the paired test is the same test before
and after costs.

For the breakout and retest phases the pairing does real work: those rows exist
only for an arm whose OWN level broke, so the two arms fire on overlapping but
different subsets of approaches. Comparing their raw means compares different
events. `n_paired` is how many approaches broke on both sides, and it is the
only population on which the two are comparable.

MULTIPLICITY. The grid is 25 geometries and the gate reads off the survivors,
so an uncorrected p-value here means very little. Benjamini-Hochberg is applied
across the grid and `survives_bh` is the column to read, exactly as
tl_placebo_summary.csv does for the structural gate.
"""
import argparse
import itertools
import math
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sim.instruments import load, spec
from sim.tl.diagnostics import CLOSE, INTRABAR, RESOLUTIONS, DiagParams, run
from sim.tl.engine import Params


def paired_diff(line, plac, rr):
    """
    Per-approach R difference between the arms, and a t-test of it against zero.

    Returns (n_paired, mean_diff, t, p). R is +rr on a hold and -1 otherwise,
    which is the same convention `expectancy` uses, so the paired mean and the
    difference of the two aggregate expectancies agree on the paired subset.

    The p-value is the normal two-sided tail. With n in the hundreds to tens of
    thousands the t and normal tails agree to well past the third decimal, and
    scipy is not a dependency of this project.
    """
    key = ['approach_bar', 'line_id']
    l = line.drop_duplicates(key).set_index(key)['outcome']
    p = plac.drop_duplicates(key).set_index(key)['outcome']
    both = l.index.intersection(p.index)
    n = len(both)
    if n < 3:
        return n, np.nan, np.nan, np.nan
    r_l = np.where(l.loc[both].to_numpy() == 'hold', rr, -1.0)
    r_p = np.where(p.loc[both].to_numpy() == 'hold', rr, -1.0)
    d = r_l - r_p
    sd = d.std(ddof=1)
    if not sd:
        return n, float(d.mean()), np.nan, np.nan
    t = float(d.mean()) / (sd / math.sqrt(n))
    return n, float(d.mean()), t, math.erfc(abs(t) / math.sqrt(2))


def benjamini_hochberg(pvals, alpha=0.05):
    """
    Which of these p-values survive BH at `alpha`.

    A NaN is a test that could not be RUN -- a geometry where the two arms had
    fewer than three approaches in common -- not a test that was run and failed.
    It is therefore dropped from the family rather than counted in it: correcting
    for experiments you never performed would be a different and stricter
    correction than the one being claimed. NaNs never survive.
    """
    p = np.asarray(pvals, dtype=float)
    ok = np.isfinite(p)
    out = np.zeros(len(p), dtype=bool)
    idx = np.flatnonzero(ok)
    if not len(idx):
        return out
    order = idx[np.argsort(p[idx])]
    m = len(order)
    crit = alpha * (np.arange(1, m + 1) / m)
    passed = p[order] <= crit
    if passed.any():
        out[order[:np.flatnonzero(passed)[-1] + 1]] = True
    return out


def expectancy(hold_rate, stop_atr, target_atr, dist_atr):
    """
    Expected R for one approach, before costs.

    Entry sits `dist` ATR off the line on the near side, so the barriers are
    not equidistant from it: the stop is (stop_atr + dist) away and the target
    (target_atr - dist). R is defined against the stop, as everywhere else.
    """
    risk = stop_atr + dist_atr
    reward = target_atr - dist_atr
    if risk <= 0 or reward <= 0:
        return np.nan, np.nan
    rr = reward / risk
    return hold_rate * rr - (1 - hold_rate), rr


def friction_r(sp, atr_price, stop_atr, dist_atr, slippage_atr=0.02):
    """Spread and slippage per trade, in R, at this stop distance."""
    risk_price = (stop_atr + dist_atr) * atr_price
    if risk_price <= 0:
        return np.nan
    spread_price = float(sp.get('spread_points_now') or 0) * sp['point']
    return spread_price / risk_price + 2 * slippage_atr / (stop_atr + dist_atr)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--symbol', default='EURUSD.a')
    ap.add_argument('--tf', default='1h')
    ap.add_argument('--tol', type=float, default=0.10)
    ap.add_argument('--start', default='2018-01-01')
    ap.add_argument('--end', default=None)
    ap.add_argument('--stops', default='0.4,0.6,0.8,1.0,1.5')
    ap.add_argument('--targets', default='0.8,1.2,1.6,2.0,3.0')
    ap.add_argument('--resolution', default=INTRABAR, choices=list(RESOLUTIONS),
                    help='how a bar that reached BOTH barriers is settled. '
                         'The default goes and looks at sub-bars; "close" '
                         'reproduces the original close-only measurement, '
                         'which prices no bracket order that exists.')
    ap.add_argument('--phase', default='approach',
                    choices=['approach', 'breakout', 'retest'],
                    help='approach = strategy A (bounce, enters AT the line); '
                         'breakout = B (enters where the break barrier was '
                         'crossed); retest = C (waits for price to come back '
                         'to the broken line first)')
    args = ap.parse_args()

    bars = load(args.symbol, args.tf, args.start, args.end)
    sp = spec(args.symbol, args.tf)
    params = Params(tol_atr=args.tol)
    stops = [float(x) for x in args.stops.split(',')]
    targets = [float(x) for x in args.targets.split(',')]

    print('%s %s  tol_atr=%.2f  phase=%s  %s..%s  %d bars'
          % (args.symbol, args.tf, args.tol, args.phase, bars.index[0].date(),
             bars.index[-1].date(), len(bars)))
    print('resolution=%s  sweeping %d geometries; probability re-measured at '
          'each\n' % (args.resolution, len(stops) * len(targets)))

    rows = []
    for stop_atr, target_atr in itertools.product(stops, targets):
        ev, _ = run(bars, args.tf, params,
                    DiagParams(stop_atr=stop_atr, target_atr=target_atr,
                               resolution=args.resolution,
                               phases=(args.phase,) if args.phase == 'approach'
                               else ('approach', args.phase)),
                    symbol=args.symbol)
        amb, by = ev.attrs.get('ambiguous_bars', 0), ev.attrs.get('resolved_by', {})
        ev = ev[ev.phase == args.phase]
        if not len(ev):
            continue
        line = ev[ev.arm == 'line']
        plac = ev[ev.arm == 'placebo']
        if not len(line) or not len(plac):
            continue
        # approach enters off the line; breakout and retest enter at the
        # price that triggered them, so their offset is zero by construction
        dist = float(line.dist_atr.mean()) if args.phase == 'approach' else 0.0
        atr_price = float(line.atr.mean())
        if target_atr - dist <= 0:
            continue                        # target inside the entry offset
        fr = friction_r(sp, atr_price, stop_atr, dist)
        p_line = float((line.outcome == 'hold').mean())
        p_plac = float((plac.outcome == 'hold').mean())
        e_line, rr = expectancy(p_line, stop_atr, target_atr, dist)
        e_plac, _ = expectancy(p_plac, stop_atr, target_atr, dist)
        n = len(line)
        # standard error of the expectancy, from the binomial on the hit rate.
        # This one is UNPAIRED and belongs to net_R -- an absolute claim, with
        # no second arm to difference against.
        se = np.sqrt(p_line * (1 - p_line) / n) * (rr + 1)
        n_pair, d_mean, d_t, d_p = paired_diff(line, plac, rr)
        rows.append({
            'stop_atr': stop_atr, 'target_atr': target_atr, 'rr': round(rr, 2),
            'n': n, 'hold_line': round(100 * p_line, 1),
            'hold_placebo': round(100 * p_plac, 1),
            'gross_R': round(e_line, 4), 'placebo_R': round(e_plac, 4),
            'friction_R': round(fr, 4),
            'net_R': round(e_line - fr, 4),
            'net_vs_placebo': round(e_line - e_plac, 4),
            't': round((e_line - fr) / se, 2) if se else np.nan,
            'n_paired': n_pair,
            'paired_R': round(d_mean, 4) if np.isfinite(d_mean) else np.nan,
            'paired_t': round(d_t, 2) if np.isfinite(d_t) else np.nan,
            'paired_p': d_p,
            'ambiguous': amb,
            'intrabar_fallback': by.get('fallback', 0),
        })
        print('  stop %.1f target %.1f  R:R %.2f  hold %.1f%% (plac %.1f%%)  '
              'gross %+.4f  friction %.4f  NET %+.4f  paired %+.4f (t %.2f, n %d)'
              % (stop_atr, target_atr, rr, 100 * p_line, 100 * p_plac,
                 e_line, fr, e_line - fr, d_mean, d_t, n_pair), flush=True)

    df = pd.DataFrame(rows)
    # BH across the whole grid: the gate reads off survivors, so the p-value
    # that matters is the corrected one.
    df['survives_bh'] = benjamini_hochberg(df['paired_p'].to_numpy())
    out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       'runs', 'r_conversion_%s_%s_%s_tol%.2f_%s.csv'
                       % (args.symbol.replace('.', ''), args.tf, args.phase,
                          args.tol, args.resolution))
    df.to_csv(out, index=False)

    print('\n=== GATE 2.2: economic edge ===')
    good = df[(df.net_R > 0) & (df.paired_R > 0) & df.survives_bh]
    print('geometries with positive net R, beating placebo on a PAIRED test, '
          'surviving BH: %d of %d' % (len(good), len(df)))

    # The SHAPE of the surface is the evidence, not its maximum. A real effect
    # occupies a contiguous region; a lucky coordinate is a spike surrounded by
    # nothing, and reading off the best cell cannot tell the two apart.
    print('\npaired R (line - placebo) by geometry, * = survives BH:')
    grid = df.pivot(index='stop_atr', columns='target_atr', values='paired_R')
    flags = df.pivot(index='stop_atr', columns='target_atr', values='survives_bh')
    print('  stop \\ target ' + ' '.join('%8s' % t for t in grid.columns))
    for st in grid.index:
        cells = ['%+8.3f%s' % (grid.loc[st, t], '*' if flags.loc[st, t] else ' ')
                 for t in grid.columns]
        print('  %-13.1f ' % st + ' '.join(cells))

    if len(good):
        cols = ['stop_atr', 'target_atr', 'rr', 'n', 'n_paired', 'net_R',
                'paired_R', 'paired_t', 'paired_p']
        print('\n' + good.sort_values('paired_R', ascending=False)
              .head(8)[cols].to_string(index=False))
        print('\nPASS -- and still IN-SAMPLE. The surviving geometries are')
        print('hypotheses to register and walk forward, not results. Read the')
        print('grid above: a contiguous block of survivors is an effect, a lone')
        print('starred cell among blanks is a coordinate.')
    else:
        best = df.loc[df.paired_R.idxmax()] if df.paired_R.notna().any() else None
        print('\nFAIL -- no geometry beats its own placebo on a corrected '
              'paired test.')
        if best is not None:
            print('  best paired was stop %.1f / target %.1f: paired R %+.4f '
                  '(t %.2f, p %.3g, n %d), net R %+.4f'
                  % (best.stop_atr, best.target_atr, best.paired_R,
                     best.paired_t, best.paired_p, best.n_paired, best.net_R))

    amb = int(df.ambiguous.max()) if len(df) else 0
    fb = int(df.intrabar_fallback.max()) if len(df) else 0
    print('\nresolution=%s; worst-case %d bars reached both barriers, %d of '
          'those fell back to the stop.' % (args.resolution, amb, fb))
    if args.resolution == CLOSE:
        print('WARNING: close-only resolution cannot see a barrier that was '
              'touched and given back, so it understates stops. Any net_R here '
              'is optimistic.')
    print('\nwrote %s' % out)


if __name__ == '__main__':
    main()
