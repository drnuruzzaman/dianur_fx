#!/usr/bin/env python
"""
reindex_runs.py — build runs/index.json, the single thing the Backtest tab fetches.

    python tools/reindex_runs.py

The page is a viewer: it renders only what the engine wrote. Rather than have it
crawl directories, parse CSVs and hash source files, everything it needs is
collected here — run metrics, a downsampled equity curve, Stage 1 gate results,
data coverage, and a hash of the code that produced it all.

The equity curve is downsampled to ~240 points on purpose: a 15M run has 140,000
equity rows and the page does not need them to draw a sparkline.
"""

import glob
import gzip
import hashlib
import json
import os
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNS = os.path.join(ROOT, 'runs')
DATA = os.path.join(ROOT, 'data')
CURVE_POINTS = 240


def code_hash():
    """Fingerprint of the engine, so a run can be tied to the code behind it."""
    h = hashlib.sha256()
    patterns = ['sim/*.py', 'sim/tl/*.py', 'sim/strategies/*.py',
                'js/chart/*.js', 'tools/*.py']
    for pat in patterns:
        for path in sorted(glob.glob(os.path.join(ROOT, pat))):
            h.update(os.path.basename(path).encode())
            with open(path, 'rb') as fh:
                h.update(fh.read())
    return h.hexdigest()[:12]


