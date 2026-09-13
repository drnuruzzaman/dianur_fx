#!/usr/bin/env python
"""
score_external.py — score somebody ELSE'S signals against real bars.

    python tools/score_external.py --list
    python tools/score_external.py                 # score everything unscored
    python tools/score_external.py --all           # rescore from scratch

WHY THIS EXISTS. A signal room shows you its winners. The panel that prompted
this had issued 11,583 signals and displayed three, two of which had won. That
is not evidence of anything, and it is not falsifiable either -- which is the
actual problem. Logging the signals as they appear and scoring them against the
bars turns a marketing display into a measurable claim.

THE INPUT IS HAND-LOGGED, deliberately. These signals exist inside a video
stream, not a page, so scraping them would mean OCR on frames -- fragile, and
against most providers' terms. Twenty or thirty entries typed as they appear is
enough to bound a hit rate, and it costs nothing but attention.

    data/external_signals.jsonl, one JSON object per line:
      {"source":"lisa", "id":"11583", "symbol":"XAUUSD.a", "side":"buy",
       "order":"stop", "entry":4392.72, "sl":4389.14,
       "tp":[4395.74,4397.74,4401.64], "t":"2026-09-09T05:41:00Z"}

    `order` is limit | stop | market -- it decides how the entry FILLS, and
    getting it wrong scores a trade that never existed.

HOW A TRADE IS SCORED. From the signal bar forward, on 1m bars:

    1  FILL     a pending order fills when a bar's range touches `entry`.
                Unfilled after `--expire-min` is recorded as `expired`, not as
                a loss -- an order that never opened is not a losing trade.
    2  OUTCOME  walk forward until the bar's range touches SL or a TP.

WHEN ONE BAR CONTAINS BOTH SL AND TP, the 1m bar cannot say which came first,
and the honest answer is that we do not know. Those are scored as the WORSE
outcome and counted separately as `ambiguous`. Scoring them the good way is how
a backtest of somebody's signals comes out flattering; reporting the count is
how the reader can see how much of the result rests on the assumption.

WHY THE FILL TIME IS THE WHOLE BALLGAME, and the first thing this tool caught.

A provider's panel reported signal #11582 -- SELL LIMIT 4387.35, SL 4390.61,
TP3 4379.61 -- as a TP3 win, +77.4 pips. Scored against Pepperstone 1m bars for
2026-09-09 it was a LOSS, and not marginally: after the limit filled at 05:12 it
never traded below 4383.22, and the stop went at 05:37. Checked against EVERY
bar where price could have filled that order between 02:00 and 05:40, the stop
came first in all twenty-one of them.

The explanation is not that the price data disagrees -- their own displayed
quotes matched this feed to about 0.2 (4386.61 vs 4386.54 at 05:24:58, 4395.40
vs 4395.62 at 05:44:34). It is that all three of their take-profits were touched
BEFORE the order filled: the market fell to 4376.17 while the sell limit was
still resting above it at 4387.35, then rose into the entry and carried on into
the stop.

So the levels are counted from SIGNAL time rather than from FILL time. For a
pending order resting away from the market that is a systematic upward bias --
price very often reaches the target before it reaches the entry, and an order
that never opened cannot have taken profit. It is a methodology error, not
necessarily a dishonest one, and it inflates precisely the trades a signal room
posts most: limits placed away from price.

Which is why step 1 below exists at all, and why `expired` is its own outcome.

R IS AGAINST THE PROVIDER'S OWN STOP. entry-to-SL is 1R, so the numbers are
comparable with everything else in this project, and comparable across
providers who quote different pip conventions.
"""
import argparse
import io
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JOURNAL = os.path.join(ROOT, 'data', 'external_signals.jsonl')
SCORED = os.path.join(ROOT, 'data', 'external_scored.jsonl')
BRIDGE = 'http://127.0.0.1:8765'


