#!/usr/bin/env python
"""
Does the SIGN of a macro release predict the dollar's next hour?

    python tools/release_surprise_eval.py [--horizon 5 15 60]

THE QUESTION, AND WHY IT IS THIS ONE. Sentiment on a macro print means
`actual - consensus`, and no feed wired into this project carries a consensus:
xoomar's forecast column is empty on every row, QuantGist's coverage report
reads 2% and its sentiment endpoints answer 402, Finnhub's calendar answers
403. Before paying for one, it is worth knowing whether the WEAKER version --
`actual - previous`, the print against last month rather than against
expectations -- carries anything at all. If the weaker version is flat, a
consensus feed has to create the entire effect on its own, which is a much
bigger claim than "it will sharpen what is already there".

THE CONTROL IS A DISTRIBUTION, NOT A DRAW. A release lands in a market with its
own drift, so the question is whether the SIGN of the print beats a coin flip
on the same bars. The first version of this drew that flip ONCE per cell, and
at n=60 a single draw lands anywhere between 36% and 68% -- so the control was
noisier than anything it could have detected. It now runs `REPS` draws and
reports where the rule falls in that null: `p` is the share of random sign
vectors that did at least as well.

TWO ERAS, split 2021-01-01, because a result that does not survive both is one
era's accident. Every other measurement in this project is reported that way.

CAVEAT, STATED BECAUSE IT MATTERS. FRED serves REVISED values, and payrolls
are revised heavily. The sign of `actual - previous` computed here is the sign
as it looks today, not always the sign that printed on the day. That is a real
weakness and it is the right order to work in anyway: if the revised version --
which is if anything cleaner than what traders saw -- shows nothing, the
first-print version will not show more. If it DOES show something, the next
step is ALFRED vintages, not a bigger claim.
"""

import argparse
import collections
import datetime
import gzip
import json
import os
import random
import ssl
import sys
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _secrets                                        # noqa: E402
from _brokerclock import to_server_ms                  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CAL = os.path.join(ROOT, 'data', 'calendar', 'history.json')
ERA = datetime.datetime(2021, 1, 1, tzinfo=datetime.timezone.utc).timestamp() * 1000
#: Draws in the null. 2000 puts the resolution of p at 0.0005, which is
#: finer than anything n=60 can support -- the cost is a second of CPU.
REPS = 2000

#: kind -> (FRED series, is the level a RATE?, does UP mean a stronger dollar?)
#:
#: The direction column is the hand-written part and the only place a view
#: enters: more jobs and faster growth are dollar-positive, a higher jobless
#: rate is dollar-negative. Hotter inflation is left as dollar-positive because
#: the market has traded it as "more Fed tightening" for this whole sample --
#: which is an assumption, and it is why CPI and PPI are reported separately
#: below rather than pooled into one number.
SERIES = {
    'NFP': ('PAYEMS', False, +1),
    'UNEMPLOYMENT': ('UNRATE', True, -1),
    'CPI': ('CPIAUCSL', False, +1),
}


def fred(series, key):
    url = 'https://api.stlouisfed.org/fred/series/observations?' + urllib.parse.urlencode({
        'series_id': series, 'api_key': key, 'file_type': 'json',
        'observation_start': '2014-01-01'})
    req = urllib.request.Request(url, headers={'User-Agent': 'NurAI/1.0'})
    with urllib.request.urlopen(req, timeout=60,
                                context=ssl._create_unverified_context()) as r:
        obs = json.loads(r.read().decode('utf-8', 'replace'))['observations']
    out = {}
    for o in obs:
        try:
            out[o['date'][:7]] = float(o['value'])
        except ValueError:
            pass
    return out


def load_bars(sym, tf='5m'):
    d = os.path.join(ROOT, 'data', 'bars', sym, tf)
    rows = []
    for f in sorted(os.listdir(d)):
        if not f.endswith('.csv.gz') or f[:4] < '2015':
            continue
        with gzip.open(os.path.join(d, f), 'rt') as fh:
            head = fh.readline().strip().split(',')
            ix = {k: i for i, k in enumerate(head)}
            for line in fh:
                c = line.strip().split(',')
                if len(c) < 5:
                    continue
                rows.append((int(c[ix['ts']]) * 1000, float(c[ix['open']]),
                             float(c[ix['close']])))
    rows.sort()
    return rows


