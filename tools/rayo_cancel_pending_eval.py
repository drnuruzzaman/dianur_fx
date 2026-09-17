#!/usr/bin/env python
"""
rayo_cancel_pending_eval.py -- cancel a RESTING ORDER when the trend turns?

    python tools/rayo_cancel_pending_eval.py
    python tools/rayo_cancel_pending_eval.py --tfs 15m 1h

RESEARCH ONLY. Touches no config and no live tool.

A DIFFERENT QUESTION FROM THE ONE ALREADY ANSWERED, and the difference is the
whole reason this file exists. tools/rayo_flip_stage1.py measured closing a
FILLED trade on a reversal and rejected it on every count. This measures
cancelling an order that HAS NOT FILLED YET -- the ticket is resting, the
market has not come to it, and the setup that justified it has since turned.

WHY IT MIGHT SURVIVE WHERE THE EXIT DID NOT. Everything that killed the exit
was a cost of BEING IN THE TRADE: a realised loss taken at the worst moment in
the trade, and a fresh round trip to get back in. A cancelled order pays none
of that. Nothing filled, so no spread, no slippage, no loss -- the ticket simply
never becomes a trade.

WHY IT STILL MIGHT NOT. A resting order occupies the cell for its whole 12-bar
expiry (tools/scalper.py resolves an unfilled ticket to the end of that window,
and the account is busy until then), so cancelling early RELEASES THE CELL
EARLY -- and releasing the cell early is exactly the half that lost on 5 of 5
frames. The rule also already beat per-bar re-issue by holding one ticket for
the full 12 bars, and cancelling shortens ticket life in the same direction
that lost.

So the two halves point opposite ways and the only way to find out is to run it.

ARMS.
  base          post the ticket, let it fill or expire -- the live rule
  cancel_hold   cancel on the reversal, but HOLD the cell until the ticket
                would have resolved anyway. The cell's timeline is base's, so
                this isolates "was cancelling a good idea" from "did releasing
                the cell early pay"
  cancel_free   cancel AND free -- the full proposal
  random_free   cancel and free after a CAUSAL random number of bars, drawn at
                the moment the ticket is posted from the same distribution of
                cancel delays. The matched control. Causal: nothing about the
                outcome enters the draw -- see the look-ahead this project
                already caught itself committing in rayo_random_control_probe.py

THE WITNESS is EMA20/50 on the execution frame turning against the ticket's
side, on a CLOSED bar, between the ticket being posted and it filling. The same
witness the exit study used and the same one the chart's lamp lights on -- and
on a pending order it means something stronger than it does on an open trade:
the EMA pair is what DECIDED the side, so a flip is the setup invalidating
rather than an opinion about a trade in progress.

A CANCELLED TICKET CONTRIBUTES NO TRADE AND NO COST. It is not a zero-R trade
-- it is not a trade at all -- so it changes the result only through what the
freed cell does next, which is precisely what the split below measures.

COSTS. net = captured spread + 0.02 ATR a side on every FILL; no swap, the
planned account is Islamic. zero = nothing charged.

THE BAR, STATED BEFORE THE RUN. Cancelling is worth doing only if cancel_free
beats base on median-era net R AND on total R a year, in at least 3 of 4 eras,
on at least two gold frames -- and beats the causal random control on those same
frames. cancel_hold is the diagnostic: if it beats base while cancel_free does
not, the cancel is sound and the cell release is what spoils it.
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


def walk(cell, bars, arm, horizon_ms, be_r, free=True, draws=None, rnd=None,
         taken_base=None, rate=None):
    """One cursor over the tickets. Returns (trades, delays, n_cancelled, n_posted).

    `delays` is the cancel delay in BARS for every cancelled ticket, which is
    what the causal control is matched to.
    """
    frame = TF_MIN[cell.tf] * 60000
    opens = bars.index.view('int64') // 10 ** 6
    ef = ema(bars['close'], 20).to_numpy(float)
    es = ema(bars['close'], 50).to_numpy(float)
    up_trend = ef > es
    out, delays, busy, cancelled, posted = [], [], -1, 0, 0

    for t in cell.tk:
        if t['ms'] < busy:
            continue
        posted += 1
        r = SC.resolve(t, cell.M, cell.expire_ms, horizon_ms, be_r)
        if not r:
            continue
        expired = (r['outcome'] == 'expired' or r.get('r') is None)
        true_end = r.get('end_ms', t['ms']) if expired else r['end_ms']
        # THE ORDER IS LIVE from the bar it is posted until it fills, or until
        # the expiry window shuts. That window is the only place a cancel can
        # happen; after the fill this is the question already answered.
        live_until = true_end if expired else r['fill_ms']
        side = 1 if t['side'] == 'buy' else -1
        j0 = int(np.searchsorted(opens, t['ms'], side='left'))

        cancel_at = None
        if arm in ('cancel_hold', 'cancel_free'):
            j = j0
            while j < len(opens) and opens[j] + frame <= live_until:
                against = (not up_trend[j]) if side > 0 else up_trend[j]
                if against:
                    cancel_at = int(opens[j] + frame)
                    delays.append(max(0, j - j0))
                    break
                j += 1
        elif arm == 'random_free' and draws and rnd.random() < (rate or 0):
            # CAUSAL: whether to cancel, and after how many bars, are BOTH
            # decided when the order is posted. The window check reads the fill
            # only to ask which arrived first, which is what really happens.
            #
            # THE RATE GATE IS NOT OPTIONAL. Without it the control drew a delay
            # for every ticket and cancelled 532 of 1045 on gold 4h against the
            # witness's 86 -- six times the rate, which compares "cancel on a
            # reversal" with "cancel most orders" and says nothing about the
            # reversal.
            #
            # AND THE DRAW RETRIES. Gating on the rate alone then taking one
            # delay undershot instead, 28 against 86: a ticket that fills
            # quickly rejects most delays, so the fit was silently eating the
            # rate. Redrawing a delay that does not fit separates the two --
            # the rate decides WHETHER, the draw only decides WHEN.
            for _ in range(8):
                j = j0 + int(rnd.choice(draws))
                if j0 <= j < len(opens) and opens[j] + frame <= live_until:
                    cancel_at = int(opens[j] + frame)
                    break

        if cancel_at is not None:
            cancelled += 1
            # NO TRADE AT ALL -- not a zero. The cell is freed at the cancel if
            # this arm frees, and otherwise stays busy exactly as long as the
            # untouched ticket would have kept it.
            busy = cancel_at if free else true_end
            continue

        if expired:
            busy = true_end
            continue
        busy = true_end
        tag = 'anyway' if (taken_base is None or t['ms'] in taken_base) else 'freed'
        out.append((t['ms'], float(r['r']), float(t['atr']), float(t['risk']), tag))
    return out, delays, cancelled, posted


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


ROW = '%-4s %-13s %-4s %6s %5s %8s %8s %7s %6s | %8s %8s %8s %8s | %s'


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
    ap.add_argument('--tfs', nargs='+', default=['5m', '15m', '30m', '1h', '4h'])
    ap.add_argument('--from', dest='start', default='2017-01-01')
    ap.add_argument('--to', dest='end', default='2026-09-14')
    args = ap.parse_args()
    years = (pd.Timestamp(args.end) - pd.Timestamp(args.start)).days / 365.25

    print('%s %s..%s  stop 5 ATR, TP1, fixed 12, break-even on 30m/1h, '
          '1m resolution, no swap' % (args.symbol, args.start, args.end))
    print('witness: EMA20/50 against the ticket, on a closed bar, BEFORE it fills')
    print(ROW % ('tf', 'arm', 'cost', 'fills', 'win%', 'median', 'mean', 'R/yr',
                 'dd R', *[e[0] for e in R.ERAS], 'vs base: eras up, R/yr'))
    verdict = {}
    for tf in args.tfs:
        sys.stderr.write('%s: loading\n' % tf)
        cell = Cell(args.symbol, tf, args.start, args.end, None, 5.0, 20, 20, 50, 12)
        bars = load(args.symbol, tf, args.start, args.end)
        hz = R.HORIZON_H.get(tf, 240) * 3600 * 1000
        be = breakeven_r(tf)

        runs, stats = {}, {}
        sys.stderr.write('  base\n')
        runs['base'], _, _, posted = walk(cell, bars, 'base', hz, be)
        taken = set(x[0] for x in runs['base'])

        sys.stderr.write('  cancel_hold\n')
        runs['cancel_hold'], delays, nc_h, _ = walk(
            cell, bars, 'cancel_hold', hz, be, free=False)

        sys.stderr.write('  cancel_free\n')
        runs['cancel_free'], d2, nc_f, posted_f = walk(
            cell, bars, 'cancel_free', hz, be, free=True, taken_base=taken)
        delays = delays or d2

        # MATCHED ON RATE AS WELL AS DELAY, taken from the arm it is controlling
        # for: cancel_free's own cancels over the tickets it was offered. The
        # rate is then CALIBRATED, because freeing the cell changes how many
        # tickets the arm is offered at all -- so the same nominal rate does not
        # produce the same number of cancels. Two passes get inside 10%; the
        # realised counts are printed either way, so a residual mismatch is
        # visible rather than assumed away.
        rate = nc_f / float(max(1, posted_f))
        for attempt in range(3):
            sys.stderr.write('  random_free (rate %.4f)\n' % rate)
            runs['random_free'], _, nc_r, _ = walk(
                cell, bars, 'random_free', hz, be, free=True, draws=delays or [0],
                rnd=random.Random(SEED), taken_base=taken, rate=rate)
            if nc_r and abs(nc_r - nc_f) <= 0.10 * max(1, nc_f):
                break
            if not nc_r:
                break
            rate *= nc_f / float(nc_r)

        stats = {'posted': posted, 'cancel_hold': nc_h, 'cancel_free': nc_f,
                 'posted_free': posted_f, 'random_free': nc_r}

        for label, spread in (('net', cell.spread_px), ('zero', 0.0)):
            base = None
            for arm in ('base', 'cancel_hold', 'cancel_free', 'random_free'):
                tr = runs[arm]
                s = summary(tr if spread else [(m, g, 0.0, rk, tg)
                                               for m, g, a, rk, tg in tr],
                            spread, years)
                if s is None:
                    continue
                if arm == 'base':
                    base = s
                show(tf, arm, label, s, base)
                if label == 'net' and base and s is not base:
                    # THE ERA COUNT, STORED AS WELL AS PRINTED. The first
                    # version of the verdict line below compared medians, R/yr
                    # and the control and FORGOT the 3-of-4 era test this file's
                    # own docstring sets -- so it called 1h a candidate on 2/4.
                    # A bar that lives only in a docstring is not a bar.
                    verdict.setdefault(tf, {})['eras_' + arm] = sum(
                        1 for x, y in zip(s['eras'], base['eras'])
                        if x is not None and y is not None and x > y)
                if arm == 'cancel_free' and label == 'net':
                    for tag in ('anyway', 'freed'):
                        sub = [x for x in tr if x[4] == tag]
                        if sub:
                            ss = summary(sub, spread, years)
                            show(tf, '  .' + tag, label, ss, None)
                            verdict.setdefault(tf, {})[tag] = ss['med']
                if label == 'net':
                    verdict.setdefault(tf, {})[arm] = (s['ryr'], s['med'])
        print('%-4s tickets posted %d; cancelled: hold %d (%.0f%%), free %d, '
              'random %d; median cancel delay %d bars'
              % (tf, stats['posted'], stats['cancel_hold'],
                 100.0 * stats['cancel_hold'] / max(1, stats['posted']),
                 stats['cancel_free'], stats['random_free'],
                 statistics.median(delays) if delays else 0))
        sys.stdout.flush()

    print()
    won = []
    for tf, v in verdict.items():
        b, c, r = v.get('base'), v.get('cancel_free'), v.get('random_free')
        eras = v.get('eras_cancel_free')
        # EVERY CLAUSE OF THE BAR IN THE DOCSTRING, including the era test:
        # higher R/yr, higher median era R, at least 3 of 4 eras better, and
        # ahead of the causal control.
        if (b and c and r and eras is not None
                and c[0] > b[0] and c[1] > b[1] and eras >= 3 and c[0] > r[0]):
            won.append(tf)
    for tf, v in sorted(verdict.items()):
        b, c, r = v.get('base'), v.get('cancel_free'), v.get('random_free')
        if b and c and r:
            print('  %-4s R/yr %+6.1f -> %+6.1f (control %+6.1f), median %+0.4f -> '
                  '%+0.4f, eras %d/4  %s'
                  % (tf, b[0], c[0], r[0], b[1], c[1],
                     v.get('eras_cancel_free') or 0,
                     'PASS' if tf in won else 'fail'))
    print('cancel_free cleared every clause of the bar on %d frame(s): %s -> %s'
          % (len(won), ', '.join(won) or '-',
             'CANDIDATE' if len(won) >= 2 else 'NOT ADOPTED'))
    return 0


if __name__ == '__main__':
    sys.exit(main())
