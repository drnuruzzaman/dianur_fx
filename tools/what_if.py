#!/usr/bin/env python
"""
what_if.py -- one account, every timeframe, the rule the live chart runs.

    python tools/what_if.py --equity 1000 --symbol XAUUSD.a

NOT A FORECAST AND NOT ADVICE. It replays the SAME rule the live panel draws on
each timeframe -- the horizon-matched channel from sim/strategies/horizon.py --
over history, from one starting balance, and reports what the account would have
done. Past behaviour of a fixed rule on fixed data.

THE MINIMUM LOT IS THE POINT OF THIS TOOL. `size_lots` returns 0 when the risk
budget will not cover the broker's minimum volume, and the engine SKIPS that
signal. A small account does not trade a scaled-down version of the strategy: it
trades a different, smaller subset of it, and often none of it at all. So every
row prints the trades a large account took beside the trades this balance could
afford, and what one minimum-lot stop-out actually costs as a percentage of the
balance. A profit figure without those beside it is misleading.
"""
import argparse
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sim.core import Config, Simulator, size_lots
from sim.fx import FX
from sim.indicators import atr
from sim.instruments import account_currency, load, spec
from sim.strategies import BASELINES, strategy_for_tf
from sim.strategies.horizon import params_for_tf

ERAS = [('in sample', '2021-01-01', None),
        ('out of sample', '2016-01-01', '2021-01-01')]
BIG = 1_000_000.0


def curve(res, eq0):
    e = res.equity['equity'].to_numpy(float)
    if not len(e):
        return {'final': eq0, 'pnl': 0.0, 'ret_pct': 0.0, 'maxdd_pct': 0.0}
    peak = np.maximum.accumulate(e)
    dd = (e - peak) / np.where(peak > 0, peak, np.nan)
    return {'final': float(e[-1]), 'pnl': float(e[-1]) - eq0,
            'ret_pct': 100.0 * (float(e[-1]) / eq0 - 1.0),
            'maxdd_pct': 100.0 * float(np.nanmin(dd))}


def one(symbol, tf, eq0, risk_pct, start, end):
    strat = strategy_for_tf(tf)
    p = params_for_tf(tf)
    try:
        bars = load(symbol, tf, start, end)
    except Exception as exc:                                  # noqa: BLE001
        return {'error': str(exc), 'strategy': strat, 'entry': p['entry']}
    if bars is None or len(bars) < max(250, p['entry'] + 50):
        return {'error': 'only %d bars for a %d-bar channel'
                         % (0 if bars is None else len(bars), p['entry']),
                'strategy': strat, 'entry': p['entry']}
    sp = spec(symbol, tf)
    fx = FX.build(account_currency())
    row = {'strategy': strat, 'entry': p['entry'],
           'span': '%s..%s' % (bars.index[0].date(), bars.index[-1].date())}

    for tag, eq in (('small', eq0), ('big', BIG)):
        cfg = Config(start_equity=eq, apply_swap=False,
                     size_base='equity', risk_pct=risk_pct)
        res = Simulator(sp, fx=fx, config=cfg).run(
            bars, BASELINES[strat](), symbol, tf)
        t = res.trades
        row[tag] = dict(curve(res, eq), trades=len(t),
                        avg_R=float(t.r_multiple.mean()) if len(t) else float('nan'))

    # WHAT ONE MINIMUM-LOT TRADE COSTS. Median 2-ATR stop over the window, at
    # the broker's smallest volume -- the floor under every trade, whatever
    # risk_pct says.
    a = np.asarray(atr(bars, p['atr_len']), dtype=float)
    a = a[np.isfinite(a)]
    if len(a):
        stop_px = float(np.median(a)) * p['atr_mult']
        lot_min = float(sp['volume_min'])
        # THE SAME ARITHMETIC size_lots USES, run backwards: what one minimum
        # lot loses on a median stop, expressed in the account currency. Not a
        # second implementation -- the same two lines from sim/core.size_lots,
        # inverted, and checked against it below.
        loss_ccy = stop_px * sp['contract_size'] * lot_min
        when = bars.index[-1]
        cost = fx.to_account(loss_ccy, sp['currency_profit'], when) if fx else loss_ccy
        # cross-check: at a risk budget of exactly `cost`, size_lots must be
        # willing to trade; a hair under, it must refuse.
        pct = 100.0 * cost / eq0
        ok = size_lots(sp, eq0, stop_px, risk_pct=pct * 1.001, fx=fx, when=when)
        no = size_lots(sp, eq0, stop_px, risk_pct=pct * 0.999, fx=fx, when=when)
        row['min_lot'] = {'stop_px': stop_px, 'lots': lot_min, 'cost': cost,
                          'pct': pct, 'consistent': bool(ok >= lot_min and no == 0.0)}
    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--symbol', default='XAUUSD.a')
    ap.add_argument('--equity', type=float, default=1000.0)
    ap.add_argument('--risk', type=float, default=0.5, help='%% per trade')
    ap.add_argument('--tfs', default='5m,15m,30m,1h,4h,1d')
    args = ap.parse_args()

    ccy = account_currency()
    print('%s  starting balance %s %.0f  risk %.2f%% per trade\n'
          % (args.symbol, ccy, args.equity, args.risk))

    for era, start, end in ERAS:
        print('=== %s ===' % era.upper())
        print('  %-4s %-8s %7s %7s %10s %9s %9s   %s'
              % ('tf', 'channel', 'trades', 'of', 'P/L ' + ccy, 'return',
                 'max DD', 'note'))
        for tf in args.tfs.split(','):
            r = one(args.symbol, tf, args.equity, args.risk, start, end)
            if 'error' in r:
                print('  %-4s %-8s %s' % (tf, '%d/%d' % (r['entry'], r['entry'] // 2),
                                          r['error']))
                continue
            s, b = r['small'], r['big']
            ml = r.get('min_lot') or {}
            note = ''
            if s['trades'] == 0:
                note = ('NO TRADE POSSIBLE - one min-lot stop costs %s %.0f (%.1f%% of balance)'
                        % (ccy, ml.get('cost') or 0, ml.get('pct') or 0))
            elif s['trades'] < b['trades']:
                note = ('only %d of %d signals affordable; min lot risks %.1f%% not %.2f%%'
                        % (s['trades'], b['trades'], ml.get('pct') or 0, args.risk))
            print('  %-4s %-8s %7d %7d %10.2f %8.1f%% %8.1f%%   %s'
                  % (tf, '%d/%d' % (r['entry'], r['entry'] // 2),
                     s['trades'], b['trades'], s['pnl'], s['ret_pct'],
                     s['maxdd_pct'], note))
        print()


if __name__ == '__main__':
    main()
