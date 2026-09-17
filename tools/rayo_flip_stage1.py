#!/usr/bin/env python
"""
rayo_flip_stage1.py -- WHY does the reversal exit lose? Split the effect.

    python tools/rayo_flip_stage1.py
    python tools/rayo_flip_stage1.py --tfs 15m 1h

RESEARCH ONLY. Touches no config and no live tool.

CORRECTION, 2026-09-17, READ THIS BEFORE QUOTING logs/rayo_flip_stage1.txt.
The `random_free` control below IS LOOK-AHEAD and its numbers are too good. It
places the exit at a random FRACTION of the trade's REALISED duration, which
nobody knows until the trade is over, so a fast loser is cut early and a slow
winner is cut late. tools/rayo_random_control_probe.py measured the damage: on
5m the look-ahead control returns +12.8 R/yr and a CAUSAL one -- draw the
holding time in BARS at the fill and commit to it -- returns -17.9. The leak is
entirely in the queue: the look-ahead arm's freed-in trades earn +15.0 R/yr, the
causal arm's lose -19.2, on the same exit rate and the same hold distribution.

The code is left as it ran so it still matches its log. THE CONCLUSIONS BELOW
ARE UNAFFECTED, and are strengthened: close_free lost to a control that was
cheating in its favour, and it also loses to the causal control on every frame
re-measured. Use the probe's `rand_bars free` rows, not this file's
`random_free` rows, for the honest comparison.

THE PROBLEM WITH tools/rayo_flip_exit_eval.py. It measured the proposal as one
change and found it loses on 0 of 5 gold frames -- but the proposal IS TWO
CHANGES bolted together, and that table cannot say which one did the damage:

    (a) the trade is CLOSED EARLY, at the reversal bar's close
    (b) the cell is FREED EARLY, so the account takes tickets it would
        otherwise have been too busy for -- 7645 fills became 14192 on 5m

(b) alone changes total R even if (a) is worthless, because the queue is
saturated: every cell rests a pending order on nearly every bar, so releasing
the account early always buys more trades, and every one of them pays a fresh
round trip. A rule can therefore "work" here for a reason that has nothing to
do with the trend having reversed. FOUR ARMS, on the same tickets:

  base         exit at the 5 ATR stop or TP1 -- the live rule
  close_only   close on the reversal, but HOLD THE CELL until the trade would
               have resolved anyway. The trade population is IDENTICAL to
               base's, so this is a PAIRED comparison and it measures (a)
               alone: is the early exit price better than the real one?
  close_free   close AND free -- the full proposal (= flip1 of the older tool)
  random_free  close and free at the SAME RATE and the SAME POINT through the
               trade, but on bars chosen AT RANDOM. The matched control. If
               close_free cannot beat this, the EMA reversal carries no
               information and the whole result is (b).

AND THE SPLIT THAT DECIDES IT. close_free's fills are labelled:
  anyway  the ticket base took too -- the trade existed either way
  freed   the ticket that EXISTS ONLY because the cell was released early
The proposal's premise is that the freed-in trade, now in the new direction, is
worth having. If `freed` scores below `anyway`, the idea is dead whatever the
exit is worth, and no better reversal witness can save it.

REVERSAL WITNESS. Deliberately the SAME one flip1 used -- EMA20 vs EMA50 on the
execution frame, first closed bar against the trade, unshifted. Stage 1 is about
the mechanism, not the witness; changing both at once is how the last answer
became unreadable. Stage 2 replaces the witness (CHoCH, higher-frame trend, RSI)
once this table says which half is worth fixing.

BREAK-EVEN IS ON, at 0.5R on 30m and 1h (sim/strategies/rayo.py BREAKEVEN), so
`base` here is the rule that actually trades. rayo_flip_exit_eval.py omitted it
and its 30m/1h base rows are very slightly not this one's.

COSTS. net = captured spread + 0.02 ATR a side, once per trade (the registry's
charge is per round trip); no swap, the planned account is Islamic. zero =
nothing charged, which is what separates "the idea is wrong" from "the idea is
unaffordable".

THE BAR, STATED BEFORE THE RUN.
  (a) is worth fixing only if close_only beats base on the paired mean net R.
  (b) is worth having only if `freed` >= `anyway` on median-era net R.
  The proposal survives at all only if close_free beats random_free on net R/yr
  on at least two gold frames. Anything less and the reversal witness is
  decoration on a throughput change.
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

# 3m/2h ARE NOT IN scalp_portfolio.TF_MIN and this tool must not edit a module
# the live scorer imports. Patched at import, exactly as tools/rayo_filters.py
# does it.
SP.TF_MIN.setdefault('3m', 3)
SP.TF_MIN.setdefault('2h', 120)

import tools.scalper as SC                                     # noqa: E402
from sim.instruments import load                               # noqa: E402
from sim.strategies.rayo import breakeven_r, ema               # noqa: E402
from tools import scalp_register as R                          # noqa: E402
from tools.scalp_portfolio import Cell                         # noqa: E402

TF_MIN = SP.TF_MIN

#: The control's draws come from here, so a rerun reproduces the table.
SEED = 20260917


def walk(cell, bars, arm, horizon_ms, be_r, rate=None, fracs=None, rnd=None,
         taken_base=None):
    """One cursor over the cell's tickets, one position at a time.

    Returns (trades, fired), trades = [(ms, gross_r, atr, risk, tag)] and
    `fired` = [frac through the trade] for every early close, which is what the
    matched control is built from.
    """
    ms1, H, L, C = cell.M
    frame = TF_MIN[cell.tf] * 60000
    opens = bars.index.view('int64') // 10 ** 6
    closes = bars['close'].to_numpy(float)
    ef = ema(bars['close'], 20).to_numpy(float)
    es = ema(bars['close'], 50).to_numpy(float)
    up = ef > es                                   # trend on bar j's CLOSE
    out, fired, busy = [], [], -1
    for t in cell.tk:
        if t['ms'] < busy:
            continue
        r = SC.resolve(t, cell.M, cell.expire_ms, horizon_ms, be_r)
        if not r:
            continue
        if r['outcome'] == 'expired' or r.get('r') is None:
            # AN EXPIRED TICKET STILL OCCUPIES THE CELL until its window shuts;
            # dropping it here would hand every arm free throughput.
            busy = r.get('end_ms', t['ms'])
            continue
        fill, end, gross = r['fill_ms'], r['end_ms'], r['r']
        true_end = end
        side = 1 if t['side'] == 'buy' else -1
        j0 = int(np.searchsorted(opens, fill - frame, side='right'))

        if arm in ('close_only', 'close_free'):
            j = j0
            while j < len(opens) and opens[j] + frame <= end:
                against = (not up[j]) if side > 0 else up[j]
                if against:
                    gross = (closes[j] - t['entry']) * side / t['risk']
                    end = int(opens[j] + frame)
                    break
                j += 1
        elif arm == 'random_free' and rate and rnd.random() < rate:
            # SAME RATE, SAME POINT THROUGH THE TRADE, DIFFERENT BAR. Snapping
            # to a real closed bar matters: an exit at an interpolated price is
            # not one the account could have taken.
            span = end - fill
            if span > 0:
                want = fill + rnd.choice(fracs) * span
                j = int(np.searchsorted(opens, want - frame, side='right'))
                if j0 <= j < len(opens) and opens[j] + frame <= end:
                    gross = (closes[j] - t['entry']) * side / t['risk']
                    end = int(opens[j] + frame)

        if end < true_end and true_end > fill:
            fired.append((end - fill) / float(true_end - fill))
        # close_only CLOSES THE TRADE BUT NOT THE CELL: the account is still
        # committed until the original exit, which is what makes this arm
        # trade-for-trade comparable with base.
        busy = true_end if arm == 'close_only' else end
        tag = 'anyway' if (taken_base is None or t['ms'] in taken_base) else 'freed'
        out.append((t['ms'], float(gross), float(t['atr']), float(t['risk']), tag))
    return out, fired


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
            'mean': float(sum(nets)) / len(nets),
            'ryr': sum(nets) / years, 'dd': R.drawdown_r(nets), 'eras': eras}


ROW = '%-4s %-11s %-4s %6s %5s %8s %8s %7s %6s | %8s %8s %8s %8s | %s'


def show(tf, arm, label, s, base):
    cmp = ''
    if base and s is not base:
        up = sum(1 for x, y in zip(s['eras'], base['eras'])
                 if x is not None and y is not None and x > y)
        cmp = '%d/4  %+6.1f' % (up, s['ryr'] - base['ryr'])
    print(ROW % (tf, arm, label, s['n'], '%.0f%%' % s['win'],
                 '%+.4f' % s['med'] if s['med'] is not None else '-',
                 '%+.4f' % s['mean'], '%+.1f' % s['ryr'], '%.1f' % s['dd'],
                 *[('%+8.4f' % e) if e is not None else '       -'
                   for e in s['eras']], cmp))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--symbol', default='XAUUSD.a')
    ap.add_argument('--tfs', nargs='+',
                    default=['5m', '15m', '30m', '1h', '4h'])
    ap.add_argument('--from', dest='start', default='2017-01-01')
    ap.add_argument('--to', dest='end', default='2026-09-14')
    args = ap.parse_args()
    years = (pd.Timestamp(args.end) - pd.Timestamp(args.start)).days / 365.25

    print('%s %s..%s  stop 5 ATR, TP1, fixed 12, break-even on 30m/1h, '
          '1m resolution, no swap' % (args.symbol, args.start, args.end))
    print('witness: EMA20 vs EMA50 on the execution frame, first closed bar '
          'against the trade (= flip1)')
    print(ROW % ('tf', 'arm', 'cost', 'n', 'win%', 'median', 'mean', 'R/yr',
                 'dd R', *[e[0] for e in R.ERAS], 'vs base: eras up, R/yr'))
    verdict = {}
    for tf in args.tfs:
        sys.stderr.write('%s: loading\n' % tf)
        cell = Cell(args.symbol, tf, args.start, args.end, None, 5.0, 20, 20, 50, 12)
        bars = load(args.symbol, tf, args.start, args.end)
        hz = R.HORIZON_H.get(tf, 240) * 3600 * 1000
        be = breakeven_r(tf)
        runs = {}

        sys.stderr.write('  base\n')
        runs['base'], _ = walk(cell, bars, 'base', hz, be)
        taken = set(x[0] for x in runs['base'])

        sys.stderr.write('  close_only\n')
        runs['close_only'], _ = walk(cell, bars, 'close_only', hz, be)

        sys.stderr.write('  close_free\n')
        runs['close_free'], fracs = walk(cell, bars, 'close_free', hz, be,
                                         taken_base=taken)
        rate = (len(fracs) / float(len(runs['close_free']))
                if runs['close_free'] else 0.0)

        sys.stderr.write('  random_free (rate %.2f)\n' % rate)
        runs['random_free'], _ = walk(cell, bars, 'random_free', hz, be,
                                      rate=rate, fracs=fracs or [0.5],
                                      rnd=random.Random(SEED))

        # THE PAIRED TEST. close_only holds the cell, so its trades are base's
        # trades one for one and the difference is the exit alone.
        pb = [g - (cell.spread_px + 2 * 0.02 * a) / rk
              for _, g, a, rk, _ in runs['base']]
        pc = [g - (cell.spread_px + 2 * 0.02 * a) / rk
              for _, g, a, rk, _ in runs['close_only']]
        paired = None
        if len(pb) == len(pc) and pb:
            d = [y - x for x, y in zip(pb, pc)]
            paired = (sum(d) / len(d), sum(1 for x in d if x > 0) / float(len(d)))

        for label, spread in (('net', cell.spread_px), ('zero', 0.0)):
            base = None
            for arm in ('base', 'close_only', 'close_free', 'random_free'):
                tr = runs[arm]
                s = summary(tr if spread else [(m, g, 0.0, rk, tg)
                                               for m, g, a, rk, tg in tr],
                            spread, years)
                if s is None:
                    continue
                if arm == 'base':
                    base = s
                show(tf, arm, label, s, base)
                if arm == 'close_free':
                    for tag in ('anyway', 'freed'):
                        sub = [x for x in tr if x[4] == tag]
                        if not sub:
                            continue
                        ss = summary(sub if spread else
                                     [(m, g, 0.0, rk, tg)
                                      for m, g, a, rk, tg in sub],
                                     spread, years)
                        show(tf, '  .' + tag, label, ss, None)
                        if label == 'net':
                            verdict.setdefault(tf, {})[tag] = ss['med']
                if label == 'net':
                    verdict.setdefault(tf, {})[arm] = s['ryr']
        if paired:
            print('%-4s paired close_only - base: mean %+0.4f R over %d trades, '
                  'better on %.0f%%' % (tf, paired[0], len(pb), 100 * paired[1]))
        print('%-4s early closes: %.0f%% of fills' % (tf, 100 * rate))
        sys.stdout.flush()

    print()
    beat = [tf for tf, v in verdict.items()
            if v.get('close_free') is not None
            and v['close_free'] > v.get('random_free', 0)]
    print('close_free beat the RANDOM control on %d frame(s): %s'
          % (len(beat), ', '.join(beat) or '-'))
    pays = [tf for tf, v in verdict.items()
            if v.get('freed') is not None and v.get('anyway') is not None
            and v['freed'] >= v['anyway']]
    print('freed-in trades >= trades taken anyway on %d frame(s): %s'
          % (len(pays), ', '.join(pays) or '-'))
    return 0


if __name__ == '__main__':
    sys.exit(main())
