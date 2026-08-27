#!/usr/bin/env python
"""
donchian_funnel.py — the behaviour of the signal, not the final equity.

    python tools/donchian_funnel.py --symbol XAUUSD.a --tf 4h

Four things a P&L curve cannot tell you:

  EVENTS      every channel break, with the channel that was broken, the ATR at
              the time and the direction -- so an individual signal can be
              inspected rather than only aggregated.

  FUNNEL      of those breaks, how many reached +1R before -1R, how many the
              reverse, how many neither within the horizon. This is the
              conditional behaviour of the break itself, before any exit rule,
              position size or cost is layered on.

  N FAMILY    the same measurement across N = 10 .. 200. What is wanted is a
              broad PLATEAU, not one good number: N=37 at +0.42R with N=36 at
              -0.05R is a coordinate, not an edge.

  COST LADDER raw -> spread -> spread+slippage -> everything. Where an edge dies
              matters: a signal worth +0.19R raw and +0.07R net is a different
              problem from one worth +0.08R raw.

N IS NOT A HORIZON. 20 bars is 5 hours on 15m and 20 trading days on D1, so the
same N is a different claim on every timeframe. Every row therefore reports
bars, years, trades and trades/year rather than just "Donchian 20".

The funnel resolves +1R/-1R on CLOSES of the execution timeframe, which is
optimistic for the near barrier -- tools/intrabar_validate.py measured that
close-resolution overstates the far barrier by 8-10 percentage points at tight
stops. It is reported as a behavioural profile, not as a tradeable expectancy;
the gates in tools/stage1.py are what decide the latter.
"""
import argparse
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sim.core import Config, Simulator
from sim.fx import FX
from sim.indicators import atr as atr_fn
from sim.instruments import account_currency, load, spec
from sim.metrics import summarise
from sim.strategies.donchian import Donchian

TF_HOURS = {'1m': 1 / 60, '5m': 5 / 60, '15m': 0.25, '30m': 0.5,
            '1h': 1.0, '4h': 4.0, '1d': 24.0}


def channel(high, low, n):
    """Upper/lower excluding the current bar, as the definition requires."""
    up = pd.Series(high).rolling(n).max().shift(1).to_numpy(float)
    dn = pd.Series(low).rolling(n).min().shift(1).to_numpy(float)
    return up, dn


def events(bars, n, trigger, atr_len=14):
    """One row per channel break, with everything needed to inspect it."""
    high = bars['high'].to_numpy(float)
    low = bars['low'].to_numpy(float)
    close = bars['close'].to_numpy(float)
    up, dn = channel(high, low, n)
    a = np.asarray(atr_fn(bars, atr_len), dtype=float)
    px_up = high if trigger == 'high' else close
    px_dn = low if trigger == 'high' else close

    rows = []
    for i in range(len(bars)):
        if not np.isfinite(a[i]) or a[i] <= 0:
            continue
        if np.isfinite(up[i]) and px_up[i] > up[i]:
            rows.append((i, 1, up[i], dn[i], close[i], a[i]))
        elif np.isfinite(dn[i]) and px_dn[i] < dn[i]:
            rows.append((i, -1, up[i], dn[i], close[i], a[i]))
    return pd.DataFrame(rows, columns=['bar', 'direction', 'channel_upper',
                                       'channel_lower', 'price', 'atr'])


