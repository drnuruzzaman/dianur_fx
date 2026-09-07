#!/usr/bin/env python
"""
friction_map.py — which cells are even worth testing?

    python tools/friction_map.py

Cost attribution across the surviving candidates showed friction consuming
anywhere from 9% to 2224% of a strategy's raw signal, and the spread half of it
is not a property of the strategy at all -- it is spread divided by ATR, fixed
by the instrument and the timeframe before a single rule is written.

XAUUSD 4h pays 0.003 R of spread per trade. USDJPY 1h pays 0.042 R: fourteen
times more, for the same nominal broker spread, because an hour of yen is a much
smaller move than four hours of gold. No entry signal repairs that, and every
strategy family tried so far has produced raw signal in the 0.00-0.24 R range,
so a cell whose floor is 0.05 R needs to be near the top of that range merely to
break even.

This estimates the floor WITHOUT running anything, so it can be used to decide
where to look rather than to explain a disappointment afterwards.

    spread_R  ~ median(spread) / (stop_atr x median(ATR))
    slip_R    ~ 2 x slippage_atr / stop_atr        (entry and exit, ATR-relative)

Both are per trade, in units of the risk committed, directly comparable to the
avg_R the gates judge.
"""
import argparse
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sim.core import Config
from sim.instruments import DATA, load, spec
from sim.indicators import atr


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--stop-atr', type=float, default=2.0,
                    help='stop distance in ATR; the R denominator')
    ap.add_argument('--start', default='2016-01-01',
                    help='recent history only -- spreads have narrowed over 27 years')
    args = ap.parse_args()

    cfg = Config()
    rows = []
    root = os.path.join(DATA, 'bars')
    for sym in sorted(os.listdir(root)):
        for tf in ['1m', '5m', '15m', '1h', '4h', '1d']:
            if not os.path.isdir(os.path.join(root, sym, tf)):
                continue
            try:
                b = load(sym, tf, args.start, None)
                sp = spec(sym, tf)
            except Exception:                             # noqa: BLE001
                continue
            if len(b) < 500:
                continue
            # sim.indicators.atr returns an ndarray, not a Series: the private
            # per-strategy copies that did return Series were removed when the
            # project unified on one ATR, and this call site was missed because
            # tools/ is not covered by the test suite.
            a = np.nanmedian(np.asarray(atr(b, 14), dtype=float))
            # same floor the simulator applies: the recorded column is 0 for
            # every FX bar after 2020, so taking it at face value would report
            # a spread-free market (see sim/core.py _spread_price)
            recorded = float(b['spread'].median())
            live = float(sp.get('spread_points_now') or 0.0)
            spread_price = max(recorded, live) * sp['point']
            risk = args.stop_atr * float(a)
            if not risk:
                continue
            spread_r = spread_price / risk
            slip_r = 2 * cfg.slippage_atr / args.stop_atr
            rows.append({'symbol': sym, 'tf': tf, 'bars': len(b),
                         'atr': round(float(a), 5),
                         'spread_pts': round(max(recorded, live), 1),
                         'recorded_pts': round(recorded, 1),
                         'spread_R': round(spread_r, 4),
                         'slip_R': round(slip_r, 4),
                         'floor_R': round(spread_r + slip_r, 4)})

    df = pd.DataFrame(rows).sort_values('floor_R')
    print('Friction floor per trade, in R (stop = %.1f ATR, from %s)\n'
          % (args.stop_atr, args.start))
    print(df.to_string(index=False))

    print('\nBest signal any strategy family has produced so far, frictionless:')
    print('  XAUUSD 4h donchian +0.2412 | EURUSD 4h stretch_follow +0.1112')
    print('  USDJPY 1h ema_cross +0.0593 | EURUSD 4h donchian -0.0107')
    print('\nA cell needs raw signal above its floor before anything else matters.')
    cheap = df[df.floor_R <= 0.03]
    dear = df[df.floor_R >= 0.06]
    print('  worth testing (floor <= 0.030 R): %s'
          % (', '.join('%s %s' % (r.symbol, r.tf) for r in cheap.itertuples()) or 'none'))
    print('  near hopeless (floor >= 0.060 R): %s'
          % (', '.join('%s %s' % (r.symbol, r.tf) for r in dear.itertuples()) or 'none'))

    out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       'runs', 'friction_map.csv')
    df.to_csv(out, index=False)
    print('\nwrote %s' % out)


if __name__ == '__main__':
    main()
