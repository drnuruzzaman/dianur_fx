#!/usr/bin/env python
"""
entry_fill_validate.py — is the ENTRY model right? Measured against ticks.

    python tools/entry_fill_validate.py --symbol XAUUSD.a --tf 4h --days 40

Exit resolution has been validated: 5m sub-bars agreed with ticks on 100% of
gold cases with zero errors. Entries never have been -- and the entry model
touches EVERY trade, where the exit ambiguity touched 0.2% of bars.

Three assumptions are checked, each of which could be flattering the result:

  1. THE BAR OPEN IS ACHIEVABLE. The simulator fills at `open[i+1]`. MetaTrader's
     bar open is a BID snapshot at the boundary; a real market order fills at the
     ASK, at whatever the book is a moment later. If the first tick of the bar is
     already away from the recorded open, every entry in every backtest is at a
     price nobody could have got.

  2. THE SPREAD FLOOR IS RIGHT. The per-bar spread column is zero for long
     stretches, so the simulator substitutes the broker's CURRENT quote -- 24
     points on gold. Ticks carry the real bid and ask, so the substitution can be
     compared with what the spread actually was.

  3. SLIPPAGE OF 0.02 ATR IS ENOUGH. Against ticks, the honest number is how far
     the ask moved between the bar boundary and a fill a realistic moment later.

WHAT IS COMPARED. For a long entry the simulator pays
`open + spread_floor + 0.02*ATR`. The achievable price is the ASK on the
first tick at or after the bar's open time.

THE SIGN CONVENTION, because getting it backwards inverts the conclusion:

    err_long  = modelled_fill - actual_ask      (a long BUYS: higher is worse)
    err_short = actual_bid - modelled_fill      (a short SELLS: lower is worse)

POSITIVE means the simulator gave itself a WORSE price than was achievable --
conservative, the safe direction. NEGATIVE means it filled BETTER than reality,
which is the failure that would inflate every result in runs/. The first
version of this file printed these labels the wrong way round.

Sampled at every bar open, not only at entries: the fill model applies to any
next-bar-open fill, and a 4h cell has ~66 entries in the tick window but
thousands of bar opens. Entries are reported separately so the subset that
actually mattered can be read on its own.
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
from sim.strategies import BASELINES
from tools.dataset import load_ticks

SLIPPAGE_ATR = 0.02


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--symbol', default='XAUUSD.a')
    ap.add_argument('--tf', default='4h')
    ap.add_argument('--start', default='2025-01-02')
    ap.add_argument('--days', type=int, default=40)
    ap.add_argument('--every', type=int, default=11,
                    help='sample every Nth day so the window is spread')
    ap.add_argument('--strategy', default='donchian')
    args = ap.parse_args()

    sp = spec(args.symbol, args.tf)
    point = sp['point']
    floor_pts = float(sp.get('spread_points_now') or 0)
    span_end = pd.Timestamp(args.start) + pd.Timedelta(days=args.days * args.every)
    bars = load(args.symbol, args.tf, args.start, str(span_end.date()))
    atr = np.asarray(atr_fn(bars, 14), dtype=float)

    # which bar opens were ACTUAL entries, so they can be reported apart
    fx = FX.build(account_currency())
    res = Simulator(sp, fx=fx, config=Config(risk_pct=0.5, apply_swap=False)).run(
        bars, BASELINES[args.strategy](), args.symbol, args.tf)
    entry_at = {t.entry_time: int(t.side) for t in res.trades.itertuples()}

    print('%s %s  %s..%s  %d bars  |  spread floor %.0f pts, slippage %.3f ATR'
          % (args.symbol, args.tf, bars.index[0].date(), bars.index[-1].date(),
             len(bars), floor_pts, SLIPPAGE_ATR))
    print('%d entries by %s in this window\n' % (len(entry_at), args.strategy))

    # Every day that contained a real entry FIRST. An arbitrary every-Nth-day
    # grid caught 1 of 38 entries, so the fill model was being measured almost
    # entirely on bars that were never traded. The remaining quota is a spread
    # sample, so the general behaviour stays visible.
    entry_days = sorted({t.date() for t in entry_at})
    grid = [d.date() for d in
            pd.date_range(args.start, span_end, freq='%dD' % args.every)]
    seen, days = set(), []
    for d in entry_days + grid:
        if d in seen:
            continue
        seen.add(d)
        days.append(pd.Timestamp(d))
        if len(days) >= args.days:
            break
    rows = []
    for day in days:
        got = list(load_ticks(args.symbol, day.date(), day.date()))
        if not got:
            continue
        ticks = got[0][1]
        if ticks.empty or 'ask' not in ticks.columns:
            continue
        day_bars = bars.loc[str(day.date()):str(day.date())]
        for t0, bar in day_bars.iterrows():
            i = bars.index.get_loc(t0)
            a = atr[i]
            if not np.isfinite(a) or a <= 0:
                continue
            # first tick AT or AFTER this bar's open — the earliest moment an
            # order placed on the previous close could possibly have filled
            tk = ticks.loc[t0:]
            if not len(tk):
                continue
            first = tk.iloc[0]
            lag_ms = (tk.index[0] - t0).total_seconds() * 1000.0
            if lag_ms > 60_000:
                continue                    # no tick near the boundary; unusable

            real_spread_pts = (first.ask - first.bid) / point
            modelled_long = bar.open + floor_pts * point + SLIPPAGE_ATR * a
            modelled_short = bar.open - SLIPPAGE_ATR * a
            # a long pays the ask; a short sells the bid
            err_long = modelled_long - first.ask        # <0 = model paid MORE
            err_short = first.bid - modelled_short      # <0 = model received MORE
            risk = 2.0 * a                              # the rule's 2-ATR stop
            rows.append({
                'when': t0, 'bar': i,
                'entry_side': entry_at.get(t0, 0),
                'lag_ms': round(lag_ms, 1),
                'bar_open': bar.open, 'tick_bid': first.bid, 'tick_ask': first.ask,
                'open_minus_bid_pts': round((bar.open - first.bid) / point, 1),
                'real_spread_pts': round(real_spread_pts, 1),
                'floor_pts': floor_pts,
                'err_long_pts': round(err_long / point, 1),
                'err_short_pts': round(err_short / point, 1),
                'err_long_R': round(err_long / risk, 4),
                'err_short_R': round(err_short / risk, 4),
            })

    if not rows:
        sys.exit('no usable bar opens — check the tick window covers --start')
    df = pd.DataFrame(rows)
    out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       'runs', 'entry_fill_%s_%s.csv'
                       % (args.symbol.replace('.', ''), args.tf))
    df.to_csv(out, index=False)

    def block(d, label):
        if not len(d):
            print('  %s: none' % label)
            return
        print('  %s  (n=%d)' % (label, len(d)))
        print('    bar open vs tick bid      median %+6.1f pts   (0 = the recorded '
              'open was tradeable)' % d.open_minus_bid_pts.median())
        print('    real spread               median %6.1f pts   floor %.0f  ->  '
              'floor is %s'
              % (d.real_spread_pts.median(), d.floor_pts.iloc[0],
                 'ADEQUATE' if d.floor_pts.iloc[0] >= d.real_spread_pts.median()
                 else 'TOO LOW'))
        print('    spread over the floor     %.0f%% of bars had a WIDER real spread'
              % (100 * (d.real_spread_pts > d.floor_pts).mean()))
        for side, col_p, col_r in (('long', 'err_long_pts', 'err_long_R'),
                                   ('short', 'err_short_pts', 'err_short_R')):
            e = d[col_r]
            cons = 100 * (d[col_p] > 0).mean()
            print('    %-5s model error       median %+7.4f R  p05 %+7.4f  p95 %+7.4f'
                  '   conservative on %.0f%% of bars'
                  % (side, e.median(), e.quantile(0.05), e.quantile(0.95), cons))

    print('=== ENTRY FILL MODEL vs TICKS ===')
    print('err > 0 = the simulator gave itself a WORSE price than was')
    print('           achievable, i.e. CONSERVATIVE.  err < 0 = optimistic.\n')
    block(df, 'every sampled bar open')
    print()
    block(df[df.entry_side != 0], 'bar opens that were REAL entries')
    print('\nmedian lag from bar boundary to first tick: %.0f ms'
          % df.lag_ms.median())
    print('wrote %s' % out)


if __name__ == '__main__':
    main()
