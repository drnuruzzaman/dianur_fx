#!/usr/bin/env python
"""
rayo_dead_order_eval.py -- release the cell when the resting order is DEAD.

    python tools/rayo_dead_order_eval.py
    python tools/rayo_dead_order_eval.py --tfs 5m --dists 2 3

RESEARCH ONLY. Touches no config and no live tool.

WHERE THIS CAME FROM. tools/rayo_cancel_diagnose.py showed that cancelling a
resting order on an EMA reversal works -- where it works at all -- for a reason
nobody intended: 95-97% of its cancels are on orders that were never going to
fill, so the rule is not avoiding bad trades, it is HANDING THE CELL BACK EARLY
on orders that are already dead. It also showed the cost of using the EMA as
the trigger: on 4 of 5 frames the few orders it cancels that WOULD have filled
are the best trades on the board (1h +0.2192 against a +0.0578 average), because
an order that fills after the trend flipped against it is price whipsawing back
through a good entry.

So the reversal was a bad proxy for the thing that actually paid. This tests the
thing directly: cancel a resting order when PRICE HAS WALKED AWAY FROM IT.

THE RULE. The ticket is a stop order `D` ATR beyond the swing. On every closed
bar while it rests, measure how far price still has to travel to reach it:

    buy   (entry - close) / atr        sell  (close - entry) / atr

and cancel when that exceeds `--dists` ATR. `atr` is the ticket's own, so the
threshold means the same thing on gold and on the yen. Nothing about the
outcome enters it -- distance is knowable on the bar it is measured.

WHAT SUCCESS LOOKS LIKE, AND IT IS NOT JUST R. A rule that frees the cell by
binning good orders is the EMA witness again with extra steps, so every arm
reports PRECISION -- of the orders it cancelled, the share that would have
expired unfilled anyway -- and the mean net R of the ones that would have
filled. A good gate cancels MANY doomed orders EARLY and almost no live ones.

ARMS. `base`, then one `dead-D` arm per threshold, and a rate- and delay-matched
CAUSAL random control for any threshold that beats base -- run only for those,
because a control is a second full walk and a threshold that already loses
cannot be rescued by one.

THE BAR, STATED BEFORE THE RUN. A threshold is a candidate only if, against
base at captured cost, it raises median-era net R AND total R a year, is better
in at least 3 of 4 eras, and beats its own causal control -- on at least two
gold frames, with the same threshold. One frame is what the reversal version
managed and it was not enough.
"""

import argparse
import os
import random
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np                                             # noqa: E402
import pandas as pd                                            # noqa: E402

import tools.scalp_portfolio as SP                             # noqa: E402

SP.TF_MIN.setdefault('3m', 3)
SP.TF_MIN.setdefault('2h', 120)

import tools.scalper as SC                                     # noqa: E402
from sim.instruments import load                               # noqa: E402
from sim.strategies.rayo import breakeven_r                    # noqa: E402
from tools import scalp_register as R                          # noqa: E402
from tools.scalp_portfolio import Cell                         # noqa: E402

TF_MIN = SP.TF_MIN
SEED = 20260917


def walk(cell, bars, horizon_ms, be_r, dist=None, draws=None, rnd=None,
         rate=None, taken_base=None):
    """One cursor. Returns (trades, delays, stats)."""
    frame = TF_MIN[cell.tf] * 60000
    opens = bars.index.view('int64') // 10 ** 6
    closes = bars['close'].to_numpy(float)
    out, delays, busy = [], [], -1
    st = {'posted': 0, 'cancelled': 0, 'cx_expired': 0, 'cx_filled': [],
          'fills': 0}

    for t in cell.tk:
        if t['ms'] < busy:
            continue
        st['posted'] += 1
        r = SC.resolve(t, cell.M, cell.expire_ms, horizon_ms, be_r)
        if not r:
            continue
        expired = (r['outcome'] == 'expired' or r.get('r') is None)
        true_end = r.get('end_ms', t['ms']) if expired else r['end_ms']
        live_until = true_end if expired else r['fill_ms']
        side = 1 if t['side'] == 'buy' else -1
        atr = float(t['atr']) or 1e-9
        j0 = int(np.searchsorted(opens, t['ms'], side='left'))

        cancel_at = None
        if dist is not None:
            j = j0
            while j < len(opens) and opens[j] + frame <= live_until:
                # HOW FAR PRICE STILL HAS TO TRAVEL, in the ticket's own ATR.
                gap = (t['entry'] - closes[j]) * side / atr
                if gap > dist:
                    cancel_at = int(opens[j] + frame)
                    delays.append(max(0, j - j0))
                    break
                j += 1
        elif draws and rnd.random() < (rate or 0):
            for _ in range(8):
                j = j0 + int(rnd.choice(draws))
                if j0 <= j < len(opens) and opens[j] + frame <= live_until:
                    cancel_at = int(opens[j] + frame)
                    break

        if cancel_at is not None:
            st['cancelled'] += 1
            # PRECISION, counted on the ticket the gate just binned: would it
            # have expired anyway, or has the gate thrown away a trade?
            if expired:
                st['cx_expired'] += 1
            else:
                st['cx_filled'].append(
                    float(r['r']) - (cell.spread_px + 2 * 0.02 * atr) / t['risk'])
            busy = cancel_at
            continue

        busy = true_end
        if expired:
            continue
        st['fills'] += 1
        tag = 'anyway' if (taken_base is None or t['ms'] in taken_base) else 'freed'
        out.append((t['ms'], float(r['r']), atr, float(t['risk']), tag))
    return out, delays, st


