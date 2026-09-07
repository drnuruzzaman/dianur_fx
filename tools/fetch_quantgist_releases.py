#!/usr/bin/env python
"""
Historical US macro releases with values -> data/news/releases.json.

    python tools/fetch_quantgist_releases.py [--pages N]

The point-in-time endpoint (`/v2/backtest`) is the one to use for anything that
feeds a measurement: it is the vendor's own "backtest-safe" query, so a row
carries what was PUBLISHED at the time rather than a later revision. Every
other calendar wired up here serves current values.

WHAT IT DOES NOT CARRY. `forecast_coverage_pct` on this account reads 0.0216 --
two percent. There is no consensus in here, on any plan this key can reach, so
a release SURPRISE in the usual sense still cannot be computed. What can be
computed is `actual - previous`, which is a different and weaker thing: it says
the print moved against last month, not against what was expected.
"""

import argparse
import datetime
import json
import os
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _secrets                                        # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'data', 'news', 'releases.json')


def get(path, key, **params):
    url = 'https://api.quantgist.com' + path
    if params:
        url += '?' + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={
        'User-Agent': 'NurAI/1.0 (local research tool)', 'X-API-Key': key})
    with urllib.request.urlopen(req, timeout=60,
                                context=ssl._create_unverified_context()) as r:
        return json.loads(r.read().decode('utf-8', 'replace'))


def ms(iso):
    try:
        return int(datetime.datetime.fromisoformat(
            (iso or '').replace('Z', '+00:00')).timestamp() * 1000)
    except (ValueError, AttributeError):
        return None


def num(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(',', '')
    mult = 1.0
    for suf, m in (('%', 1.0), ('K', 1e3), ('M', 1e6), ('B', 1e9)):
        if s.upper().endswith(suf):
            s, mult = s[:-1], m
            break
    try:
        return float(s) * mult
    except ValueError:
        return None


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--pages', type=int, default=40)
    ap.add_argument('--per', type=int, default=200)
    ap.add_argument('--out', default=OUT)
    args = ap.parse_args()

    key = _secrets.get('QUANTGIST_API_KEY')
    if not key:
        print('QUANTGIST_API_KEY missing', file=sys.stderr)
        return 2

    seen, rows = set(), []
    for page in range(args.pages):
        try:
            b = get('/v2/backtest', key, limit=args.per, offset=page * args.per)
        except urllib.error.HTTPError as e:
            print('  stopped at page %d: %d %s'
                  % (page, e.code, e.read().decode('utf-8', 'replace')[:120]))
            break
        data = b.get('data') or []
        if not data:
            break
        fresh = 0
        for e in data:
            if e.get('id') in seen:
                continue
            seen.add(e.get('id'))
            fresh += 1
            rows.append({
                't': ms(e.get('release_time')),
                'title': e.get('title'),
                'canonical': e.get('canonical_id'),
                'currency': e.get('currency'),
                'country': e.get('country'),
                'actual': num(e.get('actual')),
                'previous': num(e.get('previous')),
                'forecast': num(e.get('forecast')),
                'impact': e.get('impact_score'),
            })
        print('  page %-3d +%d (total %d)' % (page, fresh, len(rows)))
        # the API repeats the tail once it runs out of fresh rows
        if fresh == 0:
            break

    rows = [r for r in rows if r['t'] and r['actual'] is not None]
    rows.sort(key=lambda r: r['t'])
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, 'w', encoding='utf-8') as fh:
        json.dump(rows, fh, indent=1)

    withprev = sum(1 for r in rows if r['previous'] is not None)
    withfc = sum(1 for r in rows if r['forecast'] is not None)
    fmt = lambda t: datetime.datetime.fromtimestamp(
        t / 1000, datetime.timezone.utc).strftime('%Y-%m-%d')
    print('')
    print('wrote %d releases -> %s' % (len(rows), args.out))
    if rows:
        print('  span      %s .. %s' % (fmt(rows[0]['t']), fmt(rows[-1]['t'])))
    print('  with previous %d   with forecast %d' % (withprev, withfc))
    return 0


if __name__ == '__main__':
    sys.exit(main())
