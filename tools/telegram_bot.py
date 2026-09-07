#!/usr/bin/env python
"""
The bot half of the Telegram link: it answers commands.

    python tools/telegram_bot.py --once     # drain pending, reply, exit
    python tools/telegram_bot.py            # long-poll until stopped

    /status   is the platform up
    /profit   realised profit today and this month
    /help     the list

`tools/event_alert.py` PUSHES release warnings on a schedule; this PULLS
commands. They share `configs/secrets.env` and nothing else, deliberately -- a
crash in the poller must not stop a release alert from going out.

ONLY CONFIGURED CHATS ARE ANSWERED, and this is the one security property that
matters. A Telegram bot is reachable by anyone who knows its @name, so a bot
that answered whoever asked would hand this account's P/L to a stranger who
guessed `@DiaNurFxBot`. Every update from any other chat is dropped, counted,
and never replied to -- not even with a refusal, because a refusal confirms the
bot is live.

`TELEGRAM_CHAT_ID` IS A LIST, comma-separated, so one bot serves a private chat
and a group:

    TELEGRAM_CHAT_ID=686269315,-1001234567890

Group ids are NEGATIVE. The minus sign is part of the id. Find one with
`--discover`, which prints the id of anyone who writes and answers nobody --
learning an id and granting access are separate acts, and a discover mode that
replied would have leaked before you decided.

IN A GROUP, ANYONE IN THE GROUP CAN READ THE REPLY. Adding a group id is
granting the account's P/L to everyone in it, now and later. That is a choice
about who is in the room, not about the bot.

THE PROFIT NUMBERS ARE THE FOOTER'S NUMBERS. `paintRealised` in js/main.js is
the definition and this mirrors it exactly rather than inventing a second one:

  - REALISED only. Closed trades. The open runner is floating P/L and adding it
    would let an unclosed position flatter a day that has not paid out.
  - THE BROKER'S MIDNIGHT, not yours. A trading day is the server's day -- it is
    what rolls the swap and what the statement will agree with. On a +3h server
    the two disagree for three hours every night, which is exactly when a late
    New York session is still open.
  - `buy` and `sell` DEALS ONLY. A deposit arrives as a deal carrying its full
    amount in `profit`, so counting every deal would report funding the account
    as a profitable day.
  - NET of commission and swap, the same as the History tab.

If the two ever disagree, this file is wrong and js/main.js is right.
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools import _secrets                                    # noqa: E402

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding='utf-8', errors='replace')
    except (AttributeError, ValueError):
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CAL = os.path.join(ROOT, 'data', 'calendar', 'history.json')
OFFSET_FILE = os.path.join(ROOT, 'data', 'calendar', 'tg_offset.json')

# IMPORTED, NOT COPIED. `event_alert.py` already decides what each release
# is called and what its measured volatility expansion was. A second copy
# here would drift the moment one was corrected, and the alert and the
# /news reply would then describe the same event differently.
from tools.event_alert import MEANING, EXPANSION, BNE      # noqa: E402
BRIDGE = os.environ.get('DNFX_BRIDGE', 'http://127.0.0.1:8765')
TG = 'https://api.telegram.org/bot%s/%s'


def tg(token, method, **params):
    url = TG % (token, method)
    data = urllib.parse.urlencode(params).encode() if params else None
    with urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=40) as r:
        return json.loads(r.read().decode())


def bridge(path, timeout=15):
    with urllib.request.urlopen(BRIDGE + path, timeout=timeout) as r:
        return json.loads(r.read().decode())


def load_offset():
    try:
        with open(OFFSET_FILE, encoding='utf-8') as fh:
            return int(json.load(fh).get('offset', 0))
    except (OSError, ValueError, TypeError):
        return 0


def save_offset(v):
    tmp = OFFSET_FILE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as fh:
        json.dump({'offset': v}, fh)
    os.replace(tmp, OFFSET_FILE)


def money(v, cur):
    return '%s%s %s' % ('+' if v > 0 else '', format(round(v, 2), ',.2f'), cur)


def cmd_status():
    """Up means the BRIDGE answers and MetaTrader is connected through it.

    Reporting "Running" whenever this script is alive would be a lie the moment
    the terminal drops -- the script is the one part guaranteed to be running,
    since it is the thing composing the reply.
    """
    try:
        h = bridge('/health', timeout=8)
    except (urllib.error.URLError, OSError, ValueError) as err:
        return ('DiaNur Trading Platform is DOWN.\n'
                'The bridge at %s did not answer (%s).' % (BRIDGE, err))
    if not h.get('connected'):
        return ('DiaNur Trading Platform is UP but NOT CONNECTED.\n'
                'Bridge answered; MetaTrader is not attached%s.'
                % (' -- ' + h['error'] if h.get('error') else ''))
    bits = ['DiaNur Trading Platform is Running...']
    if h.get('server'):
        bits.append('server: %s' % h['server'])
    if h.get('login'):
        bits.append('login: %s' % h['login'])
    if h.get('mock'):
        bits.append('NOTE: bridge is in MOCK mode -- figures are synthetic.')
    return '\n'.join(bits)


def cmd_profit():
    """Realised day and month, on the broker's clock. Mirrors paintRealised."""
    try:
        h = bridge('/health', timeout=8)
        acct = bridge('/account', timeout=15)
        # 40 days always covers month-to-date, since no month exceeds 31.
        # A dynamic window would be tighter and buys nothing: the cost is
        # one bridge call either way, and a fixed number cannot be wrong
        # on the 31st the way an off-by-one dynamic one could.
        deals = bridge('/deals?days=40', timeout=30).get('deals', [])
    except (urllib.error.URLError, OSError, ValueError) as err:
        return 'Cannot reach the bridge at %s (%s).' % (BRIDGE, err)

    off = int(h.get('time_offset_ms') or 0)
    now_broker = datetime.fromtimestamp((time.time() * 1000 + off) / 1000, timezone.utc)
    day_start = datetime(now_broker.year, now_broker.month, now_broker.day,
                         tzinfo=timezone.utc).timestamp() * 1000 - off
    month_start = datetime(now_broker.year, now_broker.month, 1,
                           tzinfo=timezone.utc).timestamp() * 1000 - off

    day = month = 0.0
    n_day = n_month = 0
    for d in deals:
        if d.get('side') not in ('buy', 'sell'):
            continue                       # drops deposits, credits, corrections
        net = (d.get('profit') or 0) + (d.get('commission') or 0) + (d.get('swap') or 0)
        if d.get('time_ms', 0) >= month_start:
            month += net
            n_month += 1
        if d.get('time_ms', 0) >= day_start:
            day += net
            n_day += 1

    cur = acct.get('currency') or ''
    # TWO LINES, BY REQUEST. The floating/equity rows and the method footnotes
    # were dropped: a reply read on a phone is skimmed, and the two numbers
    # asked for were competing with four that were not. THE DEFINITION HAS NOT
    # CHANGED -- still realised, still the broker's day, still net of
    # commission and swap. It is only no longer restated in every message,
    # which is why the docstring above states it in full.
    lines = [
        'Profit today: *%s*' % money(day, cur),
        'This month:   *%s*' % money(month, cur),
    ]
    # The coverage warning stays. It fires only when the month figure would be
    # a FLOOR rather than a total, and a number that is quietly wrong is worse
    # than a third line that appears rarely.
    oldest = min((d.get('time_ms', 0) for d in deals), default=None)
    if oldest is None or oldest > month_start:
        lines.append('_month is a FLOOR: deal history starts after the 1st_')
    return '\n'.join(lines)


