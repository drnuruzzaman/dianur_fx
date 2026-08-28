#!/usr/bin/env python
"""
money_report.py -- one balance, every timeframe, baseline vs scale-out, in money.

    python tools/money_report.py --equity 10000 --risks 1,2

Everything else in this project reports R, because R is the only unit that
compares across instruments and account sizes. This reports ACCOUNT CURRENCY,
because R does not tell you whether you can place the order or survive the
drawdown, and both turned out to decide the answer.

THREE THINGS THIS PRINTS THAT AN R TABLE CANNOT:

  avail   how many signals a large account took. Compare with `trades`: this
          balance can only take the ones its risk budget covers, because
          `size_lots` returns 0 below the broker's minimum volume and the
          engine skips those. A small account trades a SUBSET of the strategy,
          not a scaled-down version of it.
  split   entries large enough to actually split, out of those that wanted to.
          A 25% scale-out of 0.03 lots is 0.0075, which is not an order -- so a
          small account can be trading the plain rule while believing it is
          trading the scaled one. Positions too small to split run uncapped.
  maxDD $ the drawdown in money. -32% reads differently at 10,000.

Baseline rows are the validated uncapped rule. The scale-out rows are the
variants that cleared the both-era bar on 4h in tools/scaleout_sweep.py; they
are shown on every timeframe precisely so you can see they do NOT clear it
elsewhere.
"""
import argparse
import csv
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sim.core import Config, Simulator
from sim.fx import FX
from sim.instruments import account_currency, load, spec
from sim.strategies import BASELINES, strategy_for_tf
from sim.strategies.scaleout import WithScaleOut

ERAS = [('OOS 2016-2020', '2016-01-01', '2021-01-01'),
        ('IS  2021-2026', '2021-01-01', None)]
#: gold intraday is thin before 2018 -- the same rule tools/horizon_sweep.py uses
FIRST_REAL = {('XAUUSD.a', '30m'): '2018-01-01', ('XAUUSD.a', '15m'): '2018-01-01',
              ('XAUUSD.a', '5m'): '2018-01-01', ('XAUUSD.a', '1m'): '2018-01-01'}
VARIANTS = [('baseline', None, None), ('1.5R x 25%', 1.5, 0.25),
            ('2R x 20%', 2.0, 0.20), ('2R x 25%', 2.0, 0.25)]
BIG = 1_000_000.0


def run(symbol, tf, start, end, equity, risk, r_mult, frac):
    lo = max(start, FIRST_REAL.get((symbol, tf), start))
    try:
        bars = load(symbol, tf, lo, end)
    except Exception:                                        # noqa: BLE001
        return None
    if bars is None or len(bars) < 500:
        return None
    inner = BASELINES[strategy_for_tf(tf)]()
    st = inner if r_mult is None else WithScaleOut(inner, r_mult, frac)
    cfg = Config(start_equity=equity, apply_swap=False,
                 size_base='equity', risk_pct=risk)
    res = Simulator(spec(symbol, tf), fx=FX.build(account_currency()),
                    config=cfg).run(bars, st, symbol, tf)
    t = res.trades
    e = res.equity['equity'].to_numpy(float)
    base = {'armed': int(res.stats.get('scale_armed', 0)),
            'too_small': int(res.stats.get('scale_too_small', 0))}
    if not len(t) or not len(e):
        return dict(base, trades=0, fills=0, pnl=0.0, ret=0.0, dd_pct=0.0,
                    dd_ccy=0.0, win=float('nan'), scaled=0, netR=0.0,
                    final=equity)
    peak = np.maximum.accumulate(e)
    ddser = e - peak
    pos = t.groupby('pos_id')['r_multiple'].sum()
    return dict(
        base,
        trades=len(pos), fills=len(t),
        pnl=float(e[-1]) - equity, final=float(e[-1]),
        ret=100.0 * (float(e[-1]) / equity - 1.0),
        dd_pct=100.0 * float(np.nanmin(ddser / np.where(peak > 0, peak, np.nan))),
        dd_ccy=float(np.nanmin(ddser)),
        win=100.0 * float((pos > 0).mean()),
        scaled=int((t.exit_reason == 'scale_out').sum()),
        netR=float(t.r_multiple.sum()))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--symbol', default='XAUUSD.a')
    ap.add_argument('--equity', type=float, default=10_000.0)
    ap.add_argument('--risks', default='1,2')
    ap.add_argument('--tfs', default='5m,15m,30m,1h,4h,1d')
    args = ap.parse_args()

    ccy = account_currency()
    rows = []
    print('%s   starting balance %s %.0f' % (args.symbol, ccy, args.equity))
    print('drawdown is the HISTORICAL worst -- a lower bound on the worst '
          'available, not a limit.')

    for risk in [float(x) for x in args.risks.split(',')]:
        print('\n' + '#' * 104)
        print('# RISK %.1f%% PER TRADE' % risk)
        print('#' * 104)
        for era, start, end in ERAS:
            print('\n=== %s ===' % era)
            print('  %-4s %-11s %6s %6s %7s %6s %11s %8s %8s %11s'
                  % ('tf', 'variant', 'trades', 'avail', 'split', 'win%',
                     'P/L ' + ccy, 'return', 'maxDD%', 'maxDD ' + ccy))
            for tf in args.tfs.split(','):
                avail = run(args.symbol, tf, start, end, BIG, risk, None, None)
                if not avail:
                    continue
                for name, r_mult, frac in VARIANTS:
                    st = run(args.symbol, tf, start, end, args.equity, risk,
                             r_mult, frac)
                    if not st:
                        continue
                    if st['trades'] == 0:
                        print('  %-4s %-11s %6s %6d %7s %6s %11s %8s %8s %11s'
                              % (tf, name, '0', avail['trades'], '-', '-',
                                 'NO TRADE', '-', '-', '-'))
                        continue
                    split = ('-' if r_mult is None
                             else '%d/%d' % (st['armed'],
                                             st['armed'] + st['too_small']))
                    print('  %-4s %-11s %6d %6d %7s %6.1f %11.2f %7.1f%% '
                          '%7.1f%% %11.0f'
                          % (tf, name, st['trades'], avail['trades'], split,
                             st['win'], st['pnl'], st['ret'], st['dd_pct'],
                             st['dd_ccy']))
                    rows.append(dict(risk=risk, era=era, tf=tf, variant=name,
                                     avail=avail['trades'],
                                     **{k: st[k] for k in
                                        ('trades', 'win', 'pnl', 'ret',
                                         'dd_pct', 'dd_ccy', 'scaled', 'armed',
                                         'too_small', 'netR', 'final')}))
                print()
    if rows:
        out = 'runs/money_%s_%.0f.csv' % (args.symbol.replace('.', ''), args.equity)
        with open(out, 'w', newline='', encoding='utf-8') as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
        print('wrote %s' % out)


if __name__ == '__main__':
    main()