def summary(tr, spread_px, years):
    if not tr:
        return None
    nets = [g - (spread_px + 2 * 0.02 * a) / rk for _, g, a, rk, _ in tr]
    eras = []
    for _, a, b in R.ERAS:
        lo, hi = (int(pd.Timestamp(x).value // 10 ** 6) for x in (a, b))
        sel = [n for (m, _g, _a, _rk, _t), n in zip(tr, nets) if lo <= m < hi]
        eras.append(round(sum(sel) / len(sel), 4) if sel else None)
    vals = [e for e in eras if e is not None]
    return {'n': len(tr), 'win': 100.0 * sum(1 for x in tr if x[1] > 0) / len(tr),
            'med': round(float(statistics.median(vals)), 4) if vals else None,
            'ryr': sum(nets) / years, 'dd': R.drawdown_r(nets), 'eras': eras}


ROW = '%-4s %-11s %6s %5s %8s %7s %6s | %7s %6s %8s %6s | %s'


def show(tf, arm, s, base, st):
    cmp = eras = ''
    if base and s is not base:
        up = sum(1 for x, y in zip(s['eras'], base['eras'])
                 if x is not None and y is not None and x > y)
        cmp = '%d/4  %+6.1f' % (up, s['ryr'] - base['ryr'])
        eras = up
    cx = st['cancelled']
    prec = (100.0 * st['cx_expired'] / cx) if cx else 0.0
    lost = statistics.mean(st['cx_filled']) if st['cx_filled'] else 0.0
    print(ROW % (tf, arm, s['n'], '%.0f%%' % s['win'],
                 '%+.4f' % s['med'], '%+.1f' % s['ryr'], '%.1f' % s['dd'],
                 cx, '%.0f%%' % prec, len(st['cx_filled']), '%+.3f' % lost, cmp))
    return eras


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--symbol', default='XAUUSD.a')
    ap.add_argument('--tfs', nargs='+', default=['5m', '15m', '30m', '1h', '4h'])
    ap.add_argument('--dists', nargs='+', type=float, default=[1.0, 1.5, 2.0, 3.0])
    ap.add_argument('--from', dest='start', default='2017-01-01')
    ap.add_argument('--to', dest='end', default='2026-09-14')
    args = ap.parse_args()
    years = (pd.Timestamp(args.end) - pd.Timestamp(args.start)).days / 365.25

    print('%s %s..%s  cancel a resting order once price is > D ATR away from it'
          % (args.symbol, args.start, args.end))
    print('precision = share of cancels that would have EXPIRED anyway (high is '
          'good); lost n / lost R = the ones that would have FILLED')
    print(ROW % ('tf', 'arm', 'fills', 'win%', 'median', 'R/yr', 'dd R',
                 'cancels', 'prec', 'lost n', 'lostR', 'vs base: eras up, R/yr'))
    passed = {}
    for tf in args.tfs:
        sys.stderr.write('%s: loading\n' % tf)
        cell = Cell(args.symbol, tf, args.start, args.end, None, 5.0, 20, 20, 50, 12)
        bars = load(args.symbol, tf, args.start, args.end)
        hz = R.HORIZON_H.get(tf, 240) * 3600 * 1000
        be = breakeven_r(tf)

        sys.stderr.write('  base\n')
        btr, _, bst = walk(cell, bars, hz, be)
        base = summary(btr, cell.spread_px, years)
        taken = set(x[0] for x in btr)
        show(tf, 'base', base, None, bst)

        for d in args.dists:
            sys.stderr.write('  dead-%.1f\n' % d)
            tr, delays, st = walk(cell, bars, hz, be, dist=d, taken_base=taken)
            s = summary(tr, cell.spread_px, years)
            if s is None:
                continue
            up = show(tf, 'dead-%.1f' % d, s, base, st)
            better = (s['ryr'] > base['ryr'] and s['med'] > base['med']
                      and (up or 0) >= 3)
            if not better or not st['cancelled']:
                continue
            # THE CONTROL RUNS ONLY FOR A THRESHOLD THAT ALREADY BEAT BASE.
            rate = st['cancelled'] / float(max(1, st['posted']))
            for _ in range(3):
                ctr, _, cst = walk(cell, bars, hz, be, draws=delays or [0],
                                   rnd=random.Random(SEED), rate=rate,
                                   taken_base=taken)
                if cst['cancelled'] and abs(
                        cst['cancelled'] - st['cancelled']) <= 0.10 * st['cancelled']:
                    break
                if not cst['cancelled']:
                    break
                rate *= st['cancelled'] / float(cst['cancelled'])
            cs = summary(ctr, cell.spread_px, years)
            if cs:
                show(tf, '  ctrl-%.1f' % d, cs, base, cst)
                if s['ryr'] > cs['ryr']:
                    passed.setdefault(d, []).append(tf)
        sys.stdout.flush()

    print()
    for d in sorted(passed):
        print('D=%.1f cleared every clause on %d frame(s): %s'
              % (d, len(passed[d]), ', '.join(passed[d])))
    win = [d for d in passed if len(passed[d]) >= 2]
    print('thresholds clearing the bar on >=2 frames: %s -> %s'
          % (', '.join('%.1f' % d for d in sorted(win)) or '-',
             'CANDIDATE' if win else 'NOT ADOPTED'))
    return 0


if __name__ == '__main__':
    sys.exit(main())
