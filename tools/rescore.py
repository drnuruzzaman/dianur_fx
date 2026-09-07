#!/usr/bin/env python
"""
rescore.py — apply the current four gates to every cell ever measured.

    python tools/rescore.py

WHY THIS IS NEEDED. The gates grew as they were found to be insufficient:
gate_profitable was added after a cell scored percentile 100.0 while losing
money, and gate_effect after nine of twenty-one "passes" came in under 0.05 R.
Every CSV in runs/ was scored by whatever the gate set was on the day, so the
project's own record disagrees with itself -- and the older files are the more
permissive ones.

This reads them all, re-applies the CURRENT gates uniformly, and writes one
table. It does NOT rewrite the originals: a result should be reproducible from
the raw numbers, and silently upgrading them in place would destroy the evidence
of what was believed when.

WHAT IT CANNOT FIX. Re-scoring is arithmetic on numbers already computed. It
cannot repair a cell measured before the spread floor existed, or one whose
percentile came from a different number of shifts. Those are flagged rather than
adjusted, because a stale row that looks current is worse than one that admits
it is stale.
"""
import argparse
import glob
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sim.robust import MIN_EFFECT_R, MIN_TRADES

#: files whose rows are CELLS with an expectancy and a trade count
PATTERNS = ['runs/stage1*.csv', 'runs/head_to_head*.csv', 'runs/oos/*.csv',
            'runs/summary*.csv', 'runs/mtf_gold4h.csv',
            'runs/gold_fine_tf_probe.csv']

#: the columns each file family uses for the same quantity
ALIASES = {
    'expectancy_R': ['expectancy_R', 'avg_R'],
    'trades': ['trades'],
    'pf': ['pf', 'profit_factor'],
    'percentile': ['percentile_vs_control', 'pct_vs_control'],
    'symbol': ['symbol'],
    'tf': ['tf'],
    'strategy': ['strategy', 'variant', 'tp'],
}


def pick(df, names):
    for n in names:
        if n in df.columns:
            return df[n]
    return pd.Series([np.nan] * len(df), index=df.index)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--min-effect', type=float, default=MIN_EFFECT_R)
    ap.add_argument('--min-trades', type=int, default=MIN_TRADES)
    args = ap.parse_args()

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    rows = []
    for pat in PATTERNS:
        for path in sorted(glob.glob(os.path.join(root, pat))):
            base = os.path.relpath(path, root).replace(os.sep, '/')
            try:
                d = pd.read_csv(path)
            except Exception as exc:                       # noqa: BLE001
                print('  skip %-46s %s' % (base, str(exc)[:50]))
                continue
            e = pd.to_numeric(pick(d, ALIASES['expectancy_R']), errors='coerce')
            n = pd.to_numeric(pick(d, ALIASES['trades']), errors='coerce')
            if e.isna().all() or n.isna().all():
                print('  skip %-46s no expectancy/trades columns' % base)
                continue
            out = pd.DataFrame({
                'source': base,
                'symbol': pick(d, ALIASES['symbol']).astype(str),
                'tf': pick(d, ALIASES['tf']).astype(str),
                'strategy': pick(d, ALIASES['strategy']).astype(str),
                'trades': n,
                'expectancy_R': e.round(4),
                'pf': pd.to_numeric(pick(d, ALIASES['pf']), errors='coerce'),
                'percentile': pd.to_numeric(pick(d, ALIASES['percentile']),
                                            errors='coerce'),
            })
            rows.append(out)
            print('  read %-46s %3d rows' % (base, len(out)))

    if not rows:
        sys.exit('nothing to re-score')
    df = pd.concat(rows, ignore_index=True)

    df['gate_sample'] = df.trades >= args.min_trades
    df['gate_profitable'] = (df.expectancy_R > 0) & (df.pf > 1.0)
    df['gate_effect'] = df.expectancy_R >= args.min_effect
    df['gate_control'] = df.percentile >= 95.0
    # a cell with no control run is UNKNOWN on that gate, not failing it
    df.loc[df.percentile.isna(), 'gate_control'] = np.nan
    cheap = df.gate_sample & df.gate_profitable & df.gate_effect
    df['passes_all'] = cheap & (df.gate_control == True)          # noqa: E712
    df['awaiting_control'] = cheap & df.percentile.isna()

    out = os.path.join(root, 'runs', 'rescored_all_gates.csv')
    df.to_csv(out, index=False)

    print('\n=== RE-SCORED under the current gates '
          '(sample >= %d, avg_R >= %.3f, PF > 1, percentile >= 95) ==='
          % (args.min_trades, args.min_effect))
    print('rows: %d   from %d files\n' % (len(df), df.source.nunique()))

    print('%-28s %6s %6s %6s %6s %6s' % ('gate', 'pass', 'fail', 'n/a', '', ''))
    for g in ('gate_sample', 'gate_profitable', 'gate_effect', 'gate_control'):
        v = df[g]
        print('  %-26s %6d %6d %6d' % (g, (v == True).sum(), (v == False).sum(),
                                       v.isna().sum()))            # noqa: E712

    print('\ncells passing ALL FOUR: %d of %d' % (df.passes_all.sum(), len(df)))
    p = df[df.passes_all].sort_values('expectancy_R', ascending=False)
    if len(p):
        print(p[['source', 'symbol', 'tf', 'strategy', 'trades',
                 'expectancy_R', 'pf', 'percentile']].to_string(index=False))

    print('\npassing the cheap three but never control-tested: %d'
          % df.awaiting_control.sum())
    a = df[df.awaiting_control].sort_values('expectancy_R', ascending=False)
    if len(a):
        print(a[['source', 'symbol', 'tf', 'strategy', 'trades',
                 'expectancy_R', 'pf']].head(15).to_string(index=False))

    lost = df[(df.gate_sample) & (df.gate_profitable) & (~df.gate_effect)
              & (df.gate_control == True)]                          # noqa: E712
    print('\nCELLS THE NEW EFFECT GATE REMOVES (they passed the old three): %d'
          % len(lost))
    if len(lost):
        print(lost[['source', 'symbol', 'tf', 'strategy', 'trades',
                    'expectancy_R', 'pf', 'percentile']]
              .sort_values('expectancy_R', ascending=False).to_string(index=False))

    print('\nwrote %s' % out)


if __name__ == '__main__':
    main()