def funnel(bars, ev, stop_atr=1.0, target_atr=2.0, horizon=96):
    """
    From each break: did price reach the target or the stop first?

    Deliberately independent of the strategy's own exit -- this measures what
    FOLLOWS a break, not what a particular rule made of it.
    """
    close = bars['close'].to_numpy(float)
    n = len(close)
    out = {'target_first': 0, 'stop_first': 0, 'neither': 0}
    rs = []
    for e in ev.itertuples():
        i, d, px, a = e.bar, e.direction, e.price, e.atr
        tp = px + d * target_atr * a
        sl = px - d * stop_atr * a
        end = min(n - 1, i + horizon)
        hit = None
        for j in range(i + 1, end + 1):
            if d > 0:
                if close[j] >= tp: hit = 'target_first'; break
                if close[j] <= sl: hit = 'stop_first'; break
            else:
                if close[j] <= tp: hit = 'target_first'; break
                if close[j] >= sl: hit = 'stop_first'; break
        out[hit or 'neither'] += 1
        rs.append(target_atr / stop_atr if hit == 'target_first'
                  else -1.0 if hit == 'stop_first' else 0.0)
    total = max(1, len(ev))
    out['n'] = len(ev)
    out['target_pct'] = round(100 * out['target_first'] / total, 1)
    out['stop_pct'] = round(100 * out['stop_first'] / total, 1)
    out['neither_pct'] = round(100 * out['neither'] / total, 1)
    out['expectancy_R'] = round(float(np.mean(rs)) if rs else np.nan, 4)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--symbol', default='XAUUSD.a')
    ap.add_argument('--tf', default='4h')
    ap.add_argument('--start', default='2016-01-01')
    ap.add_argument('--end', default=None)
    ap.add_argument('--ns', default='10,20,30,40,50,75,100,150,200')
    ap.add_argument('--trigger', default='close', choices=['close', 'high'])
    args = ap.parse_args()

    bars = load(args.symbol, args.tf, args.start, args.end)
    sp = spec(args.symbol, args.tf)
    fx = FX.build(account_currency())
    years = (bars.index[-1] - bars.index[0]).days / 365.25
    ns = [int(x) for x in args.ns.split(',')]
    hours = TF_HOURS.get(args.tf, 0)

    print('%s %s  %s..%s  %d bars, %.1f years  trigger=%s'
          % (args.symbol, args.tf, bars.index[0].date(), bars.index[-1].date(),
             len(bars), years, args.trigger))
    print()

    # ---- 1. events + funnel across the N family ----
    print('=== FUNNEL: what follows a break (stop 1 ATR, target 2 ATR, close-resolved) ===')
    print('N is not a horizon: N bars on %s is N x %.2f hours\n' % (args.tf, hours))
    print('%5s %11s %8s %8s %9s %9s %10s %11s'
          % ('N', 'horizon', 'breaks', '/year', 'target%', 'stop%', 'neither%', 'E(R)'))
    frows = []
    for n in ns:
        ev = events(bars, n, args.trigger)
        f = funnel(bars, ev)
        span = ('%.0f h' % (n * hours) if n * hours < 72
                else '%.0f d' % (n * hours / 24))
        print('%5d %11s %8d %8.1f %8.1f%% %8.1f%% %9.1f%% %+11.4f'
              % (n, span, f['n'], f['n'] / years if years else 0,
                 f['target_pct'], f['stop_pct'], f['neither_pct'],
                 f['expectancy_R']), flush=True)
        frows.append({'symbol': args.symbol, 'tf': args.tf, 'N': n,
                      'horizon': span, 'trigger': args.trigger, **f})

    # ---- 2. the traded strategy across the same N family ----
    print('\n=== TRADED: the full rule at each N (exit = N/2 channel) ===')
    print('%5s %8s %8s %8s %10s %8s %9s'
          % ('N', 'trades', '/year', 'win%', 'E(R)', 'pf', 'maxDD%'))
    trows = []
    for n in ns:
        strat = Donchian(entry=n, exit=max(2, n // 2), trigger=args.trigger)
        res = Simulator(sp, fx=fx, config=Config(risk_pct=0.5, apply_swap=False)).run(
            bars, strat, args.symbol, args.tf)
        if not len(res.trades):
            continue
        s = summarise(res, bars)
        print('%5d %8d %8.1f %7.1f%% %+10.4f %8.3f %8.1f%%'
              % (n, s['trades'], s['trades'] / years if years else 0,
                 s['win_rate_pct'], s['avg_R'], s['profit_factor'],
                 s['max_drawdown_pct']), flush=True)
        trows.append({'symbol': args.symbol, 'tf': args.tf, 'N': n,
                      'trigger': args.trigger, 'trades': s['trades'],
                      'per_year': round(s['trades'] / years, 1) if years else None,
                      'win_pct': round(s['win_rate_pct'], 1),
                      'expectancy_R': round(s['avg_R'], 4),
                      'pf': round(s['profit_factor'], 3),
                      'maxDD_pct': round(s['max_drawdown_pct'], 1)})

    # ---- 3. the cost ladder, at the incumbent N ----
    print('\n=== COST LADDER at N=20 (where the edge dies, if it does) ===')
    ladders = [('raw (no spread, no slippage)', dict(spread_points_default=-1,
                                                     slippage_atr=0.0)),
               ('spread only', dict(slippage_atr=0.0)),
               ('spread + slippage', dict()),
               ('everything incl. carry', dict(apply_swap=True))]
    lrows = []
    for label, kw in ladders:
        cfg_kw = dict(risk_pct=0.5, apply_swap=False)
        cfg_kw.update(kw)
        neg = cfg_kw.pop('spread_points_default', None)
        cfg = Config(**cfg_kw)
        if neg == -1:
            # a spec with no spread at all, for the frictionless reference
            sp2 = {**sp, 'spread_points_now': 0}
            cfg.spread_points_default = 0.0
        else:
            sp2 = sp
        res = Simulator(sp2, fx=fx, config=cfg).run(
            bars, Donchian(trigger=args.trigger), args.symbol, args.tf)
        s = summarise(res, bars)
        print('  %-30s E=%+.4f R  PF=%.3f  n=%d'
              % (label, s['avg_R'], s['profit_factor'], s['trades']))
        lrows.append({'symbol': args.symbol, 'tf': args.tf, 'stage': label,
                      'expectancy_R': round(s['avg_R'], 4),
                      'pf': round(s['profit_factor'], 3), 'trades': s['trades']})

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    tag = '%s_%s_%s' % (args.symbol.replace('.', ''), args.tf, args.trigger)
    for name, rows in (('funnel', frows), ('nfamily', trows), ('costs', lrows)):
        if rows:
            out = os.path.join(root, 'runs', 'donchian_%s_%s.csv' % (name, tag))
            pd.DataFrame(rows).to_csv(out, index=False)
            print('wrote %s' % out)


if __name__ == '__main__':
    main()
