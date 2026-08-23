#!/usr/bin/env python
"""
dataset.py — read the history that mt5_download.py wrote.

Data you cannot load easily is not data, so this is the other half of the
downloader: one function per thing a backtest needs.

    from tools.dataset import load_bars, load_ticks, coverage, resample

    df = load_bars('XAUUSD.a', '15m', '2019-01-01', '2024-12-31')
    for day, ticks in load_ticks('USDJPY.a', '2026-08-01', '2026-08-05'):
        ...

    python tools/dataset.py                 # print what is on disk

TIME BASE: `ts` is broker SERVER time (see mt5_download.py). Loaders return a
timezone-naive DatetimeIndex named `server_time` so nothing silently pretends it
is UTC. `manifest.json` carries the offset measured at download time if you need
to convert.
"""

import glob
import gzip
import json
import os

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, 'data')

BAR_TYPES = {
    'open': 'float64', 'high': 'float64', 'low': 'float64', 'close': 'float64',
    'tick_volume': 'int64', 'real_volume': 'int64', 'spread': 'int32',
}


def _index(df, unit):
    df.index = pd.to_datetime(df.pop('ts' if unit == 's' else 'ts_ms'), unit=unit)
    df.index.name = 'server_time'
    return df


def load_bars(symbol, tf, start=None, end=None):
    """Bars for one symbol/timeframe as a DataFrame indexed by server time."""
    files = sorted(glob.glob(os.path.join(DATA, 'bars', symbol, tf, '*.csv.gz')))
    if not files:
        raise FileNotFoundError(f'no bars for {symbol} {tf} — run mt5_download.py --bars')
    if start or end:
        # each file is one calendar year, so most of them can be skipped by name
        lo = pd.Timestamp(start).year if start else 0
        hi = pd.Timestamp(end).year if end else 9999
        files = [f for f in files if lo <= int(os.path.basename(f)[:4]) <= hi]
    frames = [pd.read_csv(f, dtype=BAR_TYPES) for f in files]
    df = _index(pd.concat(frames, ignore_index=True), 's')
    if start:
        df = df[df.index >= pd.Timestamp(start)]
    if end:
        df = df[df.index <= pd.Timestamp(end)]
    return df.sort_index()


def load_ticks(symbol, start, end=None):
    """
    Yield (date, DataFrame) per day. A generator on purpose: a year of gold
    ticks is ~100M rows and does not want to be one DataFrame.
    """
    days = pd.date_range(pd.Timestamp(start), pd.Timestamp(end or start), freq='D')
    for day in days:
        path = os.path.join(DATA, 'ticks', symbol, day.strftime('%Y-%m'),
                            '%02d.csv.gz' % day.day)
        if not os.path.exists(path):
            continue
        df = _index(pd.read_csv(path), 'ms')
        yield day.date(), df


def resample(bars, rule):
    """Aggregate loaded bars to a coarser timeframe (e.g. '1h' -> '4h')."""
    agg = {'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last',
           'tick_volume': 'sum', 'real_volume': 'sum', 'spread': 'mean'}
    cols = {k: v for k, v in agg.items() if k in bars.columns}
    return bars.resample(rule, label='left', closed='left').agg(cols).dropna(subset=['open'])


def coverage():
    """What is on disk, per symbol/timeframe, plus tick day counts."""
    rows = []
    for path in sorted(glob.glob(os.path.join(DATA, 'bars', '*', '*'))):
        parts = path.replace(os.sep, '/').split('/')
        symbol, tf = parts[-2], parts[-1]
        files = sorted(glob.glob(os.path.join(path, '*.csv.gz')))
        if not files:
            continue
        size = sum(os.path.getsize(f) for f in files)
        n = 0
        for f in files:
            with gzip.open(f, 'rt') as fh:
                n += sum(1 for _ in fh) - 1
        rows.append({'symbol': symbol, 'tf': tf, 'years': len(files), 'rows': n,
                     'first': os.path.basename(files[0])[:4],
                     'last': os.path.basename(files[-1])[:4],
                     'mb': round(size / 1e6, 1)})
    bars = pd.DataFrame(rows)

    trows = []
    for path in sorted(glob.glob(os.path.join(DATA, 'ticks', '*'))):
        symbol = os.path.basename(path)
        files = glob.glob(os.path.join(path, '*', '*.csv.gz'))
        if not files:
            continue
        names = sorted(os.path.basename(os.path.dirname(f)) + '-' + os.path.basename(f)[:2]
                       for f in files)
        trows.append({'symbol': symbol, 'days': len(files),
                      'first': names[0], 'last': names[-1],
                      'gb': round(sum(os.path.getsize(f) for f in files) / 1e9, 2)})
    ticks = pd.DataFrame(trows)
    return bars, ticks


if __name__ == '__main__':
    bars, ticks = coverage()
    print('=== BARS ===')
    print(bars.to_string(index=False) if len(bars) else 'none')
    print('\n=== TICKS ===')
    print(ticks.to_string(index=False) if len(ticks) else 'none')
    man = os.path.join(DATA, 'manifest.json')
    if os.path.exists(man):
        doc = json.load(open(man, encoding='utf-8'))
        print('\nserver offset at download: %s ms · %s · terminal build %s'
              % (doc.get('server_utc_offset_ms'), doc.get('broker_server'),
                 doc.get('terminal_build')))
