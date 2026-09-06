#!/usr/bin/env python
"""
Historical central bank decision dates, into data/calendar/history.json.

    python tools/fetch_centralbank_calendar.py --from-year 2016

WHY SCRAPING AT ALL. FRED covers the US DATA releases and nothing else -- its
one FOMC-shaped entry, release 101 "FOMC Press Release", turns out to be a
DAILY release (365 dates a year), not a meeting calendar, so it cannot supply
decision dates. The public iCal feed the other importer reads is
forward-looking. Each bank publishes its own past meetings and that is the only
free source of real history for them.

STATUS: NONE OF THIS IS GOOD ENOUGH TO SHIP YET, and the numbers below are why.
Run with `--check` (the default advice) and read the per-year table before
trusting anything. Measured 2026-09-05:

    FOMC  2016:8  2017:6  2018:7  2019:8  2020:10  2021+:0
    BOE   only 5 dates, all 2026
    ECB   only 5 dates, spread 2024-2026
    BOJ   only 5 dates, all 2025

  FOMC is the closest to working and still wrong. The Fed's per-year archive
  pages parse cleanly -- one line per meeting, "January 29-30 Meeting - 2019" --
  but CROSS-MONTH MEETINGS ARE ABSENT FROM THE PAGE ENTIRELY. 2017 should have
  eight meetings; the archive lists six, and searching the raw HTML for
  "February 1" and "November 1" returns nothing, so the January 31-February 1
  and October 31-November 1 meetings are not there to be parsed. That is a gap
  in the source, not in the regex. Years from 2021 are not under the
  fomchistorical<YYYY>.htm pattern at all and need the current calendar page,
  which has a different shape again.

  BOE, ECB and BOJ all fail the same way: their calendar pages render dates in
  JavaScript, so the HTML a plain fetch returns carries almost none of them.
  The author of always-free-macro-calendar hit exactly this and resorted to
  hardcoding ECB and BOJ dates, updated by hand each year.

WHAT THIS MEANS FOR THE DATA. This importer does NOT currently write to
data/calendar/history.json in any run that matters, because a chart marked with
six of eight FOMC meetings is worse than one marked with none: the missing two
look like quiet days. `--check` reports and writes nothing; a plain run refuses
when a parser returns empty.

THE HONEST ALTERNATIVE, if these marks are wanted: hand-curate them once. Four
banks at roughly eight meetings a year over eleven years is about 350 dates --
an afternoon against published PDFs, permanent afterwards, and verifiable line
by line. That beats four scrapers against pages built to be read rather than
parsed. Drop the result into data/calendar/history.json in the same
{t, kind, label} shape and it draws solid like everything else.

FOUR SITES, FOUR SHAPES, AND THEY WILL DRIFT. Every parser reports what it found
per year so a site redesign shows up as "0 dates in 2019" rather than as marks
quietly vanishing from a chart.

WHAT TIME A DECISION LANDS. The mark is the moment the decision is ANNOUNCED,
not when the meeting began, because that is the bar that moves:

    FOMC   the LAST day of a two-day meeting, 14:00 New York
    ECB    decision day, 13:45 Frankfurt (press conference 14:30)
    BOE    decision day, 12:00 London
    BOJ    the LAST day of a two-day meeting, around 03:00 UTC -- the BOJ does
           not fix a time and the statement lands late morning Tokyo

Times are converted through each zone's own DST rule, not a fixed offset.

MERGES into the same file as the other two importers, deduped on (kind, minute).
"""

import argparse
import collections
import datetime
import html
import json
import os
import re
import ssl
import sys
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'data', 'calendar', 'history.json')
UA = {'User-Agent': 'Mozilla/5.0 (compatible; NurAI/1.0; local research tool)'}

MONTHS = {m: i + 1 for i, m in enumerate(
    ['January', 'February', 'March', 'April', 'May', 'June', 'July',
     'August', 'September', 'October', 'November', 'December'])}
MON_RE = '|'.join(MONTHS)


def fetch(url, insecure=False):
    """Page text with tags stripped, one element per line."""
    ctx = ssl._create_unverified_context() if insecure else None
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=40, context=ctx) as r:
        h = r.read().decode('utf-8', 'replace')
    h = re.sub(r'<(script|style)[^>]*>.*?</\1>', ' ', h, flags=re.S | re.I)
    h = re.sub(r'<[^>]+>', '\n', h)
    return [l.strip() for l in html.unescape(h).split('\n') if l.strip()]


# --------------------------------------------------------------- timezones
def _nth_weekday(y, m, weekday, n):
    first = datetime.date(y, m, 1).weekday()
    first_sun0 = (first + 1) % 7
    return 1 + ((weekday - first_sun0 + 7) % 7) + (n - 1) * 7


