#!/usr/bin/env python
"""
head_to_head.py — every strategy on ONE cell, identical everything.

    python tools/head_to_head.py --symbol XAUUSD.a --tf 4h

THE EXPERIMENT. Same instrument, same span, same spread floor, same slippage,
same risk, same execution model, same capital -- then compare. The point is not
to crown a winner but to answer a question that only a shared baseline can
answer:

    does the sophisticated approach add information beyond simple
    trend-following, or does it merely cost more to reach the same place?

A trendline system is not better for being more elaborate. If Donchian returns
+0.12 R and the trendline system returns -0.08 R on the same bars, that IS the
answer, and no amount of structure vocabulary changes it.

WHY ONE TABLE AND NOT SEVEN RUNS. These numbers already existed, scattered
across runs/ from different sessions with different cost models -- before the
spread floor, before the ATR was unified, with swap on in some and off in
others. Comparing across them was comparing different simulators. This rebuilds
them together so the only difference between rows is the strategy.

The gates are reported alongside, because expectancy without a sample count and
a control percentile is the number that has misled this project most often.
"""
import argparse
import os
import sys
import time

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sim.core import Config, Simulator
from sim.fx import FX
from sim.instruments import account_currency, load, spec
from sim.metrics import summarise
from sim.robust import bootstrap, gates, time_shift_distribution
from sim.strategies import BASELINES, FEATURE_STRATEGIES, MTF_STRATEGIES

CONTEXT = ('1h', '4h', '1d')
CONTEXT_TF = '1d'


