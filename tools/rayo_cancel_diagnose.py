#!/usr/bin/env python
"""
rayo_cancel_diagnose.py -- WHAT does cancelling a resting order actually remove?

    python tools/rayo_cancel_diagnose.py
    python tools/rayo_cancel_diagnose.py --tfs 5m

RESEARCH ONLY. Touches no config and no live tool.

THE PUZZLE. In tools/rayo_cancel_pending_eval.py the `cancel_hold` arm --
cancel the order on an EMA reversal but keep the cell busy exactly as long as
the untouched ticket would have -- is nearly a no-op on every gold frame
(-0.7 to +0.4 R/yr). Yet `cancel_free`, the same cancels with the cell
released, is worth +6.3 R/yr on 5m and beats a rate- and delay-matched causal
control. Cancelling either removes bad trades or it does not; those two results
cannot both be about the trades removed.

So this counts them. On base's own cursor, every ticket the account accepts is
sorted into three boxes:

  kept        the witness never fired before the fill; the trade happened
  cancelled, would have FILLED     the cancel really did remove a trade
  cancelled, would have EXPIRED    the cancel removed NOTHING -- that order was
                                   never going to fill, and all the cancel did
                                   was hand the cell back early

THE THIRD BOX IS THE WHOLE QUESTION. If it holds most of the cancels, then
`cancel_hold` is neutral because it is barely doing anything, and `cancel_free`
is not "avoiding bad fills" at all -- it is a QUEUE RESHUFFLE, which is a much
weaker claim and a much easier one to overfit. That distinction decides whether
the 5m result is a reason to ship anything.

ONE CURSOR, base's. Every arm in the eval disagrees about which tickets exist,
so counting inside any of them would mix the question with the answer. Here the
account takes exactly what base takes, and the witness is only ASKED, never
acted on -- so the three boxes partition one fixed population.
"""

import argparse
import os
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


def diagnose(cell, bars, horizon_ms, be_r, years):
    frame = TF_MIN[cell.tf] * 60000
    opens = bars.index.view('int64') // 10 ** 6
    ef = ema(bars['close'], 20).to_numpy(float)
    es = ema(bars['close'], 50).to_numpy(float)
    up_trend = ef > es
    box = {'kept': [], 'cancelled_filled': [], 'cancelled_expired': 0}
    busy, posted = -1, 0

    for t in cell.tk:
        if t['ms'] < busy:
            continue
        posted += 1
        r = SC.resolve(t, cell.M, cell.expire_ms, horizon_ms, be_r)
        if not r:
            continue
        expired = (r['outcome'] == 'expired' or r.get('r') is None)
        true_end = r.get('end_ms', t['ms']) if expired else r['end_ms']
        live_until = true_end if expired else r['fill_ms']
        side = 1 if t['side'] == 'buy' else -1
        j = int(np.searchsorted(opens, t['ms'], side='left'))

        fired = False
        while j < len(opens) and opens[j] + frame <= live_until:
            if (not up_trend[j]) if side > 0 else up_trend[j]:
                fired = True
                break
            j += 1

        # THE CURSOR IS BASE'S WHATEVER THE WITNESS SAID. That is what makes
        # the three boxes a partition of one population rather than three
        # different histories.
        busy = true_end
        if not fired:
            if not expired:
                box['kept'].append(net(cell, t, r['r']))
        elif expired:
            box['cancelled_expired'] += 1
        else:
            box['cancelled_filled'].append(net(cell, t, r['r']))
    return box, posted


def net(cell, t, gross):
    return float(gross) - (cell.spread_px + 2 * 0.02 * t['atr']) / t['risk']


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--symbol', default='XAUUSD.a')
    ap.add_argument('--tfs', nargs='+', default=['5m', '15m', '30m', '1h', '4h'])
    ap.add_argument('--from', dest='start', default='2017-01-01')
    ap.add_argument('--to', dest='end', default='2026-09-14')
    args = ap.parse_args()
    years = (pd.Timestamp(args.end) - pd.Timestamp(args.start)).days / 365.25

    print('%s %s..%s  on BASE\'s cursor; the witness is asked, never acted on'
          % (args.symbol, args.start, args.end))
    print('%-4s %8s %8s | %10s %9s %9s | %10s %9s %9s | %10s %s'
          % ('tf', 'accepted', 'cancels', 'kept n', 'mean R', 'R/yr',
             'cx FILLED', 'mean R', 'R/yr', 'cx EXPIRED', 'of cancels'))
    for tf in args.tfs:
        sys.stderr.write('%s: loading\n' % tf)
        cell = Cell(args.symbol, tf, args.start, args.end, None, 5.0, 20, 20, 50, 12)
        bars = load(args.symbol, tf, args.start, args.end)
        hz = R.HORIZON_H.get(tf, 240) * 3600 * 1000
        box, posted = diagnose(cell, bars, hz, breakeven_r(tf), years)
        k, cf, ce = box['kept'], box['cancelled_filled'], box['cancelled_expired']
        cancels = len(cf) + ce
        print('%-4s %8d %8d | %10d %+9.4f %+9.1f | %10d %+9.4f %+9.1f | %10d %9.0f%%'
              % (tf, posted, cancels,
                 len(k), statistics.mean(k) if k else 0, sum(k) / years,
                 len(cf), statistics.mean(cf) if cf else 0, sum(cf) / years,
                 ce, 100.0 * ce / max(1, cancels)))
        sys.stdout.flush()
    print()
    print('cx FILLED is what `cancel_hold` actually removes. cx EXPIRED is the')
    print('part that removes no trade at all and only hands the cell back early.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
