#!/usr/bin/env python
"""
Score the signals that were actually ANNOUNCED, forward, against what happened.

    python tools/score_signals.py               score everything, print a table
    python tools/score_signals.py --open        only signals still running
    python tools/score_signals.py --csv out.csv also write a row per signal

WHY THIS EXISTS. `data/signal_journal.jsonl` records every signal Telegram was
told about. Until this file, NOTHING read it: the only other ledger is the
dedupe file, which stores keys so a signal is not sent twice and answers no
question about whether sending it was worth anything. So "is the live alerting
doing anything?" was unanswerable no matter how long it ran, and would have
stayed unanswerable.

IT REPRODUCES THE RULE, IT DOES NOT APPROXIMATE IT. The exit is the strategy's
own, rebuilt from bars using the parameters stored with each signal:

    exit_lo = low.rolling(exit).min().shift(1)      # long exits on close < that
    exit_hi = high.rolling(exit).max().shift(1)     # short exits on close > that

taken from `sim/strategies/donchian.py`, not restated from memory. A frozen
`channel_exit` from the moment the signal fired would be the wrong level one bar
later, which is exactly the kind of quiet mismatch that makes a live number
disagree with a backtest for reasons nobody can find.

WHAT IT IS NOT. This is not a backtest and must not be read as one. It scores a
handful of live announcements with no placebo, no era split and no matched
control -- the machinery in `sim/` exists because none of those can be skipped
when you want to know whether an edge is real. What this answers is narrower and
currently more useful: did the live path do what the measured path said it
would? A disagreement here means the live system is broken somewhere between the
rule and the message, and that is worth knowing before any number is trusted.

SMALL n IS THE POINT, EARLY ON. One or two closed signals is a track record of
one or two, and the table says so rather than computing an average that reads as
if it meant something.
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding='utf-8', errors='replace')
    except (AttributeError, ValueError):
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JOURNAL = os.path.join(ROOT, 'data', 'signal_journal.jsonl')
BRIDGE = os.environ.get('DNFX_BRIDGE', 'http://127.0.0.1:8765')

#: THE BRIDGE IGNORES `n` AND ALWAYS RETURNS 1000 BARS. Measured, not assumed:
#: asking for 500, 1000, 2000 and 5000 all return exactly 1000. The parameter is
#: still sent in case that changes, but nothing here may depend on getting more.
#:
#: That fixed window is the scorer's real limit, and it bites unevenly because
#: 1000 BARS IS A DIFFERENT AMOUNT OF TIME ON EVERY FRAME: about 236 days on 4h,
#: but roughly 17 hours on 1m. So a 4h signal stays scorable for months while a
#: 1m signal falls out of reach within a day of firing. Run this at least daily
#: if the fast cells are announcing, or their record is lost rather than bad.
BARS = 1000


def load_journal(path=JOURNAL):
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path, encoding='utf-8') as fh:
        for n, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except ValueError:
                # One malformed line must not cost the whole record. Say which.
                print('journal line %d is not JSON, skipped' % n, file=sys.stderr)
    return rows


def bars(symbol, tf, n=BARS, timeout=90):
    q = urllib.parse.urlencode({'symbol': symbol, 'tf': tf, 'n': n})
    with urllib.request.urlopen(BRIDGE + '/bars?' + q, timeout=timeout) as r:
        return json.loads(r.read().decode()).get('bars') or []


def to_ms(bar_time):
    """`bar_time` is UTC, formatted the way the bridge reports it."""
    try:
        dt = datetime.strptime(bar_time, '%Y-%m-%d %H:%M:%S')
    except (TypeError, ValueError):
        return None
    return int(dt.replace(tzinfo=timezone.utc).timestamp() * 1000)


def score_one(row, rows):
    """Walk one announced signal forward. Returns a dict, never raises.

    ENTRY IS THE NEXT BAR'S OPEN, matching the rule: it decides on a close and
    fills on the open after. `est_entry` -- the modelled fill, carrying spread
    and slippage -- is preferred when it was recorded, because that is the price
    the message quoted and the one worth holding the system to.

    THE STOP IS CHECKED BEFORE THE EXIT, and intrabar. `sim` measured that the
    stop never waits for a close, and scoring it as if it did would flatter
    every losing trade by however far price came back.
    """
    out = {'key': row.get('key'), 'symbol': row.get('symbol'), 'tf': row.get('tf'),
           'action': row.get('action'), 'bar_time': row.get('bar_time'),
           'state': 'open', 'r': None, 'mfe_r': None, 'mae_r': None,
           'bars_held': 0, 'why': ''}

    side = row.get('action')
    if side not in ('buy', 'sell'):
        out['state'] = 'skipped'
        out['why'] = 'not an entry'
        return out

    t0 = to_ms(row.get('bar_time'))
    if t0 is None:
        out['state'] = 'skipped'
        out['why'] = 'unparseable bar_time'
        return out

    idx = next((i for i, b in enumerate(rows) if b['t'] == t0), None)
    if idx is None:
        out['state'] = 'skipped'
        # A signal older than the window the bridge serves is not an error, it
        # is simply out of reach; saying so beats reporting it as unresolved.
        # The span is named because "1000 bars" means months on 4h and hours on
        # 1m, and the difference is the whole reason a signal aged out.
        span = ''
        if rows:
            days = (rows[-1]['t'] - rows[0]['t']) / 86400000.0
            span = ' (%d bars, %.0f days)' % (len(rows), days)
        out['why'] = 'aged out of the bridge window%s' % span
        return out
    if idx + 1 >= len(rows):
        out['why'] = 'no bar after the signal yet'
        return out

    params = row.get('params') or {}
    ex = int(params.get('exit') or 10)

    entry = row.get('est_entry')
    if not isinstance(entry, (int, float)):
        entry = rows[idx + 1]['o']
    stop = row.get('stop')

    # R needs a stop distance. Without one the trade has no unit and is reported
    # in price only, rather than being given a made-up denominator.
    unit = abs(entry - stop) if isinstance(stop, (int, float)) else None

    best = worst = entry
    for j in range(idx + 1, len(rows)):
        b = rows[j]
        best = max(best, b['h']) if side == 'buy' else min(best, b['l'])
        worst = min(worst, b['l']) if side == 'buy' else max(worst, b['h'])
        out['bars_held'] = j - idx

        if unit:
            if side == 'buy' and b['l'] <= stop:
                return _close(out, row, entry, stop, unit, side, 'stop', best, worst)
            if side == 'sell' and b['h'] >= stop:
                return _close(out, row, entry, stop, unit, side, 'stop', best, worst)

        # The rule's own exit, rebuilt: the extreme of the `ex` bars BEFORE this
        # one (the .shift(1) in the strategy), compared against this close.
        if j - ex >= 0:
            window = rows[j - ex:j]
            if side == 'buy' and b['c'] < min(w['l'] for w in window):
                return _close(out, row, entry, b['c'], unit, side, 'channel', best, worst)
            if side == 'sell' and b['c'] > max(w['h'] for w in window):
                return _close(out, row, entry, b['c'], unit, side, 'channel', best, worst)

    # Still running. Mark it to date so an open trade is not invisible.
    out['why'] = 'still open'
    if unit:
        last = rows[-1]['c']
        out['r'] = ((last - entry) if side == 'buy' else (entry - last)) / unit
        out['mfe_r'] = abs(best - entry) / unit
        out['mae_r'] = -abs(worst - entry) / unit
    return out


def _close(out, row, entry, px, unit, side, why, best, worst):
    out['state'] = 'closed'
    out['why'] = why
    out['exit_px'] = px
    if unit:
        out['r'] = ((px - entry) if side == 'buy' else (entry - px)) / unit
        out['mfe_r'] = abs(best - entry) / unit
        out['mae_r'] = -abs(worst - entry) / unit
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--open', action='store_true', help='only signals still running')
    ap.add_argument('--csv', help='also write one row per signal to this file')
    ap.add_argument('--quiet', action='store_true',
                    help='print only the summary line; for scheduled runs')
    args = ap.parse_args()

    journal = load_journal()
    if not journal:
        print('no signals journalled yet — %s is empty or absent' % JOURNAL)
        print('It fills as signal_alert.py announces; nothing is backfilled,')
        print('because a signal that was never sent was never a live signal.')
        return 0

    cache, scored = {}, []
    for row in journal:
        cell = (row.get('symbol'), row.get('tf'))
        if cell not in cache:
            try:
                cache[cell] = bars(*cell)
            except (urllib.error.URLError, OSError, ValueError) as err:
                print('%s %s: bars unavailable (%s)' % (cell + (err,)), file=sys.stderr)
                cache[cell] = []
        scored.append(score_one(row, cache[cell]))

    if not args.quiet:
        shown = [s for s in scored if not args.open or s['state'] == 'open']
        fmt = '%-11s %-4s %-5s %-17s %-8s %-26s %7s %7s %7s %5s'
        print(fmt % ('symbol', 'tf', 'side', 'bar (UTC)', 'state', 'why',
                     'R', 'MFE R', 'MAE R', 'bars'))
        num = lambda v: '%7.2f' % v if isinstance(v, (int, float)) else '      -'
        for s in shown:
            print(fmt % (s['symbol'], s['tf'], s['action'], s['bar_time'],
                         s['state'], s['why'][:26], num(s['r']), num(s['mfe_r']),
                         num(s['mae_r']), s['bars_held']))
        print()

    closed = [s for s in scored if s['state'] == 'closed' and isinstance(s['r'], float)]
    print('%d journalled · %d closed · %d open · %d unscorable'
          % (len(scored), len(closed),
             sum(1 for s in scored if s['state'] == 'open'),
             sum(1 for s in scored if s['state'] == 'skipped')))
    if closed:
        tot = sum(s['r'] for s in closed)
        wins = sum(1 for s in closed if s['r'] > 0)
        print('closed net %+.2f R, mean %+.2f R, %d/%d won'
              % (tot, tot / len(closed), wins, len(closed)))
        # SAID OUT LOUD rather than left for the reader to remember. A mean of
        # three trades is a mean of three trades, and this table is the exact
        # shape that invites reading it as a result.
        if len(closed) < 30:
            print('n=%d. This is a track record, not a measurement — no placebo,'
                  % len(closed))
            print('no era split, no matched control. See sim/ for those.')

    if args.csv:
        import csv
        cols = ['key', 'symbol', 'tf', 'action', 'bar_time', 'state', 'why',
                'r', 'mfe_r', 'mae_r', 'bars_held']
        with open(args.csv, 'w', newline='', encoding='utf-8') as fh:
            w = csv.DictWriter(fh, fieldnames=cols, extrasaction='ignore')
            w.writeheader()
            w.writerows(scored)
        print('wrote %s' % args.csv)
    return 0


if __name__ == '__main__':
    sys.exit(main())
