#!/usr/bin/env python
"""
rayo_breakeven_eval.py -- move the stop to break-even once a Rayo trade is in profit?

    python tools/rayo_breakeven_eval.py                 gold 5m..4h
    python tools/rayo_breakeven_eval.py --tfs 15m 1h

RESEARCH ONLY. Touches no config and no live tool.

THE QUESTION (asked 2026-09-17): once an order is in profit, move its stop to
the entry so a reversal cannot turn it into a loss. TP1 sits at 0.9R, so the
trigger has to come before it; the trade-off is winners knocked out at entry
by ordinary noise before they reach TP1.

ARMS, same tickets, fixed 12, one trade at a time, resolved on 1m bars:
  base        5 ATR stop or TP1, a tie is the loss -- the live rule
  beX         once the favourable excursion reaches X R (0.3 / 0.5 / 0.7), the
              stop moves to the ENTRY from the NEXT minute on (the minute that
              triggers it cannot also be stopped by it -- nothing in a 1m bar
              says which came first). Exit at entry is gross 0R.
  be5+c       as be0.5, but the stop moves to entry PLUS the round-trip cost
              (spread + 0.02 ATR a side), so a break-even exit is flat net.
A trade still open at the horizon (240h; 720h on 4h) is closed at market.

COSTS. net = captured spread + 0.02 ATR a side, no swap (Islamic account);
zero = nothing charged.

THE BAR, STATED BEFORE THE RUN. An arm is a candidate on a frame only if,
against base at captured cost, it raises median-era net R, is better in at
least 3 of 4 eras, and does not lower total R a year -- and it is worth taking
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
from tools import scalp_register as R                          # noqa: E402
from tools.scalp_portfolio import Cell                         # noqa: E402

ARMS = {'base': None, 'be0.3': (0.3, False), 'be0.5': (0.5, False),
        'be0.7': (0.7, False), 'be5+c': (0.5, True)}


def first(mask):
    idx = np.flatnonzero(mask)
    return int(idx[0]) if idx.size else None


def trade_be(t, r, M, horizon_ms, trig, with_cost, spread_px):
    """Re-race a filled trade with a break-even stop. Returns (gross_r, end_ms)."""
    ms, H, L, C = M
    side = 1 if t['side'] == 'buy' else -1
    entry, sl, risk = t['entry'], t['sl'], t['risk']
    tgt = t['tp'][0]
    f = int(np.searchsorted(ms, r['fill_ms'], side='left'))
    z = int(np.searchsorted(ms, t['ms'] + horizon_ms, side='right'))
    h, l = H[f:z], L[f:z]
    if not len(h):
        return r['r'], r['end_ms']
    fav = (h - entry) * side if side > 0 else (entry - l)
    i_trig = first(fav >= trig * risk)
    i_sl = first(l <= sl) if side > 0 else first(h >= sl)
    i_tp = first(h >= tgt) if side > 0 else first(l <= tgt)
    INF = 1 << 60
    i_sl0, i_tp0 = (INF if i_sl is None else i_sl), (INF if i_tp is None else i_tp)
    if i_trig is None or i_trig >= min(i_sl0, i_tp0):
        return r['r'], r['end_ms']          # never triggered before it ended
    be = entry + side * ((spread_px + 2 * 0.02 * t['atr']) if with_cost else 0.0)
    after = slice(i_trig + 1, None)
    i_be = first(l[after] <= be) if side > 0 else first(h[after] >= be)
    i_be = INF if i_be is None else i_trig + 1 + i_be
    # TP on the trigger minute itself still counts
    if i_tp0 <= i_be:
        if i_tp0 == INF:
            return (C[z - 1] - entry) * side / risk, int(ms[z - 1])
        return abs(tgt - entry) / risk, int(ms[f + i_tp0])
    return (be - entry) * side / risk, int(ms[f + i_be])


def run(cell, arm, horizon_ms):
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
        gross, end = r['r'], r['end_ms']
        if ARMS[arm]:
            trig, wc = ARMS[arm]
            gross, end = trade_be(t, r, cell.M, horizon_ms, trig, wc, cell.spread_px)
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
    return {'n': len(tr),
            'win': 100.0 * sum(1 for x in tr if x[1] > 1e-9) / len(tr),
            'be': 100.0 * sum(1 for x in tr if abs(x[1]) <= 0.05) / len(tr),
            'loss': 100.0 * sum(1 for x in tr if x[1] < -0.5) / len(tr),
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
    print('%s %s..%s  stop 5 ATR, TP1 0.9R, fixed 12, 1m resolution, no swap'
          % (args.symbol, args.start, args.end))
    print('%-4s %-6s %-4s %6s %5s %4s %5s %8s %7s %6s | %8s %8s %8s %8s | %s'
          % ('tf', 'arm', 'cost', 'n', 'win%', 'be%', 'loss%', 'median', 'R/yr', 'dd R',
             *[e[0] for e in R.ERAS], 'vs base'))
    verdicts = {}
    for tf in args.tfs:
        sys.stderr.write('%s: loading\n' % tf)
        cell = Cell(args.symbol, tf, args.start, args.end, None, 5.0, 20, 20, 50, 12)
        hz = R.HORIZON_H.get(tf, 240) * 3600 * 1000
        runs = {}
        for arm in ARMS:
            sys.stderr.write('  %s\n' % arm)
            runs[arm] = run(cell, arm, hz)
        for label, spread in (('net', cell.spread_px), ('zero', None)):
            base = None
            for arm in ARMS:
                tr = runs[arm]
                s = (summary([(m, g, 0.0, rk) for m, g, a, rk in tr], 0.0, years)
                     if spread is None else summary(tr, spread, years))
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
                    cmp = '%d/4 %+6.1f %s' % (up, s['ryr'] - base['ryr'],
                                             'CANDIDATE' if ok and label == 'net' else '')
                print('%-4s %-6s %-4s %6d %4.0f%% %3.0f%% %4.0f%% %+8.4f %+7.1f %6.1f | %s | %s'
                      % (tf, arm, label, s['n'], s['win'], s['be'], s['loss'], s['med'],
                         s['ryr'], s['dd'],
                         ' '.join(('%+8.4f' % e) if e is not None else '       -'
                                  for e in s['eras']), cmp))
        sys.stdout.flush()
    print()
    for arm in list(ARMS)[1:]:
        wins = [tf for tf in args.tfs if verdicts.get((tf, arm))]
        print('%-6s better than base on %d frame(s): %s -> %s'
              % (arm, len(wins), ', '.join(wins) or '-',
                 'LIVE CANDIDATE' if len(wins) >= 2 else 'not adopted'))
    return 0


if __name__ == '__main__':
    sys.exit(main())
