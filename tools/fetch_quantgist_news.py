#!/usr/bin/env python
"""
QuantGist market intelligence -> data/news/quantgist.json.

    python tools/fetch_quantgist_news.py

Key from configs/secrets.env as QUANTGIST_API_KEY.

WHY A FILE AND NOT A FETCH FROM THE BROWSER. The key would be in the page
source, readable by anyone with the dev tools open, and the app is served over
plain HTTP on localhost. Every other feed here works the same way: a tool with
the key writes a file, and the chart reads the file.

WHAT THE FREE PLAN ACTUALLY SERVES, measured against the live API rather than
read off the pricing page:

    /v1/news          200  headlines, impact_score, symbols, asset_classes
    /v1/news/radar    200  scored clusters: topic, why_it_matters, confidence,
                           affected_assets -- and the assets list names XAUUSD,
                           EURUSD, USDJPY and GBPUSD directly, which is what
                           makes this usable on these charts at all
    /v1/calendar      200  economic releases with actual and previous
    /v2/backtest      200  point-in-time event query

    /v1/sentiment/*   402  "requires the starter plan"
    /v1/intelligence/* 402 same

SO THERE IS NO SENTIMENT SCORE IN HERE. `impact_score` says how much a story
is expected to MOVE something; it does not say which way. Nothing in this file
is a direction, and the panel that reads it must not imply one -- see the note
in js/ui/newspanel.js.

FORECAST IS NULL ON EVERY CALENDAR ROW, exactly as it is on the xoomar feed. A
release surprise still cannot be computed from any source wired up here.
"""

import argparse
import datetime
import shutil
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _secrets                                        # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'data', 'news', 'quantgist.json')
BASE = 'https://api.quantgist.com'

#: Chart symbol -> the tickers this feed uses for it. The radar's
#: `affected_assets` mixes FX pairs with the ETF proxies a US desk would trade,
#: so gold matches on GLD as well as XAUUSD and a story tagged only GLD is
#: still about the metal on this chart.
ALIASES = {
    'XAUUSD': ['XAUUSD', 'GLD', 'GC', 'IAU'],
    'EURUSD': ['EURUSD', 'FXE', 'DXY', 'UUP'],
    'GBPUSD': ['GBPUSD', 'FXB', 'DXY', 'UUP'],
    'USDJPY': ['USDJPY', 'FXY', 'DXY', 'UUP'],
    'AUDUSD': ['AUDUSD', 'FXA', 'DXY', 'UUP'],
    'USDCAD': ['USDCAD', 'FXC', 'DXY', 'UUP'],
    'AUDJPY': ['AUDJPY', 'FXA', 'FXY'],
    'BTCUSD': ['BTCUSD', 'BITO', 'COIN'],
    'NAS100': ['QQQ', 'NDX', 'SPY'],
    'US30': ['DIA', 'SPY'],
}


def get(path, key, **params):
    url = BASE + path
    if params:
        url += '?' + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={
        'User-Agent': 'NurAI/1.0 (local research tool)', 'X-API-Key': key})
    with urllib.request.urlopen(req, timeout=60,
                                context=ssl._create_unverified_context()) as r:
        return json.loads(r.read().decode('utf-8', 'replace'))


def try_get(path, key, **params):
    """A paywalled or missing endpoint costs its own rows, never the run."""
    try:
        return get(path, key, **params), None
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8', 'replace')[:200]
        return None, '%d %s' % (e.code, body)
    except Exception as exc:                                # noqa: BLE001
        return None, str(exc)[:200]


def ms(iso):
    if not iso:
        return None
    try:
        return int(datetime.datetime.fromisoformat(
            iso.replace('Z', '+00:00')).timestamp() * 1000)
    except ValueError:
        return None


#: The smallest automatic interval that will be honoured, in minutes. This
#: spends somebody's API quota, and below five minutes the only thing that
#: changes is the bill: the radar re-clusters in hours and the rail drops
#: anything older than two days, so a one-minute poll fetches one document sixty
#: times an hour. The Settings panel offers nothing lower; this is the floor for
#: a hand-edited config.
MIN_INTERVAL = 5


def configured_interval(path=None):
    """`news.fetch_minutes` from configs/alerts.json, or 0 for manual only.

    ONE DEFINITION OF THE INTERVAL, READ HERE. Both supervisors -- the timer in
    serve.py while the app is open, and alerts_daemon.py when it is not -- call
    this tool with --scheduled and nothing else, so neither of them carries a
    copy of this rule and neither needs restarting when the number changes.
    Read on every run for the same reason: the point of putting it in Settings
    is that it takes effect without one.
    """
    path = path or os.path.join(ROOT, 'configs', 'alerts.json')
    try:
        with open(path, encoding='utf-8') as fh:
            news = (json.load(fh) or {}).get('news') or {}
        n = int(news.get('fetch_minutes') or 0)
    except (OSError, ValueError, TypeError):
        return 0
    return max(MIN_INTERVAL, n) if n > 0 else 0


