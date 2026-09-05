#!/usr/bin/env python
"""
Real historical release dates from FRED, into data/calendar/history.json.

    python tools/fetch_fred_calendar.py --from-year 2016

The key comes from configs/secrets.env (copy configs/secrets.env.example). An
exported FRED_API_KEY wins over the file, and --key wins over both.

WHY THIS EXISTS. tools/fetch_macro_calendar.py reads a public iCal feed that is
FORWARD-LOOKING -- a few months back, about a year ahead -- so a replay walking
2016-2026 had sourced marks only at its right edge. FRED keeps the actual
release-date history for the US statistical releases going back decades, which
is the only free source of real history found for them.

WHAT IT COVERS, AND WHAT IT CANNOT.

  COVERED, from FRED's own release calendar: CPI, PPI, the Employment Situation
  (which is the NFP and unemployment print -- one release, two headline
  numbers) and GDP.

  NOT COVERED: FOMC, ECB, BOE and BOJ meeting dates. FRED publishes DATA
  releases, not central bank meeting calendars, so no amount of querying it will
  produce them. The iCal importer supplies those from about 2026-05 onward and
  nothing here changes that. Their historical dates are published by each bank
  and would be four more scrapers.

RELEASE IDS ARE DISCOVERED, NOT HARDCODED. `/fred/releases` is paged and its ids
are stable, but writing `release_id = 10` from memory into a file is exactly the
kind of unverifiable constant that silently mislabels a decade of marks. The
names are matched instead, the resolved ids are printed, and a name that matches
nothing is reported rather than skipped quietly.

RELEASE DATES ARE NOT RELEASE TIMES. FRED gives the DAY a release happened, not
the minute. Every US release here goes out at 08:30 New York except GDP, which
is also 08:30 -- so the day is combined with that wall-clock time and converted
through the same DST arithmetic js/chart/newsevents.js uses. Any of these that
was ever published at a different hour will be an hour or two out; the day is
exact, which is what a chart mark is read for.

MERGES, never overwrites: run this after the iCal importer and both sets end up
in one file, deduped on (kind, minute).
"""

import argparse
import collections
import datetime
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _secrets                                        # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'data', 'calendar', 'history.json')
API = 'https://api.stlouisfed.org/fred/'

#: FRED release name -> the kinds it produces. The Employment Situation is one
#: release carrying both headline numbers, so it emits two marks at the same
#: minute; they are separate kinds because a reader filtering for NFP should not
#: also have to know that unemployment shares its release.
WANTED = {
    'Consumer Price Index': [('CPI', 'CPI')],
    'Producer Price Index': [('PPI', 'PPI')],
    'Employment Situation': [('NFP', 'NFP'), ('UNEMPLOYMENT', 'Unemployment')],
    'Gross Domestic Product': [('GDP', 'GDP')],
}

RELEASE_HOUR_NY = (8, 30)      # every release above; see the module docstring


def nth_weekday(y, m, weekday, n):
    first = datetime.date(y, m, 1).weekday()          # Mon=0
    first_sun0 = (first + 1) % 7                      # convert to Sun=0
    return 1 + ((weekday - first_sun0 + 7) % 7) + (n - 1) * 7


def ny_to_utc_ms(d, hh, mm):
    """New York wall clock -> UTC ms, with the post-2007 US DST rule."""
    y = d.year
    dst_from = datetime.datetime(y, 3, nth_weekday(y, 3, 0, 2), 7,
                                 tzinfo=datetime.timezone.utc)
    dst_to = datetime.datetime(y, 11, nth_weekday(y, 11, 0, 1), 6,
                               tzinfo=datetime.timezone.utc)
    naive = datetime.datetime(d.year, d.month, d.day, hh, mm,
                              tzinfo=datetime.timezone.utc)
    offset = 4 if dst_from <= naive < dst_to else 5
    return int((naive.timestamp() + offset * 3600) * 1000)


