#!/usr/bin/env python
"""
A hawkish/dovish score for FOMC statements, and a check that it reads English.

    python tools/fomc_tone.py

WHY A LEXICON AND NOT A CLASSIFIER. A classifier needs labels, and nobody
publishes "this statement was hawkish" with a number attached -- the labels
would have to come from the market reaction, which is the thing the score is
supposed to help predict. Training on that is circular. A transparent lexicon
can be argued with, and every term below can be checked against a statement by
eye.

TOPIC + DIRECTION, NOT BARE WORDS. "Inflation" is not hawkish; "inflation
remains elevated" is, and "inflation has eased" is the opposite. Scoring word
lists alone gets this backwards on roughly half the sentences, because the
Fed's vocabulary barely changes between meetings -- what changes is which verb
attaches to which noun. Each rule below is a (topic, direction) pair matched
inside one sentence.

THE LEVEL IS ALMOST USELESS; THE CHANGE IS THE SIGNAL. Statements are written
by editing the last one, so the absolute tone drifts very little and most of it
is boilerplate the market has already priced. `d_tone` -- this statement against
the previous -- is the communication surprise, and it is what to feed a model.

VALIDATION IS THE POINT OF THIS FILE. A tone score that cannot be checked is a
number with a nice name. Three checks run below, in order of how much they
prove:

  1. ACTION      does tone line up with what the Committee did at that meeting?
                 A scorer that calls hike statements dovish is broken, and this
                 catches it without any market data.
  2. NEXT ACTION does tone at this meeting anticipate the NEXT decision? This is
                 the honest test of whether the text carries policy information.
  3. GOLD        does d_tone line up with what XAUUSD did in the hour after?
                 Uses tools/event_impact_eval.py's output, so it inherits the
                 broker-clock correction rather than repeating it.
"""

import argparse
import collections
import json
import math
import os
import re
import ssl
import statistics
import sys
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _secrets                                            # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STMT = os.path.join(ROOT, 'data', 'research', 'fomc_statements.json')
IMPACT = os.path.join(ROOT, 'data', 'research', 'event_impact.jsonl')
OUT = os.path.join(ROOT, 'data', 'research', 'fomc_tone.json')

#: (topic, hawkish direction, dovish direction). Matched within a sentence.
#:
#: The words are the Fed's own. "Elevated", "moderated", "solid", "softened"
#: and "restrictive" are terms of art in these statements and they move
#: deliberately -- the Committee changes one adjective and the market reads it,
#: which is exactly why a topic/direction pair is the right unit.
RULES = [
    (r'inflation|price', r'elevated|high|persist|firm|increas|rose|rising|remains?\s+somewhat\s+elevated',
     r'eased|moderat|declin|slow|progress|lower|come\s+down|subdued'),
    (r'employment|labor|job|unemploy', r'strong|solid|robust|tight|low\s+unemployment|gains?\s+have\s+been\s+strong',
     r'soften|slow|moderat|weak|decelerat|low\s+job\s+gains|has\s+risen|edged\s+up'),
    (r'polic|stance|target\s+range|federal\s+funds', r'restrictiv|firm|rais|tighten|increase\s+the\s+target',
     r'accommodat|lower|cut|eas|reduce\s+the\s+target|support'),
    (r'risk|outlook', r'upside\s+risks?\s+to\s+inflation|inflation\s+risks?',
     r'downside\s+risks?|uncertain|risks?\s+to\s+employment'),
    (r'growth|activity|spending|demand', r'strong|solid|robust|expand.{0,20}solid|picked\s+up',
     r'slow|moderat|soften|weak|declin'),
]

#: The decision itself, read out of the statement rather than a rate series --
#: it is stated in words in every one of them.
RAISE = re.compile(r'rais\w*\s+the\s+target\s+range|increase\s+the\s+target\s+range', re.I)
CUT = re.compile(r'lower\w*\s+the\s+target\s+range|reduc\w*\s+the\s+target\s+range', re.I)


def sentences(text):
    return [s for s in re.split(r'(?<=[.;])\s+', text) if len(s) > 20]


def tone(paras):
    """`(score, hawk, dove)` -- score in [-1, +1], hawkish positive."""
    hawk = dove = 0
    for s in sentences(' '.join(paras)):
        low = s.lower()
        for topic, hre, dre in RULES:
            if not re.search(topic, low):
                continue
            h = len(re.findall(hre, low))
            d = len(re.findall(dre, low))
            # A sentence saying inflation eased AND remains elevated is genuinely
            # both; counting both is more honest than picking a winner.
            hawk += h
            dove += d
    total = hawk + dove
    return ((hawk - dove) / total if total else 0.0), hawk, dove


