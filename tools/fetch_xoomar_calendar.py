#!/usr/bin/env python
"""
The PRIMARY calendar importer: xoomar.com -> data/calendar/history.json.

    python tools/fetch_xoomar_calendar.py

Key from configs/secrets.env as XOOMAR_API_KEY. It is OPTIONAL -- the API
answers keyless at 30 requests/minute and 120 with a key -- so a missing key
slows this down rather than breaking it.

TWO ENDPOINTS, BECAUSE ONE DOES NOT COVER EVERYTHING.

  /api/markets/calendar/csv   487 rows, 2020-01 to 2027-12, with real UTC
                              timestamps. Carries FOMC rate decisions (57 of
                              them) and the US data releases -- NFP, CPI,
                              unemployment, GDP. This is the good part: one
                              request, no scraping, minute-accurate.

  /api/markets/rates/{XM,GB,JP}   daily policy-rate series back to 1999/1955/1959
                              with a `changed` flag. The euro area is XM, not
                              EU or EA, both of which return empty.

WHAT THE RATES ENDPOINT IS AND IS NOT. `changed: true` marks a day the policy
rate MOVED. That is a decision date, but only the subset where the decision was
to move -- a hold leaves the series flat and produces no mark. So ECB, BOE and
BOJ marks here are RATE CHANGES, not meeting calendars, and they are labelled
that way. Since 2016 that is 20 ECB, 25 BOE and 6 BOJ. A full meeting calendar
for those three still does not exist in any free source found; see
tools/fetch_centralbank_calendar.py for the four scrapers that failed and why.

THE US SERIES IS DELIBERATELY NOT USED for rate changes. `rates/US` is the Fed
Funds EFFECTIVE rate, which drifts a basis point at a time -- 3,545 "changes"
since 1971 -- so it is a market rate, not a decision. FOMC decisions come from
the calendar CSV instead.

WHERE FRED STILL EARNS ITS PLACE. This covers 2020 onward; FRED's release
history goes back to 2016 and further. Run both -- they merge into one file,
deduped on (kind, minute) -- and FRED fills the years before this starts.
"""

import argparse
import collections
import csv
import datetime
import io
import json
import os
import re
import ssl
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _secrets                                        # noqa: E402
from fetch_centralbank_calendar import to_utc_ms       # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'data', 'calendar', 'history.json')
BASE = 'https://xoomar.com/api/markets/'

#: event_name -> (kind, label). Anything unlisted is skipped and counted, so a
#: new event type shows up in the report rather than vanishing.
#:
#: TWO ROW SHAPES SHARE THIS FEED AND ONLY ONE IS A RELEASE. Compare:
#:
#:   Nonfarm Payrolls (Change in Thousands)  2020-02-01T12:30Z  period 2020-02
#:   Nonfarm Payrolls (Employment Situation) 2026-03-06T12:30Z  period February 2026
#:
#: The first is a DATA SERIES POINT dated to the reference month -- the 1st of
#: every month, which is not when anything was published. The second is the
#: actual release. Marking the series rows would put an NFP line on the first of
#: every month for six years, and it would look plausible. `is_release` below is
#: the guard, and it keys on `period`: a bare `YYYY-MM` is a series point, a
#: human string like "February 2026" or "January 2021 meeting" is a release.
EVENTS = {
    'FOMC Rate Decision': ('FOMC', 'FOMC'),
    'Nonfarm Payrolls (Change in Thousands)': ('NFP', 'NFP'),
    'Nonfarm Payrolls (Employment Situation)': ('NFP', 'NFP'),
    'US Unemployment Rate': ('UNEMPLOYMENT', 'Unemployment'),
    'CPI Inflation YoY': ('CPI', 'CPI'),
    'CPI (Consumer Price Index)': ('CPI', 'CPI'),
    'US Real GDP (Billions USD)': ('GDP', 'GDP'),
}

SERIES_PERIOD = re.compile(r'^\d{4}-\d{2}$')


def is_release(row):
    """True when this row is a published release, not a series observation."""
    return not SERIES_PERIOD.match((row.get('period') or '').strip())


#: The GDP releases arrive under long descriptive names that change every
#: quarter, so they are matched by prefix rather than listed.
GDP_PREFIXES = ('GDP ', 'Gross Domestic Product')

#: country -> (kind, label, announcement hour, zone). Times are each bank's own
#: announcement, converted through that zone's DST rule.
RATE_BANKS = {
    'XM': ('ECB', 'ECB rate change', 13, 45, 'EU'),
    'GB': ('BOE', 'BOE rate change', 12, 0, 'UK'),
    'JP': ('BOJ', 'BOJ rate change', 12, 0, 'JP'),
}


def fetch(path, key):
    hdrs = {'User-Agent': 'NurAI/1.0 (local research tool)'}
    if key:
        hdrs['X-API-Key'] = key
    req = urllib.request.Request(BASE + path, headers=hdrs)
    with urllib.request.urlopen(req, timeout=60,
                                context=ssl._create_unverified_context()) as r:
        return r.read().decode('utf-8', 'replace')


