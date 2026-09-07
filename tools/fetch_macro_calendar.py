#!/usr/bin/env python
"""
Pull the public macro calendar into data/calendar/history.json.

    python tools/fetch_macro_calendar.py

SOURCE. The iCal feed published by github.com/yyu0310/always-free-macro-calendar
-- FOMC, ECB, BOE and BOJ rate decisions plus US CPI, PPI and unemployment,
with real release timestamps rather than schedule guesses.

WHAT THIS DOES NOT GIVE YOU, AND IT IS THE FIRST THING TO KNOW. The feed is
FORWARD-LOOKING. At the time of writing it holds 33 events running from
2026-05-12 to 2027-07-30 and nothing before that: Google Calendar's basic.ics
exposes recent and upcoming events, not an archive. So this backfills the last
few months of a replay window and none of the years before it.

Two other routes were tried and closed:

  THE REPOSITORY HAS NO DATASET. It is a Google Apps Script that scrapes central
  bank pages and calls Finnhub, then writes into a Google Calendar. There is no
  CSV or JSON to clone.

  FINNHUB'S CALENDAR IS PAID. `/calendar/economic` returns HTTP 403 -- "You
  don't have access to this resource" -- on a free key, while `/quote` on the
  same key returns 200. The economic calendar is not in the free tier, so the
  script's own US data path is unavailable to a free key.

WHAT WOULD GIVE REAL HISTORY, if it is wanted later: FRED publishes actual
release dates for CPI, PPI and the Employment Situation going back decades
(`/fred/releases/dates`, free with a key), and the ECB, BOE and BOJ publish
their past meeting calendars. That is a different importer and a bigger job than
this one; this file is deliberately the small honest version.

OUTPUT is the shape js/chart/newsevents.js already merges:

    [{"t": 1780000000000, "kind": "FOMC", "label": "FOMC"}, ...]

`t` is UTC epoch milliseconds. Anything written here draws SOLID, because these
are real timestamps -- unlike the derived NFP marks, which are dashed because a
schedule rule only knows the day.
"""

import argparse
import json
import os
import re
import sys
import urllib.request

ICS = ('https://calendar.google.com/calendar/ical/'
       '55a4f43e580604a1dc84e794620385a01c4a127f30199bed4d3c66de4c87d5de'
       '%40group.calendar.google.com/public/basic.ics')

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'data', 'calendar', 'history.json')

#: Feed titles are Traditional Chinese; match on the ASCII prefix and the one
#: event that has none. Order matters -- FOMC minutes must be tested before the
#: bare FOMC rate decision or every minutes entry is mislabelled.
RULES = [
    ('FOMC', '會議紀錄', 'FOMC', 'FOMC minutes'),
    ('FOMC', None, 'FOMC', 'FOMC'),
    ('ECB', None, 'ECB', 'ECB'),
    ('BOE', None, 'BOE', 'BOE'),
    ('BOJ', None, 'BOJ', 'BOJ'),
    ('US CPI', None, 'CPI', 'CPI'),
    ('US PPI', None, 'PPI', 'PPI'),
    ('US NFP', None, 'NFP', 'NFP'),
    ('US', '失業率', 'UNEMPLOYMENT', 'Unemployment'),
]


def classify(summary):
    for prefix, contains, kind, label in RULES:
        if not summary.startswith(prefix):
            continue
        if contains and contains not in summary:
            continue
        return kind, label
    return None, None


def parse_dt(value):
    """ICS DTSTART -> UTC epoch ms. Only the UTC (`...Z`) form is emitted here."""
    m = re.match(r'^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})Z$', value)
    if not m:
        return None
    import datetime
    y, mo, d, hh, mm, ss = (int(x) for x in m.groups())
    dt = datetime.datetime(y, mo, d, hh, mm, ss, tzinfo=datetime.timezone.utc)
    return int(dt.timestamp() * 1000)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--url', default=ICS)
    ap.add_argument('--out', default=OUT)
    ap.add_argument('--merge', action='store_true',
                    help='keep events already in the output file and add to them')
    args = ap.parse_args()

    req = urllib.request.Request(args.url,
                                 headers={'User-Agent': 'NurAI/1.0 (local research tool)'})
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            raw = r.read().decode('utf-8', 'replace')
    except Exception as exc:
        print('fetch failed: %s: %s' % (type(exc).__name__, exc), file=sys.stderr)
        return 2

    events, skipped = [], []
    for block in re.findall(r'BEGIN:VEVENT(.*?)END:VEVENT', raw, re.S):
        dt = re.search(r'^DTSTART[^:]*:(\S+)', block, re.M)
        sm = re.search(r'^SUMMARY:(.*)$', block, re.M)
        if not dt or not sm:
            continue
        t = parse_dt(dt.group(1))
        kind, label = classify(sm.group(1).strip())
        if t is None or kind is None:
            skipped.append(sm.group(1).strip())
            continue
        events.append({'t': t, 'kind': kind, 'label': label})

    if args.merge and os.path.exists(args.out):
        with open(args.out) as fh:
            events += json.load(fh)

    # dedupe on (kind, minute): re-running must not double every event
    seen, out = set(), []
    for e in sorted(events, key=lambda e: e['t']):
        key = (e['kind'], e['t'] // 60000)
        if key in seen:
            continue
        seen.add(key)
        out.append(e)

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, 'w') as fh:
        json.dump(out, fh, indent=1)

    import collections
    by = collections.Counter(e['kind'] for e in out)
    lo = min((e['t'] for e in out), default=0)
    hi = max((e['t'] for e in out), default=0)
    import datetime
    fmt = lambda ms: datetime.datetime.fromtimestamp(ms / 1000, datetime.timezone.utc).strftime('%Y-%m-%d')
    print('wrote %d events -> %s' % (len(out), args.out))
    print('  span   %s .. %s' % (fmt(lo), fmt(hi)))
    print('  kinds  %s' % ', '.join('%s %d' % kv for kv in sorted(by.items())))
    if skipped:
        print('  skipped %d unrecognised titles' % len(skipped), file=sys.stderr)
    print('')
    print('These draw SOLID (real timestamps). NFP stays derived and dashed')
    print('except where this feed supplies one. Nothing before the span above')
    print('has a sourced mark -- see this file\'s header for why.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
