#!/usr/bin/env python
"""
intrabar_validate.py — is a lower timeframe good enough to say what happened
inside a bar, and how would you know?

    python tools/intrabar_validate.py --symbol EURUSD.a --tf 1h --days 30

When one bar contains both the stop and the target, an OHLC bar cannot say which
came first. The engine currently assumes the stop, which is safe but wrong some
of the time. The proposal is to look at a lower timeframe instead -- but a lower
timeframe is still bars, so it is still a guess, just a smaller one. Before
trusting it anywhere, it has to be checked against something that is not a
guess.

Ticks are that something. This builds ambiguous cases from real bars, asks 5m,
1m and the pessimistic rule what happened, and scores all three against the tick
record. What comes out is the number that decides whether the feature is worth
having: how often each rule is RIGHT.

Method
------
For each HTF bar, a long is placed at the bar's open with a stop between the
open and the low and a target between the open and the high, so the bar's own
high/low guarantee BOTH were touched. That is the ambiguous case by
construction, and it isolates the resolver from any strategy's opinion.

Bars from MetaTrader are bid-priced, and a long's stop and target both trigger
on the bid, so bid ticks are the like-for-like ground truth. Shorts trigger on
the ask and would need the spread added; they are left out rather than fudged.
"""
import argparse
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sim.instruments import load
from tools.dataset import load_ticks

STOP, TARGET, UNRESOLVED = 'stop', 'target', 'unresolved'


def verdict_from_bars(sub, stop, target):
    """
    Walk sub-bars in order and return whichever level a bar reaches first.

    A sub-bar holding BOTH is still ambiguous -- the recursion does not stop
    just because the bars got smaller -- and says so rather than guessing, so
    the caller can count how often the finer data still fails to answer.
    """
    for _, b in sub.iterrows():
        hit_stop = b.low <= stop
        hit_target = b.high >= target
        if hit_stop and hit_target:
            return UNRESOLVED
        if hit_stop:
            return STOP
        if hit_target:
            return TARGET
    return UNRESOLVED


def verdict_from_ticks(ticks, stop, target):
    """Ground truth: the first bid to cross either level."""
    bid = ticks.bid.to_numpy()
    s = np.flatnonzero(bid <= stop)
    t = np.flatnonzero(bid >= target)
    if not len(s) and not len(t):
        return UNRESOLVED
    if not len(t):
        return STOP
    if not len(s):
        return TARGET
    return STOP if s[0] < t[0] else TARGET


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--symbol', default='EURUSD.a')
    ap.add_argument('--tf', default='1h', help='the timeframe being resolved')
    ap.add_argument('--subs', default='5m,1m', help='candidate resolvers, finest last')
    ap.add_argument('--start', default='2025-01-02')
    ap.add_argument('--days', type=int, default=30)
    ap.add_argument('--every', type=int, default=13,
                    help='sample every Nth day so the window is spread, not clustered')
    args = ap.parse_args()

    tf_delta = pd.Timedelta(args.tf.replace('m', 'min'))
    subs = args.subs.split(',')

    span_end = pd.Timestamp(args.start) + pd.Timedelta(days=args.days * args.every)
    htf = load(args.symbol, args.tf, args.start, str(span_end.date()))
    sub_bars = {s: load(args.symbol, s, args.start, str(span_end.date())) for s in subs}

    days = pd.date_range(args.start, span_end, freq='%dD' % args.every)[:args.days]
    print('%s: resolving %s bars with %s, scored against ticks'
          % (args.symbol, args.tf, ' and '.join(subs)))
    print('%d sample days from %s\n' % (len(days), args.start))

    rows = []
    for day in days:
        got = list(load_ticks(args.symbol, day.date(), day.date()))
        if not got:
            continue
        ticks = got[0][1]
        if ticks.empty:
            continue
        for t0, bar in htf.loc[str(day.date()):str(day.date())].iterrows():
            t1 = t0 + tf_delta
            # both levels strictly inside the bar's range -> both were touched
            stop = bar.low + 0.25 * (bar.open - bar.low)
            target = bar.open + 0.75 * (bar.high - bar.open)
            if not (bar.low < stop < bar.open < target < bar.high):
                continue                      # degenerate bar, nothing to resolve
            tk = ticks.loc[t0:t1 - pd.Timedelta('1ms')]
            if len(tk) < 20:
                continue
            truth = verdict_from_ticks(tk, stop, target)
            if truth == UNRESOLVED:
                continue                      # ticks disagree with the bar; skip
            row = {'when': t0, 'truth': truth, 'ticks': len(tk)}
            for s in subs:
                sb = sub_bars[s].loc[t0:t1 - pd.Timedelta('1ms')]
                row[s] = verdict_from_bars(sb, stop, target)
                row[s + '_n'] = len(sb)
            rows.append(row)

    if not rows:
        raise SystemExit('no usable cases -- check the tick window covers --start')
    df = pd.DataFrame(rows)
    out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       'runs', 'intrabar_%s_%s.csv' % (args.symbol.replace('.', ''), args.tf))
    df.to_csv(out, index=False)

    n = len(df)
    print('=== %d ambiguous %s bars, ground truth from ticks ===' % (n, args.tf))
    print('  truth: stop first %d (%.1f%%) | target first %d (%.1f%%)\n'
          % ((df.truth == STOP).sum(), 100 * (df.truth == STOP).mean(),
             (df.truth == TARGET).sum(), 100 * (df.truth == TARGET).mean()))

    print('%-14s %10s %10s %10s' % ('rule', 'resolved', 'correct', 'wrong'))
    pess = (df.truth == STOP).mean()
    print('%-14s %9.1f%% %9.1f%% %9.1f%%'
          % ('pessimistic', 100.0, 100 * pess, 100 * (1 - pess)))
    for s in subs:
        got = df[s] != UNRESOLVED
        agree = (df[s] == df.truth) & got
        print('%-14s %9.1f%% %9.1f%% %9.1f%%   (%.1f sub-bars/bar)'
              % (s, 100 * got.mean(), 100 * agree.mean(),
                 100 * (got & ~agree).mean(), df[s + '_n'].mean()))

    print('\nOf the cases a resolver could not settle, pessimistic is the fallback:')
    for s in subs:
        stuck = df[df[s] == UNRESOLVED]
        if len(stuck):
            print('  %-4s unresolved %d (%.1f%%) -- of those, truth was stop %.0f%%'
                  % (s, len(stuck), 100 * len(stuck) / n,
                     100 * (stuck.truth == STOP).mean()))
        else:
            print('  %-4s unresolved 0' % s)

    if len(subs) > 1:
        a, b = subs[0], subs[-1]
        both = (df[a] != UNRESOLVED) & (df[b] != UNRESOLVED)
        if both.any():
            print('\n%s vs %s where both resolved: agree %.1f%% of %d cases'
                  % (a, b, 100 * (df.loc[both, a] == df.loc[both, b]).mean(), both.sum()))
    print('\nwrote %s' % out)


if __name__ == '__main__':
    main()