def action_of(paras):
    text = ' '.join(paras)
    if RAISE.search(text):
        return 1
    if CUT.search(text):
        return -1
    return 0


def fred_series(series, key):
    url = 'https://api.stlouisfed.org/fred/series/observations?' + urllib.parse.urlencode({
        'series_id': series, 'api_key': key, 'file_type': 'json',
        'observation_start': '2014-01-01'})
    with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'NurAI/1.0'}),
                                timeout=60, context=ssl._create_unverified_context()) as r:
        obs = json.loads(r.read().decode('utf-8', 'replace'))['observations']
    out = {}
    for o in obs:
        try:
            out[o['date']] = float(o['value'])
        except ValueError:
            pass
    return out


def agreement(pairs):
    """Share of pairs where the two signs agree, ignoring zeros on either side."""
    used = [(a, b) for a, b in pairs if a and b]
    if not used:
        return float('nan'), 0
    ok = sum(1 for a, b in used if (a > 0) == (b > 0))
    return 100.0 * ok / len(used), len(used)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--out', default=OUT)
    args = ap.parse_args()

    stmts = [s for s in json.load(open(STMT, encoding='utf-8')) if s['primary']]
    stmts.sort(key=lambda s: s['date'])
    print('scoring %d statements  %s .. %s'
          % (len(stmts), stmts[0]['date'], stmts[-1]['date']))

    rows, prev = [], None
    for s in stmts:
        sc, hawk, dove = tone(s['paragraphs'])
        act = action_of(s['paragraphs'])
        rows.append({'date': s['date'], 'tone': round(sc, 4), 'hawk': hawk, 'dove': dove,
                     'action': act, 'words': s['words'],
                     'd_tone': None if prev is None else round(sc - prev, 4)})
        prev = sc

    with open(args.out, 'w', encoding='utf-8') as fh:
        json.dump(rows, fh, indent=1)

    print('')
    print('tone: median %+.3f   range %+.3f .. %+.3f'
          % (statistics.median(r['tone'] for r in rows),
             min(r['tone'] for r in rows), max(r['tone'] for r in rows)))
    acts = collections.Counter(r['action'] for r in rows)
    print('decisions read from the text: %d hikes, %d cuts, %d holds'
          % (acts[1], acts[-1], acts[0]))

    # ---- check 1: does tone match the decision at THAT meeting? -------------
    print('')
    print('CHECK 1  tone at the meeting vs what was decided there')
    for name, sel in (('hike', 1), ('hold', 0), ('cut', -1)):
        v = [r['tone'] for r in rows if r['action'] == sel]
        if v:
            print('   %-5s n=%-3d median tone %+.3f' % (name, len(v), statistics.median(v)))

    # ---- check 2: does tone anticipate the NEXT decision? -------------------
    pairs = [(rows[i]['tone'], rows[i + 1]['action']) for i in range(len(rows) - 1)]
    pct, n = agreement(pairs)
    print('')
    print('CHECK 2  tone vs the NEXT meeting\'s decision: %.1f%% agree (n=%d)' % (pct, n))
    dpairs = [(rows[i]['d_tone'], rows[i + 1]['action'])
              for i in range(1, len(rows) - 1) if rows[i]['d_tone'] is not None]
    pct2, n2 = agreement(dpairs)
    print('         change in tone vs the next decision: %.1f%% agree (n=%d)' % (pct2, n2))

    # ---- check 3: does d_tone line up with gold's reaction? -----------------
    if os.path.exists(IMPACT):
        imp = {}
        for line in open(IMPACT, encoding='utf-8'):
            r = json.loads(line)
            if r['kind'] == 'FOMC':
                imp[r['t'] // 86400000] = r
        joined = []
        for r in rows:
            y, m, d = (int(x) for x in r['date'].split('-'))
            import datetime as _dt
            day = int(_dt.datetime(y, m, d, tzinfo=_dt.timezone.utc).timestamp() * 1000) // 86400000
            hit = imp.get(day)
            if hit and r['d_tone'] is not None and hit.get('realized_ret_60m') is not None:
                joined.append((r['d_tone'], hit['realized_ret_60m']))
        print('')
        if joined:
            # hawkish should push gold DOWN, so agreement is with the NEGATED move
            pct3, n3 = agreement([(dt, -ret) for dt, ret in joined])
            print('CHECK 3  change in tone vs XAUUSD 60m after: %.1f%% agree (n=%d)'
                  % (pct3, n3))
            print('         (hawkish is counted as correct when gold FELL)')
        else:
            print('CHECK 3  no FOMC rows joined -- run tools/event_impact_eval.py first')
    print('')
    print('wrote %s' % args.out)
    return 0


if __name__ == '__main__':
    sys.exit(main())
