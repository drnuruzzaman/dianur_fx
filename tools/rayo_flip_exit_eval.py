#!/usr/bin/env python
"""
rayo_flip_exit_eval.py -- close a Rayo trade when its trend reverses?

    python tools/rayo_flip_exit_eval.py                  gold 5m..4h
    python tools/rayo_flip_exit_eval.py --tfs 15m 1h

RESEARCH ONLY. Touches no config and no live tool.

THE QUESTION (asked 2026-09-17): a signal should be monitored after it fills;
if the trend reverses, close it and let the next ticket -- now in the new
direction -- be taken. Does that beat holding to the stop or TP1?

ARMS, all on the same tickets, fixed 12, one trade at a time, resolved on 1m:
  base     exit at the 5 ATR stop or TP1 (ties to the stop) -- the live rule
  flip1    as base, PLUS close at the close of the first CLOSED bar of the
           trade's own frame on which EMA20 vs EMA50 points against the trade
  flip2    as flip1, but the trend must point against it on TWO consecutive
           closed bars (one wobble does not close it)
A reversal close happens at that bar's close price, and frees the cell from
that moment, so the next ticket -- decided on the following bar -- can be
taken. The EMAs here are NOT shifted: on the close of bar j the trader knows
bar j's close, and that is the whole point of watching the trade.

A trade still open at the horizon (240h, 720h on 4h) is closed at market and
counted, as the registry does.

COSTS. net = captured spread + 0.02 ATR a side on entry, the SAME charge again
on a reversal close is NOT added (the registry's cost is per round trip), no
swap (the planned account is Islamic); zero = nothing charged.

THE BAR, STATED BEFORE THE RUN. A flip arm is a candidate on a frame only if,
against base at captured cost, it raises median-era net R per trade, is better
in at least 3 of 4 eras, and does not lower total R a year. It is worth taking
live only if that holds on at least two gold frames.
"""

import argparse
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np                                             # noqa: E402
import pandas as pd                                            # noqa: E402

import tools.scalper as SC                                     # noqa: E402
from sim.instruments import load                               # noqa: E402
from sim.strategies.rayo import ema                            # noqa: E402
from tools import scalp_register as R                          # noqa: E402
from tools.scalp_portfolio import Cell                         # noqa: E402

TF_MIN = {'1m': 1, '3m': 3, '5m': 5, '15m': 15, '30m': 30, '1h': 60, '2h': 120, '4h': 240}


def run(cell, bars, arm, horizon_ms):
    """One cursor over the cell's tickets. Returns [(ms, gross_r, atr, risk)]."""
    ms1, H, L, C = cell.M
    frame = TF_MIN[cell.tf] * 60000
    opens = bars.index.view('int64') // 10 ** 6
    closes = bars['close'].to_numpy(float)
    ef = ema(bars['close'], 20).to_numpy(float)
    es = ema(bars['close'], 50).to_numpy(float)
    up = ef > es                                   # trend on bar j's CLOSE
    need = {'flip1': 1, 'flip2': 2}.get(arm)
    out, busy = [], -1
    for t in cell.tk:
        if t['ms'] < busy:
            continue
        r = SC.resolve(t, cell.M, cell.expire_ms, horizon_ms)
        if not r:
            continue
        if r['outcome'] == 'expired' or r.get('r') is None:
            busy = r.get('end_ms', t['ms'])
            continue
        fill, end, gross = r['fill_ms'], r['end_ms'], r['r']
        if need:
            side = 1 if t['side'] == 'buy' else -1
            # first bar whose CLOSE is after the fill
            j = int(np.searchsorted(opens, fill - frame, side='right'))
            run_len = 0
            while j < len(opens) and opens[j] + frame <= end:
                against = (not up[j]) if side > 0 else up[j]
                run_len = run_len + 1 if against else 0
                if run_len >= need:
                    gross = (closes[j] - t['entry']) * side / t['risk']
                    end = int(opens[j] + frame)
                    break
                j += 1
        busy = end
        out.append((t['ms'], float(gross), float(t['atr']), float(t['risk'])))
    return out