def bars(symbol, tf, count, base, timeout=120):
    q = urllib.parse.urlencode({'symbol': symbol, 'tf': tf, 'count': count,
                                'months': 0})
    with urllib.request.urlopen('%s/bars?%s' % (base, q), timeout=timeout) as r:
        return json.load(r).get('bars') or []


def load_jsonl(path):
    out = []
    if not os.path.exists(path):
        return out
    with io.open(path, encoding='utf-8') as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def score_one(sig, rows, expire_min):
    """Walk 1m bars from the signal time and report what happened."""
    t0 = datetime.fromisoformat(sig['t'].replace('Z', '+00:00'))
    side = 1 if str(sig['side']).lower() == 'buy' else -1
    entry, sl = float(sig['entry']), float(sig['sl'])
    tps = [float(x) for x in (sig.get('tp') or [])]
    risk = abs(entry - sl)
    if not risk:
        return {'outcome': 'bad_input', 'why': 'entry equals stop'}

    fwd = [b for b in rows if b['t'] >= int(t0.timestamp() * 1000)]
    if not fwd:
        return {'outcome': 'no_bars',
                'why': 'no bars at or after %s' % sig['t']}

    order = str(sig.get('order', 'market')).lower()
    fill_i, fill_t = None, None
    if order == 'market':
        fill_i, fill_t = 0, fwd[0]['t']
    else:
        deadline = int((t0 + timedelta(minutes=expire_min)).timestamp() * 1000)
        for i, b in enumerate(fwd):
            if b['t'] > deadline:
                break
            if b['l'] <= entry <= b['h']:
                fill_i, fill_t = i, b['t']
                break
        if fill_i is None:
            return {'outcome': 'expired', 'why':
                    'never touched %s within %d min' % (entry, expire_min)}

    best = 0                       # highest TP index reached
    for b in fwd[fill_i:]:
        hit_sl = (b['l'] <= sl) if side > 0 else (b['h'] >= sl)
        reach = 0
        for k, tp in enumerate(tps, start=1):
            if (b['h'] >= tp) if side > 0 else (b['l'] <= tp):
                reach = k
        if reach:
            best = max(best, reach)
        if hit_sl and reach:
            # THE BAR CANNOT SAY WHICH CAME FIRST. Take the worse one.
            return {'outcome': 'sl', 'ambiguous': True, 'tp_reached': best,
                    'exit_price': sl, 'r': -1.0,
                    'exit_t': b['t'], 'bars_held': fwd.index(b) - fill_i,
                    'fill_t': fill_t}
        if hit_sl:
            return {'outcome': 'sl', 'ambiguous': False, 'tp_reached': best,
                    'exit_price': sl, 'r': -1.0,
                    'exit_t': b['t'], 'bars_held': fwd.index(b) - fill_i,
                    'fill_t': fill_t}
        if reach == len(tps) and tps:
            px = tps[-1]
            return {'outcome': 'tp%d' % reach, 'ambiguous': False,
                    'tp_reached': reach, 'exit_price': px,
                    'r': round(abs(px - entry) / risk, 4),
                    'exit_t': b['t'], 'bars_held': fwd.index(b) - fill_i,
                    'fill_t': fill_t}

    last = fwd[-1]
    return {'outcome': 'open', 'ambiguous': False, 'tp_reached': best,
            'exit_price': last['c'],
            'r': round((last['c'] - entry) * side / risk, 4),
            'exit_t': last['t'], 'bars_held': len(fwd) - fill_i,
            'fill_t': fill_t}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--base', default=BRIDGE)
    ap.add_argument('--journal', default=JOURNAL)
    ap.add_argument('--serial', action='store_true',
                    help='one live order at a time: skip a signal posted while '
                         'the previous one was still open')
    ap.add_argument('--expire-min', type=int, default=60,
                    help='a pending order unfilled this long is `expired`')
    ap.add_argument('--count', type=int, default=5000,
                    help='1m bars to pull per symbol')
    ap.add_argument('--list', action='store_true')
    args = ap.parse_args()

    sigs = load_jsonl(args.journal)
    if not sigs:
        print('no signals in %s' % args.journal)
        print(__doc__.split('    data/external_signals.jsonl')[1].split('\n\n')[0])
        return 1
    if args.list:
        for s in sigs:
            print('%-8s %-7s %-9s %-5s %-6s entry %-10s sl %-10s tp %s'
                  % (s.get('source'), s.get('id'), s.get('symbol'),
                     s.get('side'), s.get('order'), s.get('entry'),
                     s.get('sl'), s.get('tp')))
        return 0

    cache = {}
    rows_out = []
    # ONE LIVE ORDER AT A TIME, when asked for. Our own generator journals a
    # ticket on EVERY bar -- that is deliberate, the raw record stays complete --
    # so scoring all of them would count one idea as many, which is exactly the
    # error that made the first scalper backtest look plausible. A provider's
    # feed is usually already one-at-a-time, hence the flag rather than always.
    busy = {}
    skipped_serial = 0
    for s in sorted(sigs, key=lambda x: x.get('t') or ''):
        sym = s['symbol']
        if sym not in cache:
            cache[sym] = bars(sym, '1m', args.count, args.base)
        if args.serial:
            key = '%s|%s' % (sym, s.get('tf') or '')
            t0 = datetime.fromisoformat(s['t'].replace('Z', '+00:00'))
            if busy.get(key) and t0 < busy[key]:
                skipped_serial += 1
                continue
        res = score_one(s, cache[sym], args.expire_min)
        if args.serial and res.get('exit_t'):
            busy['%s|%s' % (sym, s.get('tf') or '')] = datetime.fromtimestamp(
                res['exit_t'] / 1000, tz=timezone.utc)
        rows_out.append(dict(s, **{'result': res}))

    fmt = '%-7s %-5s %-6s %10s %10s %-8s %6s %7s  %s'
    print(fmt % ('id', 'side', 'order', 'entry', 'sl', 'outcome', 'TP', 'R', 'note'))
    print('-' * 88)
    scored = []
    for r in rows_out:
        x = r['result']
        note = x.get('why', '')
        if x.get('ambiguous'):
            note = 'SL and TP in one bar - scored as the loss'
        print(fmt % (r.get('id'), r.get('side'), r.get('order'), r.get('entry'),
                     r.get('sl'), x['outcome'],
                     x.get('tp_reached', '-'),
                     ('%+.2f' % x['r']) if 'r' in x else '-', note))
        if x['outcome'] in ('sl', 'open') or x['outcome'].startswith('tp'):
            scored.append(x)

    if args.serial and skipped_serial:
        print('%d signals skipped: posted while the previous one was still open'
              % skipped_serial)
    closed = [x for x in scored if x['outcome'] != 'open']
    print('-' * 88)
    if closed:
        rs = [x['r'] for x in closed]
        wins = [x for x in rs if x > 0]
        amb = sum(1 for x in closed if x.get('ambiguous'))
        avg = sum(rs) / len(rs)
        print('scored %d  |  win %d (%.0f%%)  |  avg %+.3f R  |  total %+.2f R'
              % (len(rs), len(wins), 100.0 * len(wins) / len(rs), avg, sum(rs)))
        if wins:
            w = sum(wins) / len(wins)
            be = 1.0 / (w + 1.0)
            print('avg win %+.2f R against a 1R loss -> break-even hit rate %.0f%%'
                  % (w, 100 * be))
        if amb:
            print('%d of %d were AMBIGUOUS (SL and TP in the same 1m bar) and '
                  'scored as losses' % (amb, len(rs)))
        print('\nTHIS IS A SAMPLE, NOT A VERDICT. %d trades cannot separate a '
              'real edge\nfrom a run of luck in either direction.' % len(rs))
    else:
        print('nothing closed yet')

    with io.open(SCORED, 'w', encoding='utf-8') as fh:
        for r in rows_out:
            fh.write(json.dumps(r) + '\n')
    print('\nwrote %s' % os.path.relpath(SCORED, ROOT))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
