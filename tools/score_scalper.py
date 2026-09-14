#!/usr/bin/env python
"""
score_scalper.py — what the Rayo Scalper's LIVE tickets actually did.

    python tools/score_scalper.py                 score new tickets, print a table
    python tools/score_scalper.py --all           rescore everything from scratch
    python tools/score_scalper.py --csv out.csv   one row per scored ticket

WHY THIS EXISTS. `data/scalper_journal.jsonl` had 1,595 tickets in it and NOT
ONE had an outcome. `tools/score_signals.py` reads the retired Donchian ledger
and reproduces a channel exit this rule does not have;
`tools/score_external.py` grades hand-logged third-party signals and writes to
a fixed path that is not this one. So "is the live rule doing what the backtest
said" was unanswerable, and would have stayed unanswerable however long it ran
-- which is the same hole [[live-signals-were-never-scored]] recorded for the
Donchian, reopened for its replacement.

IT REUSES THE BACKTEST'S OWN RESOLVER. `tools/scalper.py resolve()` decides the
fill, races the stop against the ladder and scores an intrabar tie as the loss.
Re-implementing that here would let the forward number and the registered
number disagree for reasons nobody could find, which is the failure this whole
file is meant to close rather than create.

THE CLOCK IS THE BRIDGE'S, NOT THE ARCHIVE'S, and this is not a detail. Stored
research bars are BROKER time (EET/EEST); the journal's `ms` came from the
bridge and is TRUE UTC. Scoring live tickets against archive bars would run two
to three hours out and produce a confident, wrong answer -- the same class of
bug that once had the panel gating a session three hours from the one it was
measured on. 1m bars are pulled from the bridge here for exactly that reason,
which also means this tool NEEDS THE BRIDGE UP and says so rather than scoring
an empty set.

IT NEVER MIXES TWO RULES. Tickets are grouped by (symbol, tf, mode, stop_atr),
because the journal spans a rule change: everything before 2026-09-14 was
posted at a 4.0 ATR stop and everything after at 5.0. Pooling them would
average two different strategies and call it a forward test. Every row carries
the parameters it was posted under, so the split is read from the data rather
than from a date somebody remembers.

ONE LIVE ORDER AT A TIME, PER CELL, because that is how each cell was
registered. The rule reposts a pending order on nearly every bar, so an uptrend
lasting a day proposes the same buy stop a hundred times; scoring all of them
counts one idea a hundred times over and turns a single good break into a
hundred winners. A ticket posted while the previous one is still live is
skipped, not scored.

WHAT IT WILL AND WILL NOT TELL YOU. With a few days of data this is a
sanity check, not a verdict: it can catch the live path posting something the
backtest never would -- wrong levels, wrong side, fills that never happen -- and
it cannot distinguish a real edge from luck at these sample sizes. The `n`
column is there to stop anyone reading it as though it could.
"""

import argparse
import io
import json
import os
import statistics
import sys
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import numpy as np                                              # noqa: E402

from tools import scalper as SC                                 # noqa: E402

JOURNAL = os.path.join(ROOT, 'data', 'scalper_journal.jsonl')
SCORED = os.path.join(ROOT, 'data', 'scalper_scored.jsonl')
BRIDGE = 'http://127.0.0.1:8765'

TF_MIN = {'1m': 1, '5m': 5, '15m': 15, '30m': 30, '1h': 60, '4h': 240,
          '1d': 1440}

#: Hours before an unresolved trade is abandoned. Matches tools/scalp_register.
HORIZON_H = {'1m': 24, '5m': 24, '15m': 24, '30m': 24, '1h': 24,
             '4h': 120, '1d': 720}


def load_jsonl(path):
    out = []
    if not os.path.exists(path):
        return out
    with io.open(path, encoding='utf-8') as fh:
        for n, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except ValueError:
                print('%s line %d is not JSON, skipped' % (path, n),
                      file=sys.stderr)
    return out


