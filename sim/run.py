#!/usr/bin/env python
"""
run.py — execute a backtest and write a run folder.

    python -m sim.run --symbol XAUUSD.a --tf 4h --strategy donchian
    python -m sim.run --all                      # both baselines x symbols x tfs
    python -m sim.run --all --start 2007-01-01 --end 2019-12-31

Each run writes runs/<id>/ with trades.csv, equity.csv, metrics.json, by_year.csv
and config.json. config.json carries a hash of the input files, so a number can
always be traced to the history that produced it.
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sim.core import Config, Simulator
from sim.fx import FX
from sim.instruments import account_currency, data_hash, load, spec
from sim.metrics import by_year, summarise
from sim.strategies import BASELINES

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNS = os.path.join(ROOT, 'runs')


def one(symbol, tf, strategy_name, params, cfg, fx, start=None, end=None, tag=''):
    bars = load(symbol, tf, start, end)
    sp = spec(symbol, tf)
    strat = BASELINES[strategy_name](**params)
    sim = Simulator(sp, fx=fx, config=cfg)
    result = sim.run(bars, strat, symbol, tf)
    stats = summarise(result, bars)
    stats['data_hash'] = data_hash(symbol, tf)

    run_id = '%s_%s_%s_%s%s' % (datetime.now(timezone.utc).strftime('%Y%m%d'),
                                strategy_name, symbol.replace('.', ''), tf,
                                ('_' + tag if tag else ''))
    out = os.path.join(RUNS, run_id)
    os.makedirs(out, exist_ok=True)
    result.trades.to_csv(os.path.join(out, 'trades.csv'), index=False)
    result.equity.to_csv(os.path.join(out, 'equity.csv'))
    by_year(result).to_csv(os.path.join(out, 'by_year.csv'), index=False)
    json.dump(stats, open(os.path.join(out, 'metrics.json'), 'w', encoding='utf-8'),
              indent=2, default=str)
    json.dump({
        'run_id': run_id, 'symbol': symbol, 'tf': tf, 'strategy': strategy_name,
        'params': params, 'config': cfg.__dict__,
        'start': str(bars.index[0]), 'end': str(bars.index[-1]),
        'bars': len(bars), 'data_hash': stats['data_hash'],
        'account_currency': fx.account if fx else None,
        'fx_rate_freq': fx.freq if fx else None,
        'created_utc': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'time_base': 'broker server time',
    }, open(os.path.join(out, 'config.json'), 'w', encoding='utf-8'), indent=2)
    return run_id, stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--symbol', default='XAUUSD.a')
    ap.add_argument('--tf', default='4h')
    # baselines only: tl_bounce, tl_breakout and rsi_divergence need a
    # trendline feature table, so they run through sim/run_tl.py instead
    ap.add_argument('--strategy', default='donchian', choices=sorted(BASELINES))
    ap.add_argument('--all', action='store_true', help='every baseline x symbol x tf')
    ap.add_argument('--symbols', default='XAUUSD.a,USDJPY.a')
    ap.add_argument('--tfs', default='15m,1h,4h,1d')
    ap.add_argument('--start'), ap.add_argument('--end')
    ap.add_argument('--risk-pct', type=float, default=0.5)
    ap.add_argument('--equity', type=float, default=25_000.0)
    # slippage is ATR-relative now; a fixed point count meant different
    # things on different instruments (see sim/core.py Config)
    ap.add_argument('--slippage-atr', type=float, default=0.02)
    ap.add_argument('--no-swap', action='store_true')
    ap.add_argument('--no-short', action='store_true')
    ap.add_argument('--tag', default='')
    args = ap.parse_args()

    cfg = Config(risk_pct=args.risk_pct, start_equity=args.equity,
                 slippage_atr=args.slippage_atr, apply_swap=not args.no_swap,
                 allow_short=not args.no_short)
    fx = FX.build(account_currency())

    jobs = ([(s, tf, name) for s in args.symbols.split(',')
             for tf in args.tfs.split(',') for name in sorted(BASELINES)]
            if args.all else [(args.symbol, args.tf, args.strategy)])

    rows = []
    for symbol, tf, name in jobs:
        try:
            run_id, stats = one(symbol, tf, name, {}, cfg, fx, args.start, args.end, args.tag)
        except FileNotFoundError as exc:
            print('! skip %s %s: %s' % (symbol, tf, exc))
            continue
        rows.append({
            'symbol': symbol, 'tf': tf, 'strategy': name,
            'trades': stats.get('trades', 0),
            'net_%s' % (fx.account if fx else 'ccy'): stats.get('net_acct'),
            'return_pct': stats.get('return_pct'),
            'cagr_pct': stats.get('cagr_pct'),
            'buy_hold_pct': stats.get('buy_and_hold_pct'),
            'win_pct': stats.get('win_rate_pct'),
            'avg_R': stats.get('avg_R'),
            'pf': stats.get('profit_factor'),
            'maxdd_pct': stats.get('max_drawdown_pct'),
            'sharpe': stats.get('sharpe_daily_annualised'),
            'swap_ccy': stats.get('cost_swap_ccy'),
            'run_id': run_id,
        })
        print('* %-10s %-4s %-10s trades=%-5s net=%-10s dd=%s%%'
              % (symbol, tf, name, stats.get('trades'), stats.get('net_acct'),
                 stats.get('max_drawdown_pct')))

    if rows:
        df = pd.DataFrame(rows)
        os.makedirs(RUNS, exist_ok=True)
        path = os.path.join(RUNS, 'summary.csv')
        df.to_csv(path, index=False)
        print('\n' + df.drop(columns=['run_id']).to_string(index=False))
        print('\n* wrote %s' % path)


if __name__ == '__main__':
    main()
