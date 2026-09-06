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


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--limit', type=int, default=60)
    ap.add_argument('--out', default=OUT)
    args = ap.parse_args()

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
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
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