def file_age_minutes(path):
    """Minutes since the feed was FETCHED, or None when there is no file.

    `fetchedAt` FROM INSIDE THE FILE, not the mtime, for the same reason the
    Settings panel reads it: the tool writes that field at the moment it spoke
    to the API, while mtime moves when a file is copied, restored or touched by
    a sync -- and an mtime bumped by a backup would silently suppress a fetch
    that was genuinely due. mtime is the fallback and nothing more.
    """
    if not os.path.exists(path):
        return None
    stamp = None
    try:
        with open(path, encoding='utf-8') as fh:
            stamp = (json.load(fh) or {}).get('fetchedAt')
    except (OSError, ValueError):
        pass
    if not isinstance(stamp, (int, float)):
        try:
            stamp = os.path.getmtime(path) * 1000
        except OSError:
            return None
    return (time.time() * 1000 - stamp) / 60000.0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--limit', type=int, default=60)
    ap.add_argument('--out', default=OUT)
    # THE FILE'S OWN AGE IS THE CLOCK, which is what makes this safe to call
    # from more than one place. serve.py runs a timer while the app is open and
    # alerts_daemon.py runs one when it is not; without this they would both
    # fetch on their own schedule and the feed would be pulled twice as often
    # as anybody asked for. Asking "is what is on disk older than N minutes"
    # has no such problem: whoever gets there first satisfies the interval for
    # everyone, and a restart does not reset a countdown, because there is no
    # countdown to reset.
    ap.add_argument('--if-older-than', type=int, default=0, metavar='MIN',
                    help='fetch only when the existing file is older than this '
                         'many minutes; 0 (default) always fetches')
    ap.add_argument('--scheduled', action='store_true',
                    help='take the interval from news.fetch_minutes in '
                         'configs/alerts.json, and do nothing when it is 0')
    args = ap.parse_args()

    due = args.if_older_than
    if args.scheduled:
        due = configured_interval()
        if not due:
            # OFF IS A SETTING, NOT A FAILURE. Exit 0, so a supervisor calling
            # this every minute does not log an error a minute for honouring
            # the configuration it was given.
            print('automatic news fetch is off (news.fetch_minutes = 0)')
            return 0

    if due > 0:
        age = file_age_minutes(args.out)
        if age is not None and age < due:
            # Exit 0 -- NOT fetching because it is not due is success, and a
            # non-zero exit here would make every supervisor log an error a
            # minute for doing exactly what it was told.
            print('not due: %s is %.1f min old, interval is %d min'
                  % (args.out, age, due))
            return 0

    key = _secrets.get('QUANTGIST_API_KEY')
    if not key:
        print('QUANTGIST_API_KEY missing from configs/secrets.env', file=sys.stderr)
        return 2

    notes = []
    radar, err = try_get('/v1/news/radar', key, limit=args.limit)
    if err:
        notes.append('radar: ' + err)
    news, err = try_get('/v1/news', key, limit=args.limit)
    if err:
        notes.append('news: ' + err)

    clusters = []
    for c in ((radar or {}).get('items') or []):
        clusters.append({
            'id': c.get('id'),
            'topic': c.get('topic'),
            'headline': c.get('headline'),
            'why': c.get('why_it_matters'),
            'impact': c.get('impact_score'),
            'confidence': c.get('confidence'),
            'assets': c.get('affected_assets') or [],
            'sources': c.get('source_count'),
            'firstSeen': ms(c.get('first_seen')),
            'latestSeen': ms(c.get('latest_seen')),
        })

    heads = []
    for h in ((news or {}).get('data') or []):
        heads.append({
            'id': h.get('id'),
            'title': h.get('title'),
            'summary': h.get('summary'),
            'impact': h.get('impact_score'),
            'symbols': h.get('symbols') or [],
            'classes': h.get('asset_classes') or [],
            'why': h.get('market_relevance_reason'),
            't': ms(h.get('published_at') or h.get('release_time')),
        })

    doc = {
        'fetchedAt': int(datetime.datetime.now(datetime.timezone.utc).timestamp() * 1000),
        'source': 'quantgist',
        # Nothing in this document is a direction -- see the module docstring.
        # The flag is written so the panel states the limitation from the DATA
        # rather than from a constant someone can forget to update when a paid
        # plan starts returning scores.
        'hasSentiment': False,
        'aliases': ALIASES,
        'clusters': clusters,
        'headlines': heads,
        'notes': notes,
    }
    # ---------------------------------------------------------------- #
    # A TOTAL FAILURE MUST NOT OVERWRITE A GOOD FILE.                    #
    # ---------------------------------------------------------------- #
    # This cost real data once. Both endpoints answered 401 -- the API key had
    # been revoked -- and this function cheerfully wrote a document with zero
    # clusters and zero headlines over two days of perfectly good news, then
    # exited 0 so every caller believed it had worked. The panel went blank and
    # nothing on screen said why.
    #
    # An empty result is only ever legitimate as a QUIET FEED, and a quiet feed
    # does not come with an error note attached. So: nothing came back AND
    # something failed means the fetch failed, full stop -- leave the previous
    # file where it is and exit non-zero. An empty result with no errors is
    # still written, because that genuinely is the news.
    if not clusters and not heads and notes:
        print('NOT WRITING %s -- every endpoint failed and there is nothing to '
              'write. The existing file is left untouched.' % args.out,
              file=sys.stderr)
        for n in notes:
            print('  ! %s' % n, file=sys.stderr)
        return 3

    os.makedirs(os.path.dirname(args.out), exist_ok=True)

    # KEEP ONE GENERATION BACK, the same way the alerts writer does. A fetch
    # that returns a THINNER but non-empty document is not an error and is
    # written -- and `.prev` is then the only way back if it turns out to have
    # been a bad hour on the feed rather than a quiet one.
    if os.path.exists(args.out):
        try:
            shutil.copyfile(args.out, args.out + '.prev')
        except OSError as exc:
            print('  ! could not keep a .prev: %s' % exc, file=sys.stderr)

    tmp = args.out + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as fh:
        json.dump(doc, fh, indent=1)
    os.replace(tmp, args.out)

    print('wrote %s' % args.out)
    print('  clusters  %d' % len(clusters))
    print('  headlines %d' % len(heads))
    for n in notes:
        print('  ! %s' % n)
    print('')
    print('No sentiment field: /v1/sentiment/* answers 402 on the free plan, and')
    print('impact_score is a magnitude, not a direction.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