def summary(tr, spread_px, years):
    if not tr:
        return None
    nets = [g - (spread_px + 2 * 0.02 * a) / rk for _, g, a, rk in tr]
    eras = []
    for _, a, b in R.ERAS:
        lo, hi = (int(pd.Timestamp(x).value // 10 ** 6) for x in (a, b))
        sel = [n for (m, *_), n in zip(tr, nets) if lo <= m < hi]
        eras.append(round(sum(sel) / len(sel), 4) if sel else None)
    vals = [e for e in eras if e is not None]
    return {'n': len(tr), 'win': 100.0 * sum(1 for x in tr if x[1] > 0) / len(tr),
            'med': round(float(statistics.median(vals)), 4), 'ryr': sum(nets) / years,
            'dd': R.drawdown_r(nets), 'eras': eras}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--symbol', default='XAUUSD.a')
    ap.add_argument('--tfs', nargs='+', default=['5m', '15m', '30m', '1h', '4h'])
    ap.add_argument('--from', dest='start', default='2017-01-01')
    ap.add_argument('--to', dest='end', default='2026-09-14')
    args = ap.parse_args()
    years = (pd.Timestamp(args.end) - pd.Timestamp(args.start)).days / 365.25
    print('%s %s..%s  stop 5 ATR, TP1, fixed 12, 1m resolution, no swap'
          % (args.symbol, args.start, args.end))
    print('%-4s %-6s %-4s %6s %5s %8s %7s %6s | %8s %8s %8s %8s | %s'
          % ('tf', 'arm', 'cost', 'n', 'win%', 'median', 'R/yr', 'dd R',
             *[e[0] for e in R.ERAS], 'vs base: eras up, R/yr'))
    verdicts = {}
    for tf in args.tfs:
        sys.stderr.write('%s: loading\n' % tf)
        cell = Cell(args.symbol, tf, args.start, args.end, None, 5.0, 20, 20, 50, 12)
        bars = load(args.symbol, tf, args.start, args.end)
        hz = R.HORIZON_H.get(tf, 240) * 3600 * 1000
        runs = {}
        for arm in ('base', 'flip1', 'flip2'):
            sys.stderr.write('  %s\n' % arm)
            runs[arm] = run(cell, bars, arm, hz)
        for label, spread in (('net', cell.spread_px), ('zero', None)):
            base = None
            for arm in ('base', 'flip1', 'flip2'):
                tr = runs[arm]
                if spread is None:
                    s = summary([(m, g, 0.0, rk) for m, g, a, rk in tr], 0.0, years)
                else:
                    s = summary(tr, spread, years)
                if s is None:
                    continue
                cmp = ''
                if arm == 'base':
                    base = s
                elif base:
                    up = sum(1 for x, y in zip(s['eras'], base['eras'])
                             if x is not None and y is not None and x > y)
                    ok = s['med'] > base['med'] and up >= 3 and s['ryr'] >= base['ryr']
                    if label == 'net':
                        verdicts[(tf, arm)] = ok
                    cmp = '%d/4  %+6.1f  %s' % (up, s['ryr'] - base['ryr'],
                                               'CANDIDATE' if ok and label == 'net' else '')
                print('%-4s %-6s %-4s %6d %4.0f%% %+8.4f %+7.1f %6.1f | %s | %s'
                      % (tf, arm, label, s['n'], s['win'], s['med'], s['ryr'], s['dd'],
                         ' '.join(('%+8.4f' % e) if e is not None else '       -'
                                  for e in s['eras']), cmp))
        sys.stdout.flush()
    print()
    for arm in ('flip1', 'flip2'):
        wins = [tf for tf in args.tfs if verdicts.get((tf, arm))]
        print('%s better than base on %d frame(s): %s -> %s'
              % (arm, len(wins), ', '.join(wins) or '-',
                 'LIVE CANDIDATE' if len(wins) >= 2 else 'not adopted'))
    return 0


if __name__ == '__main__':
    sys.exit(main())