def api(path, key, **params):
    params.update({'api_key': key, 'file_type': 'json'})
    url = API + path + '?' + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url, timeout=40) as r:
            return json.loads(r.read().decode('utf-8', 'replace'))
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8', 'replace')[:300]
        raise SystemExit('FRED %s failed: HTTP %s %s' % (path, e.code, body))


def resolve_releases(key):
    """Every release id whose name matches one we want. Paged."""
    found, offset = {}, 0
    while True:
        page = api('releases', key, limit=1000, offset=offset)
        rows = page.get('releases', [])
        for r in rows:
            for name in WANTED:
                if r.get('name', '').strip() == name:
                    found[name] = r['id']
        if len(rows) < 1000:
            break
        offset += 1000
    return found


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--key', default=os.environ.get('FRED_API_KEY'))
    ap.add_argument('--from-year', type=int, default=2016)
    ap.add_argument('--to-year', type=int, default=2027)
    ap.add_argument('--out', default=OUT)
    ap.add_argument('--replace', action='store_true',
                    help='discard what is already in the file instead of merging')
    args = ap.parse_args()

    key = args.key or _secrets.get('FRED_API_KEY')
    if not key:
        print('no FRED key. Get a free one at\n'
              '  https://fred.stlouisfed.org/docs/api/api_key.html\n'
              'then put it in configs/secrets.env as\n'
              '  FRED_API_KEY=<key>\n'
              '(copy configs/secrets.env.example), or pass --key <key>.',
              file=sys.stderr)
        return 2
    args.key = key

    ids = resolve_releases(args.key)
    for name in WANTED:
        if name not in ids:
            print('  ! no FRED release named %r -- nothing emitted for it' % name,
                  file=sys.stderr)
    if not ids:
        print('no wanted release matched; nothing written', file=sys.stderr)
        return 2
    print('resolved releases: %s'
          % ', '.join('%s=%d' % (n, i) for n, i in sorted(ids.items())))

    events = []
    for name, rid in sorted(ids.items()):
        got = api('release/dates', args.key, release_id=rid,
                  realtime_start=f'{args.from_year}-01-01',
                  realtime_end=f'{args.to_year}-12-31',
                  include_release_dates_with_no_data='true',
                  limit=10000, sort_order='asc')
        dates = got.get('release_dates', [])
        for row in dates:
            try:
                d = datetime.date.fromisoformat(row['date'])
            except Exception:
                continue
            if not (args.from_year <= d.year <= args.to_year):
                continue
            t = ny_to_utc_ms(d, *RELEASE_HOUR_NY)
            for kind, label in WANTED[name]:
                events.append({'t': t, 'kind': kind, 'label': label})
        print('  %-24s id=%-5d %5d dates' % (name, rid, len(dates)))

    if not args.replace and os.path.exists(args.out):
        with open(args.out) as fh:
            try:
                events += json.load(fh)
            except Exception:
                pass

    seen, out = set(), []
    for e in sorted(events, key=lambda e: e['t']):
        k = (e['kind'], e['t'] // 60000)
        if k in seen:
            continue
        seen.add(k)
        out.append(e)

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, 'w') as fh:
        json.dump(out, fh, indent=1)

    by = collections.Counter(e['kind'] for e in out)
    fmt = lambda ms: datetime.datetime.fromtimestamp(
        ms / 1000, datetime.timezone.utc).strftime('%Y-%m-%d')
    print('')
    print('wrote %d events -> %s' % (len(out), args.out))
    print('  span   %s .. %s' % (fmt(min(e['t'] for e in out)),
                                 fmt(max(e['t'] for e in out))))
    print('  kinds  %s' % ', '.join('%s %d' % kv for kv in sorted(by.items())))
    print('')
    print('FOMC / ECB / BOE / BOJ are NOT in this file from FRED -- it publishes')
    print('data releases, not meeting calendars. Run tools/fetch_macro_calendar.py')
    print('for those; it merges into the same file.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
