#!/usr/bin/env python
"""
spread_log.py -- record the LIVE spread, to check the one the backtest charges.

    python tools/spread_log.py                      XAUUSD.a, every 30s
    python tools/spread_log.py --symbol USDJPY.a --every 15
    python tools/spread_log.py --report             what has been collected

WHY THIS EXISTS, AND IT IS NOT A DETAIL. On 2026-09-16 the Rayo backtest was
changed to charge the spread RECORDED ON EACH BAR instead of a flat 19 points
captured once from the broker. On gold that column is complete -- 100% of bars
every year since 2017, median rising 5 to 12 points as the price rose -- and the
change flipped the fast cells:

    XAUUSD 1m, net R per fill      flat 19      per-bar
      2017-19                      -0.0624      +0.0374
      2019-21                      -0.0267      +0.0353
      2021-23                      -0.0061      +0.0286

Four eras, one direction, on identical gross. That was either the most
important result this project has produced or a measurement artefact, and one
number decided which: whether MetaTrader's per-bar `spread` field is the spread
you would actually have been charged.

IT IS NOT. ANSWERED 2026-09-16 BY `--paired`, and the answer was unambiguous at
n=20: the live book was ABOVE the recorded value of its own minute in 20 of 20
minutes, median +9.0 points, p10 +7, p90 +11. Not one minute at or below. If
the column were the charge the difference would straddle zero; 20 of 20 in a
four-point band says the recorded value is the LOW end of its minute. Charging
it is charging the best quote available in the minute you traded.

So the flat 19 points the backtest always charged was approximately right --
live samples ran 16-20, median 18 -- and the era table above is dead. Gold 1m
after the measured premium: -0.043, -0.020, -0.013 and +0.014 by era, which is
where the flat charge had it. This file's job is done; it stays because the
question recurs every time somebody notices the recorded column.

WHY IT WAS DOUBTED, AND THE DOUBT WAS RIGHT. MT5 writes one spread value per
bar for a period in which the spread moved continuously. If that value is the
bar's minimum, charging it is charging the best quote of the minute --
systematically optimistic, and most optimistic exactly where it matters, on the
fast cells that trade hundreds of times a week. The first hint was a live
reading of 18 points at a broker hour whose recorded median is 9.

WHAT IT COLLECTS. `spread_points_now` from the bridge's /spec, with a UTC
timestamp, appended to data/spread_log.csv -- no aggregation, because the
question is about the DISTRIBUTION and any summary here would have to guess
which one. `--report` puts it beside the recorded column for the same hours, so
the comparison is like-for-like rather than a live median against an all-hours
one.

IT IS A READER, NOT A TRADER. One GET per tick against a read-only bridge.
"""

import argparse
import csv
import io
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'data', 'spread_log.csv')
BRIDGE = os.environ.get('DNFX_BRIDGE', 'http://127.0.0.1:8765')

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding='utf-8', errors='replace')
    except (AttributeError, ValueError):
        pass


ERR = os.path.join(ROOT, 'data', 'spread_log.err')


def _note_error(exc):
    """One line per failure, so a gap in the CSV has an explanation beside it."""
    try:
        with io.open(ERR, 'a', encoding='utf-8') as fh:
            # % binds tighter than +, so the concatenation needs the parens
            fh.write(('%s %s: %s' + chr(10))
                     % (datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
                        type(exc).__name__, exc))
    except Exception:                                          # noqa: BLE001
        pass                                   # a logger cannot fail on logging


def live_spread(symbol, timeout=8):
    """Points, or None when the bridge cannot say."""
    url = '%s/spec?symbol=%s' % (BRIDGE.rstrip('/'), symbol)
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            d = json.loads(r.read().decode() or '{}')
    except Exception:                                          # noqa: BLE001
        return None
    pts = d.get('spread_points_now')
    if pts is None and d.get('spread_current') and d.get('point'):
        pts = float(d['spread_current']) / float(d['point'])
    return float(pts) if pts is not None else None