def _last_weekday(y, m, weekday):
    d = datetime.date(y, m, 1)
    nxt = datetime.date(y + (m == 12), (m % 12) + 1, 1)
    last = nxt - datetime.timedelta(days=1)
    back = (last.weekday() + 1) % 7 - weekday
    return last.day - (back % 7)


def to_utc_ms(d, hh, mm, zone):
    """Wall clock in `zone` -> UTC ms. Only the zones this file needs."""
    y = d.year
    naive = datetime.datetime(d.year, d.month, d.day, hh, mm,
                              tzinfo=datetime.timezone.utc)
    if zone == 'NY':
        lo = datetime.datetime(y, 3, _nth_weekday(y, 3, 0, 2), 7, tzinfo=datetime.timezone.utc)
        hi = datetime.datetime(y, 11, _nth_weekday(y, 11, 0, 1), 6, tzinfo=datetime.timezone.utc)
        off = -4 if lo <= naive < hi else -5
    elif zone in ('EU', 'UK'):
        # EU summer time: last Sunday March 01:00 UTC to last Sunday October 01:00 UTC
        lo = datetime.datetime(y, 3, _last_weekday(y, 3, 0), 1, tzinfo=datetime.timezone.utc)
        hi = datetime.datetime(y, 10, _last_weekday(y, 10, 0), 1, tzinfo=datetime.timezone.utc)
        summer = lo <= naive < hi
        off = (2 if summer else 1) if zone == 'EU' else (1 if summer else 0)
    elif zone == 'JP':
        off = 9                                  # Japan has no DST
    else:
        off = 0
    return int((naive.timestamp() - off * 3600) * 1000)


# ------------------------------------------------------------------- FOMC
def fomc(from_year, to_year):
    """
    federalreserve.gov. Past years live on fomchistorical<YYYY>.htm, where each
    meeting is one line: "January 29-30 Meeting - 2019". The decision is
    announced on the LAST day at 14:00 New York.

    Unscheduled meetings ("October 4 (unscheduled)") are KEPT -- an emergency
    cut is the most market-moving FOMC event there is, and dropping it because
    it was not on the schedule would remove exactly the bars worth looking at.
    """
    out = []
    pat = re.compile(rf'^({MON_RE})\s+(\d+)(?:\s*[-–]\s*(?:({MON_RE})\s+)?(\d+))?'
                     rf'.*?(?:Meeting|unscheduled).*?-\s*(\d{{4}})\s*$')
    for y in range(from_year, to_year + 1):
        try:
            lines = fetch(f'https://www.federalreserve.gov/monetarypolicy/fomchistorical{y}.htm')
        except urllib.error.HTTPError:
            continue                              # year not archived yet
        except Exception as exc:
            print('  ! FOMC %d: %s' % (y, exc), file=sys.stderr)
            continue
        found = 0
        for line in lines:
            m = pat.match(line)
            if not m:
                continue
            mon1, d1, mon2, d2, yr = m.groups()
            yr = int(yr)
            if not (from_year <= yr <= to_year):
                continue
            month = MONTHS[mon2] if mon2 else MONTHS[mon1]
            day = int(d2) if d2 else int(d1)
            try:
                d = datetime.date(yr, month, day)
            except ValueError:
                continue
            out.append({'t': to_utc_ms(d, 14, 0, 'NY'), 'kind': 'FOMC', 'label': 'FOMC'})
            found += 1
        print('  FOMC %d: %d meetings' % (y, found))
    return out


# -------------------------------------------------------------------- BOE
def boe(from_year, to_year):
    """
    bankofengland.co.uk. The MPC announces at 12:00 London. Dates appear as
    "Thursday 6 February 2025" style entries across the upcoming-dates page and
    the decision archive.
    """
    out, seen = [], set()
    urls = ['https://www.bankofengland.co.uk/monetary-policy/upcoming-mpc-dates',
            'https://www.bankofengland.co.uk/news/monetary-policy-summary-and-minutes']
    pat = re.compile(rf'\b(\d{{1,2}})\s+({MON_RE})\s+(\d{{4}})\b')
    for u in urls:
        try:
            lines = fetch(u)
        except Exception as exc:
            print('  ! BOE %s: %s' % (u.rsplit('/', 1)[-1], exc), file=sys.stderr)
            continue
        for line in lines:
            for d1, mon, yr in pat.findall(line):
                yr = int(yr)
                if not (from_year <= yr <= to_year):
                    continue
                try:
                    d = datetime.date(yr, MONTHS[mon], int(d1))
                except ValueError:
                    continue
                if d in seen:
                    continue
                seen.add(d)
                out.append({'t': to_utc_ms(d, 12, 0, 'UK'), 'kind': 'BOE', 'label': 'BOE'})
    print('  BOE: %d dates' % len(out))
    return out


