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
"""
import argparse
import itertools
import os
import sys

import numpy as np
import pandas as pd
from scipy import stats

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sim.instruments import load, spec
from sim.tl.diagnostics import DiagParams, run
from sim.tl.engine import Params


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


#: How an outcome scores, in R. HOLD is the target; BREAK is the stop; CHOP is
#: scored as a FULL LOSS, not as zero. That is deliberate and load-bearing: a
#: trade that reached neither barrier inside the horizon has no exit rule here,
#: and `expectancy` above already computes its hold rate over ALL events rather
#: than over decided ones. Scoring chop nearer breakeven -- which a time-based
#: exit would justify -- lifts every number in the grid, so the two functions
#: must agree on the convention or the CSV contradicts itself.
PAIR_KEY = ['approach_bar', 'line_id']


def _r_per_event(outcome, rr):
    return np.where(np.asarray(outcome) == 'hold', rr, -1.0)


def paired_diff(line, placebo, rr):
    """
    (n_pairs, mean difference in R, t, p) for line MINUS placebo.

    PAIRED, not two-sample, and the difference is not cosmetic. Both arms see
    the same approaches at the same bars in the same market conditions, so the
    variance that dominates a two-sample comparison -- how trendy the sample
    happened to be -- cancels within each pair. Comparing the two means
    independently throws that away and needs a far larger sample to see the
    same effect.

    Rows that do not pair on (approach_bar, line_id) are DROPPED rather than
    filled. An unmatched row is an approach one arm had and the other did not,
    and there is no difference to take.

    `t` is NaN when the differences have no variance -- every pair identical.
    That is not a significant result, it is the absence of a test to run, and
    returning a huge t there would be the single easiest way to manufacture a
    finding.
    """
    a = line[PAIR_KEY + ['outcome']].drop_duplicates(PAIR_KEY)
    b = placebo[PAIR_KEY + ['outcome']].drop_duplicates(PAIR_KEY)
    m = a.merge(b, on=PAIR_KEY, suffixes=('_line', '_plac'))
    n = len(m)
    if not n:
        return 0, np.nan, np.nan, np.nan
    d = _r_per_event(m.outcome_line, rr) - _r_per_event(m.outcome_plac, rr)
    mean = float(d.mean())
    if n < 2:
        return n, mean, np.nan, np.nan
    sd = float(d.std(ddof=1))
    if not np.isfinite(sd) or sd == 0:
        return n, mean, np.nan, np.nan
    t = mean / (sd / np.sqrt(n))
    p = 2 * stats.t.sf(abs(t), df=n - 1)
    return n, mean, float(t), float(p)


def benjamini_hochberg(pvalues, alpha=0.05):
    """
    Which of a family of tests survive a false-discovery-rate correction.

    Sweeping 25 geometries and reporting the best one is how a grid search
    manufactures significance: at alpha 0.05, better than one in the family is
    expected to clear it on noise alone. BH controls the expected PROPORTION of
    rejections that are false, which is the right question for a screen -- and
    it is less brutal than Bonferroni, which would make a real effect
    undetectable at this sample size.

    IT STEPS UP. The largest k whose p-value clears k/m * alpha carries every
    smaller p-value with it, even ones that failed their own threshold. That is
    the procedure, not a bug: given that the k-th is a discovery, the ones
    below it are too.

    A NaN is a test that could not be RUN -- zero variance, no pairs -- not one
    that failed. It leaves the family entirely, so m shrinks and the remaining
    tests are corrected against the number actually performed. Counting it
    would penalise the others for a test nobody did.
    """
    p = np.asarray(pvalues, dtype=float)
    out = np.zeros(p.shape, dtype=bool)
    real = np.flatnonzero(np.isfinite(p))
    m = len(real)
    if not m:
        return out
    order = real[np.argsort(p[real], kind='stable')]
    thresholds = (np.arange(1, m + 1) / m) * alpha
    passed = np.flatnonzero(p[order] <= thresholds)
    if not len(passed):
        return out
    out[order[:passed[-1] + 1]] = True
    return out


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
    print('sweeping %d geometries; probability re-measured at each\n'
          % (len(stops) * len(targets)))

    rows = []
    for stop_atr, target_atr in itertools.product(stops, targets):
        ev, _ = run(bars, args.tf, params,
                    DiagParams(stop_atr=stop_atr, target_atr=target_atr,
                               phases=(args.phase,) if args.phase == 'approach'
                               else ('approach', args.phase)))
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
        # standard error of the expectancy, from the binomial on the hit rate
        se = np.sqrt(p_line * (1 - p_line) / n) * (rr + 1)
        rows.append({
            'stop_atr': stop_atr, 'target_atr': target_atr, 'rr': round(rr, 2),
            'n': n, 'hold_line': round(100 * p_line, 1),
            'hold_placebo': round(100 * p_plac, 1),
            'gross_R': round(e_line, 4), 'placebo_R': round(e_plac, 4),
            'friction_R': round(fr, 4),
            'net_R': round(e_line - fr, 4),
            'net_vs_placebo': round(e_line - e_plac, 4),
            't': round((e_line - fr) / se, 2) if se else np.nan,
        })
        print('  stop %.1f target %.1f  R:R %.2f  hold %.1f%% (plac %.1f%%)  '
              'gross %+.4f  friction %.4f  NET %+.4f'
              % (stop_atr, target_atr, rr, 100 * p_line, 100 * p_plac,
                 e_line, fr, e_line - fr), flush=True)

    df = pd.DataFrame(rows)
    out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       'runs', 'r_conversion_%s_%s_%s_tol%.2f.csv'
                       % (args.symbol.replace('.', ''), args.tf, args.phase,
                          args.tol))
    df.to_csv(out, index=False)

    print('\n=== GATE 2.2: economic edge ===')
    good = df[(df.net_R > 0) & (df.net_vs_placebo > 0)]
    print('geometries with positive net R AND beating placebo: %d of %d'
          % (len(good), len(df)))
    if len(good):
        print(good.sort_values('net_R', ascending=False)
              .head(8).to_string(index=False))
        print('\nPASS -- but note these are IN-SAMPLE and the geometry was chosen')
        print('by looking. The surviving geometries are hypotheses to register,')
        print('not results. Best net R is %+.4f against a friction floor of %.4f.'
              % (good.net_R.max(), good.loc[good.net_R.idxmax(), 'friction_R']))
    else:
        best = df.loc[df.net_R.idxmax()] if len(df) else None
        print('FAIL -- no geometry converts the structural edge into money.')
        if best is not None:
            print('  best was stop %.1f / target %.1f: gross %+.4f, friction %.4f,'
                  ' net %+.4f' % (best.stop_atr, best.target_atr, best.gross_R,
                                  best.friction_R, best.net_R))
            print('  placebo there: %+.4f' % best.placebo_R)
    print('\nwrote %s' % out)


if __name__ == '__main__':
    main()