#: Measured volatility expansion, banded. The bands are wide on purpose: the
#: underlying numbers come from 667 releases, and separating 2.0x from 2.2x is
#: not something that sample supports. Separating 4x from 1.5x plainly is.
IMPACT_BANDS = [(3.0, 'VERY HIGH', '🔴'),
                (2.0, 'HIGH', '🟠'),
                (1.5, 'MEDIUM', '🟡'),
                (0.0, 'LOW', '⚪')]


def impact_of(kind, vendor):
    """(icon, level, basis) for one release.

    THE MEASURED NUMBER WINS WHERE THERE IS ONE. The vendor's label is not just
    coarse, it is INCONSISTENT WITHIN A KIND: in this calendar GDP is tagged
    `high` once and left unrated four times, for the same release. And where it
    is applied it does not discriminate -- CPI, FOMC, GDP and NFP are all `high`
    to the vendor, while measured expansion across them runs 4.05x to 1.58x.

    So a kind this project has measured reports its own band and says so. A kind
    it has not falls back to the vendor's word, marked as the vendor's, because
    an unmeasured `high` still carries information and dropping it would be a
    loss.
    """
    exp = EXPANSION.get(kind)
    if exp is not None:
        for lo, level, icon in IMPACT_BANDS:
            if exp >= lo:
                return icon, level, '(measured %.2fx)' % exp
    if (vendor or '').lower() == 'high':
        return '🟠', 'HIGH', '(vendor, unmeasured)'
    return '⚪', 'UNRATED', '(unmeasured)'