def from_upcoming(key, from_year, to_year):
    """
    Releases that have NOT happened yet, from the JSON view of the calendar.

    THE CSV DOES NOT CARRY THEM in a usable form -- its rows are keyed to a
    reference period and the scheduled future ones arrive without the fields
    that make a mark worth drawing. The JSON view answers the same calendar
    with camelCase keys and includes what is still to come: as of writing,
    CPI on the 11th, FOMC on the 16th and payrolls on 2 October.

    THIS IS WHAT MAKES "in 2h 35m" POSSIBLE. Every other source here is
    historical, so a chart could only ever say how long ago something landed.
    """
    try:
        raw = fetch('calendar?importance=high', key)
        rows = json.loads(raw).get('data') or []
    except Exception as exc:                                # noqa: BLE001
        print('  ! calendar(json): %s' % exc, file=sys.stderr)
        return []
    out = []
    for r in rows:
        name = (r.get('eventName') or '').strip()
        hit = EVENTS.get(name)
        if not hit and name.startswith(GDP_PREFIXES):
            hit = ('GDP', 'GDP')
        if not hit:
            continue
        ts = (r.get('scheduledAt') or '').replace('Z', '+00:00')
        try:
            d = datetime.datetime.fromisoformat(ts)
        except ValueError:
            continue
        if not (from_year <= d.year <= to_year):
            continue
        kind, label = hit
        row = {'t': int(d.timestamp() * 1000), 'kind': kind, 'label': label}
        imp = (r.get('importance') or '').strip().lower()
        if imp in ('high', 'medium', 'low'):
            row['impact'] = imp
        for src, dst in (('actual', 'actual'), ('previous', 'previous')):
            v = r.get(src)
            if v not in (None, ''):
                row[dst] = str(v)
        out.append(row)
    fut = sum(1 for r in out
              if r['t'] > datetime.datetime.now(datetime.timezone.utc).timestamp() * 1000)
    print('  calendar(json): %d events, %d of them still upcoming' % (len(out), fut))
    return out


def from_calendar(key, from_year, to_year):
    raw = fetch('calendar/csv', key)
    rows = list(csv.DictReader(io.StringIO(raw)))
    out, skipped = [], collections.Counter()
    for r in rows:
        name = (r.get('event_name') or '').strip()
        hit = EVENTS.get(name)
        if not hit and name.startswith(GDP_PREFIXES):
            hit = ('GDP', 'GDP')
        if not hit:
            skipped[name] += 1
            continue
        if not is_release(r):
            skipped['%s [series point]' % name] += 1
            continue
        ts = (r.get('scheduled_at_utc') or '').replace('Z', '+00:00')
        try:
            d = datetime.datetime.fromisoformat(ts)
        except ValueError:
            continue
        if not (from_year <= d.year <= to_year):
            continue
        kind, label = hit
        row = {'t': int(d.timestamp() * 1000), 'kind': kind, 'label': label}
        # CARRIED FOR THE HOVER, and only when the feed actually has them.
        # `importance` is the vendor's own high/medium/low; `actual` and
        # `previous` are the two numbers a reader wants beside a release mark.
        # There is no FORECAST column in this feed -- empty on every row -- so
        # nothing carried here can be a surprise, or a sentiment.
        imp = (r.get('importance') or '').strip().lower()
        if imp in ('high', 'medium', 'low'):
            row['impact'] = imp
        for f in ('actual', 'previous'):
            v = (r.get(f) or '').strip()
            if v:
                row[f] = v
        out.append(row)
    print('  calendar/csv: %d rows -> %d events' % (len(rows), len(out)))
    for n, c in skipped.most_common(4):
        print('    skipped %-42s x%d' % (n[:42], c))
    return out


def from_rates(key, from_year, to_year):
    out = []
    for country, (kind, label, hh, mm, zone) in RATE_BANKS.items():
        try:
            data = json.loads(fetch('rates/' + country, key)).get('data', [])
        except Exception as exc:
            print('  ! rates/%s: %s' % (country, exc), file=sys.stderr)
            continue
        n = 0
        for row in data:
            if not row.get('changed'):
                continue
            try:
                d = datetime.date.fromisoformat(row['date'])
            except Exception:
                continue
            if not (from_year <= d.year <= to_year):
                continue
            out.append({'t': to_utc_ms(d, hh, mm, zone), 'kind': kind, 'label': label})
            n += 1
        print('  rates/%-3s %5d rows -> %3d rate changes in range' % (country, len(data), n))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--from-year', type=int, default=2016)
    ap.add_argument('--to-year', type=int, default=2027)
    ap.add_argument('--out', default=OUT)
    ap.add_argument('--replace', action='store_true')
    args = ap.parse_args()

    key = _secrets.get('XOOMAR_API_KEY')
    print('xoomar.com %s' % ('with key' if key else 'keyless (30 req/min)'))

    events = from_calendar(key, args.from_year, args.to_year)
    events += from_upcoming(key, args.from_year, args.to_year)
    events += from_rates(key, args.from_year, args.to_year)
    if not events:
        print('nothing parsed; refusing to write', file=sys.stderr)
        return 2

    if not args.replace and os.path.exists(args.out):
        with open(args.out) as fh:
            try:
                events += json.load(fh)
            except Exception:
                pass

    # DUPLICATES MERGE, THEY DO NOT LOSE.
    #
    # Dropping the second row for a (kind, minute) threw away fields: the CSV
    # view and the JSON view answer the same calendar, and each carries things
    # the other omits -- the JSON one has `importance` on releases the CSV
    # leaves bare. Keeping whichever arrived first meant CPI showed no impact
    # while the vendor plainly had it. Later rows now fill in blanks without
    # overwriting anything already known.
    bykey = {}
    order = []
    for e in sorted(events, key=lambda e: e['t']):
        k = (e['kind'], e['t'] // 60000)
        if k not in bykey:
            bykey[k] = dict(e)
            order.append(k)
            continue
        for f, v in e.items():
            if v not in (None, '') and bykey[k].get(f) in (None, ''):
                bykey[k][f] = v
    out = [bykey[k] for k in order]

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
    print('ECB / BOE / BOJ marks are RATE CHANGES, not every meeting -- a hold')
    print('leaves the rate series flat and produces no mark. FOMC and the US')
    print('data releases are full calendars.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
