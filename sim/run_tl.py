#!/usr/bin/env python
"""
run_tl.py — run the trendline / divergence strategies, and answer the question
the spec insists on: does MTF confluence actually add edge?

    python -m sim.run_tl --symbol XAUUSD.a --start 2021-01-01
    python -m sim.run_tl --all --carry-free
    python -m sim.run_tl --ab                 # confluence off vs required

15M is the execution timeframe; 1H/4H/D1 are context. Features are built once
per symbol and shared by every strategy and every confluence mode, so an A/B
compares strategies, not accidentally different inputs.
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sim.core import Config, Simulator
from sim.fx import FX
from sim.instruments import account_currency, data_hash, load, spec
from sim.metrics import by_year, summarise
from sim.strategies import FEATURE_STRATEGIES
from sim.tl import build

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNS = os.path.join(ROOT, 'runs')
EXEC_TF = '15m'
CONTEXT = ('1h', '4h', '1d')

_feature_cache = {}


def features_for(symbol, start, end):
    key = (symbol, start, end)
    if key not in _feature_cache:
        t0 = time.time()
        bars = {tf: load(symbol, tf, start, end) for tf in (EXEC_TF, *CONTEXT)}
        feat, states, lines = build(bars, EXEC_TF, CONTEXT)
        print('* features %s: %d rows x %d cols, %d lines, %.1fs'
              % (symbol, feat.shape[0], feat.shape[1], len(lines), time.time() - t0))
        _feature_cache[key] = (bars[EXEC_TF], feat, lines)
    return _feature_cache[key]


def one(symbol, strategy_name, start, end, cfg, fx, mode, tag='', **kw):
    bars, feat, lines = features_for(symbol, start, end)
    strat = FEATURE_STRATEGIES[strategy_name](feat, confluence_mode=mode, **kw)
    sim = Simulator(spec(symbol, EXEC_TF), fx=fx, config=cfg)
    result = sim.run(bars, strat, symbol, EXEC_TF)
    stats = summarise(result, bars)
    stats['data_hash'] = data_hash(symbol, EXEC_TF)
    stats['confluence_mode'] = mode

    sig = pd.DataFrame(strat.signal_log)
    if len(sig):
        stats['signals_seen'] = int(len(sig))
        stats['signals_taken'] = int(sig['taken'].sum())
        stats['mean_confluence'] = round(float(sig['confluence'].mean()), 3)
        stats['agreement_mix'] = sig['agreement'].value_counts().to_dict()

    run_id = '%s_%s_%s_%s%s' % (datetime.now(timezone.utc).strftime('%Y%m%d'),
                                strategy_name, symbol.replace('.', ''), mode,
                                ('_' + tag if tag else ''))
    out = os.path.join(RUNS, run_id)
    os.makedirs(out, exist_ok=True)
    result.trades.to_csv(os.path.join(out, 'trades.csv'), index=False)
    result.equity.to_csv(os.path.join(out, 'equity.csv'))
    by_year(result).to_csv(os.path.join(out, 'by_year.csv'), index=False)
    if len(sig):
        sig.to_csv(os.path.join(out, 'signals.csv'), index=False)
    lines.to_csv(os.path.join(out, 'trendlines.csv'), index=False)
    json.dump(stats, open(os.path.join(out, 'metrics.json'), 'w', encoding='utf-8'),
              indent=2, default=str)
    # config.json is the provenance record: without it a run has no timestamp,
    # no parameters and no data hash, and 36 runs ended up in exactly that state.
    json.dump({
        'run_id': run_id, 'symbol': symbol, 'tf': EXEC_TF, 'context': list(CONTEXT),
        'strategy': strategy_name, 'confluence_mode': mode, 'params': strat.params(),
        'config': cfg.__dict__, 'start': str(bars.index[0]), 'end': str(bars.index[-1]),
        'bars': len(bars), 'data_hash': stats['data_hash'],
        'account_currency': fx.account if fx else None,
        'fx_rate_freq': fx.freq if fx else None,
        'created_utc': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'time_base': 'broker server time',
    }, open(os.path.join(out, 'config.json'), 'w', encoding='utf-8'),
        indent=2, default=str)
    return run_id, stats


def row_of(symbol, name, mode, stats, run_id):
    return {
        'symbol': symbol, 'strategy': name, 'confluence': mode,
        'trades': stats.get('trades', 0),
        'net_AUD': stats.get('net_acct'),
        'return_pct': stats.get('return_pct'),
        'win_pct': stats.get('win_rate_pct'),
        'avg_R': stats.get('avg_R'),
        'pf': stats.get('profit_factor'),
        'maxdd_pct': stats.get('max_drawdown_pct'),
        'sharpe': stats.get('sharpe_daily_annualised'),
        'sig_seen': stats.get('signals_seen'),
        'sig_taken': stats.get('signals_taken'),
        'run_id': run_id,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--symbol', default='XAUUSD.a')
    ap.add_argument('--symbols', default='XAUUSD.a,USDJPY.a')
    ap.add_argument('--strategy', default=None, choices=sorted(FEATURE_STRATEGIES))
    ap.add_argument('--all', action='store_true')
    ap.add_argument('--ab', action='store_true',
                    help='every strategy with confluence off AND required')
    ap.add_argument('--start', default='2021-01-01')
    ap.add_argument('--end', default=None)
    ap.add_argument('--carry-free', action='store_true',
                    help='flatten before rollover so swap never applies')
    ap.add_argument('--risk-pct', type=float, default=0.5)
    ap.add_argument('--equity', type=float, default=25_000.0)
    ap.add_argument('--tag', default='')
    args = ap.parse_args()

    cfg = Config(risk_pct=args.risk_pct, start_equity=args.equity,
                 flat_by_hour=21 if args.carry_free else None,
                 apply_swap=not args.carry_free)
    fx = FX.build(account_currency())

    symbols = args.symbols.split(',') if (args.all or args.ab) else [args.symbol]
    names = sorted(FEATURE_STRATEGIES) if (args.all or args.ab) else [args.strategy or 'tl_bounce']
    modes = ['off', 'require'] if args.ab else ['off']

    rows = []
    for symbol in symbols:
        for name in names:
            for mode in modes:
                run_id, stats = one(symbol, name, args.start, args.end, cfg, fx,
                                    mode, args.tag)
                rows.append(row_of(symbol, name, mode, stats, run_id))
                print('  %-10s %-15s conf=%-8s trades=%-5s net=%-10s pf=%s'
                      % (symbol, name, mode, stats.get('trades'),
                         stats.get('net_acct'), stats.get('profit_factor')))

    df = pd.DataFrame(rows)
    path = os.path.join(RUNS, 'summary_tl%s.csv' % ('_ab' if args.ab else ''))
    df.to_csv(path, index=False)
    print('\n' + df.drop(columns=['run_id']).to_string(index=False))
    print('\n* wrote %s' % path)

    if args.ab:
        print('\n=== does MTF confluence add edge? ===')
        for (symbol, name), g in df.groupby(['symbol', 'strategy']):
            off = g[g['confluence'] == 'off'].iloc[0]
            req = g[g['confluence'] == 'require'].iloc[0]
            if not off['trades'] or not req['trades']:
                print('  %-10s %-15s inconclusive (too few trades)' % (symbol, name))
                continue
            verdict = ('helps' if (req['avg_R'] or 0) > (off['avg_R'] or 0) else 'hurts')
            print('  %-10s %-15s avg_R %+0.3f -> %+0.3f (%s), trades %d -> %d'
                  % (symbol, name, off['avg_R'] or 0, req['avg_R'] or 0,
                     verdict, off['trades'], req['trades']))


if __name__ == '__main__':
    main()
