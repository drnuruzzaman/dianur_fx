#!/usr/bin/env python
"""
MEASURED event impact for XAUUSD -- the layer that needs no vendor.

    python tools/event_impact_eval.py [--symbol XAUUSD.a] [--tf 1m]

WHY THIS LAYER FIRST. The three-layer split -- SURPRISE, SENTIMENT, IMPACT --
is right, and the three are not equally available:

    SURPRISE   BLOCKED. Defined as actual - forecast, and no feed reachable
               from this project carries a forecast: xoomar returns null in
               both its CSV and JSON views, QuantGist reports 2% coverage and
               paywalls its sentiment endpoints, Finnhub answers 403, and FRED
               publishes observations with no consensus field at all. Measured,
               not assumed -- see the checks in fetch_quantgist_news.py.

    SENTIMENT  BUILDABLE BUT NOT YET. Hawkish/dovish from FOMC statements needs
               the statement corpus, which the Fed publishes, plus a scorer.
               That is a modelling project and it belongs after this one.

    IMPACT     AVAILABLE NOW, from data already on disk: 801 dated events and
               a decade of 1m bars. Nothing here is assigned by hand.

AND THE HAND-ASSIGNED NUMBER IS THE ONE TO KILL FIRST. The vendor's
`importance: high` is on 85 of 801 rows and says nothing about how far gold
actually moved. Everything below is observed.

NO LOOK-AHEAD, AND IT IS THE POINT OF THE TWO COLUMNS.

    realized_*   what the market did AFTER this event. Known only afterwards,
                 so it is a label, never a feature.
    prior_*      the same statistic over every EARLIER event of the same kind,
                 expanding. This is the pre-event feature -- "what does an NFP
                 usually do" -- and it is computable 15 minutes before the
                 release without knowing anything about it.

Mixing those two is how a macro feature set leaks. They are separate columns
here so a model cannot take the wrong one by accident.
"""

import argparse
import collections
import datetime
import gzip
import json
import math
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _brokerclock import to_server_ms                      # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CAL = os.path.join(ROOT, 'data', 'calendar', 'history.json')
OUT = os.path.join(ROOT, 'data', 'research', 'event_impact.jsonl')

#: minutes after the release at which a return is taken
HORIZONS = (1, 5, 15, 30, 60)
#: bars of context before the release, for the volatility baseline
PRE = 60


def load_bars(sym, tf, from_year=2015):
    d = os.path.join(ROOT, 'data', 'bars', sym, tf)
    ts, o, h, lo, c, v = [], [], [], [], [], []
    for f in sorted(os.listdir(d)):
        if not f.endswith('.csv.gz') or f[:4] < str(from_year):
            continue
        with gzip.open(os.path.join(d, f), 'rt') as fh:
            head = fh.readline().strip().split(',')
            ix = {k: i for i, k in enumerate(head)}
            has_v = 'volume' in ix or 'tick_volume' in ix
            vk = ix.get('volume', ix.get('tick_volume'))
            for line in fh:
                p = line.rstrip('\n').split(',')
                if len(p) < 5:
                    continue
                ts.append(int(p[ix['ts']]) * 1000)
                o.append(float(p[ix['open']]))
                h.append(float(p[ix['high']]))
                lo.append(float(p[ix['low']]))
                c.append(float(p[ix['close']]))
                v.append(float(p[vk]) if has_v and p[vk] else 0.0)
    return ts, o, h, lo, c, v


def index_at(ts, t):
    """First bar at or after `t`, or None when the series does not cover it."""
    loi, hii = 0, len(ts) - 1
    if not ts or t < ts[0] or t > ts[-1]:
        return None
    while loi < hii:
        mid = (loi + hii) // 2
        if ts[mid] < t:
            loi = mid + 1
        else:
            hii = mid
    return loi


def true_range(h, lo, c, i):
    if i <= 0:
        return h[i] - lo[i]
    return max(h[i] - lo[i], abs(h[i] - c[i - 1]), abs(lo[i] - c[i - 1]))


