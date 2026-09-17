#!/usr/bin/env python
"""
rayo_reissue_eval.py -- fixed 12-bar ticket vs re-issued every closed bar.

    python tools/rayo_reissue_eval.py                         gold, 1m..4h
    python tools/rayo_reissue_eval.py --tfs 15m 1h

RESEARCH ONLY. Reads the bar archive, prints a table. Touches no config, no
live tool, no ledger.

THE QUESTION. The live scorer and every registered number take ONE ticket and
leave it resting, with its original levels, for 12 bars; the next ticket is
considered only when that one has expired or its trade has closed. The chart
(right-rail panel, TP bands, Strategy Replay) re-issues the order on every
closed bar instead. Which rule is worth trading?

THREE ARMS, all on the same tickets (sim.strategies.rayo.tickets, stop 5 ATR,
exit TP1 0.9R, session off), all one trade at a time, all resolved on 1m bars
with the scorer's own race (tools/scalper.resolve: fill on a touch of the
entry, stop against TP1, a tie is the loss):

  fixed12      ticket of bar i rests from bar i's open for 12 bars.
               The live scorer and registry.
  reissue      the order in force during bar j is the ticket decided on the
               CLOSE of bar j-1 -- which is ticket j-1, whose inputs end at
               j-2. Exactly what the chart shows: one bar later than it could.
  reissue-now  the order in force during bar j is ticket j. Its inputs end at
               bar j-1, so it is knowable at bar j's open; this is the
               re-issue rule without the chart's one-bar lag.

A ticket's inputs are all shifted by one bar (see rayo.py CAUSALITY), so
`fixed12` and `reissue-now` both start resting at bar i's open without
look-ahead.

NO 24h HORIZON. A filled trade is followed for HORIZON_H (240h to 2h, 720h for
4h) and, if still open, closed at market and COUNTED -- see memory
rayo-24h-horizon-dropped-unresolved-trades.

COSTS. Two columns, no swap (the planned account is Islamic):
  net     the captured spread (sim/instruments spec) + 0.02 ATR a side
  raw     spread 0 (the planned RAW ECN account) + 0.02 ATR a side.
          ECN commission is NOT charged; it is a real cost the user has not
          given a number for.

THE BAR, STATED BEFORE THE RUN. A re-issue arm is a better rule on a frame
only if, against fixed12 on the same frame and cost, it raises net R per trade
in at least 3 of the 4 eras AND does not lower total R a year. It is worth
switching the live scorer to only if that holds on at least two gold frames.

THE DATA STARTS 2017-06-12, where the 1m archive begins, so the first era is
18 months rather than 24.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding='utf-8', errors='replace')
    except (AttributeError, ValueError):
        pass

import numpy as np                                             # noqa: E402
import pandas as pd                                            # noqa: E402

import tools.scalper as SC                                     # noqa: E402
import tools.scalp_portfolio as SP                             # noqa: E402
from tools.scalp_portfolio import Cell                         # noqa: E402

for _k, _v in (('3m', 3), ('2h', 120)):
    SP.TF_MIN.setdefault(_k, _v)

TF_MIN = dict(SP.TF_MIN)
HORIZON_H = {'1m': 240, '3m': 240, '5m': 240, '15m': 240, '30m': 240,
             '1h': 240, '2h': 240, '4h': 720}
ERAS = [('2017-19', '2017-06-12', '2019-01-01'),
        ('2019-21', '2019-01-01', '2021-01-01'),
        ('2021-23', '2021-01-01', '2023-01-01'),
        ('2023-26', '2023-01-01', '2026-09-14')]
ARMS = ('fixed12', 'reissue', 'reissue-now')


def trade(t, r):
    """(ms, gross R, atr, risk) for a filled trade, or None."""
    if r is None or r['outcome'] == 'expired' or r.get('r') is None:
        return None
    return (t['ms'], float(r['r']), float(t['atr']), float(t['risk']))


def run_fixed(cell, horizon_ms):
    out, busy = [], -1
    for t in cell.tk:
        if t['ms'] < busy:
            continue
        r = SC.resolve(t, cell.M, cell.expire_ms, horizon_ms)
        if not r:
            continue
        busy = r.get('end_ms', t['ms'])
        x = trade(t, r)
        if x:
            out.append(x)
    return out


def run_reissue(cell, bar_ms, horizon_ms, lag):
    """The order in force during bar j is the ticket of bar j-lag."""
    ms, H, L, _ = cell.M
    tk = {t['ms']: t for t in cell.tk}
    frame = TF_MIN[cell.tf] * 60000
    out, free_at = [], -1
    for j in range(lag, len(bar_ms)):
        b0 = int(bar_ms[j])
        if b0 < free_at:
            continue
        t = tk.get(int(bar_ms[j - lag]))
        if t is None:
            continue
        a = int(np.searchsorted(ms, max(b0, free_at), side='left'))
        z = int(np.searchsorted(ms, b0 + frame, side='left'))
        if z <= a:
            continue
        e = t['entry']
        hit = np.flatnonzero((L[a:z] <= e) & (H[a:z] >= e))
        if not hit.size:
            continue
        f = a + int(hit[0])
        # the scorer's race, started at the fill minute: a one-minute expiry
        # window that the touch just found is inside
        r = SC.resolve(dict(t, ms=int(ms[f])), cell.M, 1, horizon_ms)
        if not r or r['outcome'] == 'expired':
            continue
        free_at = r.get('end_ms', int(ms[f]))
        x = trade(t, r)
        if x:
            out.append((int(ms[f]),) + x[1:])
    return out


def summary(tr, cell, years, spread_px):
    if not tr:
        return None
    nets = [g - (spread_px + 2 * 0.02 * atr) / risk for _, g, atr, risk in tr]
    eras = []
    for _, a, b in ERAS:
        lo = int(pd.Timestamp(a).value // 10 ** 6)
        hi = int(pd.Timestamp(b).value // 10 ** 6)
        sel = [n for (m, *_), n in zip(tr, nets) if lo <= m < hi]
        eras.append(sum(sel) / len(sel) if sel else None)
    return {'n': len(tr), 'win': 100.0 * sum(1 for x in tr if x[1] > 0) / len(tr),
            'net': sum(nets) / len(nets), 'ryr': sum(nets) / years, 'eras': eras}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--symbol', default='XAUUSD.a')
    ap.add_argument('--tfs', nargs='+',
                    default=['1m', '3m', '5m', '15m', '30m', '1h', '2h', '4h'])
    ap.add_argument('--from', dest='start', default='2017-06-12')
    ap.add_argument('--to', dest='end', default='2026-09-14')
    args = ap.parse_args()
    years = (pd.Timestamp(args.end) - pd.Timestamp(args.start)).days / 365.25

    print('%s %s..%s  stop 5 ATR, TP1 0.9R, one trade at a time, 1m resolution,'
          ' ties to the stop, no swap, open at horizon closed at market'
          % (args.symbol, args.start, args.end))
    print('net = captured spread + 0.02 ATR a side;  raw = spread 0 + 0.02 ATR '
          'a side (no ECN commission charged)')
    verdicts = {}
    for tf in args.tfs:
        sys.stderr.write('%s %s: loading\n' % (args.symbol, tf))
        cell = Cell(args.symbol, tf, args.start, args.end, None, 5.0, 20, 20, 50, 12)
        bar_ms = SP.load(args.symbol, tf, args.start, args.end).index.view('int64') // 10 ** 6
        hz = HORIZON_H.get(tf, 240) * 3600 * 1000
        runs = {}
        for arm in ARMS:
            sys.stderr.write('  %s\n' % arm)
            if arm == 'fixed12':
                runs[arm] = run_fixed(cell, hz)
            else:
                runs[arm] = run_reissue(cell, bar_ms, hz, 1 if arm == 'reissue' else 0)
        print()
        print('%s %s   (captured spread %.2f)' % (args.symbol, tf, cell.spread_px))
        print('%-12s %-4s %6s %5s %8s %7s | %8s %8s %8s %8s | %s'
              % ('arm', 'cost', 'n', 'win%', 'R/trade', 'R/yr',
                 *[e[0] for e in ERAS], 'vs fixed12: eras up, R/yr'))
        for label, spread in (('net', cell.spread_px), ('raw', 0.0)):
            base = summary(runs['fixed12'], cell, years, spread)
            for arm in ARMS:
                s = summary(runs[arm], cell, years, spread)
                if s is None:
                    print('%-12s %-4s  no trades' % (arm, label))
                    continue
                cmp = ''
                if arm != 'fixed12' and base:
                    up = sum(1 for x, y in zip(s['eras'], base['eras'])
                             if x is not None and y is not None and x > y)
                    better = up >= 3 and s['ryr'] >= base['ryr']
                    verdicts[(tf, label, arm)] = better
                    cmp = '%d/4  %+7.1f  %s' % (up, s['ryr'] - base['ryr'],
                                                'BETTER' if better else '')
                print('%-12s %-4s %6d %4.0f%% %+8.4f %+7.1f | %s | %s'
                      % (arm, label, s['n'], s['win'], s['net'], s['ryr'],
                         ' '.join(('%+8.4f' % e) if e is not None else '       -'
                                  for e in s['eras']), cmp))
        sys.stdout.flush()

    print()
    for label in ('net', 'raw'):
        for arm in ARMS[1:]:
            wins = [tf for tf in args.tfs if verdicts.get((tf, label, arm))]
            print('%-4s %-12s better than fixed12 on %d frame(s): %s -> %s'
                  % (label, arm, len(wins), ', '.join(wins) or '-',
                     'CANDIDATE' if len(wins) >= 2 else 'not adopted'))
    return 0


if __name__ == '__main__':
    sys.exit(main())