def paired(symbol, timeout=30):
    """Each live sample against the RECORDED spread of its own minute.

    THE ONLY VERSION OF THIS COMPARISON THAT CAN SETTLE ANYTHING. `--report`
    puts a live median beside a recorded median for the same broker hour, which
    is better than pooling everything but still compares two DISTRIBUTIONS --
    one built from a few minutes of today, the other from a year of that hour.
    A difference can be the hour, the day, the price level, or the thing being
    tested, and nothing in the table says which.

    Pairing removes every one of those: same instrument, same minute, one
    number each. If MetaTrader's per-bar spread were the charge, the paired
    difference would sit at zero with noise either side. If it is the minute's
    BEST quote -- which is the hypothesis that would invalidate the fast-cell
    result -- then an instantaneous poll lands above it far more often than
    below, and the median difference is the size of the optimism.

    BARS COME FROM THE BRIDGE, not the stored archive, which on this machine
    ends weeks back. The bridge serves 1000 bars and ignores `n`, so this
    covers roughly the last seventeen hours of 1m -- the window the sampler has
    been filling.

    WHAT IT CANNOT DO is prove the column honest on HISTORY. It compares today
    against today; the nine-year result rests on the column behaving the same
    way in 2017, which nothing here can reach. Said plainly rather than left
    for the reader to notice.
    """
    if not os.path.exists(OUT):
        print('nothing logged yet -- %s does not exist' % OUT)
        return 1
    live = {}
    with io.open(OUT, encoding='utf-8') as fh:
        for r in csv.DictReader(fh):
            if r.get('symbol') != symbol:
                continue
            try:
                when = datetime.fromisoformat(r['t'].replace('Z', '+00:00'))
                # floor to the minute: that is the bar a sample belongs to
                key = int(when.timestamp()) // 60 * 60
                live.setdefault(key, []).append(float(r['points']))
            except (ValueError, KeyError):
                continue
    if not live:
        print('nothing logged for %s' % symbol)
        return 1

    q = urllib.parse.urlencode({'symbol': symbol, 'tf': '1m', 'n': 1000})
    try:
        with urllib.request.urlopen(BRIDGE.rstrip('/') + '/bars?' + q,
                                    timeout=timeout) as r:
            bars = json.loads(r.read().decode()).get('bars') or []
    except Exception as exc:                                   # noqa: BLE001
        print('bars unavailable from the bridge: %s' % exc, file=sys.stderr)
        return 2
    if not bars or 's' not in bars[0]:
        print('the bridge is not serving per-bar spread -- restart it so the '
              "'s' field is present (bridge/mt5_bridge.py, 2026-09-16)",
              file=sys.stderr)
        return 2

    rec = {b['t'] // 1000: b['s'] for b in bars if b.get('s')}
    pairs = []
    for key, pts in sorted(live.items()):
        got = rec.get(key)
        if got:
            # the median of the samples in that minute, so a minute polled
            # twice does not count twice
            lv = sorted(pts)
            pairs.append((key, lv[len(lv) // 2], float(got)))

    print('%s -- %d live minutes, %d bars from the bridge, %d PAIRED'
          % (symbol, len(live), len(bars), len(pairs)))
    if not pairs:
        span = ''
        if bars:
            span = ' (bridge window %s .. %s UTC)' % (
                datetime.fromtimestamp(bars[0]['t'] / 1000, timezone.utc)
                .strftime('%d %b %H:%M'),
                datetime.fromtimestamp(bars[-1]['t'] / 1000, timezone.utc)
                .strftime('%d %b %H:%M'))
        print('no minute appears in both%s -- the sampler and the bridge window '
              'do not overlap yet' % span)
        return 1

    diffs = sorted(p[1] - p[2] for p in pairs)
    n = len(diffs)
    med = diffs[n // 2]
    above = sum(1 for d in diffs if d > 0)
    equal = sum(1 for d in diffs if d == 0)
    print()
    print('live minus recorded, per minute:')
    print('  median %+.1f pts   mean %+.1f   p10 %+.1f   p90 %+.1f'
          % (med, sum(diffs) / n, diffs[int(.1 * n)], diffs[int(.9 * n)]))
    print('  live ABOVE the bar in %d of %d minutes (%.0f%%), equal in %d'
          % (above, n, 100.0 * above / n, equal))
    print()
    if n < 60:
        print('n=%d. Not a verdict -- an hour of samples is an hour of one '
              'session.' % n)
    print('ZERO would mean the recorded column IS the charge. A positive median')
    print('means it is the better quote of the minute, and the fast-cell result')
    print('is optimistic by about that much spread.')
    return 0

def report(symbol):
    """The live log against the recorded column, hour by hour.

    HOUR BY HOUR, because the recorded spread on gold runs 8-11 points through
    the liquid day and 17 at 01:00 and 23:00. A pooled live median taken over
    an afternoon against a pooled recorded median taken over nine years would
    compare two different mixtures of hours and call the difference a finding.
    """
    if not os.path.exists(OUT):
        print('nothing logged yet -- %s does not exist' % OUT)
        return 1
    rows = []
    with io.open(OUT, encoding='utf-8') as fh:
        for r in csv.DictReader(fh):
            if r.get('symbol') != symbol:
                continue
            try:
                rows.append((datetime.fromisoformat(r['t'].replace('Z', '+00:00')),
                             float(r['points'])))
            except (ValueError, KeyError):
                continue
    if not rows:
        print('nothing logged for %s' % symbol)
        return 1

    # BROKER HOURS ON BOTH SIDES. The recorded column is indexed in broker
    # server time (EET/EEST) and the log is stamped in true UTC, so comparing
    # them raw would line a 09:00 live sample up against 06:00 or 07:00 of
    # history -- two different hours of the session, which is the one thing
    # this comparison must not do. `to_server_ms` is the same arithmetic
    # js/chart/scalper.js uses.
    from tools._brokerclock import to_server_ms                # noqa: E402
    from sim.instruments import load                           # noqa: E402

    live = {}
    for when, pts in rows:
        ms = int(when.timestamp() * 1000)
        # tz-aware, then read the hour: the broker clock is produced as an
        # offset from UTC, so the naive reading of it IS the broker hour.
        hour = datetime.fromtimestamp(to_server_ms(ms) / 1000.0,
                                      tz=timezone.utc).hour
        live.setdefault(hour, []).append(pts)

    span = (rows[-1][0] - rows[0][0]).total_seconds() / 3600.0
    print('%s -- %d live samples over %.1f h (%s .. %s UTC)'
          % (symbol, len(rows), span,
             rows[0][0].strftime('%d %b %H:%M'), rows[-1][0].strftime('%d %b %H:%M')))

    # The recorded column over the same span of calendar, most recent first --
    # a year, so every hour has enough bars, but not nine, because the spread
    # has trended with the price.
    bars = load(symbol, '5m', '2025-09-01', '2026-12-31')
    rec = bars['spread'].astype(float)
    rec = rec[rec > 0]

    print()
    print('%-6s %10s %10s %10s %8s' % ('hour', 'live med', 'recorded', 'diff',
                                       'live n'))
    tot_l, tot_r = [], []
    for h in sorted(live):
        lv = sorted(live[h])
        lm = lv[len(lv) // 2]
        rr = rec[rec.index.hour == h]
        rm = rr.median() if len(rr) else float('nan')
        print('%-6d %10.1f %10.1f %+10.1f %8d' % (h, lm, rm, lm - rm, len(lv)))
        tot_l.append(lm)
        tot_r.append(rm)
    if tot_l:
        import statistics
        print()
        print('mean of the hourly differences: %+.1f points'
              % statistics.fmean(l - r for l, r in zip(tot_l, tot_r)))
        print('A live median ABOVE the recorded one on the same hours means the')
        print('per-bar column understates what you would be charged, and the')
        print('fast-cell result that rests on it is optimistic by that much.')
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--symbol', default='XAUUSD.a')
    ap.add_argument('--every', type=int, default=30, help='seconds between reads')
    ap.add_argument('--report', action='store_true',
                    help='live vs recorded, per broker hour')
    ap.add_argument('--paired', action='store_true',
                    help='live vs the recorded spread of the SAME minute -- '
                         'the comparison that can actually settle it')
    args = ap.parse_args()

    if args.paired:
        return paired(args.symbol)
    if args.report:
        return report(args.symbol)

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    fresh = not os.path.exists(OUT)
    print('logging %s every %ds -> %s  (Ctrl+C to stop)'
          % (args.symbol, args.every, os.path.relpath(OUT, ROOT)))
    # NOTHING IN THIS LOOP MAY KILL IT. The first version ran for ten samples
    # and stopped -- the python process gone, its console still open, which is
    # the signature of an unhandled exception rather than anything external
    # (the app server launched the same way is still up days later). The cause
    # was never established and does not matter: a logger whose entire purpose
    # is to still be sampling in twenty-four hours cannot exit on a transient,
    # and "it died and I do not know why" is not a reason to run it again
    # unguarded. Whatever it was is now caught, written to
    # data/spread_log.err, and slept off.
    n, errs = 0, 0
    while True:
        try:
            pts = live_spread(args.symbol)
        except Exception as exc:                               # noqa: BLE001
            pts, errs = None, errs + 1
            _note_error(exc)
        if pts is not None:
            try:
                # APPEND AND FLUSH PER SAMPLE. A logger that buffers loses the
                # window it was started for the moment anything interrupts it.
                # The append is guarded because on Windows a reader can hold
                # the handle -- `--report` is a reader, and it was running at
                # the minute the first attempt stopped.
                with io.open(OUT, 'a', encoding='utf-8', newline='') as fh:
                    w = csv.writer(fh)
                    if fresh:
                        w.writerow(['t', 'symbol', 'points'])
                        fresh = False
                    w.writerow([datetime.now(timezone.utc)
                                .strftime('%Y-%m-%dT%H:%M:%SZ'),
                                args.symbol, '%.1f' % pts])
            except Exception as exc:                           # noqa: BLE001
                errs += 1
                _note_error(exc)
                time.sleep(args.every)
                continue
            n += 1
            if n % 20 == 1:
                print('  %s %s %.0f pts (%d samples, %d errors)'
                      % (datetime.now(timezone.utc).strftime('%H:%M:%S'),
                         args.symbol, pts, n, errs))
        time.sleep(args.every)


if __name__ == '__main__':
    sys.exit(main())
