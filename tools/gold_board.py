#!/usr/bin/env python
"""
gold_board.py — one instrument, every timeframe, live state beside the record.

    python tools/gold_board.py                  # XAUUSD, all frames
    python tools/gold_board.py --symbol USDJPY.a
    python tools/gold_board.py --ledger         # every scored signal, per cell
    python tools/gold_board.py --tfs 15m,4h

WHY THIS AND NOT signals_board.py. That one answers "is anything asking for a
trade right now" across every watched cell and stops there. This one takes ONE
instrument and puts three different kinds of fact on the same line:

    what the rule says now          from /signal, asked on a flat basis
    what it has actually done       from /signal/recent, scored from the FILL
    what it was measured at         the pre-registered expectation

Those three disagree constantly and that is the point. A cell can be 0 for 6
this week and still be the one with the best measured edge -- 5m gold was
exactly that the day this was written -- and a reader who only ever sees the
recent window will chase whichever cell got lucky.

THE WINDOWS ARE NOT THE SAME LENGTH, and the table says so in its own column.
/signal/recent scans a fixed number of DAYS per timeframe, capped at 2000 bars,
so 1m covers about a day and 1d covers four months. Net R is comparable down a
column only within one row's own history -- never across rows. Printing them
side by side without the window would invite exactly that comparison.

NOTHING IS HARDCODED HERE. Grades and the measured expectations are parsed out
of configs/alerts.json, the same file the alerter and both boards read, so a
re-registration shows up here without anyone remembering to update a table.
"""
import argparse
import io
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ALERTS = os.path.join(ROOT, 'configs', 'alerts.json')
LADDER = ['1m', '5m', '15m', '30m', '1h', '4h', '1d']

#: pulls "OOS +0.2662 / IS +0.1155" out of the pre-registration note
# `[\d.]+` also swallowed the sentence's full stop -- '+0.1896.' is not a
# float. Digits-dot-digits, so the terminator cannot be part of the number.
EXP_RE = re.compile(r'OOS\s*([+-]\d+\.\d+).*?IS\s*([+-]\d+\.\d+)', re.S)


def cells(symbol):
    """{tf: (grade, enabled, (oos, is_) or None)} for one symbol."""
    with io.open(ALERTS, encoding='utf-8') as fh:
        d = json.load(fh)
    out = {}
    for c in d.get('signals', {}).get('watch', []):
        if c.get('symbol') != symbol:
            continue
        m = EXP_RE.search(c.get('note') or '')
        exp = (float(m.group(1)), float(m.group(2))) if m else None
        out[c.get('tf')] = (c.get('grade') or '-', bool(c.get('enabled')), exp)
    return out


def get(base, path, timeout=300, **params):
    q = urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen('%s%s?%s' % (base, path, q), timeout=timeout) as r:
            return json.load(r)
    except Exception as exc:                                # noqa: BLE001
        return {'error': str(exc)[:70]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--base', default='http://127.0.0.1:8765')
    ap.add_argument('--symbol', default='XAUUSD.a')
    ap.add_argument('--tfs', default=','.join(LADDER))
    ap.add_argument('--ledger', action='store_true',
                    help='also print every scored signal per cell')
    args = ap.parse_args()

    reg = cells(args.symbol)
    tfs = [t for t in args.tfs.split(',') if t]

    print('%s - EVERY TIMEFRAME: live signal and scored window' % args.symbol)
    hdr = '%-4s %-11s %-9s %-6s %-15s %5s %6s %8s %7s  %s'
    print(hdr % ('tf', 'grade', 'N', 'now', 'last signal', 'win', 'up/dn',
                 'net R', 'floor', 'measured OOS/IS'))
    print('-' * 104)

    ledgers = []
    for tf in tfs:
        grade, enabled, exp = reg.get(tf, ('-', False, None))
        s = get(args.base, '/signal', symbol=args.symbol, tf=tf, position='flat')
        if s.get('error'):
            print('%-4s %-11s  %s' % (tf, grade, s['error']))
            continue
        r = get(args.base, '/signal/recent', symbol=args.symbol, tf=tf)
        sig = [] if r.get('error') else (r.get('signals') or [])
        days = 0 if r.get('error') else r.get('scanned_days', 0)
        floor = 0.0 if r.get('error') else r.get('cost_floor_r', 0.0)
        closed = [x for x in sig
                  if not x.get('open') and x.get('r_net_est') is not None]
        wins = sum(1 for x in closed if x['r_net_est'] > 0)
        tot = sum(x['r_net_est'] for x in closed)
        last = sig[-1] if sig else None
        p = s.get('params') or {}
        print(hdr % (
            tf, grade + ('' if enabled else '*'),
            '%s/%s' % (p.get('entry'), p.get('exit')),
            (s.get('action') or '-').upper(),
            ('%s %s' % (last['action'].upper(), last['bar_time_server'][5:16]))
            if last else '-',
            '%dd' % days, '%d/%d' % (wins, len(closed) - wins),
            '%+.2f' % tot, '%.3f' % floor,
            ('%+.4f / %+.4f' % exp) if exp else 'not registered'))
        if args.ledger and sig:
            ledgers.append((tf, sig, floor))

    print('-' * 104)
    print("* = disabled.   net R is scored from the FILL, net of that cell's "
          'live cost floor.')
    print('WINDOWS DIFFER IN LENGTH (see `win`): net R compares within a row, '
          'never across rows.')
    print('A 6-to-60 day window says little about expectancy -- the measured '
          'column is the claim.')

    for tf, sig, floor in ledgers:
        print('\n%s %s - every signal in the window (cost floor %.3f R)'
              % (args.symbol, tf, floor))
        print('  %-4s %-17s %10s %10s %-9s %8s' %
              ('side', 'signal (server)', 'close', 'exit', 'reason', 'net R'))
        for x in sig:
            if x.get('open'):
                print('  %-4s %-17s %10.2f %10s %-9s %8s'
                      % (x['action'].upper(), x['bar_time_server'][:16],
                         x['close'], '-', 'OPEN', '-'))
            else:
                print('  %-4s %-17s %10.2f %10.2f %-9s %+8.2f'
                      % (x['action'].upper(), x['bar_time_server'][:16],
                         x['close'], x['exit_price'], x['exit_reason'],
                         x['r_net_est']))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
