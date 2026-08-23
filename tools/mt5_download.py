#!/usr/bin/env python
"""
mt5_download.py — bulk history exporter for backtesting and optimisation.

Talks to the MetaTrader 5 terminal directly rather than through the read-only
web bridge: the bridge serves a live UI (60-second tick windows, capped bar
counts) and holding its lock for a multi-gigabyte pull would stall the page.

Like the bridge, this is READ ONLY. It calls copy_rates_* and copy_ticks_*
only. There is no order_send anywhere in this file.

    python tools/mt5_download.py --probe
    python tools/mt5_download.py --bars --years 20 --tf 15m,1h,4h,1d
    python tools/mt5_download.py --ticks --days 90
    python tools/mt5_download.py --bars --tf 1m --years 5      # intrabar proxy

Output (git-ignored, resumable — existing chunks are skipped):

    data/bars/<SYMBOL>/<TF>/<YYYY>.csv.gz     ts,open,high,low,close,tick_volume,real_volume,spread
    data/ticks/<SYMBOL>/<YYYY-MM>/<DD>.csv.gz ts_ms,bid,ask,last,volume,flags
    data/manifest.json                        coverage, row counts, provenance

TIME BASE: timestamps are the broker's SERVER time exactly as MetaTrader
reports it, not corrected to UTC. That is deliberate — the server offset shifts
with the broker's DST, so a single correction applied across 20 years would be
wrong for half of it. The manifest records the offset measured at download time
so a backtest can convert if it needs to. Bar timestamps are seconds; tick
timestamps are milliseconds.
"""

import argparse
import gzip
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

try:
    import MetaTrader5 as mt5
except ImportError:
    sys.exit('MetaTrader5 package not installed:  pip install MetaTrader5')

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from sim.clock import resolve_offset      # noqa: E402  (needs ROOT on the path)
DATA = os.path.join(ROOT, 'data')

TF = {
    '1m': mt5.TIMEFRAME_M1, '5m': mt5.TIMEFRAME_M5, '15m': mt5.TIMEFRAME_M15,
    '30m': mt5.TIMEFRAME_M30, '1h': mt5.TIMEFRAME_H1, '4h': mt5.TIMEFRAME_H4,
    '1d': mt5.TIMEFRAME_D1, '1w': mt5.TIMEFRAME_W1,
}

# Scoped to the two instruments actually being researched. Override with
# --symbols to widen; names are the broker's own tickers, suffix included.
DEFAULT_SYMBOLS = ['XAUUSD.a', 'USDJPY.a']

BAR_HEAD = 'ts,open,high,low,close,tick_volume,real_volume,spread\n'
TICK_HEAD = 'ts_ms,bid,ask,last,volume,flags\n'


def utc(y, m=1, d=1):
    return datetime(y, m, d, tzinfo=timezone.utc)


def connect():
    if not mt5.initialize():
        sys.exit('cannot reach the MetaTrader 5 terminal: %s' % (mt5.last_error(),))
    info = mt5.terminal_info()
    acct = mt5.account_info()
    print('* terminal : %s  build %s' % (info.name, info.build))
    print('* maxbars  : %s  (History "Max bars in chart" caps every fetch)' % info.maxbars)
    print('* server   : %s' % (acct.server if acct else 'not logged in'))
    return info


# Symbols probed ONLY to read the server clock -- never downloaded. Crypto
# leads deliberately: it trades through the weekend, so the offset stays
# measurable on a Saturday, which is precisely when this used to go wrong. The
# guard in sim.clock catches a stale reading either way; this is what lets a
# weekend run produce a fresh one instead of merely refusing to be fooled.
OFFSET_PROBES = ['BTCUSD.a', 'BTCUSD', 'ETHUSD.a', 'ETHUSD'] + DEFAULT_SYMBOLS


def server_offset_ms(previous=None, previous_confidence=None):
    """sim.clock.resolve_offset against the live terminal."""
    ticks = []
    for name in OFFSET_PROBES:
        try:
            t = mt5.symbol_info_tick(name)
        except Exception:                                 # noqa: BLE001
            continue                                      # symbol not offered here
        if t and t.time_msc:
            ticks.append(t.time_msc)
    return resolve_offset(ticks, time.time() * 1000, previous, previous_confidence)


def ensure(name):
    """Make a symbol visible; the terminal will not serve history otherwise."""
    info = mt5.symbol_info(name)
    if info is None:
        return None
    if not info.visible:
        mt5.symbol_select(name, True)
        time.sleep(0.15)
    return mt5.symbol_info(name)