def bridge_1m(symbol, count, base, timeout=180):
    q = urllib.parse.urlencode({'symbol': symbol, 'tf': '1m',
                                'count': count, 'months': 0})
    with urllib.request.urlopen('%s/bars?%s' % (base, q), timeout=timeout) as r:
        rows = json.load(r).get('bars') or []
    if not rows:
        return None
    ms = np.array([int(b['t']) for b in rows], dtype=np.int64)
    return (ms,
            np.array([float(b['h']) for b in rows]),
            np.array([float(b['l']) for b in rows]),
            np.array([float(b['c']) for b in rows]))


def costs_for(symbol, tf):
    """Spread in price units, plus what swap costs a night, from the spec.

    THE SAME COST MODEL THE REGISTRY USED, so a live mean net R and a
    registered expected_net_r are the same kind of number. Quoting a live
    figure gross against a registered figure net would make the live path look
    better by exactly the amount that decides whether the cell is worth having.
    """
    from sim.instruments import spec
    sp = spec(symbol, tf)
    pts = (sp.get('spread_points_now') or sp.get('spread_floor_points')
           or (30.0 if 'XAU' in symbol.upper() else 10.0))
    return {'spread_px': sp['point'] * float(pts), 'point': sp['point'],
            'swap_long': float(sp.get('swap_long_points') or 0.0),
            'swap_short': float(sp.get('swap_short_points') or 0.0),
            'roll_wd': int(sp.get('swap_rollover_3days_weekday', 3))}


def swap_r(c, side, risk, fill_ms, exit_ms):
    """Financing in R. Lot size cancels, so this never goes via money."""
    per_night = c['swap_long'] if side == 'buy' else c['swap_short']
    if not per_night:
        return 0.0
    d0 = datetime.fromtimestamp(fill_ms / 1000.0, timezone.utc).date()
    d1 = datetime.fromtimestamp(exit_ms / 1000.0, timezone.utc).date()
    charge = 0.0
    for n in range((d1 - d0).days):
        day = d0 + timedelta(days=n + 1)
        charge += 3.0 if day.weekday() == c['roll_wd'] else 1.0
    return charge * per_night * c['point'] / risk


