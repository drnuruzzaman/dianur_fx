#!/usr/bin/env python
"""
PASS 1 of the TP-band study: build the event dataset, and prove it is clean.

    python tools/event_dataset.py
    python tools/event_dataset.py --symbols XAUUSD.a --tfs 1h 4h

WHAT PASS 1 IS FOR. Every earlier study in this project asked one question and
answered it with one walk. That works for "is this exit better" and does not work
for "under which conditions is taking profit here right", which needs the same
trades described many ways. So this writes the trades down once, with every
detector's view of the bar they were decided on, and leaves the questions to
later passes.

WHAT IT WRITES, per cell, into data/research/events:

    <symbol>_<tf>.trades.jsonl   one row per trade
    <symbol>_<tf>.bands.jsonl    one row per (trade, candidate band)

Features are stamped at the SIGNAL bar. Outcomes are prefixed `y_` so that no
later pass can feed a label to a model by accident.

THREE SOURCES PER CELL. The shipped configuration (validated rule + structural
trail) and its two matched controls, all through the same extraction. A
condition discovered on the real trades can then be checked immediately against
trades that had no reason to work -- which is the check eleven entry gates
failed and the reason this project trusts controls over point estimates.

THE AUDIT IS NOT OPTIONAL. For a sample of trades in every cell, each feature is
rebuilt from bars[0..signalI] alone and must match the full-series value exactly.
Two detectors here were survivorship biased in ways that would have made a
strategy look excellent -- `zones.detect` drops levels that later broke, and
`supplydemand.detect` scores zones on future touches -- and both were caught by
reading the code rather than by running it. A cell whose audit reports a leak is
NOT written into the report as usable.

NO VERDICTS. Nothing here scores a strategy, and nothing here should be read as
evidence for one.
"""

import argparse
import glob
import gzip
import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NODE = shutil.which('node')
RUNNER = os.path.join(ROOT, 'tools', 'event_dataset.mjs')

DEFAULT_SYMBOLS = ['XAUUSD.a', 'USDJPY.a']
DEFAULT_TFS = ['5m', '15m', '30m', '1h', '4h', '1d', '1w']

#: Enough history for the later passes' calendar blocks to mean anything.
MIN_BARS = {'1m': 200000, '5m': 20000, '15m': 20000, '30m': 10000,
            '1h': 5000, '4h': 2000, '1d': 800, '1w': 300}


def load_bars(symbol, tf, year_from):
    files = sorted(glob.glob(os.path.join(ROOT, 'data', 'bars', symbol, tf, '*.csv.gz')))
    rows = []
    for f in files:
        if int(os.path.basename(f)[:4]) < year_from:
            continue
        with gzip.open(f, 'rt') as fh:
            head = fh.readline().strip().split(',')
            ix = {k: i for i, k in enumerate(head)}
            for line in fh:
                c = line.strip().split(',')
                if len(c) < 5:
                    continue
                rows.append({'t': int(c[ix['ts']]) * 1000,
                             'o': float(c[ix['open']]), 'h': float(c[ix['high']]),
                             'l': float(c[ix['low']]), 'c': float(c[ix['close']]),
                             'v': 1})
    rows.sort(key=lambda r: r['t'])
    return rows


def coverage(path):
    """Share of rows where each column is not null -- a null is a detector with
    nothing to say there, which later passes must handle rather than impute."""
    n = 0
    seen = {}
    with open(path) as fh:
        for line in fh:
            row = json.loads(line)
            if row.get('source') != 'shipped':
                continue
            n += 1
            for k, v in row.items():
                if v is not None:
                    seen[k] = seen.get(k, 0) + 1
    return n, {k: seen.get(k, 0) / n for k in seen} if n else (0, {})


