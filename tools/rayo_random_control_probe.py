#!/usr/bin/env python
"""
rayo_random_control_probe.py -- is the random control real, or look-ahead?

    python tools/rayo_random_control_probe.py --tfs 5m 15m 1h
    python tools/rayo_random_control_probe.py --tfs 5m --timestop

RESEARCH ONLY. Touches no config and no live tool.

THE ANOMALY. In tools/rayo_flip_stage1.py the matched RANDOM control -- close
the trade and free the cell at a random bar -- beat the live rule itself on 5m
(+12.8 vs -5.3 net R/yr) and 15m (+11.5 vs +5.8). A control is supposed to be
the thing an idea must beat, not the best arm on the board, so either it cheats
or the rule's exit is mis-specified on the fast frames. Both are worth knowing
and they have opposite consequences, so this separates them.

THE SUSPECT, AND IT IS MINE. That control drew a FRACTION of the way through
the trade and multiplied it by `end - fill` -- the trade's TRUE duration, which
nobody knows until the trade is over. A fast loser is therefore cut early and a
slow winner is cut late, and the placement is conditioned on the outcome. That
is look-ahead, of exactly the kind sim/tl/ spends its whole design avoiding.

ARMS.
  base          the live rule: 5 ATR stop or TP1, break-even where the rule
                uses it
  rand_frac     the Stage 1 control, reproduced: exit at a random FRACTION of
                the realised duration. LOOK-AHEAD, kept so the comparison is
                against the actual suspect rather than a description of it
  rand_bars     CAUSAL: at the fill, draw k bars from the same distribution of
                absolute holding times and commit to bailing at the close of
                bar k. If the stop or TP1 arrives first, it arrives first --
                that check reads `end` only to decide WHICH CAME FIRST, which
                is what would really happen, not to place the exit
  timestop-K    CAUSAL, no randomness: every trade bails at bar K (--timestop)

Each random/timestop arm runs in two modes:
  free  the cell is released at the early exit -- more tickets get taken
  only  the cell is held to the original exit -- the trade population is
        base's, one for one, so it is a paired measure of the exit alone

And the free arms tag fills `anyway` / `freed`, as Stage 1 did.

WHAT THE ANSWER MEANS.
  rand_bars ~ base, rand_frac >> base  -> the anomaly was my look-ahead. The
      Stage 1 conclusion is unaffected: close_free lost to a control that was
      CHEATING, which is worse for it, not better.
  rand_bars still >> base              -> the anomaly is real and the finding
      is about the rule, not the control: holding a fast-frame gold scalp to
      the 5 ATR stop is worse than bailing on a clock. That is a live question
      about the exit, and a separate one from the reversal rule.
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
from sim.strategies.rayo import breakeven_r, ema               # noqa: E402
from tools import scalp_register as R                          # noqa: E402
from tools.scalp_portfolio import Cell                         # noqa: E402

TF_MIN = SP.TF_MIN
SEED = 20260917


def walk(cell, bars, arm, horizon_ms, be_r, free=True, rate=None, draws=None,
         rnd=None, k=None, taken_base=None):
    """One cursor over the cell's tickets. Returns (trades, fracs, bars_held).

    `fracs` and `bars_held` are collected from the EMA-reversal arm and are what
    the two random arms are then matched to.
    """
    frame = TF_MIN[cell.tf] * 60000
    opens = bars.index.view('int64') // 10 ** 6
    closes = bars['close'].to_numpy(float)
    ef = ema(bars['close'], 20).to_numpy(float)
    es = ema(bars['close'], 50).to_numpy(float)
    up = ef > es
    out, fracs, held, busy = [], [], [], -1
    for t in cell.tk:
        if t['ms'] < busy:
            continue
        r = SC.resolve(t, cell.M, cell.expire_ms, horizon_ms, be_r)
        if not r:
            continue
        if r['outcome'] == 'expired' or r.get('r') is None:
            busy = r.get('end_ms', t['ms'])
            continue
        fill, end, gross = r['fill_ms'], r['end_ms'], r['r']
        true_end = end
        side = 1 if t['side'] == 'buy' else -1
        # the first bar of the frame whose CLOSE lands after the fill
        j0 = int(np.searchsorted(opens, fill - frame, side='right'))

        if arm == 'flip':
            j = j0
            while j < len(opens) and opens[j] + frame <= end:
                if (not up[j]) if side > 0 else up[j]:
                    gross = (closes[j] - t['entry']) * side / t['risk']
                    end = int(opens[j] + frame)
                    break
                j += 1
        elif arm == 'rand_frac' and rate and rnd.random() < rate:
            # LOOK-AHEAD, ON PURPOSE. `span` is the realised duration.
            span = end - fill
            if span > 0:
                want = fill + rnd.choice(draws) * span
                j = int(np.searchsorted(opens, want - frame, side='right'))
                if j0 <= j < len(opens) and opens[j] + frame <= end:
                    gross = (closes[j] - t['entry']) * side / t['risk']
                    end = int(opens[j] + frame)
        elif arm in ('rand_bars', 'timestop'):
            # CAUSAL. The bail bar is fixed at the fill. Nothing about the
            # outcome enters the choice; `end` is consulted only to see whether
            # the stop or the target got there first.
            kk = k if arm == 'timestop' else (
                rnd.choice(draws) if (rate is None or rnd.random() < rate) else None)
            if kk is not None:
                j = j0 + int(kk)
                if j0 <= j < len(opens) and opens[j] + frame <= end:
                    gross = (closes[j] - t['entry']) * side / t['risk']
                    end = int(opens[j] + frame)

        if end < true_end and true_end > fill:
            fracs.append((end - fill) / float(true_end - fill))
            held.append(max(1, int(round((end - fill) / float(frame)))))
        busy = end if free else true_end
        tag = 'anyway' if (taken_base is None or t['ms'] in taken_base) else 'freed'
        out.append((t['ms'], float(gross), float(t['atr']), float(t['risk']), tag))
    return out, fracs, held


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


ROW = '%-4s %-16s %-4s %6s %5s %8s %8s %7s %6s | %8s %8s %8s %8s | %s'


def show(tf, arm, label, s, base):
    cmp = ''
    if base and s is not base:
        upn = sum(1 for x, y in zip(s['eras'], base['eras'])
                  if x is not None and y is not None and x > y)
        cmp = '%d/4  %+6.1f' % (upn, s['ryr'] - base['ryr'])
    print(ROW % (tf, arm, label, s['n'], '%.0f%%' % s['win'],
                 '%+.4f' % s['med'] if s['med'] is not None else '-',
                 '%+.4f' % s['mean'], '%+.1f' % s['ryr'], '%.1f' % s['dd'],
                 *[('%+8.4f' % e) if e is not None else '       -'
                   for e in s['eras']], cmp))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--symbol', default='XAUUSD.a')
    ap.add_argument('--tfs', nargs='+', default=['5m', '15m', '1h'])
    ap.add_argument('--from', dest='start', default='2017-01-01')
    ap.add_argument('--to', dest='end', default='2026-09-14')
    ap.add_argument('--timestop', action='store_true',
                    help='also sweep fixed causal bail bars K')
    ap.add_argument('--ks', nargs='+', type=int, default=[2, 4, 8, 16, 32])
    args = ap.parse_args()
    years = (pd.Timestamp(args.end) - pd.Timestamp(args.start)).days / 365.25

    print('%s %s..%s  stop 5 ATR, TP1, fixed 12, break-even on 30m/1h, '
          '1m resolution, no swap' % (args.symbol, args.start, args.end))
    print('free = cell released at the early exit;  only = cell held, paired '
          'with base')
    print(ROW % ('tf', 'arm', 'cost', 'n', 'win%', 'median', 'mean', 'R/yr',
                 'dd R', *[e[0] for e in R.ERAS], 'vs base: eras up, R/yr'))
    for tf in args.tfs:
        sys.stderr.write('%s: loading\n' % tf)
        cell = Cell(args.symbol, tf, args.start, args.end, None, 5.0, 20, 20, 50, 12)
        bars = load(args.symbol, tf, args.start, args.end)
        hz = R.HORIZON_H.get(tf, 240) * 3600 * 1000
        be = breakeven_r(tf)

        runs = {}
        sys.stderr.write('  base\n')
        runs['base'], _, _ = walk(cell, bars, 'base', hz, be)
        taken = set(x[0] for x in runs['base'])

        # THE DISTRIBUTIONS BOTH RANDOM ARMS ARE MATCHED TO come from the
        # reversal arm, which is the thing Stage 1 was testing.
        sys.stderr.write('  flip (for the matched distributions)\n')
        flip, fracs, held = walk(cell, bars, 'flip', hz, be)
        rate = len(fracs) / float(len(flip)) if flip else 0.0
        sys.stderr.write('    rate %.2f, median hold %d bars\n'
                         % (rate, statistics.median(held) if held else 0))

        for arm, draws in (('rand_frac', fracs or [0.5]),
                           ('rand_bars', held or [1])):
            for free in (True, False):
                sys.stderr.write('  %s %s\n' % (arm, 'free' if free else 'only'))
                runs[(arm, free)], _, _ = walk(
                    cell, bars, arm, hz, be, free=free, rate=rate, draws=draws,
                    rnd=random.Random(SEED), taken_base=taken if free else None)
        if args.timestop:
            for k in args.ks:
                for free in (True, False):
                    sys.stderr.write('  timestop-%d %s\n'
                                     % (k, 'free' if free else 'only'))
                    runs[('timestop-%d' % k, free)], _, _ = walk(
                        cell, bars, 'timestop', hz, be, free=free, k=k,
                        rnd=random.Random(SEED), taken_base=taken if free else None)

        order = ['base']
        for arm in ['rand_frac', 'rand_bars'] + (
                ['timestop-%d' % k for k in args.ks] if args.timestop else []):
            order += [(arm, False), (arm, True)]

        for label, spread in (('net', cell.spread_px), ('zero', 0.0)):
            base = None
            for key in order:
                tr = runs.get(key)
                if not tr:
                    continue
                name = key if key == 'base' else '%s %s' % (
                    key[0], 'free' if key[1] else 'only')
                s = summary(tr if spread else [(m, g, 0.0, rk, tg)
                                               for m, g, a, rk, tg in tr],
                            spread, years)
                if s is None:
                    continue
                if key == 'base':
                    base = s
                show(tf, name, label, s, base)
                if key != 'base' and key[1] and label == 'net':
                    for tag in ('anyway', 'freed'):
                        sub = [x for x in tr if x[4] == tag]
                        if sub:
                            show(tf, '  .' + tag, label,
                                 summary(sub, spread, years), None)

        # THE PAIRED TEST, on the arms that hold the cell.
        pb = [g - (cell.spread_px + 2 * 0.02 * a) / rk
              for _, g, a, rk, _ in runs['base']]
        for key in order:
            if key == 'base' or key[1]:
                continue
            pc = [g - (cell.spread_px + 2 * 0.02 * a) / rk
                  for _, g, a, rk, _ in runs[key]]
            if len(pc) == len(pb) and pb:
                d = [y - x for x, y in zip(pb, pc)]
                print('%-4s paired %s only - base: mean %+0.4f R over %d, '
                      'better on %.0f%%'
                      % (tf, key[0], sum(d) / len(d), len(pb),
                         100.0 * sum(1 for x in d if x > 0) / len(d)))
        print('%-4s reversal rate %.0f%%, median hold %d bars'
              % (tf, 100 * rate, statistics.median(held) if held else 0))
        sys.stdout.flush()
    return 0


if __name__ == '__main__':
    sys.exit(main())