# -------------------------------------------------------------------- ECB
def ecb(from_year, to_year):
    """
    ecb.europa.eu. Monetary policy decisions are announced at 13:45 Frankfurt.
    The site's certificate chain does not verify from this environment, so the
    fetch is explicitly insecure -- acceptable for a public calendar page whose
    contents are checked by the parser anyway, and stated here rather than
    hidden behind a global override.
    """
    out, seen = [], set()
    pat = re.compile(rf'\b(\d{{1,2}})\s+({MON_RE})\s+(\d{{4}})\b')
    for u in ['https://www.ecb.europa.eu/press/calendars/mgcgc/html/index.en.html',
              'https://www.ecb.europa.eu/press/press_conference/monetary-policy-statement/html/index.en.html']:
        try:
            lines = fetch(u, insecure=True)
        except Exception as exc:
            print('  ! ECB %s: %s' % (u.rsplit('/', 2)[-2], exc), file=sys.stderr)
            continue
        for line in lines:
            for d1, mon, yr in pat.findall(line):
                yr = int(yr)
                if not (from_year <= yr <= to_year):
                    continue
                try:
                    d = datetime.date(yr, MONTHS[mon], int(d1))
                except ValueError:
                    continue
                if d in seen:
                    continue
                seen.add(d)
                out.append({'t': to_utc_ms(d, 13, 45, 'EU'), 'kind': 'ECB', 'label': 'ECB'})
    print('  ECB: %d dates' % len(out))
    return out


# -------------------------------------------------------------------- BOJ
def boj(from_year, to_year):
    """
    boj.or.jp. Monetary Policy Meetings run two days and the statement lands
    late morning Tokyo; 12:00 JST is used, which is 03:00 UTC.
    """
    out, seen = [], set()
    pat = re.compile(rf'({MON_RE})\s+(\d{{1,2}})\s*(?:[-–]\s*(\d{{1,2}}))?,?\s*(\d{{4}})')
    for u in ['https://www.boj.or.jp/en/mopo/mpmsche_minu/index.htm',
              'https://www.boj.or.jp/en/mopo/mpmdeci/mpr_2025/index.htm']:
        try:
            lines = fetch(u)
        except Exception as exc:
            print('  ! BOJ %s: %s' % (u.rsplit('/', 2)[-2], exc), file=sys.stderr)
            continue
        for line in lines:
            for mon, d1, d2, yr in pat.findall(line):
                yr = int(yr)
                if not (from_year <= yr <= to_year):
                    continue
                try:
                    d = datetime.date(yr, MONTHS[mon], int(d2 or d1))
                except ValueError:
                    continue
                if d in seen:
                    continue
                seen.add(d)
                out.append({'t': to_utc_ms(d, 12, 0, 'JP'), 'kind': 'BOJ', 'label': 'BOJ'})
    print('  BOJ: %d dates' % len(out))
    return out


BANKS = {'fomc': fomc, 'boe': boe, 'ecb': ecb, 'boj': boj}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--from-year', type=int, default=2016)
    ap.add_argument('--to-year', type=int, default=2027)
    ap.add_argument('--banks', nargs='+', default=list(BANKS), choices=list(BANKS))
    ap.add_argument('--out', default=OUT)
    ap.add_argument('--check', action='store_true',
                    help='report what each parser found and write nothing')
    args = ap.parse_args()

    events = []
    for b in args.banks:
        events += BANKS[b](args.from_year, args.to_year)

    per = collections.defaultdict(collections.Counter)
    for e in events:
        y = datetime.datetime.fromtimestamp(e['t'] / 1000, datetime.timezone.utc).year
        per[e['kind']][y] += 1
    print('')
    print('PER YEAR (a 0 where meetings certainly happened means a parser broke)')
    for kind in sorted(per):
        yrs = per[kind]
        row = ' '.join('%d:%d' % (y, yrs.get(y, 0))
                       for y in range(args.from_year, args.to_year + 1))
        print('  %-5s %s' % (kind, row))

    if args.check:
        print('\n--check: nothing written')
        return 0
    if not events:
        print('nothing parsed; refusing to write', file=sys.stderr)
        return 2

    if os.path.exists(args.out):
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
    print('')
    print('wrote %d events -> %s' % (len(out), args.out))
    print('  kinds  %s' % ', '.join('%s %d' % kv for kv in sorted(by.items())))
    return 0


if __name__ == '__main__':
    sys.exit(main())