def _curated(now, back, ahead):
    """The macro marks the charts draw, from data/calendar/history.json.

    Few, far-reaching, and the only ones this project has MEASURED. Kinds here
    carry a volatility expansion from tools/event_impact_eval.py.
    """
    try:
        with open(CAL, encoding='utf-8') as fh:
            events = json.load(fh)
    except (OSError, ValueError):
        return []
    out = []
    for e in events:
        t = e.get('t')
        if not isinstance(t, (int, float)) or not (now - back <= t <= now + ahead):
            continue
        kind = e.get('kind') or e.get('label') or '?'
        out.append({'t': t, 'name': kind, 'kind': kind, 'cur': 'USD',
                    'vendor': e.get('impact'), 'src': 'chart',
                    'forecast': None, 'previous': None})
    return out


def _forexfactory(now, back, ahead):
    """The Calendar menu's own feed, through the bridge.

    RICHER AND SHORTER. It carries every currency, a forecast and a previous,
    and its own high/medium/low -- but only about a week of it, where the
    curated file reaches months out. Neither source is a superset, which is why
    /news reads both.

    A DEAD BRIDGE IS NOT AN ERROR HERE. The curated file is on disk and answers
    without it, so a bridge that is down costs detail rather than the reply.
    """
    try:
        with urllib.request.urlopen(BRIDGE + '/calendar', timeout=12) as r:
            data = json.loads(r.read().decode())
    except (urllib.error.URLError, OSError, ValueError):
        return []
    out = []
    for e in data.get('events', []):
        t = e.get('ts')
        if not isinstance(t, (int, float)) or not (now - back <= t <= now + ahead):
            continue
        title = (e.get('title') or '').strip()
        if not title:
            continue
        out.append({'t': t, 'name': title,
                    'kind': _kind_of(title, e.get('currency') or ''),
                    'cur': e.get('currency') or '', 'vendor': e.get('impact'),
                    'src': 'ff', 'forecast': e.get('forecast') or None,
                    'previous': e.get('previous') or None})
    return out


#: Which currency each measured kind belongs to. WITHOUT THIS the title match
#: below is actively wrong: "CNY CPI y/y" contains "CPI" and would be handed
#: the US CPI's measured 2.02x expansion, which was measured on US prints
#: against gold and the majors and says nothing about a Chinese release.
#: "EUR Revised GDP q/q" had the same problem. A substring match across
#: currencies does not transfer a measurement, it launders one.
HOME = {'NFP': 'USD', 'CPI': 'USD', 'PPI': 'USD', 'GDP': 'USD',
        'UNEMPLOYMENT': 'USD', 'FOMC': 'USD',
        'ECB': 'EUR', 'BOE': 'GBP', 'BOJ': 'JPY'}