def equity_curve(path):
    """(timestamp_ms, equity) pairs, downsampled by taking every Nth row."""
    if not os.path.exists(path):
        return []
    import csv
    rows = []
    with open(path, newline='', encoding='utf-8') as fh:
        r = csv.DictReader(fh)
        key = 'time' if 'time' in (r.fieldnames or []) else (r.fieldnames or [None])[0]
        for row in r:
            rows.append((row.get(key), row.get('equity')))
    if not rows:
        return []
    step = max(1, len(rows) // CURVE_POINTS)
    out = []
    for i in range(0, len(rows), step):
        t, v = rows[i]
        try:
            ts = int(datetime.fromisoformat(str(t)).replace(
                tzinfo=timezone.utc).timestamp() * 1000)
            out.append([ts, round(float(v), 2)])
        except (ValueError, TypeError):
            continue
    if out and len(rows) > 1:                     # always keep the last point
        t, v = rows[-1]
        try:
            ts = int(datetime.fromisoformat(str(t)).replace(
                tzinfo=timezone.utc).timestamp() * 1000)
            out.append([ts, round(float(v), 2)])
        except (ValueError, TypeError):
            pass
    return out


def read_runs():
    out = []
    for path in sorted(glob.glob(os.path.join(RUNS, '*', 'metrics.json'))):
        folder = os.path.dirname(path)
        try:
            m = json.load(open(path, encoding='utf-8'))
        except ValueError:
            continue
        cfg = {}
        cpath = os.path.join(folder, 'config.json')
        if os.path.exists(cpath):
            try:
                cfg = json.load(open(cpath, encoding='utf-8'))
            except ValueError:
                cfg = {}
        # runs written before run_tl.py recorded a config have no timestamp of
        # their own; the folder's mtime is the honest substitute, flagged as such
        created = cfg.get('created_utc')
        source = 'config' if created else 'mtime'
        if not created:
            try:
                created = datetime.fromtimestamp(
                    os.path.getmtime(path), timezone.utc).isoformat(timespec='seconds')
            except OSError:
                # the folder vanished between the glob and here; skip it rather
                # than abort the whole index (this actually happened once)
                print('! skipping %s: disappeared mid-scan' % folder)
                continue
        out.append({
            'created_utc': created, 'created_source': source,
            'run_id': os.path.basename(folder),
            'symbol': m.get('symbol'), 'tf': m.get('tf'),
            'strategy': m.get('strategy'), 'params': m.get('params', {}),
            'confluence': m.get('confluence_mode'),
            'trades': m.get('trades', 0),
            'net_acct': m.get('net_acct'), 'return_pct': m.get('return_pct'),
            'win_pct': m.get('win_rate_pct'), 'avg_R': m.get('avg_R'),
            'pf': m.get('profit_factor'), 'maxdd_pct': m.get('max_drawdown_pct'),
            'sharpe': m.get('sharpe_daily_annualised'),
            'buy_hold_pct': m.get('buy_and_hold_pct'),
            'swap_model': m.get('swap_model'),
            'first_bar': m.get('first_bar'), 'last_bar': m.get('last_bar'),
            'data_hash': m.get('data_hash'),
            'exit_reasons': m.get('exit_reasons', {}),
            'cost_spread': m.get('cost_spread_ccy'),
            'cost_slippage': m.get('cost_slippage_ccy'),
            'cost_swap': m.get('cost_swap_ccy'),
            'equity': equity_curve(os.path.join(folder, 'equity.csv')),
            'has_trades_csv': os.path.exists(os.path.join(folder, 'trades.csv')),
        })
    return out


def read_gates():
    """Stage 1 results, if they have been run."""
    path = os.path.join(RUNS, 'stage1.csv')
    if not os.path.exists(path):
        return []
    import csv
    out = []
    with open(path, newline='', encoding='utf-8') as fh:
        for row in csv.DictReader(fh):
            def num(k):
                try:
                    return round(float(row.get(k) or 'nan'), 4)
                except ValueError:
                    return None
            out.append({
                'symbol': row.get('symbol'), 'tf': row.get('tf'),
                'strategy': row.get('strategy'),
                'trades': int(float(row.get('trades') or 0)),
                'avg_R': num('avg_R'), 'pf': num('pf'),
                'control_p50': num('control_avgR_p50'),
                'percentile': num('percentile_vs_control'),
                'gate_sample': str(row.get('gate_sample')).lower() == 'true',
                'gate_control': str(row.get('gate_beats_control')).lower() == 'true',
                'dd_historical': num('dd_historical'), 'dd_p95': num('dd_p95'),
                'prob_negative': num('prob_net_negative'),
            })
    return out


def read_data_coverage():
    """What history is on disk, so a result can be read against its inputs."""
    out = {'bars': [], 'ticks': [], 'manifest': {}}
    man = os.path.join(DATA, 'manifest.json')
    if os.path.exists(man):
        try:
            doc = json.load(open(man, encoding='utf-8'))
            out['manifest'] = {k: doc.get(k) for k in
                               ('updated_utc', 'broker_server', 'server_utc_offset_ms',
                                'terminal_build', 'time_base', 'on_disk')}
        except ValueError:
            pass
    for d in sorted(glob.glob(os.path.join(DATA, 'bars', '*', '*'))):
        files = sorted(glob.glob(os.path.join(d, '*.csv.gz')))
        if not files:
            continue
        parts = d.replace(os.sep, '/').split('/')
        rows = 0
        for f in files:
            with gzip.open(f, 'rt') as fh:
                rows += sum(1 for _ in fh) - 1
        out['bars'].append({
            'symbol': parts[-2], 'tf': parts[-1], 'years': len(files), 'rows': rows,
            'first': os.path.basename(files[0])[:4],
            'last': os.path.basename(files[-1])[:4],
            'mb': round(sum(os.path.getsize(f) for f in files) / 1e6, 1),
        })
    for d in sorted(glob.glob(os.path.join(DATA, 'ticks', '*'))):
        files = glob.glob(os.path.join(d, '*', '*.csv.gz'))
        if not files:
            continue
        names = sorted(os.path.basename(os.path.dirname(f)) + '-' +
                       os.path.basename(f)[:2] for f in files)
        out['ticks'].append({
            'symbol': os.path.basename(d), 'days': len(files),
            'first': names[0], 'last': names[-1],
            'gb': round(sum(os.path.getsize(f) for f in files) / 1e9, 2),
        })
    return out


def main():
    os.makedirs(RUNS, exist_ok=True)
    doc = {
        'generated_utc': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'code_hash': code_hash(),
        'runs': read_runs(),
        'gates': read_gates(),
        'data': read_data_coverage(),
    }
    path = os.path.join(RUNS, 'index.json')

    # Tripwire: 26 run folders once disappeared with no delete request logged and
    # no reproducible cause. Recording the previous count makes a drop visible
    # rather than something you notice weeks later.
    previous = None
    if os.path.exists(path):
        try:
            previous = len(json.load(open(path, encoding='utf-8')).get('runs', []))
        except ValueError:
            previous = None
    doc['previous_run_count'] = previous
    if previous is not None and len(doc['runs']) < previous:
        doc['run_count_warning'] = ('run count fell from %d to %d since the last '
                                    'index' % (previous, len(doc['runs'])))
        print('! WARNING: %s' % doc['run_count_warning'])

    json.dump(doc, open(path, 'w', encoding='utf-8'), indent=1, default=str)
    print('* %s: %d runs, %d gate rows, %d bar series, %d tick series (code %s)'
          % (path, len(doc['runs']), len(doc['gates']), len(doc['data']['bars']),
             len(doc['data']['ticks']), doc['code_hash']))


if __name__ == '__main__':
    main()
