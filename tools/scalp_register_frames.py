#!/usr/bin/env python
"""
scalp_register_frames.py -- measure a frame that is NOT in the registry yet.

    python tools/scalp_register_frames.py --symbol XAUUSD.a --tfs 3m 2h

WHY THIS EXISTS. tools/scalp_register.py iterates configs/alerts.json and can
only re-measure cells that are already listed, so a brand-new frame has nowhere
to start: there is no row to filter to with --tfs. This runs the SAME
measurement -- scalp_register.measure, stress_median and verdict, imported, not
copied -- over an arbitrary symbol/timeframe list and prints a registry-shaped
row for it.

IT WRITES NOTHING TO configs/alerts.json. The output goes to --out so the rows
can be read, argued with, and merged deliberately; research does not edit the
live config.

TIMEFRAMES MISSING FROM scalp_portfolio.TF_MIN are patched in at import, so a
frame can be measured before anything else in the repo knows about it. 3m and 2h
were added to that module for real when they joined the registry (2026-09-17);
the setdefault below is now a no-op for them and still matters for the next new
frame.

FINGERPRINT THE BARS FIRST. data/bars has been wrong about its own timeframes
before -- 161 files in the wrong folder -- so this refuses a frame whose modal
bar spacing does not match its label rather than quietly registering a number
measured on the wrong resolution.
"""

import argparse
import collections
import io
import json
import os
import sys
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import tools.scalp_portfolio as SP                             # noqa: E402

SP.TF_MIN.setdefault('3m', 3)
SP.TF_MIN.setdefault('2h', 120)

from sim.instruments import load                               # noqa: E402
from tools import scalp_register as R                          # noqa: E402
import tools.scalper as SC                                     # noqa: E402


def fingerprint(symbol, tf, start, end):
    """(ok, n, first, last, modal_gap_minutes). The archive has lied before."""
    df = load(symbol, tf, start, end)
    gaps = df.index.to_series().diff().dt.total_seconds().div(60).dropna()
    modal = collections.Counter(gaps.astype(int)).most_common(1)[0][0]
    return (modal == SP.TF_MIN[tf], len(df), str(df.index[0].date()),
            str(df.index[-1].date()), modal)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--symbol', default='XAUUSD.a')
    ap.add_argument('--tfs', nargs='+', default=['3m', '2h'])
    ap.add_argument('--stop-atr', type=float, default=5.0)
    ap.add_argument('--from', dest='start', default='2017-01-01')
    ap.add_argument('--to', dest='end', default='2026-09-14')
    ap.add_argument('--session', default='none')
    ap.add_argument('--swing', type=int, default=20)
    ap.add_argument('--fast', type=int, default=20)
    ap.add_argument('--slow', type=int, default=50)
    ap.add_argument('--expire', type=int, default=12)
    ap.add_argument('--exit-tp', type=int, default=1, choices=(1, 2, 3))
    ap.add_argument('--zero-cost', action='store_true',
                    help='measure with NO spread, slippage or swap -- the block '
                         'every registry row carries beside its costed numbers')
    ap.add_argument('--out', default=os.path.join(ROOT, 'runs',
                                                  'scalp_register_newframes.json'))
    args = ap.parse_args()

    SC.EXIT_TP[0] = args.exit_tp
    session = SC.parse_session(args.session)
    cfg_args = SimpleNamespace(stop_atr=args.stop_atr, swing=args.swing,
                               fast=args.fast, slow=args.slow,
                               expire=args.expire, exit_tp=args.exit_tp,
                               start=args.start, end=args.end,
                               session=args.session, zero_cost=args.zero_cost)

    print('FINGERPRINT')
    for tf in args.tfs:
        ok, n, a, b, modal = fingerprint(args.symbol, tf, args.start, args.end)
        print('  %-4s %-8d %s..%s  modal gap %d min  %s'
              % (tf, n, a, b, modal, 'OK' if ok else 'MISLABELLED -- REFUSED'))
        if not ok:
            return 2

    print()
    print('%-12s %-4s %7s %7s %10s %9s %8s %9s  %s'
          % ('symbol', 'tf', 'fills', 'win%', 'median R', 'R/yr', 'dd R',
             '@%.1fx spr' % R.STRESS, 'verdict'))
    print('-' * 96)
    rows = []
    for tf in args.tfs:
        sys.stderr.write('  measuring %s %s ...\n' % (args.symbol, tf))
        m = R.measure(args.symbol, tf, args.start, args.end, session, cfg_args)
        # NO STRESS ARM AND NO VERDICT UNDER --zero-cost, exactly as
        # scalp_register.zero_cost_main does none of either. stress_median()
        # builds its own Cell and charges 1.5x the REAL spread, so comparing it
        # against an uncosted median produced 'spread-fragile, -200% of the
        # edge' -- a sentence about two different questions.
        if (not args.zero_cost and m.get('measurable')
                and m.get('expected_net_r') is not None
                and m['expected_net_r'] > 0 and m['total_r_per_year'] > 0
                and m['n'] >= R.MIN_FILLS):
            sys.stderr.write('    stress arm ...\n')
            m['stress_net_r'] = R.stress_median(args.symbol, tf, args.start,
                                                args.end, session, cfg_args)
        m['_tf'] = tf
        if args.zero_cost:
            trade, thin, note = False, False, None
            row = dict(symbol=args.symbol, tf=tf,
                       **{k: v for k, v in m.items() if k != '_tf'})
        else:
            trade, thin, note = R.verdict(m, False)
            row = dict(symbol=args.symbol, tf=tf, enabled=False,
                       tradeable=trade, control=False, thin=thin, note=note,
                       **{k: v for k, v in m.items() if k != '_tf'})
        rows.append(row)
        if m.get('measurable'):
            st = m.get('stress_net_r')
            print('%-12s %-4s %7d %6.1f%% %+10.4f %+9.1f %8.1f %9s  %s'
                  % (args.symbol, tf, m['n'], m['win_pct'], m['expected_net_r'],
                     m['total_r_per_year'], m['drawdown_r'],
                     ('%+0.4f' % st) if st is not None else '-',
                     '-' if args.zero_cost else
                     ('TRADEABLE' if trade else ('THIN' if thin else 'journal'))))
        else:
            print('%-12s %-4s %7s  not measurable' % (args.symbol, tf, '-'))
        os.makedirs(os.path.dirname(args.out), exist_ok=True)
        with io.open(args.out, 'w', encoding='utf-8') as fh:
            fh.write(json.dumps({'meta': R._meta(cfg_args), 'watch': rows},
                                indent=2, ensure_ascii=False) + '\n')
        sys.stdout.flush()

    print()
    for r in rows:
        if r.get('note'):
            print('%s %s: %s' % (r['symbol'], r['tf'], r['note']))
    print('\nwritten to %s -- configs/alerts.json NOT touched.'
          % os.path.relpath(args.out, ROOT))
    return 0


if __name__ == '__main__':
    sys.exit(main())
