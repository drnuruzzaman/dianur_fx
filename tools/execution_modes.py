#!/usr/bin/env python
"""
execution_modes.py — the same strategy under all three exit assumptions.

    python tools/execution_modes.py --symbol XAUUSD.a --tf 1h --strategy tl_breakout

When a bar holds both the stop and the target, OHLC cannot say which came
first. Pessimistic assumes the stop, optimistic assumes the target, and intrabar
goes and looks at sub-bars. Running all three side by side turns an
unverifiable assumption into a measured interval.

Two things this prints that a bare comparison table does not, and both change
how the numbers should be read:

  TRADES AFFECTED. Three near-identical columns mean nothing until you know
  whether two trades differed or forty. Most strategies here set no target at
  all, in which case the race never happens and the three modes are identical
  by construction -- worth seeing stated rather than inferring it from three
  matching rows.

  THE PESSIMISTIC-OPTIMISTIC SPREAD IS A ROBUSTNESS METRIC. It measures how
  much of the result rests on something the data cannot answer. A wide spread
  says the strategy is fragile whichever column turns out to be true, and that
  is a finding in itself, not just an error bar.

Intrabar is the realistic estimate; pessimistic stays the gate-facing number.
Sub-bar resolution was validated against ticks at zero errors first -- see
sim/intrabar.py and tools/intrabar_validate.py.
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
from sim.intrabar import INTRABAR, OPTIMISTIC, PESSIMISTIC
from sim.metrics import summarise
from sim.strategies import BASELINES, REGISTRY

MODES = [PESSIMISTIC, INTRABAR, OPTIMISTIC]


def build_strategy(name, symbol, tf, start, end):
    """Baselines need only bars; the feature strategies need a feature table."""
    if name in BASELINES:
        return BASELINES[name](), load(symbol, tf, start, end)
    from sim.run_tl import EXEC_TF, features_for
    if tf != EXEC_TF:
        print('* note: %s executes on %s; ignoring --tf %s' % (name, EXEC_TF, tf))
    bars, feat, _lines = features_for(symbol, start, end)
    return REGISTRY[name](feat), bars


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--symbol', default='XAUUSD.a')
    ap.add_argument('--tf', default='1h')
    ap.add_argument('--strategy', default='donchian', choices=sorted(REGISTRY))
    ap.add_argument('--start', default='2018-01-01')
    ap.add_argument('--end', default=None)
    ap.add_argument('--sub-tf', default=None,
                    help='resolve with this timeframe instead of the default')
    ap.add_argument('--swap', action='store_true',
                    help='charge carry (off by default: swap-free account)')
    args = ap.parse_args()

    fx = FX.build(account_currency())
    sp = spec(args.symbol, args.tf)
    results, stats = {}, {}
    for mode in MODES:
        strat, bars = build_strategy(args.strategy, args.symbol, args.tf,
                                     args.start, args.end)
        cfg = Config(risk_pct=0.5, apply_swap=args.swap, execution=mode,
                     intrabar_tf=args.sub_tf)
        sim = Simulator(sp, fx=fx, config=cfg)
        res = sim.run(bars, strat, args.symbol, args.tf)
        results[mode] = (res, sim)
        stats[mode] = summarise(res, bars)

    ref, ref_sim = results[PESSIMISTIC]
    print('\nStrategy: %s' % args.strategy)
    print('Instrument: %s' % args.symbol)
    print('Timeframe: %s   (%s .. %s, carry %s)'
          % (args.tf, bars.index[0].date(), bars.index[-1].date(),
             'charged' if args.swap else 'swap-free'))
    print()

    def row(label, fn, fmt='%s'):
        cells = ''.join((fmt % fn(stats[m], results[m])).rjust(13) for m in MODES)
        print('%-22s%s' % (label, cells))

    print('%-22s%13s%13s%13s' % ('', 'Pessimistic', 'Intrabar', 'Optimistic'))
    print('-' * 61)
    row('Trades', lambda s, r: len(r[0].trades), '%d')
    row('Win Rate', lambda s, r: 100 * (r[0].trades.r_multiple > 0).mean(), '%.1f%%')
    row('Profit Factor', lambda s, r: s.get('profit_factor', float('nan')), '%.3f')
    row('avg R', lambda s, r: r[0].trades.r_multiple.mean(), '%+.4f')
    row('Net (AUD)', lambda s, r: r[0].trades.net_acct.sum(), '%.0f')
    row('Max Drawdown', lambda s, r: s.get('max_drawdown_pct', float('nan')), '%.1f%%')
    row('Sharpe', lambda s, r: s.get('sharpe_daily_annualised', float('nan')), '%.2f')

    # --- what actually differed ---
    print()
    amb = ref_sim.ambiguous
    print('Bars holding BOTH stop and target : %d' % amb)
    if not amb:
        tgt = ref.trades.target_price.notna().sum() if len(ref.trades) else 0
        print('  -> the three modes are IDENTICAL by construction.')
        print('     %s set a target on %d of %d trades; with no target there is no'
              % (args.strategy, tgt, len(ref.trades)))
        print('     race to resolve, so nothing here depends on the assumption.')
    else:
        by = results[INTRABAR][1].resolved_by
        tot = sum(by.values()) or 1
        for how, k in sorted(by.items(), key=lambda kv: -kv[1]):
            note = ' (sub-bar held both; fell back to the stop)' if how == 'fallback' else ''
            print('  resolved by %-12s %4d  (%.0f%%)%s' % (how, k, 100 * k / tot, note))
        # Resolving one bar differently changes WHEN that position closes, which
        # changes which signal the next entry catches, and the trade sequences
        # diverge from there. So the modes cannot be compared trade-by-trade
        # -- lining them up positionally after the first divergence compares
        # unrelated trades and invents disagreement. What is comparable is the
        # count and the totals.
        p, o = results[PESSIMISTIC][0].trades, results[OPTIMISTIC][0].trades
        first = p.index[0] if len(p) else None
        for k in range(min(len(p), len(o))):
            if p.iloc[k].entry_time != o.iloc[k].entry_time:
                first = k
                break
        else:
            first = None
        print('  direct effect                 : %d bars, out of %d trades'
              % (amb, len(p)))
        if first is not None:
            print('  sequences diverge from trade  : #%d, after which the two runs'
                  % (first + 1))
            print('                                  take different trades entirely')
        print('  trades taken                  : %d pessimistic / %d optimistic'
              % (len(p), len(o)))

        pf_p = stats[PESSIMISTIC].get('profit_factor', float('nan'))
        pf_o = stats[OPTIMISTIC].get('profit_factor', float('nan'))
        net_p = p.net_acct.sum()
        net_o = o.net_acct.sum()
        print('\n  Pessimistic-Optimistic spread : PF %.3f | net %.0f AUD'
              % (abs(pf_o - pf_p), abs(net_o - net_p)))
        print('  That spread is how much of this result rests on an assumption')
        print('  the bar data cannot settle. Read it as a robustness figure, not')
        print('  an error bar: a wide one means the strategy is fragile whichever')
        print('  column turns out to be true.')

    out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       'runs', 'exec_modes_%s_%s_%s.csv'
                       % (args.symbol.replace('.', ''), args.tf, args.strategy))
    pd.DataFrame({m: stats[m] for m in MODES}).to_csv(out)
    print('\nwrote %s' % out)


if __name__ == '__main__':
    main()
