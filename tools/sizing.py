#!/usr/bin/env python
"""
sizing.py — STEP 8. Same trades, different money. What changes and what cannot.

    python tools/sizing.py --symbol XAUUSD.a --tf 4h --start 2018-01-01

WHY THIS IS A DIFFERENT KIND OF EXPERIMENT. Every change tested so far -- exits,
a trend filter, breakout quality, the retest -- altered WHICH trades were taken,
and each one lost more by dropping winners than it gained by dropping losers.
The top decile of trades is 272% of the total return, so anything that touches
the trade list is fighting that arithmetic. Sizing does not touch it. It changes
what each trade is worth.

AVG R CANNOT MOVE, and if it does something is wrong. R is the outcome divided
by the risk taken, so scaling the risk scales both. A sizing comparison that
showed a better avg_R would be evidence of a bug -- most likely the trade list
having silently changed -- so avg_R is printed as a CONTROL COLUMN and should be
identical across variants except where the trade counts differ.

WHERE SIZING *CAN* CHANGE THE TRADE LIST, and it is one place: `size_lots`
returns 0 when the risk budget will not cover the broker's minimum lot, and the
engine skips those signals. A smaller account skips more. That is real -- it is
the same arithmetic that makes gold 4h untradeable at 0.5% on a small balance --
so the trade count is printed too, and a variant that took a different number of
trades is not directly comparable on terminal equity alone.

WHAT ACTUALLY DIFFERS: the equity path. Compounding raises terminal equity and
raises drawdown together; constant cash risk gives up the upside and flattens
the path; flat lots ignores the stop distance entirely and therefore takes wildly
uneven risk per trade, which is included precisely because it is what most people
actually do.
"""
import argparse
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sim.core import Config, Simulator
from sim.fx import FX
from sim.instruments import account_currency, load, spec
from sim.strategies import BASELINES


def curve_stats(res, start_equity):
    """Terminal equity, worst peak-to-trough, and whether it ever hit ruin."""
    eq = res.equity['equity'].to_numpy(float)
    if not len(eq):
        return {}
    peak = np.maximum.accumulate(eq)
    dd = (eq - peak) / np.where(peak > 0, peak, np.nan)
    return {
        'final': float(eq[-1]),
        'ret_pct': 100.0 * (eq[-1] / start_equity - 1.0),
        'maxdd_pct': 100.0 * float(np.nanmin(dd)),
        'min_equity': float(np.nanmin(eq)),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--symbol', default='XAUUSD.a')
    ap.add_argument('--tf', default='4h')
    ap.add_argument('--strategy', default='donchian')
    ap.add_argument('--start', default='2018-01-01')
    ap.add_argument('--end', default=None)
    ap.add_argument('--equity', type=float, default=25_000.0)
    args = ap.parse_args()

    bars = load(args.symbol, args.tf, args.start, args.end)
    sp = spec(args.symbol, args.tf)
    fx = FX.build(account_currency())

    variants = [
        ('0.5% of live equity', dict(size_base='equity', risk_pct=0.5)),
        ('1.0% of live equity', dict(size_base='equity', risk_pct=1.0)),
        ('2.0% of live equity', dict(size_base='equity', risk_pct=2.0)),
        ('0.5% of START equity', dict(size_base='start', risk_pct=0.5)),
        ('1.0% of START equity', dict(size_base='start', risk_pct=1.0)),
        ('flat 0.10 lots', dict(size_base='flat', flat_lots=0.10)),
    ]

    print('%s %s %s   %s..%s   start equity %s %s'
          % (args.symbol, args.tf, args.strategy, bars.index[0].date(),
             bars.index[-1].date(), args.equity, account_currency()))
    print('avg_R is a CONTROL: it must not move except where the trade count does.\n')
    print('  %-22s %7s %9s %11s %9s %9s'
          % ('sizing', 'trades', 'avg_R', 'final', 'return', 'max DD'))

    base_r = None
    for label, kw in variants:
        cfg = Config(start_equity=args.equity, apply_swap=False, **kw)
        res = Simulator(sp, fx=fx, config=cfg).run(
            bars, BASELINES[args.strategy](), args.symbol, args.tf)
        t = res.trades
        st = curve_stats(res, args.equity)
        r = float(t.r_multiple.mean()) if len(t) else float('nan')
        if base_r is None:
            base_r = r
        drift = '' if abs(r - base_r) < 5e-4 else '   <- avg_R MOVED'
        print('  %-22s %7d %+9.4f %11.0f %8.1f%% %8.1f%%%s'
              % (label, len(t), r, st.get('final', 0), st.get('ret_pct', 0),
                 st.get('maxdd_pct', 0), drift))

    print('\nRuin gate is %.0f%% of start equity; a run that trips it stops trading.'
          % Config().ruin_pct)


if __name__ == '__main__':
    main()