def write_gz(path, header, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.part'
    with gzip.open(tmp, 'wt', encoding='utf-8', newline='') as fh:
        fh.write(header)
        fh.writelines(rows)
    os.replace(tmp, path)          # never leave a half-written chunk behind


# --------------------------------------------------------------------------- #
# probe                                                                       #
# --------------------------------------------------------------------------- #
def probe(symbols, tfs, back_years=25):
    """Report what history the terminal will actually serve, before pulling it."""
    print('\n=== BARS: earliest bar the terminal will serve ===')
    print('%-11s %-5s %12s  %-10s  %-10s' % ('symbol', 'tf', 'bars', 'first', 'last'))
    out = {}
    frm, to = utc(datetime.now().year - back_years), datetime.now(timezone.utc)
    for name in symbols:
        if ensure(name) is None:
            print('%-11s  -- not offered by this broker' % name)
            continue
        for tf in tfs:
            rows = mt5.copy_rates_range(name, TF[tf], frm, to)
            if rows is None or len(rows) == 0:
                # nudge the terminal into downloading, then retry once
                mt5.copy_rates_from_pos(name, TF[tf], 0, 100000)
                rows = mt5.copy_rates_range(name, TF[tf], frm, to)
            if rows is None or len(rows) == 0:
                print('%-11s %-5s %12s' % (name, tf, 'none'))
                continue
            first = datetime.fromtimestamp(int(rows[0]['time']), timezone.utc)
            last = datetime.fromtimestamp(int(rows[-1]['time']), timezone.utc)
            print('%-11s %-5s %12d  %s  %s'
                  % (name, tf, len(rows), first.date(), last.date()))
            out.setdefault(name, {})[tf] = {
                'bars': len(rows), 'first': str(first.date()), 'last': str(last.date()),
            }

    print('\n=== TICKS: is deep tick history actually there? ===')
    print('(one day sampled per probe point; brokers keep far less tick than bar history)')
    now = datetime.now(timezone.utc)
    points = [('yesterday', 1), ('1 month', 30), ('6 months', 182),
              ('1 year', 365), ('3 years', 1095), ('10 years', 3652)]
    for name in symbols:
        if ensure(name) is None:
            continue
        line = []
        for label, days in points:
            day = (now - timedelta(days=days)).replace(hour=0, minute=0, second=0, microsecond=0)
            got = mt5.copy_ticks_range(name, day, day + timedelta(days=1), mt5.COPY_TICKS_ALL)
            line.append('%s=%s' % (label, ('%dk' % (len(got) / 1000)) if got is not None and len(got) else 'none'))
        print('  %-11s %s' % (name, '  '.join(line)))
    return out


# --------------------------------------------------------------------------- #
# bars                                                                        #
# --------------------------------------------------------------------------- #
def fetch_bars(symbols, tfs, years, progress=None, cancelled=None):
    """One file per symbol/timeframe/calendar-year, so a rerun resumes cheaply."""
    this_year = datetime.now(timezone.utc).year
    first_year = this_year - years + 1
    total_rows = 0
    years_list = list(range(first_year, this_year + 1))
    total_jobs = len(symbols) * len(tfs) * len(years_list)
    done_jobs = 0
    for name in symbols:
        if ensure(name) is None:
            print('! %s not offered — skipped' % name)
            continue
        for tf in tfs:
            for year in years_list:
                done_jobs += 1
                if cancelled is not None and cancelled():
                    if progress:
                        progress(phase='bars', symbol=name, tf=tf, item=str(year),
                                 done=done_jobs, total=total_jobs, rows=total_rows,
                                 note='cancelled')
                    return total_rows
                if progress:
                    progress(phase='bars', symbol=name, tf=tf, item=str(year),
                             done=done_jobs, total=total_jobs, rows=total_rows)
                path = os.path.join(DATA, 'bars', name, tf, '%d.csv.gz' % year)
                if os.path.exists(path):
                    continue
                # a day of margin each side absorbs the server/UTC offset;
                # duplicates are impossible because we slice by year below
                rows = mt5.copy_rates_range(
                    name, TF[tf],
                    utc(year) - timedelta(days=1),
                    utc(year + 1) + timedelta(days=1))
                if rows is None or len(rows) == 0:
                    continue
                lines = []
                for r in rows:
                    ts = int(r['time'])
                    if not (utc(year).timestamp() <= ts < utc(year + 1).timestamp()):
                        continue
                    lines.append('%d,%s,%s,%s,%s,%d,%d,%d\n' % (
                        ts, r['open'], r['high'], r['low'], r['close'],
                        int(r['tick_volume']), int(r['real_volume']), int(r['spread'])))
                if not lines:
                    continue
                write_gz(path, BAR_HEAD, lines)
                total_rows += len(lines)
                print('  %-11s %-4s %d  %7d bars' % (name, tf, year, len(lines)))
    print('* bars written: %d rows' % total_rows)
    return total_rows


# --------------------------------------------------------------------------- #
# ticks                                                                       #
# --------------------------------------------------------------------------- #
def fetch_ticks(symbols, days, progress=None, cancelled=None):
    """One file per symbol/day. Ticks are big; this is why it is day-chunked."""
    total = 0
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    total_jobs = len(symbols) * (days + 1)
    done_jobs = 0
    for name in symbols:
        if ensure(name) is None:
            continue
        for back in range(days, -1, -1):
            day = today - timedelta(days=back)
            done_jobs += 1
            if cancelled is not None and cancelled():
                if progress:
                    progress(phase='ticks', symbol=name, tf='tick', item=str(day.date()),
                             done=done_jobs, total=total_jobs, rows=total, note='cancelled')
                return total
            if progress:
                progress(phase='ticks', symbol=name, tf='tick', item=str(day.date()),
                         done=done_jobs, total=total_jobs, rows=total)
            if day.weekday() == 5:                       # Saturday: no session
                continue
            path = os.path.join(DATA, 'ticks', name, day.strftime('%Y-%m'),
                                '%02d.csv.gz' % day.day)
            if os.path.exists(path):
                continue
            got = mt5.copy_ticks_range(name, day, day + timedelta(days=1), mt5.COPY_TICKS_ALL)
            if got is None or len(got) == 0:
                continue
            lines = ['%d,%s,%s,%s,%d,%d\n' % (
                int(t['time_msc']), t['bid'], t['ask'], t['last'],
                int(t['volume']), int(t['flags'])) for t in got]
            write_gz(path, TICK_HEAD, lines)
            total += len(lines)
            print('  %-11s %s  %9d ticks  (%.1f MB on disk)'
                  % (name, day.date(), len(lines), os.path.getsize(path) / 1e6))
    print('* ticks written: %d rows' % total)
    return total


def write_manifest(extra):
    os.makedirs(DATA, exist_ok=True)
    path = os.path.join(DATA, 'manifest.json')
    doc = {}
    if os.path.exists(path):
        try:
            doc = json.load(open(path, encoding='utf-8'))
        except ValueError:
            doc = {}
    acct = mt5.account_info()
    term = mt5.terminal_info()
    offset, confidence = server_offset_ms(doc.get('server_utc_offset_ms'),
                                          doc.get('server_offset_confidence'))
    doc.update({
        'updated_utc': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'broker_server': acct.server if acct else None,
        'terminal_build': term.build if term else None,
        'terminal_maxbars': term.maxbars if term else None,
        # keep the last known good offset when the market is closed
        'server_utc_offset_ms': offset,
        'server_offset_confidence': confidence,
        'time_base': 'broker server time as reported by MetaTrader (NOT UTC-corrected); '
                     'bars in seconds, ticks in milliseconds',
        'read_only': True,
    })
    doc.update(extra or {})
    tally = {}
    for kind in ('bars', 'ticks'):
        base = os.path.join(DATA, kind)
        if not os.path.isdir(base):
            continue
        files = 0
        size = 0
        for root, _dirs, names in os.walk(base):
            for f in names:
                if f.endswith('.csv.gz'):
                    files += 1
                    size += os.path.getsize(os.path.join(root, f))
        tally[kind] = {'files': files, 'bytes': size, 'mb': round(size / 1e6, 1)}
    doc['on_disk'] = tally
    json.dump(doc, open(path, 'w', encoding='utf-8'), indent=2)
    print('* manifest: %s' % path)
    print('* on disk : %s' % tally)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--symbols', default=','.join(DEFAULT_SYMBOLS))
    ap.add_argument('--tf', default='15m,1h,4h,1d')
    ap.add_argument('--years', type=int, default=20)
    ap.add_argument('--days', type=int, default=90, help='tick window, in days back')
    ap.add_argument('--probe', action='store_true', help='report availability, download nothing')
    ap.add_argument('--bars', action='store_true')
    ap.add_argument('--ticks', action='store_true')
    args = ap.parse_args()

    symbols = [s.strip() for s in args.symbols.split(',') if s.strip()]
    tfs = [t.strip() for t in args.tf.split(',') if t.strip() in TF]

    connect()
    try:
        if args.probe:
            found = probe(symbols, tfs)
            write_manifest({'probe': found})
            return
        if args.bars:
            print('\n=== downloading bars: %s | %s | %d years ==='
                  % (','.join(symbols), ','.join(tfs), args.years))
            fetch_bars(symbols, tfs, args.years)
        if args.ticks:
            print('\n=== downloading ticks: %s | %d days ===' % (','.join(symbols), args.days))
            fetch_ticks(symbols, args.days)
        if not (args.bars or args.ticks):
            print('nothing to do — pass --probe, --bars and/or --ticks')
            return
        write_manifest(None)
    finally:
        mt5.shutdown()


if __name__ == '__main__':
    main()
