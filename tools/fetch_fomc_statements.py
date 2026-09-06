#!/usr/bin/env python
"""
FOMC statements -> data/research/fomc_statements.json.

    python tools/fetch_fomc_statements.py [--from-year 2015]

The Fed publishes every statement as a press release at a predictable URL, and
the FOMC calendar pages list them. Two listings are needed: the current
calendar covers roughly the last five years, and one page per year covers the
rest.

WHAT IS KEPT. The statement text only -- the numbered paragraphs of the policy
decision. Not the implementation note (a technical annex about the operating
framework, which is not policy signalling), and not the vote roster, which
names dissenters but says nothing about tone in words a scorer can read.

TWO STATEMENTS ON ONE DAY ARE NORMAL. The Fed posts the policy statement and
sometimes a separate release on the same date -- `monetary20260128a.htm` and
`...b.htm`. The `a` release is the statement; the rest are kept but flagged so
the scorer can ignore them.

NO KEY, NO PLAN, NO VENDOR. This is the raw material for the hawkish/dovish
score, and it is public.
"""

import argparse
import datetime
import html
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'data', 'research', 'fomc_statements.json')
BASE = 'https://www.federalreserve.gov'
UA = {'User-Agent': 'Mozilla/5.0 (DiaNurFx research; local tool)'}


def get(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=60,
                                context=ssl._create_unverified_context()) as r:
        return r.read().decode('utf-8', 'replace')


def statement_links(from_year):
    """Every /newsevents/pressreleases/monetaryYYYYMMDD?.htm the Fed lists."""
    pages = [BASE + '/monetarypolicy/fomccalendars.htm']
    this_year = datetime.date.today().year
    # The historical pages carry one year each and only exist past a lag.
    for y in range(from_year, this_year + 1):
        pages.append(BASE + '/monetarypolicy/fomchistorical%d.htm' % y)

    found = {}
    for p in pages:
        try:
            body = get(p)
        except urllib.error.HTTPError:
            continue                       # a year with no archive page yet
        except Exception as exc:           # noqa: BLE001
            print('  ! %s: %s' % (p.rsplit('/', 1)[-1], str(exc)[:70]), file=sys.stderr)
            continue
        for name in re.findall(r'/newsevents/pressreleases/(monetary\d{8}[a-z]\.htm)', body):
            found[name] = BASE + '/newsevents/pressreleases/' + name
        time.sleep(0.3)
    return found


#: "...approved the following statement for release by a 9-3 vote:" -- a
#: procedural preamble the Fed added with the shorter 2026 format. It contains
#: no policy language, and because it appears in some statements and not
#: others it would dilute a tone denominator unevenly across formats.
VOTE_PREAMBLE = re.compile(r'approved the following statement for release', re.I)

#: The dissent line. Dropped from the prose -- it is a list of names -- but the
#: DIRECTION in it is policy signal of exactly the kind a tone score is for:
#: three members preferring a hike is hawkish in a way no adjective is.
#: ANYWHERE in the roster, not anchored at its start. The dissent is
#: usually a clause inside the "Voting for ..." paragraph rather than a
#: paragraph of its own; anchoring found 1 dissent in 99 statements,
#: which is wrong by an order of magnitude for this period.
DISSENT = re.compile(r'Voting against', re.I)
PREFER_HIKE = re.compile(r'preferred to (?:rais|increas)\w*\s+the\s+target\s+range', re.I)
PREFER_CUT = re.compile(r'preferred to (?:lower|reduc)\w*\s+the\s+target\s+range', re.I)


def extract(body):
    """`(prose paragraphs, dissent record)` for one release."""
    m = re.search(r'<div[^>]+class="col-xs-12 col-sm-8[^"]*"[^>]*>(.*?)</div>\s*</div>',
                  body, re.S)
    section = m.group(1) if m else body
    out, dissent = [], {'n': 0, 'dir': 0}
    for p in re.findall(r'<p[^>]*>(.*?)</p>', section, re.S):
        txt = html.unescape(re.sub(r'<[^>]+>', ' ', p))
        txt = re.sub(r'\s+', ' ', txt).strip()
        hit = DISSENT.search(txt)
        if hit:
            # Read only from "Voting against" onward. The same paragraph
            # usually lists everyone who voted FOR first, and those names
            # would be counted as dissenters.
            clause = txt[hit.start():]
            head = re.split(r',?\s+who\s+preferred', clause)[0]
            dissent['n'] = max(1, len(re.findall(r'[A-Z]\.\s', head)))
            if PREFER_HIKE.search(clause):
                dissent['dir'] = 1
            elif PREFER_CUT.search(clause):
                dissent['dir'] = -1
            # Skip as prose only when the dissent WAS the paragraph; as a
            # clause inside the roster, the roster is dropped below anyway.
            if txt.lower().startswith('voting against'):
                continue
        # Short lines are captions, dates and links; the roster is names.
        if len(txt) < 80 or txt.startswith('Voting for') or VOTE_PREAMBLE.search(txt):
            continue
        out.append(txt)
    return out, dissent


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--from-year', type=int, default=2015)
    ap.add_argument('--out', default=OUT)
    args = ap.parse_args()

    links = statement_links(args.from_year)
    print('listed %d releases' % len(links))

    rows = []
    for name, url in sorted(links.items()):
        d = datetime.date(int(name[8:12]), int(name[12:14]), int(name[14:16]))
        if d.year < args.from_year:
            continue
        try:
            paras, dissent = extract(get(url))
        except Exception as exc:                        # noqa: BLE001
            print('  ! %s: %s' % (name, str(exc)[:70]), file=sys.stderr)
            continue
        if not paras:
            continue
        text = ' '.join(paras)
        rows.append({
            'date': d.isoformat(),
            # 18:00 UTC is the usual 2pm ET release; the calendar file carries
            # the exact minute for the ones that matter and this is only used
            # to order statements, never to join to a bar.
            'name': name,
            'primary': name.endswith('a.htm'),
            'paragraphs': paras,
            'dissent_n': dissent['n'],
            'dissent_dir': dissent['dir'],
            'words': len(text.split()),
            'url': url,
        })
        print('  %s  %-24s %4d words  %s'
              % (d, name, len(text.split()), 'statement' if name.endswith('a.htm') else 'other'))
        time.sleep(0.3)

    rows.sort(key=lambda r: (r['date'], r['name']))
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, 'w', encoding='utf-8') as fh:
        json.dump(rows, fh, indent=1)
    prim = sum(1 for r in rows if r['primary'])
    print('')
    print('wrote %d releases (%d primary statements) -> %s' % (len(rows), prim, args.out))
    if rows:
        print('  span %s .. %s' % (rows[0]['date'], rows[-1]['date']))
    return 0


if __name__ == '__main__':
    sys.exit(main())