def move(bars, t, mins):
    """% change from the open of the bar at `t` to the close `mins` later."""
    lo, hi = 0, len(bars) - 1
    while lo < hi:                       # first bar at or after the release
        mid = (lo + hi) // 2
        if bars[mid][0] < t:
            lo = mid + 1
        else:
            hi = mid
    if bars[lo][0] < t or bars[lo][0] > t + 3600e3:
        return None
    j = lo + mins // 5
    if j >= len(bars):
        return None
    o = bars[lo][1]
    return None if not o else (bars[j][2] - o) / o * 100.0


def rate(hits, n):
    return 100.0 * hits / n if n else float('nan')


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--horizons', type=int, nargs='+', default=[5, 15, 60])
    ap.add_argument('--seed', type=int, default=20260906)
    args = ap.parse_args()

    key = _secrets.get('FRED_API_KEY')
    if not key:
        print('FRED_API_KEY missing', file=sys.stderr)
        return 2

    events = json.load(open(CAL))
    print('loading price series...')
    px = {'EURUSD.a': load_bars('EURUSD.a'), 'XAUUSD.a': load_bars('XAUUSD.a')}
    for k, v in px.items():
        print('  %-10s %d bars' % (k, len(v)))

    rng = random.Random(args.seed)
    print('')
    hdr = 'kind         pair     era       n   ' + '  '.join(
        'h%-3d hit%%  p   ' % h for h in args.horizons)
    print(hdr)
    print('-' * len(hdr))

    for kind, (series, _israte, sign) in SERIES.items():
        vals = fred(series, key)
        months = sorted(vals)
        chg = {}
        for i in range(2, len(months)):
            a = vals[months[i]] - vals[months[i - 1]]
            b = vals[months[i - 1]] - vals[months[i - 2]]
            chg[months[i]] = (a, b)

        rows = []
        for e in events:
            if e.get('kind') != kind:
                continue
            t = e['t']
            d = datetime.datetime.fromtimestamp(t / 1000, datetime.timezone.utc)
            # a release in month M reports month M-1
            ref = (d.replace(day=1) - datetime.timedelta(days=1)).strftime('%Y-%m')
            if ref not in chg:
                continue
            a, b = chg[ref]
            if a == b:
                continue
            rows.append((t, 1 if (a - b) > 0 else -1))

        for pair, bars in px.items():
            # EURUSD falls when the dollar rises; gold usually does too
            usd = -1 if pair.startswith('EUR') else -1
            for era, name in ((0, 'pre-21'), (1, 'post-21')):
                sel = [r for r in rows if (r[0] >= ERA) == bool(era)]
                cells, n_used = [], 0
                for h in args.horizons:
                    ups, wants = [], []
                    for t, s in sel:
                        # the archive is broker server time, the calendar UTC
                        m = move(bars, to_server_ms(t), h)
                        if m is None:
                            continue
                        ups.append(m > 0)
                        wants.append(s * sign * usd > 0)   # predicted direction
                    n = len(ups)
                    n_used = max(n_used, n)
                    if not n:
                        cells.append('     —      ')
                        continue
                    hit = sum(u == w for u, w in zip(ups, wants))
                    ge = 0
                    for _ in range(REPS):
                        r = sum(u == (rng.random() < 0.5) for u in ups)
                        if r >= hit:
                            ge += 1
                    cells.append('%5.1f p=%-4.2f' % (rate(hit, n), (ge + 1) / (REPS + 1)))
                if n_used:
                    print('%-12s %-8s %-8s %3d  %s'
                          % (kind, pair.replace('.a', ''), name, n_used,
                             ' '.join(cells)))
    print('')
    print('hit%% = the release sign called the direction; p = share of %d random' % REPS)
    print('sign vectors that did at least as well on the same events. A finding has')
    print('to be low in BOTH eras -- one era alone is how every failed gate here began.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