def run_cell(symbol, tf, year_from, out_dir, heap, audit_n, max_rows,
             summary_only=False):
    bars = load_bars(symbol, tf, year_from)
    if len(bars) < MIN_BARS.get(tf, 5000):
        print('  ! %s %s: only %d bars, skipped' % (symbol, tf, len(bars)), file=sys.stderr)
        return None
    tmp = tempfile.mkdtemp(prefix='events_')
    try:
        bp = os.path.join(tmp, 'bars.json')
        with open(bp, 'w') as fh:
            json.dump(bars, fh)
        cp = os.path.join(tmp, 'cfg.json')
        with open(cp, 'w') as fh:
            json.dump({'barsPath': bp, 'tf': tf, 'cell': symbol + '|' + tf,
                       'outDir': out_dir, 'auditN': audit_n,
                       'maxRows': max_rows, 'summaryOnly': summary_only}, fh)
        out = subprocess.run([NODE, '--max-old-space-size=%d' % heap, RUNNER, cp],
                             cwd=ROOT, capture_output=True, text=True, timeout=43200)
        sys.stderr.write(out.stderr)
        if out.returncode != 0:
            print('  ! node failed on %s %s' % (symbol, tf), file=sys.stderr)
            return None
        return json.loads(out.stdout.strip().splitlines()[-1])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--symbols', nargs='+', default=DEFAULT_SYMBOLS)
    ap.add_argument('--tfs', nargs='+', default=DEFAULT_TFS)
    ap.add_argument('--from-year', type=int, default=2016)
    ap.add_argument('--out', default=os.path.join(ROOT, 'data', 'research', 'events'))
    ap.add_argument('--heap', type=int, default=8192)
    ap.add_argument('--max-rows', type=int, default=15000,
                    help='trades per source that get features; above this every '
                         'k-th is taken (0 = no cap). The cell summary is always '
                         'computed from every trade.')
    ap.add_argument('--summary-only', action='store_true',
                    help='rewrite <cell>.summary.json from every trade and skip '
                         'feature extraction -- the walk is cheap, the features '
                         'are not')
    ap.add_argument('--audit-n', type=int, default=12,
                    help='trades per cell rebuilt from a prefix (0 disables, '
                         'which should only ever be a debugging convenience)')
    args = ap.parse_args()

    if NODE is None:
        print('node is not installed', file=sys.stderr)
        return 2

    print('')
    print('PASS 1 -- EVENT DATASET. Three sources per cell (shipped rule, and')
    print('its two matched controls), features at the signal bar, outcomes')
    print('prefixed y_. No modelling, no selection, no verdicts.')
    print('')
    print('  %-16s%9s%9s%9s%9s   %s' % ('cell', 'bars', 'trades', 'controls',
                                        'bands', 'look-ahead audit'))

    ok, leaked, cells = 0, [], []
    for symbol in args.symbols:
        for tf in args.tfs:
            res = run_cell(symbol, tf, args.from_year, args.out, args.heap,
                           args.audit_n, args.max_rows, args.summary_only)
            if not res:
                continue
            c = res['counts']
            ctrl = c['randEntry']['trades'] + c['randSide']['trades']
            bands = sum(v['bands'] for v in c.values())
            a = res['audit']
            verdict = ('%d rebuilt, clean' % a['checked'] if not a['bad']
                       else 'LEAK: ' + ' '.join(a['bad']))
            if a['bad']:
                leaked.append(res['cell'])
            else:
                ok += 1
            cells.append(res)
            print('  %-16s%9d%9d%9d%9d   %s'
                  % (res['cell'], res['bars'], c['shipped']['trades'], ctrl,
                     bands, verdict))

    if not cells:
        print('\n  (no cell had enough history)')
        return 0

    print('')
    print('FEATURE COVERAGE on the shipped trades -- the share of rows where the')
    print('detector had something to say. A null is not a zero: no trendline is')
    print('a different state from a trendline at distance zero, and later passes')
    print('must treat it that way rather than imputing.')
    tf_path = cells[0]['files'][0]
    n, cov = coverage(tf_path)
    thin = sorted((v, k) for k, v in cov.items() if v < 0.95 and not k.startswith('y_'))
    print('  %s (%d rows)' % (os.path.basename(tf_path), n))
    for v, k in thin:
        print('    %-24s %5.0f%%' % (k, 100 * v))

    print('')
    if leaked:
        print('!! %d cell(s) FAILED THE LOOK-AHEAD AUDIT and must not be modelled:'
              % len(leaked))
        for c in leaked:
            print('   ' + c)
        return 1
    print('%d cells written, every one rebuilt from a prefix without a leak.' % ok)
    print('Pass 1 is done when this line says so and not before.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
