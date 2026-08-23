#!/usr/bin/env python
"""
cost_report.py — what is actually eating the edge, per trade, in R.

    python tools/cost_report.py --symbol EURUSD.a --tf 4h --strategy stretch_follow

Twenty-six cells across six strategy families have now landed inside a band of
roughly +-0.13 R per trade, while the walk-forward fit put the cost drag at
about -0.094 R. That says the binding constraint is not signal quality but
friction -- and friction is arithmetic rather than luck, so it is worth knowing
exactly which part of it does the damage before trying to fix any of it.

The simulator already records spread, slippage and swap per trade separately;
they were simply never reported. Note that gross_ccy is computed from fill
prices that ALREADY contain spread and slippage, so those two fields are
attribution rather than a second deduction: a frictionless comparison has to add
them back, not subtract them again.

R is the risk actually committed on that trade (risk_price x units), recovered
from net_ccy / r_multiple, so every figure below is directly comparable to the
avg_R the gates are judged on.
"""
import argparse
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sim.core import Config, Simulator
from sim.fx import FX
from sim.instruments import account_currency, load, spec
from sim.strategies import BASELINES


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--symbol', default='EURUSD.a')
    ap.add_argument('--tf', default='4h')
    ap.add_argument('--strategy', default='stretch_follow', choices=sorted(BASELINES))
    ap.add_argument('--start', default='1999-01-01')
    ap.add_argument('--end', default=None)
    ap.add_argument('--swap', action='store_true', help='include carry (gates run without)')
    args = ap.parse_args()

    cfg = Config(risk_pct=0.5, apply_swap=args.swap)
    fx = FX.build(account_currency())
    bars = load(args.symbol, args.tf, args.start, args.end)
    res = Simulator(spec(args.symbol, args.tf), fx=fx, config=cfg).run(
        bars, BASELINES[args.strategy](), args.symbol, args.tf)
    t = res.trades
    if not len(t):
        raise SystemExit('no trades')

    # R denominator: risk_price * units, recovered from the reported multiple
    ok = t.r_multiple != 0
    denom = pd.Series(np.nan, index=t.index)
    denom[ok] = t.net_ccy[ok] / t.r_multiple[ok]
    t = t[denom.notna() & (denom > 0)].copy()
    denom = denom[denom.notna() & (denom > 0)]

    spread_r = (t.spread_ccy / denom)
    slip_r = (t.slippage_ccy / denom)
    swap_r = (t.swap_ccy / denom)
    net_r = t.r_multiple
    # costs are already inside the fills, so add them back to undo them
    gross_r = net_r - spread_r - slip_r - swap_r

    print('%s %s %s   %s .. %s   %d trades'
          % (args.symbol, args.tf, args.strategy, bars.index[0].date(),
             bars.index[-1].date(), len(t)))
    print('\n--- per trade, in R ---')
    rows = [('signal, frictionless', gross_r.mean()),
            ('  spread', spread_r.mean()),
            ('  slippage', slip_r.mean()),
            ('  swap', swap_r.mean()),
            ('NET (what the gates see)', net_r.mean())]
    for label, v in rows:
        print('  %-26s %+8.4f' % (label, v))
    total_cost = spread_r.mean() + slip_r.mean() + swap_r.mean()
    print('  %-26s %+8.4f' % ('total friction', total_cost))
    if gross_r.mean() != 0:
        print('  friction as %% of raw signal   %.0f%%'
              % (100 * abs(total_cost) / abs(gross_r.mean())))

    print('\n--- by exit reason ---')
    g = t.assign(_g=gross_r, _s=spread_r, _l=slip_r, _n=net_r).groupby('exit_reason')
    summary = g.agg(trades=('_n', 'size'), gross_R=('_g', 'mean'),
                    spread_R=('_s', 'mean'), slip_R=('_l', 'mean'),
                    net_R=('_n', 'mean'), bars=('bars_held', 'median'))
    print(summary.round(4).to_string())

    print('\n--- what would fix it ---')
    for label, keep in [('friction unchanged', 1.0), ('friction halved', 0.5),
                        ('friction quartered', 0.25), ('frictionless', 0.0)]:
        v = gross_r.mean() + keep * total_cost
        print('  %-22s net %+8.4f R   %s' % (label, v, 'profitable' if v > 0 else ''))
    # the other lever: the same edge measured against a bigger denominator
    print('\n  Widening the stop scales BOTH signal and friction in R, so it only')
    print('  helps where friction is a fixed price paid per trade (spread) rather')
    print('  than a fraction of the move. Spread is %.0f%% of friction here.'
          % (100 * abs(spread_r.mean()) / abs(total_cost) if total_cost else 0))


if __name__ == '__main__':
    main()
