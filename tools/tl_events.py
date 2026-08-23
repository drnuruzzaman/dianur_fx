#!/usr/bin/env python
"""
tl_events.py — build the event store and report the funnel.

    python tools/tl_events.py --symbols XAUUSD.a --tfs 1h,4h --tols 0.32,0.10
    python tools/tl_events.py --fixtures          # no data/ needed

This is step 3-5 of the pipeline and it is deliberately boring: it converts
detected lines into typed FACTS and counts them. No strategy, no side, no stop,
no money.

The funnel is the report that saves the most time, because it kills strategies
before they are written. Strategy C needs BREAK -> RETEST -> REJECTION; if a
year of 1h gold gives 1000 breaks, 380 retests and 40 rejected retests, then C
has a 40-event sample in that cell and no backtest of it can mean anything. The
honest response is to widen the cell or drop the hypothesis, and it costs one
minute to find out here instead of a week of tuning.

The sample floor is applied per instrument x timeframe x tolerance x event, not
pooled. Pooling is how a strategy with no sample anywhere acquires one.
"""

import argparse
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sim.tl.engine import Params
from sim.tl.events import (EventParams, SAMPLE_FLOOR, build, cells, funnel)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIX = os.path.join(ROOT, 'tests', 'fixtures')
FIXTURES = [('bars_XAUUSDa_1h.csv', 'XAUUSD.a', '1h'),
            ('bars_USDJPYa_15m.csv', 'USDJPY.a', '15m')]


def load_fixture(name):
    return pd.read_csv(os.path.join(FIX, name), parse_dates=['t']).set_index('t')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--symbols', default='XAUUSD.a,USDJPY.a')
    ap.add_argument('--tfs', default='15m,1h,4h')
    ap.add_argument('--tols', default='0.32,0.10',
                    help='engine tolerances to build at. Both REGISTERED '
                         'tolerances are built by default and kept as separate '
                         'cells, never merged: they are two different '
                         'detectors and a result at one says nothing about the '
                         'other.')
    ap.add_argument('--start', default='2021-01-01')
    ap.add_argument('--end', default=None)
    ap.add_argument('--min-swing-atr', type=float, default=None)
    ap.add_argument('--retest-atr', type=float, default=0.4)
    ap.add_argument('--retest-bars', type=int, default=24)
    ap.add_argument('--floor', type=int, default=SAMPLE_FLOOR)
    ap.add_argument('--fixtures', action='store_true',
                    help='run on the committed test fixtures instead of data/')
    ap.add_argument('--out', default=None)
    args = ap.parse_args()

    ep = EventParams(retest_atr=args.retest_atr, retest_bars=args.retest_bars)
    tols = [float(x) for x in args.tols.split(',')]

    if args.fixtures:
        series = [(sym, tf, load_fixture(name)) for name, sym, tf in FIXTURES]
    else:
        from sim.instruments import load
        series = []
        for sym in args.symbols.split(','):
            for tf in args.tfs.split(','):
                bars = load(sym, tf, args.start, args.end)
                if len(bars) < 300:
                    print('  %-10s %-4s only %d bars — skipped'
                          % (sym, tf, len(bars)))
                    continue
                series.append((sym, tf, bars))

    stores = []
    for sym, tf, bars in series:
        for tol in tols:
            kw = {'tol_atr': tol}
            if args.min_swing_atr is not None:
                kw['min_swing_atr'] = args.min_swing_atr
            ev = build(bars, tf, sym, Params(**kw), ep)
            stores.append(ev)
            print('  %-10s %-4s tol=%.2f  %d bars %s..%s -> %d events'
                  % (sym, tf, tol, len(bars), bars.index[0].date(),
                     bars.index[-1].date(), len(ev)), flush=True)

    if not stores:
        sys.exit('no series produced events')
    ev = pd.concat(stores, ignore_index=True)

    print('\n=== FUNNEL (facts only; no strategy has been applied) ===')
    print(funnel(ev).to_string(index=False))

    c = cells(ev, args.floor)
    print('\n=== CELL SIZES vs the sample floor of %d ===' % args.floor)
    print(c.to_string(index=False))
    testable = c[c.testable]
    print('\ncells at or above the floor: %d of %d' % (len(testable), len(c)))
    if len(c) and not len(testable):
        print('NOTHING is testable at this floor. Widen the span or the cell '
              'before writing a strategy, do not lower the floor.')

    out = args.out or os.path.join(ROOT, 'runs', 'events', 'events.csv')
    os.makedirs(os.path.dirname(out), exist_ok=True)
    ev.to_csv(out, index=False)
    funnel(ev).to_csv(out.replace('.csv', '_funnel.csv'), index=False)
    c.to_csv(out.replace('.csv', '_cells.csv'), index=False)
    print('\nwrote %s (%d events) + _funnel.csv + _cells.csv' % (out, len(ev)))


if __name__ == '__main__':
    main()