def _kind_of(title, cur):
    """Map a feed title onto one of our kinds, or None.

    Substring, not equality: the feed says "CPI m/m" and "Core CPI m/m" where
    our table says "CPI". Longest key first so FOMC does not lose to a shorter
    match inside the same title.

    THE CURRENCY MUST MATCH the kind's home, or this returns None and the event
    keeps the vendor's own impact label. That is the honest answer for a release
    this project has never measured.
    """
    up = (title or '').upper()
    for k in sorted(HOME, key=len, reverse=True):
        if k in up and HOME[k] == cur:
            return k
    if cur == 'USD' and ('NON-FARM' in up or 'NONFARM' in up or 'PAYROLL' in up):
        return 'NFP'
    return None


def _merge(a, b):
    """Both lists, with the curated entry winning a collision but INHERITING.

    A COLLISION IS THE SAME MINUTE AND THE SAME KIND. Matching on time alone
    would drop unrelated releases that share a slot -- 12:30 UTC carries several
    at once -- and matching on title alone would never fire, because the two
    sources spell nothing the same way.

    THE WINNER TAKES WHAT IT LACKS FROM THE LOSER. Dropping the twin outright
    threw away real information: the curated `USD PPI` has no vendor rating and
    no forecast, while the feed's `PPI m/m` at the same minute carries both, so
    the merged reply read UNRATED for a release the feed called high. The
    curated entry keeps its identity -- the measured kind, the plain-word
    meaning -- and fills its blanks from the twin.
    """
    by_key = {}
    for e in a:
        if e['kind']:
            by_key.setdefault((int(e['t'] // 60000), e['kind']), e)
    out = list(a)
    for e in b:
        key = (int(e['t'] // 60000), e['kind']) if e['kind'] else None
        win = by_key.get(key) if key else None
        if win is None:
            out.append(e)
            continue
        for field in ('vendor', 'forecast', 'previous'):
            if not win.get(field) and e.get(field):
                win[field] = e[field]
        # the feed's title is the specific one ("Core PPI m/m" against "PPI"),
        # but the curated name is what the chart marks, so the name is left
        # alone and the detail arrives in the fields above
    return sorted(out, key=lambda e: e['t'])


def cmd_news(arg=''):
    """What just landed and what is coming, from BOTH calendars.

    TWO SOURCES, because neither contains the other. `data/calendar/history.json`
    is what the charts mark and what this project has measured -- few events,
    months of reach. The bridge's `/calendar` is the Calendar menu's own
    ForexFactory feed -- every currency, a forecast and a previous, about a week
    of reach. Reading only the first is thin; only the second is short-sighted.

    DEFAULT IS HIGH AND MEDIUM ONLY. The feed is 61 low-impact rows out of 81,
    and a reply that listed them all would bury the three that matter. `/news
    all` lifts the filter.

    A LOOK BACK AS WELL AS FORWARD, because "current" is most of the question: a
    release that landed twenty minutes ago is still moving the tape.

    TIMES ARE UTC, and labelled. The calendars are UTC and the bar archive is
    broker time; this project has already shipped one bug from conflating them.
    """
    show_all = 'all' in (arg or '').lower()
    now = time.time() * 1000
    back, ahead, limit = 3 * 3600_000, 14 * 86400_000, 12

    merged = _merge(_curated(now, back, ahead), _forexfactory(now, back, ahead))
    if not merged:
        return 'Nothing in either calendar for the next 14 days.'

    kept = []
    for e in merged:
        icon, level, basis = impact_of(e['kind'], e['vendor'])
        if not show_all and level in ('LOW', 'UNRATED') and e['src'] == 'ff':
            continue                       # curated marks always survive
        kept.append((e, icon, level, basis))
    if not kept:
        return ('Nothing high or medium impact in the next 14 days.\n'
                'Send `/news all` for everything.')

    lines = []
    for e, icon, level, basis in kept[:limit]:
        when = datetime.fromtimestamp(e['t'] / 1000, timezone.utc)
        mins = (e['t'] - now) / 60_000
        if mins < 0:
            rel = '%dm ago' % abs(round(mins))
        elif mins < 90:
            rel = 'in %dm' % round(mins)
        elif mins < 48 * 60:
            rel = 'in %.1fh' % (mins / 60)
        else:
            rel = 'in %dd' % round(mins / 1440)
        head = '%s *%s%s*  %s' % (icon, (e['cur'] + ' ') if e['cur'] else '',
                                  e['name'], rel)
        # BRISBANE FIRST, because that is the clock the reader is on, with UTC
        # kept beside it rather than dropped: the calendars are UTC, the bar
        # archive is broker time, and this project has already shipped one bug
        # from conflating two clocks. A local time with no reference is how
        # that happens again.
        bits = [head,
                '   %s AEST  (%s UTC)'
                % (when.astimezone(BNE).strftime('%a %d %b %H:%M'),
                   when.strftime('%H:%M')),
                '   impact %s %s' % (level, basis)]
        if e['forecast'] or e['previous']:
            bits.append('   forecast %s  ·  previous %s'
                        % (e['forecast'] or '-', e['previous'] or '-'))
        if e['kind'] and MEANING.get(e['kind']) and e['src'] == 'chart':
            bits.append('   _%s_' % MEANING[e['kind']])
        lines.append('\n'.join(bits))

    head = 'Calendar, %d of %d%s:' % (len(lines), len(kept),
                                      '' if show_all else ' (high/medium)')
    tail = '' if show_all else '\n\n_`/news all` for low impact too_'
    return head + '\n\n' + '\n'.join(lines) + tail



HELP = ('DiaNurFx bot\n'
        '/status  is the platform up\n'
        '/profit  realised profit today and this month\n'
        '/news    what just landed and what is coming\n'
        '/help    this')

HANDLERS = {'/status': cmd_status, '/profit': cmd_profit, '/news': cmd_news,
            '/help': lambda: HELP, '/start': lambda: HELP}


def handle(token, allowed, upd, discover=False, seen=None):
    """Reply to one update. Returns True if a reply was sent.

    REPLIES GO BACK TO THE CHAT THAT ASKED, not to a configured destination.
    In a group the asker is the group, and answering into a private chat
    instead would make the bot look broken to everyone who can see the command.
    """
    msg = upd.get('message') or upd.get('edited_message') or {}
    ch = msg.get('chat') or {}
    chat = str(ch.get('id') or '')
    text = (msg.get('text') or '').strip()
    if not text:
        return False
    if discover:
        # No reply, by design: this mode exists to LEARN an id, and a bot that
        # answers while discovering has already leaked before you decided.
        # Printed once per chat because the offset is never advanced here, so
        # the same updates come back on every poll.
        if seen is not None and chat in seen:
            return False
        if seen is not None:
            seen.add(chat)
        print('  chat id %-16s type=%-11s %s' %
              (chat, ch.get('type'), ch.get('title') or ch.get('username')
               or ch.get('first_name') or ''))
        return False
    if chat not in allowed:
        # silent by design -- see the module docstring
        print('ignored a message from chat %s (%s)' % (chat or '?', ch.get('type')))
        return False
    # tolerate /profit@DiaNurFxBot, which is what a group chat sends
    cmd = text.split()[0].split('@')[0].lower()
    fn = HANDLERS.get(cmd)
    # ARGUMENTS ARE PASSED WHEN A HANDLER WANTS THEM. `/news all` is the
    # only one today; handlers that take none are called with none, so
    # adding an argument later does not touch the others.
    arg = text.split(None, 1)[1] if len(text.split(None, 1)) > 1 else ''
    if fn is None:
        reply = 'Unknown command: %s\n\n%s' % (cmd, HELP)
    else:
        import inspect
        reply = fn(arg) if inspect.signature(fn).parameters else fn()
    tg(token, 'sendMessage', chat_id=chat, text=reply, parse_mode='Markdown')
    print('replied to %s' % cmd)
    return True


def drain(token, allowed, wait, discover=False, seen=None):
    """One getUpdates pass.

    DISCOVER MODE NEVER ADVANCES THE OFFSET. Confirming an update deletes it
    from Telegram's queue permanently, so a discover run that consumed would
    destroy the very thing it was asked to find the moment anything went wrong
    -- which is exactly what happened the first time this was used: a second
    poller raced it, the winner consumed the group's message, and its output
    went to a log nobody was reading. The id was unrecoverable and the user had
    to send another message.

    Read-only discovery is re-runnable. It reprints the same ids every poll,
    which is why `seen` exists.
    """
    offset = load_offset()
    try:
        res = tg(token, 'getUpdates', offset=offset, timeout=wait)
    except (urllib.error.URLError, OSError, ValueError) as err:
        print('getUpdates failed: %s' % err, file=sys.stderr)
        time.sleep(5)
        return 0
    if not res.get('ok'):
        print('getUpdates refused: %s' % res, file=sys.stderr)
        time.sleep(5)
        return 0
    n = 0
    for upd in res.get('result', []):
        if not discover:
            # advance the offset FIRST so a command that crashes a handler is
            # not retried forever on every poll
            offset = max(offset, upd['update_id'] + 1)
            save_offset(offset)
        try:
            n += 1 if handle(token, allowed, upd, discover, seen) else 0
        except Exception as err:                              # noqa: BLE001
            print('handler failed: %s' % err, file=sys.stderr)
    return n


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--once', action='store_true',
                    help='drain what is pending, reply, exit')
    ap.add_argument('--wait', type=int, default=25,
                    help='long-poll seconds per getUpdates (default 25)')
    ap.add_argument('--discover', action='store_true',
                    help='print the chat id of anyone who messages; reply to nobody')
    ap.add_argument('--seconds', type=int, default=90,
                    help='how long --discover listens before giving up')
    ap.add_argument('--check', action='store_true',
                    help='print what /status and /profit would say, send nothing')
    args = ap.parse_args()

    _secrets.load()
    token = os.environ.get('TELEGRAM_BOT_TOKEN')
    # A COMMA-SEPARATED LIST, so the same bot can serve a private chat and a
    # group. Group ids are negative and look like -1001234567890; that minus
    # sign is part of the id, not a typo.
    allowed = {c.strip() for c in (os.environ.get('TELEGRAM_CHAT_ID') or '').split(',')
               if c.strip()}

    if args.check:
        print('--- /status ---'); print(cmd_status())
        print(); print('--- /profit ---'); print(cmd_profit())
        return 0

    if not token:
        print('TELEGRAM_BOT_TOKEN must be set in configs/secrets.env',
              file=sys.stderr)
        return 2

    if args.discover:
        print('DISCOVER -- printing chat ids, replying to nobody, consuming')
        print('nothing. Add the bot to the group, then send a message there.')
        print('Listening for %ds. Nothing is written to configs/secrets.env.'
              % args.seconds)
        seen = set()
        deadline = time.time() + args.seconds
        while time.time() < deadline:
            try:
                drain(token, allowed, min(args.wait, 10), discover=True, seen=seen)
            except KeyboardInterrupt:
                break
        if not seen:
            print('nothing seen. Send a message in the group and run this again.')
        return 0

    if not allowed:
        print('TELEGRAM_CHAT_ID must be set in configs/secrets.env',
              file=sys.stderr)
        return 2

    if args.once:
        drain(token, allowed, 0)
        return 0

    print('polling as the bot; answering %d chat(s): %s. Ctrl-C to stop.'
          % (len(allowed), ', '.join(sorted(allowed))))
    while True:
        try:
            drain(token, allowed, args.wait)
        except KeyboardInterrupt:
            print('stopped')
            return 0


if __name__ == '__main__':
    sys.exit(main())