def build_factories(symbol, tf, start, end, want):
    """
    A zero-argument factory per strategy, so `time_shift_distribution` can
    rebuild each one per shift.

    The feature strategies need a trendline/regime table and the MTF ones need a
    context series; both are built ONCE here and closed over, because rebuilding
    a 67-column feature frame 61 times per cell would cost hours to measure the
    same thing.
    """
    out = {}
    feat_cache = {'feat': None}
    for name in want:
        if name in BASELINES:
            out[name] = BASELINES[name]
            continue
        if name in MTF_STRATEGIES:
            cls = MTF_STRATEGIES[name]
            ctx = load(symbol, CONTEXT_TF, '1990-01-01', end)
            out[name] = (lambda cls=cls, ctx=ctx:
                         cls(context_bars=ctx, context_tf=CONTEXT_TF, exec_tf=tf))
            continue
        if name in FEATURE_STRATEGIES:
            cls = FEATURE_STRATEGIES[name]
            # Built ONCE per cell, not once per strategy: three feature
            # strategies were paying for three identical 28-second builds of the
            # same table.
            if feat_cache.get('feat') is None:
                t0 = time.time()
                frames = {}
                for f in {tf, *CONTEXT}:
                    try:
                        frames[f] = load(symbol, f, start, end)
                    except Exception:                      # noqa: BLE001
                        pass
                from sim.tl.features import build as build_features
                feat, _states, lines = build_features(
                    frames, exec_tf=tf,
                    context=tuple(x for x in CONTEXT if x != tf))
                feat_cache['feat'] = feat
                print('  * features %s %s: %d rows x %d cols, %d lines, %.0fs'
                      % (symbol, tf, feat.shape[0], feat.shape[1], len(lines),
                         time.time() - t0), flush=True)
            # exec_tf MUST be passed. MTFStrategy defaults it to '15m' and names
            # its columns from it, so a table built at 4h was being asked for
            # '15m_atr' and every feature strategy failed with a KeyError.
            out[name] = (lambda cls=cls, f=feat_cache: cls(f['feat'], exec_tf=tf))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--symbols', default='XAUUSD.a')
    ap.add_argument('--tfs', default='4h')
    ap.add_argument('--start', default='2016-01-01')
    ap.add_argument('--end', default=None)
    ap.add_argument('--shifts', type=int, default=60,
                    help='0 skips the control gate (much faster, much weaker)')
    ap.add_argument('--strategies', default='all')
    ap.add_argument('--swap', action='store_true',
                    help='charge carry (off by default: swap-free account)')
    ap.add_argument('--label', default='')
    args = ap.parse_args()

    known = {**BASELINES, **MTF_STRATEGIES, **FEATURE_STRATEGIES}
    want = (list(known) if args.strategies == 'all'
            else [x for x in args.strategies.split(',') if x in known])
    if not want:
        sys.exit('no known strategy in %r; pick from %s'
                 % (args.strategies, ','.join(sorted(known))))

    fx = FX.build(account_currency())
    cfg = Config(risk_pct=0.5, apply_swap=args.swap)
    cells = [(y, t) for y in args.symbols.split(',') for t in args.tfs.split(',')]

    print('%d cells x up to %d strategies  carry %s'
          % (len(cells), len(want), 'charged' if args.swap else 'off (swap-free)'))
    print('identical spread floor, slippage, risk %.2f%%, capital %s'
          % (cfg.risk_pct, cfg.start_equity))
    print('TWO PHASES, because one 61-shift pass over every cell runs for hours and')
    print('most cells are ruled out by arithmetic before any control is needed.')
    print('')
    print('  PROBE  one simulation per cell: expectancy, PF, drawdown, trade count.')
    print('  GATE   the 60-shift time-shift control, run ONLY on cells that are both')
    print('         profitable and above the 200-trade floor. A control percentile on')
    print('         an unprofitable or unjudgeable cell answers nothing.')
    print('')
    print('Never pooled across instruments: pooling manufactured a +0.784 correlation')
    print('out of nothing earlier in this project. One row per instrument per')
    print('timeframe.')

    rows = []
    for symbol, tf in cells:
        try:
            bars = load(symbol, tf, args.start, args.end)
        except Exception as exc:                          # noqa: BLE001
            print('  %-10s %-4s skipped: %s' % (symbol, tf, str(exc)[:70]))
            continue
        if len(bars) < 400:
            print('  %-10s %-4s skipped: only %d bars' % (symbol, tf, len(bars)))
            continue
        sp = spec(symbol, tf)
        years = (bars.index[-1] - bars.index[0]).days / 365.25
        # a 1d context frame cannot gate a 1d execution frame
        here = [w for w in want if not (w in MTF_STRATEGIES and tf == CONTEXT_TF)]
        print('')
        print('--- %s %s  %s..%s  %d bars, %.1f y ---'
              % (symbol, tf, bars.index[0].date(), bars.index[-1].date(),
                 len(bars), years), flush=True)
        factories = build_factories(symbol, tf, args.start, args.end, here)

        for name in here:
            factory = factories.get(name)
            if factory is None:
                continue
            t0 = time.time()
            try:
                real = Simulator(sp, fx=fx, config=cfg).run(
                    bars, factory(), symbol, tf)
            except Exception as exc:                      # noqa: BLE001
                print('  %-16s FAILED: %s' % (name, str(exc)[:90]), flush=True)
                continue
            t = real.trades
            if not len(t):
                print('  %-16s no trades' % name, flush=True)
                continue
            sm = summarise(real, bars)
            g = gates(t, pd.DataFrame())
            row = {'symbol': symbol, 'tf': tf, 'strategy': name,
                   'trades': sm['trades'],
                   'per_year': round(sm['trades'] / years, 1) if years else None,
                   'win_pct': round(sm['win_rate_pct'], 1),
                   'expectancy_R': round(sm['avg_R'], 4),
                   'pf': round(sm['profit_factor'], 3),
                   'maxDD_pct': round(sm['max_drawdown_pct'], 1),
                   'sharpe': round(sm['sharpe_daily_annualised'], 2),
                   'gate_sample': bool(g.get('gate_sample')),
                   'gate_profitable': bool(g.get('gate_profitable')),
                   'gate_effect': bool(g.get('gate_effect')),
                   'pct_vs_control': None, 'gate_control': None,
                   'probe_secs': round(time.time() - t0, 1)}

            # PHASE 2, only where a control percentile can mean something
            # the control is expensive; only spend it where the cheap gates
            # already pass, EFFECT SIZE included
            if (row['gate_sample'] and row['gate_profitable']
                    and row['gate_effect'] and args.shifts > 0):
                real2, ctrl = time_shift_distribution(
                    bars, sp, cfg, factory, symbol, tf,
                    n_shifts=args.shifts, fx=fx)
                g2 = gates(real2.trades, ctrl)
                g2.update(bootstrap(real2.trades))
                row['pct_vs_control'] = g2.get('percentile_vs_control')
                row['gate_control'] = bool(g2.get('gate_beats_control'))
                row['prob_net_negative'] = g2.get('prob_net_negative')
            rows.append(row)
            flag = ('GATED pct=%s %s' % (row['pct_vs_control'],
                                         'PASS' if row['gate_control'] else 'no')
                    if row['pct_vs_control'] is not None else
                    ('thin' if not row['gate_sample'] else
                     'unprofitable' if not row['gate_profitable'] else
                     'too small' if not row['gate_effect'] else ''))
            print('  %-16s n=%-5d %6.1f/yr  E=%+.4f R  PF=%-6.3f DD=%6.1f%%  %s'
                  % (name, row['trades'], row['per_year'] or 0,
                     row['expectancy_R'], row['pf'], row['maxDD_pct'], flag),
                  flush=True)

    if not rows:
        sys.exit('no cells produced trades')
    df = pd.DataFrame(rows)
    out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       'runs', 'head_to_head%s.csv' % (args.label or '_all'))
    df.to_csv(out, index=False)

    print('')
    print('=== EVERY CELL, identical conditions ===')
    cols = ['symbol', 'tf', 'strategy', 'trades', 'per_year', 'win_pct',
            'expectancy_R', 'pf', 'maxDD_pct', 'gate_sample',
            'gate_profitable', 'gate_effect', 'pct_vs_control',
            'gate_control']
    print(df.sort_values('expectancy_R', ascending=False)[cols]
          .to_string(index=False))

    passed = df[(df.gate_sample) & (df.gate_profitable)
                & (df.gate_effect) & (df.gate_control == True)]
    print('')
    print('passing ALL FOUR gates: %d of %d cells' % (len(passed), len(df)))
    if len(passed):
        print(passed[['symbol', 'tf', 'strategy', 'trades', 'expectancy_R',
                      'pf', 'pct_vs_control']].to_string(index=False))
    print('')
    print('by strategy, mean expectancy across every cell it ran in:')
    print(df.groupby('strategy').expectancy_R
          .agg(['count', 'mean', 'min', 'max']).round(4)
          .sort_values('mean', ascending=False).to_string())
    print('')
    print('wrote %s' % out)


if __name__ == '__main__':
    main()