def measure(bars, t, step_min):
    """Everything observable about one release. None when uncovered."""
    ts, o, h, lo, c, v = bars
    i = index_at(ts, t)
    if i is None or i < PRE or i + 60 // step_min >= len(ts):
        return None
    # the release bar must actually be at the release, not hours later
    if ts[i] - t > 15 * 60000:
        return None

    entry = o[i]
    if not entry:
        return None

    out = {}
    for m in HORIZONS:
        j = i + max(1, m // step_min)
        out['ret_%dm' % m] = (c[j] - entry) / entry * 100.0 if j < len(ts) else None

    win = range(i, min(len(ts), i + 60 // step_min + 1))
    hi = max(h[k] for k in win)
    low = min(lo[k] for k in win)
    out['mfe_pct'] = (hi - entry) / entry * 100.0
    out['mae_pct'] = (low - entry) / entry * 100.0
    out['range_60m_pct'] = (hi - low) / entry * 100.0

    tr_pre = [true_range(h, lo, c, k) for k in range(i - PRE, i)]
    tr_post = [true_range(h, lo, c, k) for k in win]
    atr_pre = sum(tr_pre) / len(tr_pre)
    atr_post = sum(tr_post) / len(tr_post)
    out['atr_before'] = atr_pre
    out['atr_after'] = atr_post
    out['vol_ratio'] = (atr_post / atr_pre) if atr_pre else None
    out['range_expansion'] = (out['range_60m_pct'] / (atr_pre / entry * 100.0)
                              if atr_pre else None)

    vpre = [v[k] for k in range(i - PRE, i)]
    vpost = [v[k] for k in win]
    mpre = sum(vpre) / len(vpre) if vpre else 0.0
    mpost = sum(vpost) / len(vpost) if vpost else 0.0
    out['volume_ratio'] = (mpost / mpre) if mpre else None
    return out


def summarise(rows, field):
    vals = [r[field] for r in rows if r.get(field) is not None]
    return statistics.median(vals) if vals else float('nan')


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--symbol', default='XAUUSD.a')
    ap.add_argument('--tf', default='1m')
    ap.add_argument('--out', default=OUT)
    args = ap.parse_args()

    step = {'1m': 1, '5m': 5}.get(args.tf)
    if not step:
        print('use 1m or 5m: the horizons start at one minute', file=sys.stderr)
        return 2

    print('loading %s %s ...' % (args.symbol, args.tf))
    bars = load_bars(args.symbol, args.tf)
    print('  %d bars' % len(bars[0]))

    events = sorted(json.load(open(CAL)), key=lambda e: e['t'])
    rows = []
    by_kind = collections.defaultdict(list)
    skipped = 0

    for e in events:
        # UTC -> broker server clock; the stored bars are server time.
        m = measure(bars, to_server_ms(e['t']), step)
        if m is None:
            skipped += 1
            continue
        kind = e.get('kind')
        # PRIOR = every earlier event of this kind, and nothing else. Computed
        # before this event's own numbers join the pool, so the feature is one
        # a model could have had 15 minutes early.
        pool = by_kind[kind]
        prior = {
            'prior_n': len(pool),
            'prior_vol_ratio': summarise(pool, 'vol_ratio') if pool else None,
            'prior_range_60m_pct': summarise(pool, 'range_60m_pct') if pool else None,
            'prior_abs_ret_15m': (statistics.median(
                [abs(r['ret_15m']) for r in pool if r.get('ret_15m') is not None])
                if pool else None),
        }
        row = {'t': e['t'], 'kind': kind, 'label': e.get('label'),
               'vendor_impact': e.get('impact'),
               'actual': e.get('actual'), 'previous': e.get('previous'),
               **{('realized_' + k): val for k, val in m.items()},
               **prior}
        rows.append(row)
        by_kind[kind].append(m)

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, 'w', encoding='utf-8') as fh:
        for r in rows:
            fh.write(json.dumps(r) + '\n')

    fmt = lambda v, p=2: ('%.*f' % (p, v)) if isinstance(v, float) and not math.isnan(v) else '—'
    print('\nwrote %d events -> %s   (%d not covered by the price series)'
          % (len(rows), args.out, skipped))
    print('')
    print('MEASURED IMPACT, median per event type. Nothing here is assigned.')
    print('')
    print('kind            n   |ret|5m  |ret|15m  |ret|60m   range60  vol x  volume x  vendor')
    print('-' * 92)
    for kind in sorted(by_kind):
        rs = [r for r in rows if r['kind'] == kind]
        if not rs:
            continue
        absret = lambda f: statistics.median(
            [abs(r['realized_' + f]) for r in rs if r.get('realized_' + f) is not None])
        vend = collections.Counter(r['vendor_impact'] for r in rs if r['vendor_impact'])
        print('%-12s %4d   %7s  %8s  %8s  %8s  %5s  %8s  %s'
              % (kind, len(rs), fmt(absret('ret_5m'), 3), fmt(absret('ret_15m'), 3),
                 fmt(absret('ret_60m'), 3),
                 fmt(summarise([{'x': r['realized_range_60m_pct']} and r for r in rs],
                               'realized_range_60m_pct'), 3),
                 fmt(summarise(rs, 'realized_vol_ratio')),
                 fmt(summarise(rs, 'realized_volume_ratio')),
                 (vend.most_common(1)[0][0] if vend else '—')))
    print('')
    print('`vol x` is ATR in the hour after against the hour before; `volume x` the')
    print('same for tick volume. A number near 1.0 is an event the market ignored,')
    print('whatever the vendor called it.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
