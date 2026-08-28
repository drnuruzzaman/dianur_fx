#!/usr/bin/env python
"""
horizon_sweep.py — the same channel DURATION on every cell, in and out of sample.

    python tools/horizon_sweep.py --tfs 15m,1h,4h

WHY THE SETTING MUST DIFFER PER CELL, and why that is not the same as fitting.

N=20 is a 5-hour channel on 15m and a 3.3-day channel on 4h. Running both at 20
is not "the same strategy on two timeframes", it is two different strategies --
and it is why 15m read -0.043 R in every previous sweep here. Corrected for
duration, gold 15m goes +0.1712 at N=320 and +0.4966 at N=800, passing every
gate at four consecutive lengths.

So the parameter that travels is HOURS, not bars, and each cell gets the N that
its own bar size implies. That is a per-timeframe setting derived from one rule.
It is the opposite of per-cell fitting, which this project has measured and found
harmful: re-optimising Donchian per walk-forward window turned +107.7 net R into
+55.6 and made two windows negative (runs/walkforward_XAUUSDa_4h_donchian.csv).

WHAT THIS TOOL WILL NOT DO. It will not report the best N per cell as though it
were a finding. Every horizon is run on every cell and the WHOLE curve is
written, in sample and out, because a table showing only the winner of three
tries per cell is a table of three-way maxima and reads far better than the
strategy is.

MULTIPLICITY IS REAL HERE. 5 instruments x 3 timeframes x 3 horizons is 45 cells
per era. At a 95th-percentile control gate, chance alone passes about 2. A cell
is worth something when it passes IS *and* OOS at the same horizon; a lone pass
in one era is what noise looks like, and the summary counts both.
"""
import argparse
import os
import subprocess
import sys

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# ONE definition, shared with the live signal path. A private copy here is how
# the sweep and the panel end up trading different rules under one name.
from sim.strategies.horizon import BARS_PER_DAY, n_for  # noqa: E402

#: The range that passed on gold: 4h N=20/30/50 is 3.3/5.0/8.3 days, and 15m
#: reproduced it at 320/480/800. Fixed here rather than swept wider, so this
#: stays a test of the horizon finding and does not drift into a search.
HORIZONS = (3.3, 5.0, 8.3)

IS_START = '2021-01-01'
OOS_START, OOS_END = '2016-01-01', '2020-12-31'

#: Where each series becomes REAL bars at its own timeframe, keyed by
#: (symbol, tf) -- NOT by symbol.
#:
#: Keying this by symbol alone was wrong and it cost the flagship cell. Gold's
#: thin history is a 15m problem: measured bar-to-bar gaps are 0% true 15m in
#: 2016 (the file holds hourly bars), 83% in 2017, 99% from 2018. But 4h is
#: 94% true 4h gaps in 2016 already, and 1h is 95% -- both are dense from the
#: broker's first day. Blanket-starting gold at 2018 threw away two real years
#: at 4h and 1h, which dropped 4h N=20 out of sample from 207 trades to 131 and
#: failed it on the >=200 sample gate. The gate was right; the window was not.
FIRST_REAL = {
    ('XAUUSD.a', '30m'): '2018-01-01',    # 2016 is 0% true 30m gaps, 2017 70%
    ('XAUUSD.a', '15m'): '2018-01-01',    # 2016 0%, 2017 83%
    ('XAUUSD.a', '5m'): '2018-01-01',     # 2016 0%, 2017 93%
    ('XAUUSD.a', '1m'): '2018-01-01',
}


def run(symbols, tfs, era, start, end, shifts, label):
    """One stage1 invocation per (symbol, tf): the N list differs per tf."""
    out = []
    for sym in symbols:
        for tf in tfs:
            lo = max(start, FIRST_REAL.get((sym, tf), start))
            names = ['donchian_n%d' % n_for(tf, d) for d in HORIZONS]
            tag = '_hz_%s_%s_%s' % (era, sym.replace('.', ''), tf)
            cmd = [sys.executable, os.path.join(ROOT, 'tools', 'stage1.py'),
                   '--symbols', sym, '--tfs', tf,
                   '--strategies', ','.join(dict.fromkeys(names)),
                   '--shifts', str(shifts), '--start', lo, '--label', tag]
            if end:
                cmd += ['--end', end]
            print('  %-9s %-4s %s  %s..%s' % (sym, tf, ','.join(names), lo, end or 'now'),
                  flush=True)
            r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                               timeout=14400)
            if r.returncode:
                print('     FAILED: %s' % r.stderr.strip().splitlines()[-1:], flush=True)
                continue
            f = os.path.join(ROOT, 'runs', 'stage1%s.csv' % tag)
            if not os.path.exists(f):
                print('     no output', flush=True)
                continue
            d = pd.read_csv(f)
            d['era'] = era
            d['days'] = [round(int(s.replace('donchian_n', '')) / BARS_PER_DAY[tf], 2)
                         for s in d.strategy]
            out.append(d)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--symbols',
                    default='XAUUSD.a,USDJPY.a,EURUSD.a,GBPUSD.a,AUDUSD.a')
    ap.add_argument('--tfs', default='15m,1h,4h')
    ap.add_argument('--shifts', type=int, default=60)
    ap.add_argument('--label', default='')
    args = ap.parse_args()

    symbols = [s for s in args.symbols.split(',') if s]
    tfs = [t for t in args.tfs.split(',') if t]

    print('IN SAMPLE  %s..now' % IS_START)
    rows = run(symbols, tfs, 'IS', IS_START, None, args.shifts, args.label)
    print('OUT OF SAMPLE  %s..%s' % (OOS_START, OOS_END))
    rows += run(symbols, tfs, 'OOS', OOS_START, OOS_END, args.shifts, args.label)

    if not rows:
        sys.exit('nothing ran')
    d = pd.concat(rows, ignore_index=True)
    d['ok'] = d[['gate_sample', 'gate_profitable', 'gate_effect',
                 'gate_beats_control']].all(axis=1)
    out = os.path.join(ROOT, 'runs', 'horizon_matrix%s.csv' % args.label)
    d.to_csv(out, index=False)

    print('\n%s' % ('=' * 78))
    print('PASSES, in sample and out, at the SAME horizon')
    print('=' * 78)
    key = ['symbol', 'tf', 'strategy', 'days']
    piv = d.pivot_table(index=key, columns='era', values='ok', aggfunc='first')
    both = piv[(piv.get('IS') == True) & (piv.get('OOS') == True)]  # noqa: E712
    if len(both):
        for (sym, tf, strat, days) in both.index:
            r_is = d[(d.symbol == sym) & (d.tf == tf) & (d.strategy == strat)
                     & (d.era == 'IS')].iloc[0]
            r_oo = d[(d.symbol == sym) & (d.tf == tf) & (d.strategy == strat)
                     & (d.era == 'OOS')].iloc[0]
            print('  %-9s %-4s %-14s %4.1fd   IS %5d %+7.4f   OOS %5d %+7.4f'
                  % (sym, tf, strat, days, r_is.trades, r_is.avg_R,
                     r_oo.trades, r_oo.avg_R))
    else:
        print('  none')
    print('\n  IS passes: %d   OOS passes: %d   BOTH: %d   of %d cells per era'
          % ((d[d.era == 'IS'].ok).sum(), (d[d.era == 'OOS'].ok).sum(),
             len(both), len(d[d.era == 'IS'])))
    print('  At a 95th-percentile gate, ~%.1f passes per era are expected by '
          'chance alone.' % (0.05 * len(d[d.era == 'IS'])))
    print('wrote %s' % os.path.relpath(out, ROOT))


if __name__ == '__main__':
    main()