def registered():
    """expected_net_r per cell, and the stop it was measured at."""
    try:
        with io.open(os.path.join(ROOT, 'configs', 'alerts.json'),
                     encoding='utf-8') as fh:
            sc = json.load(fh)['scalper']
    except (OSError, ValueError, KeyError):
        return {}, None
    exp = {(c['symbol'], c['tf']): c.get('expected_net_r')
           for c in sc.get('watch') or []}
    return exp, (sc.get('measurement') or {}).get('stop_atr')


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--journal', default=JOURNAL)
    ap.add_argument('--out', default=SCORED)
    ap.add_argument('--base', default=BRIDGE)
    ap.add_argument('--count', type=int, default=20000,
                    help='1m bars to pull per symbol')
    ap.add_argument('--all', action='store_true',
                    help='rescore from scratch instead of adding new tickets')
    ap.add_argument('--csv', help='also write one row per scored ticket')
    ap.add_argument('--min-n', type=int, default=1,
                    help='hide cells with fewer than this many fills')
    args = ap.parse_args()

    sigs = load_jsonl(args.journal)
    if not sigs:
        print('no tickets in %s' % os.path.relpath(args.journal, ROOT))
        return 1

    # ONLY A CLOSED TICKET IS DONE. A trade still running when the scorer last
    # ran was written as `open`; treating that as scored would freeze it that
    # way for ever and the ledger would fill up with trades that never ended.
    # Open rows are dropped and recomputed, so they resolve on a later pass.
    done, keep = set(), []
    if not args.all:
        for r in load_jsonl(args.out):
            if r.get('outcome') in ('sl', 'tp1', 'tp2', 'tp3', 'expired'):
                done.add(r.get('id'))
                keep.append(r)

    # ---- bars, once per symbol, from the bridge ----
    symbols = sorted({s['symbol'] for s in sigs})
    M = {}
    for sym in symbols:
        try:
            M[sym] = bridge_1m(sym, args.count, args.base)
        except Exception as exc:                        # noqa: BLE001
            # THE BRIDGE BEING DOWN IS NOT A RESULT. Saying so and stopping is
            # the difference between "no edge" and "nobody asked".
            print('BRIDGE UNAVAILABLE for %s (%s) -- nothing scored. Is '
                  'serve.py running?' % (sym, exc), file=sys.stderr)
            return 2
        if M[sym] is None:
            print('bridge returned no 1m bars for %s -- nothing scored' % sym,
                  file=sys.stderr)
            return 2

    # ---- group by the rule each ticket was posted under ----
    groups = defaultdict(list)
    for s in sigs:
        groups[(s['symbol'], s['tf'], s.get('mode', 'break'),
                s.get('stop_atr'))].append(s)

    scored, fresh = [], 0
    if not args.all:
        scored = keep

    for key in sorted(groups, key=lambda k: (k[0], TF_MIN.get(k[1], 0), k[3] or 0)):
        sym, tf, mode, stop = key
        rows = sorted(groups[key], key=lambda r: r['ms'])
        cost = costs_for(sym, tf)
        horizon_ms = HORIZON_H.get(tf, 24) * 3600 * 1000
        busy = -1
        for r in rows:
            if r['ms'] < busy:
                continue                     # one live order at a time, per cell
            SC.EXIT_TP[0] = int(r.get('exit_tp') or 1)
            expire_ms = int(r.get('expire_bars') or 12) * TF_MIN[tf] * 60 * 1000
            res = SC.resolve(r, M[sym], expire_ms, horizon_ms)
            if not res:
                continue                     # before the bars we can see
            busy = res.get('end_ms', r['ms'])
            if r.get('id') in done:
                continue
            out = {'id': r.get('id'), 'symbol': sym, 'tf': tf, 'mode': mode,
                   'stop_atr': stop, 'side': r['side'], 'age': r.get('age'),
                   't': r.get('t'), 'ms': r['ms'],
                   'outcome': res['outcome'], 'gross_r': res.get('r'),
                   'ambiguous': bool(res.get('ambiguous')),
                   'fill_ms': res.get('fill_ms'), 'end_ms': res.get('end_ms')}
            if res['outcome'] in ('sl', 'tp1', 'tp2', 'tp3'):
                cr = (cost['spread_px'] + 2 * 0.02 * float(r['atr'])) / float(r['risk'])
                sw = swap_r(cost, r['side'], float(r['risk']),
                            res['fill_ms'], res['end_ms'])
                # NET IS COMPUTED FROM THE ROUNDED PARTS, not rounded after
                # the fact. Otherwise the row does not add up to itself -- the
                # stored net was 1e-5 away from gross - cost + swap -- and a
                # ledger a reader cannot check by arithmetic is a ledger they
                # have to trust. Same rule as the registry's eras.
                out['cost_r'] = round(cr, 5)
                out['swap_r'] = round(sw, 5)
                out['net_r'] = round(res['r'] - out['cost_r'] + out['swap_r'], 5)
            scored.append(out)
            fresh += 1

    tmp = args.out + '.tmp'
    with io.open(tmp, 'w', encoding='utf-8') as fh:
        for x in scored:
            fh.write(json.dumps(x) + '\n')
    os.replace(tmp, args.out)

    # ---- the table ----
    exp_reg, reg_stop = registered()
    by = defaultdict(list)
    for x in scored:
        by[(x['symbol'], x['tf'], x['stop_atr'])].append(x)

    print('LIVE TICKETS, SCORED -- %d in the journal, %d newly scored'
          % (len(sigs), fresh))
    print('one live order at a time PER CELL; costs and swap charged as the '
          'registry charges them')
    print('a few days is a sanity check, not a verdict -- read the n column@'
          .replace('@', ''))
    print()
    print('%-11s %-4s %5s %6s %7s %7s %9s %9s %11s'
          % ('symbol', 'tf', 'stop', 'fills', 'open', 'win%', 'gross R',
             'net R', 'registered'))
    print('-' * 78)
    for key in sorted(by, key=lambda k: (k[0], TF_MIN.get(k[1], 0), k[2] or 0)):
        sym, tf, stop = key
        v = by[key]
        closed = [x for x in v if x.get('net_r') is not None]
        openish = [x for x in v if x['outcome'] == 'open']
        # A CELL WITH ONLY OPEN TRADES STILL EXISTS. Hiding it on the closed
        # count made freshly-registered cells vanish from their own report on
        # the day they were switched on, which reads as "not firing".
        if len(closed) + len(openish) < args.min_n:
            continue
        if not closed:
            print('%-11s %-4s %5s %6d %7d %6s  %9s %9s %11s'
                  % (sym, tf, stop, 0, len(openish), '-', '-', '-',
                     'none closed yet'))
            continue
        reg = exp_reg.get((sym, tf))
        same_rule = (reg is not None and reg_stop is not None
                     and stop == reg_stop)
        print('%-11s %-4s %5s %6d %7d %6s%% %+9.4f %+9.4f %11s'
              % (sym, tf, stop, len(closed), len(openish),
                 ('%.0f' % (100.0 * sum(1 for x in closed if x['gross_r'] > 0)
                            / len(closed))) if closed else '-',
                 statistics.mean([x['gross_r'] for x in closed]),
                 statistics.mean([x['net_r'] for x in closed]),
                 ('%+.4f' % reg) if same_rule else
                 ('%+.4f*' % reg) if reg is not None else '-'))
    print()
    print('* the registered figure was measured at stop %s; a row at a '
          'different stop is NOT comparable to it.' % reg_stop)

    # ---- what the age gate would have done, live ----
    cap = None
    try:
        with io.open(os.path.join(ROOT, 'configs', 'alerts.json'),
                     encoding='utf-8') as fh:
            q = (json.load(fh)['scalper'].get('quality') or {})
        cap = q.get('max_age') if q.get('enforce') else None
    except (OSError, ValueError, KeyError):
        pass
    if cap is not None:
        cur = [x for x in scored
               if x.get('net_r') is not None and x['stop_atr'] == reg_stop]
        fresh_r = [x['net_r'] for x in cur if (x.get('age') or 0) <= cap]
        stale_r = [x['net_r'] for x in cur if (x.get('age') or 0) > cap]
        print()
        print('THE AGE GATE, AGAINST WHAT ACTUALLY HAPPENED (stop %s only):'
              % reg_stop)
        for lab, vals in (('sent (age <= %d)' % cap, fresh_r),
                          ('held (age > %d)' % cap, stale_r)):
            print('  %-22s n=%-5d mean net %s' % (
                lab, len(vals),
                ('%+.4f' % statistics.mean(vals)) if vals else '   n/a'))
        print('  Sorting on a score AFTER the fact is not evidence it works --')
        print('  the gate was adopted on a nine-year queue simulation. This is')
        print('  here to catch it doing the OPPOSITE of what was measured.')

    if args.csv:
        import csv
        cols = ['id', 'symbol', 'tf', 'stop_atr', 'side', 'age', 't',
                'outcome', 'gross_r', 'cost_r', 'swap_r', 'net_r', 'ambiguous']
        with io.open(args.csv, 'w', encoding='utf-8', newline='') as fh:
            w = csv.DictWriter(fh, fieldnames=cols, extrasaction='ignore')
            w.writeheader()
            for x in scored:
                w.writerow(x)
        print('\nwrote %s' % args.csv)

    print('\nwrote %s' % os.path.relpath(args.out, ROOT))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
