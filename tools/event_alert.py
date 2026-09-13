#!/usr/bin/env python
"""
Telegram alert a fixed number of minutes before a macro release.

    python tools/event_alert.py --dry-run          # print, send nothing
    python tools/event_alert.py                    # send
    python tools/event_alert.py --lead 10 --impact high
    python tools/event_alert.py --test             # one message, to prove wiring

Reads `data/calendar/history.json` -- the same file the charts mark releases
from, so an alert and a chart mark can never disagree about when something is.

THE CLOCK, WHICH IS THE ONE THING THAT MUST NOT BE GUESSED. Calendar timestamps
in that file are UTC. The bar archive is broker server time (EET/EEST) and this
project has already shipped one bug from joining the two raw -- it put the NFP
volatility spike at +180 minutes and made a real effect look absent. Nothing
here touches bars, so the correction does not apply; what matters is that `now`
is taken as UTC and compared to a UTC field. `--local` prints the local time
alongside for the reader, and prints it as a CONVERSION, never as the basis of
the comparison.

IDEMPOTENT BY DESIGN, because the scheduler will run this far more often than
events occur. Every alert sent is recorded in `data/calendar/alerted.json` keyed
by the event's timestamp and kind, and a key already there is skipped. Running
every minute for a week sends one message per event, not ten thousand.

IT WILL NOT FIRE ON THE PAST. The window is `now <= event <= now + lead`, so an
event that has already happened is never announced -- which matters because a
laptop that was asleep will otherwise wake up and send a burst of alerts for
releases that landed hours ago. A missed alert is missed; a late one is worse
than useless because it reads as a live warning.

CREDENTIALS LIVE IN configs/secrets.env, like every other key here:

    TELEGRAM_BOT_TOKEN=123456:ABC-DEF...
    TELEGRAM_CHAT_ID=123456789

Without both, the tool refuses to send, prints exactly what it WOULD have sent,
and exits 2 with the setup instructions. It never fails silently, and it never
sends to a default anywhere.
"""

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

#: BRISBANE, AS A FIXED OFFSET AND NOT A ZONE NAME. Queensland has not observed
#: daylight saving since 1992, so +10:00 is correct every day of the year and
#: needs no tz database -- which matters on Windows, where the IANA data is
#: absent unless the `tzdata` package happens to be installed and `zoneinfo`
#: raises rather than falling back. This trick is SAFE HERE AND NOWHERE NEAR
#: Sydney or Melbourne, which do shift.
BNE = timezone(timedelta(hours=10), 'AEST')

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools import _secrets                                    # noqa: E402
from tools import notify                                      # noqa: E402

# The message carries an emoji and Windows consoles default to cp1252, which
# raises on it. The MESSAGE is fine -- Telegram takes UTF-8 -- so a console that
# cannot render it must not be able to kill a tool whose job is to send. Both
# streams are reconfigured to replace rather than raise.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding='utf-8', errors='replace')
    except (AttributeError, ValueError):
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CAL = os.path.join(ROOT, 'data', 'calendar', 'history.json')
STATE = os.path.join(ROOT, 'data', 'calendar', 'alerted.json')
API = 'https://api.telegram.org/bot%s/sendMessage'

#: What each release is, in a sentence, for the reader who is not holding the
#: acronym in their head at 3am. Absent kinds fall through to the bare label.
MEANING = {
    'NFP': 'US payrolls',
    'CPI': 'US inflation',
    'PPI': 'US producer prices',
    'GDP': 'US growth',
    'FOMC': 'Fed rate decision',
    'UNEMPLOYMENT': 'US unemployment rate',
    'ECB': 'ECB rate decision',
    'BOE': 'Bank of England rate decision',
    'BOJ': 'Bank of Japan rate decision',
}

#: Measured volatility expansion after each kind (tools/event_impact_eval.py,
#: 667 releases against 3.26M one-minute bars). Quoted because the vendor's own
#: impact label calls CPI, FOMC, GDP and NFP all "high" while the measured
#: expansion spans 4.05x to 1.58x -- the alert should say the measured thing.
EXPANSION = {'FOMC': 4.05, 'NFP': 2.57, 'CPI': 2.02, 'GDP': 1.58, 'BOJ': 0.77}


#: Bridge titles -> the curated kinds above, so an event that arrives from the
#: broker's calendar still gets its plain-English meaning and, where one was
#: measured, its volatility expansion. Matched on a lowercased substring of the
#: title, longest first, so "core ppi m/m" does not become CPI.
TITLE_KIND = [
    ('non-farm employment', 'NFP'), ('nonfarm payroll', 'NFP'),
    ('unemployment rate', 'UNEMPLOYMENT'),
    ('federal funds rate', 'FOMC'), ('fomc', 'FOMC'),
    ('main refinancing rate', 'ECB'), ('ecb', 'ECB'),
    ('official bank rate', 'BOE'), ('boe', 'BOE'),
    ('boj policy rate', 'BOJ'), ('boj', 'BOJ'),
    ('core ppi', 'PPI'), ('ppi', 'PPI'),
    ('core cpi', 'CPI'), ('cpi', 'CPI'),
    ('gdp', 'GDP'),
]


def _kind_for(title):
    t = (title or '').lower()
    for needle, kind in sorted(TITLE_KIND, key=lambda r: -len(r[0])):
        if needle in t:
            return kind
    return None


def load_events(base):
    """The broker's economic calendar, falling back to the local markers.

    WHY THE BRIDGE AND NOT data/calendar/history.json. That file is not a
    calendar -- it is 801 rows of NINE curated kinds (NFP, CPI, PPI, GDP,
    FOMC, ECB, BOE, BOJ, UNEMPLOYMENT) used to draw event lines on charts, with
    only about thirty entries still in the future. The Calendar tab has always
    read the bridge's /calendar instead, so the panel showed an ECB rate
    decision and a press conference that this alerter had never heard of. Two
    views of "what is coming" that disagree is the failure this closes.

    FALLING BACK, NOT FAILING. If the bridge is down the markers are still
    better than silence before a payrolls print, so the local file is used and
    the caller is told which source answered.

    The local rows carry `kind`; the bridge rows carry `title`, `currency` and
    `impact`. `_kind_for` maps a bridge title onto a curated kind where one
    exists, so MEANING and the measured EXPANSION still attach.
    """
    try:
        with urllib.request.urlopen(base.rstrip('/') + '/calendar',
                                    timeout=20) as r:
            rows = (json.loads(r.read().decode()) or {}).get('events') or []
        out = []
        for e in rows:
            # `ts`, and `t` only as a courtesy. The bridge names this field
            # `ts`; reading `t` (the local markers' name) matched nothing, so
            # every row was dropped and the loader fell back to the markers
            # while reporting the bridge as unavailable -- it had answered
            # perfectly. Hence the explicit complaint below.
            t = e.get('ts', e.get('t'))
            if not isinstance(t, (int, float)):
                continue
            title = e.get('title') or e.get('label') or ''
            out.append({'t': int(t), 'label': title,
                        'kind': _kind_for(title) or (e.get('currency') or ''),
                        'impact': (e.get('impact') or '').lower(),
                        'currency': e.get('currency') or '',
                        # ForexFactory supplies both on most numeric releases
                        # and the message used to drop them, leaving a time
                        # with no sense of what would count as a surprise.
                        'forecast': e.get('forecast') or '',
                        'previous': e.get('previous') or ''})
        if out:
            return out, 'bridge /calendar'
        # ANSWERED, BUT WITH NOTHING USABLE. Silently falling back here hid a
        # field-name bug for a whole session; a source that replies and yields
        # zero rows is a different fault from one that will not reply, and it
        # has to say so.
        print('calendar: bridge returned %d event(s) but none had a usable '
              'timestamp -- falling back to %s'
              % (len(rows), os.path.relpath(CAL, ROOT)), file=sys.stderr)
    except Exception as exc:                                   # noqa: BLE001
        print('calendar: bridge unreachable (%s) -- falling back to %s'
              % (exc, os.path.relpath(CAL, ROOT)), file=sys.stderr)
    with open(CAL, encoding='utf-8') as fh:
        return json.load(fh), 'local markers (bridge unavailable)'


#: Impact as a THRESHOLD, not an exact match. The filter used to compare the
#: strings, so `impact: high` silently dropped every medium event and, worse,
#: `impact: medium` would have dropped every HIGH one -- the setting that reads
#: like "at least medium" excluded exactly the releases it exists to catch.
#: A holiday ranks below low: it is a market-closed marker, not a release.
RANK = {'holiday': 0, '': 0, 'low': 1, 'medium': 2, 'high': 3}


SETTINGS = os.path.join(ROOT, 'configs', 'alerts.json')


def load_settings():
    """configs/alerts.json, or {} when it is missing or unreadable.

    ABSENT MEANS ON. A settings file that fails to parse must not silence a
    release warning -- the failure mode of this tool should be a message you
    did not need, never a missing one.
    """
    try:
        with open(SETTINGS, encoding='utf-8') as fh:
            return json.load(fh) or {}
    except (OSError, ValueError):
        return {}


def load_state():
    if not os.path.exists(STATE):
        return {}
    try:
        with open(STATE, encoding='utf-8') as fh:
            return json.load(fh)
    except (ValueError, OSError):
        # A corrupt state file must not stop alerts; the cost of losing it is
        # at most one duplicate message, and the cost of raising here is
        # silence before a release.
        return {}


def save_state(state):
    tmp = STATE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as fh:
        json.dump(state, fh, indent=1, sort_keys=True)
    os.replace(tmp, STATE)


def prune(state, now_ms, keep_days=30):
    """Drop keys for events older than `keep_days`, so the file cannot grow."""
    cut = now_ms - keep_days * 86400_000
    return {k: v for k, v in state.items() if int(k.split('|')[0]) >= cut}


def compose(ev, mins, local_tz):
    when = datetime.fromtimestamp(ev['t'] / 1000, timezone.utc)
    kind = ev.get('kind') or 'release'
    label = ev.get('label') or ''
    meaning = MEANING.get(kind)

    # THE HEADLINE IS THE EVENT, NOT THE CURRENCY. `kind` falls back to the
    # currency code for anything outside the nine curated kinds, so a bridge
    # event with no match announced itself as "*EUR* in 266 min" -- a warning
    # naming neither what is coming nor why anyone should care. The title
    # leads whenever it says more than the kind does.
    if label and label.upper() != kind.upper():
        head = ('%s - %s' % (kind, label)) if meaning else label
    else:
        head = kind
    bits = [u'⚠️  *%s* in %d min' % (head, mins)]
    if meaning:
        bits.append('_%s_' % meaning)
    if ev.get('currency') and ev['currency'] not in head:
        bits.append(ev['currency'])
    bits.append('%s UTC' % when.strftime('%a %d %b %H:%M'))
    if local_tz:
        bits.append('%s Brisbane' % when.astimezone(BNE).strftime('%a %d %b %H:%M'))

    # FORECAST AND PREVIOUS, when the calendar carries them. The bridge has
    # had both all along and the message dropped them, leaving the reader a
    # time and no idea what number would count as a surprise.
    fc = str(ev.get('forecast') or '').strip()
    pv = str(ev.get('previous') or '').strip()
    if fc or pv:
        bits.append('forecast %s  |  previous %s' % (fc or '--', pv or '--'))

    exp = EXPANSION.get(kind)
    if exp:
        bits.append('measured volatility after: %.2fx the hour before' % exp)
    elif ev.get('impact'):
        bits.append('vendor impact: %s (not measured here)' % ev['impact'])
    return chr(10).join(bits)
def send(token, chat, text):
    """Telegram-only send to a comma-separated chat list. KEPT FOR CALLERS.

    THE ROUTING MOVED to tools/notify.py, which fans one message out across
    every configured destination and channel -- this now delegates to it and
    exists only so an older call site or a one-off script that imports `send`
    still works. New code should call `notify.deliver(kind, text)` instead,
    because this signature cannot express a WhatsApp destination or a
    destination that only wants one kind of alert.

    ONE FAILURE MUST NOT SILENCE THE REST, unchanged: each chat is attempted
    independently and this raises only if EVERY one failed -- a real outage,
    where the caller must not record the alert as sent.
    """
    dests = [{'id': 'arg-%d' % n, 'channel': 'telegram', 'target': c,
              'label': c, 'enabled': True}
             for n, c in enumerate([x.strip() for x in str(chat).split(',')
                                    if x.strip()], 1)]
    res = notify.deliver('news', text, dests=dests)
    if not res['sent']:
        raise RuntimeError('every destination failed: %s'
                           % '; '.join(r.get('error', '?')
                                       for r in res['results']))
    return res['sent']


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--base', default='http://127.0.0.1:8765',
                    help='the bridge, whose /calendar is the event source')
    ap.add_argument('--lead', type=int, default=10,
                    help='minutes before the release to alert (default 10)')
    ap.add_argument('--impact', default=None,
                    help='only alert on this vendor impact, e.g. high')
    ap.add_argument('--kinds', default=None,
                    help='comma-separated kinds to alert on, e.g. NFP,CPI,FOMC')
    ap.add_argument('--dry-run', action='store_true',
                    help='print what would be sent and send nothing')
    ap.add_argument('--test', action='store_true',
                    help='send one message now to prove the wiring')
    ap.add_argument('--whoami', action='store_true',
                    help='find the chat id and write it to configs/secrets.env')
    ap.add_argument('--local', action='store_true',
                    help='include Brisbane time in the message')
    args = ap.parse_args()

    _secrets.load()
    token = os.environ.get('TELEGRAM_BOT_TOKEN')
    chat = os.environ.get('TELEGRAM_CHAT_ID')

    # THE SETTINGS MODAL CAN SWITCH THIS OFF. The scheduled task keeps running
    # either way -- unregistering it from the UI would mean the page needing
    # rights over Windows tasks, and a checkbox that silently uninstalls a
    # scheduled job is a surprise nobody wants. So the task polls and exits
    # quietly instead, which costs a few milliseconds a minute.
    #
    # `--test` and `--dry-run` ignore the switch on purpose: both exist to
    # answer "is this wired up", and a wiring check that goes silent because a
    # preference is off tells you nothing.
    cfg = load_settings()
    news = cfg.get('news') or {}
    if not args.test and not news.get('enabled', True):
        print('news alerts are switched off in configs/alerts.json')
        return 0
    if news.get('lead_minutes') and '--lead' not in sys.argv:
        args.lead = int(news['lead_minutes'])
    if news.get('impact') and news['impact'] != 'any' and not args.impact:
        args.impact = news['impact']

    if args.whoami:
        return whoami(token)

    # WHERE IT GOES is now a list in configs/alerts.json, resolved per KIND --
    # so one group can take the calendar and not the entries. Read once here
    # rather than per event: a destination list that changed mid-run would
    # announce two events to two different sets of chats, which is the kind of
    # inconsistency nobody would think to look for.
    dests = notify.destinations(cfg, 'news')
    if not dests:
        print('no destination wants news alerts -- Settings -> Destinations',
              file=sys.stderr)

    def announce(text):
        res = notify.deliver('news', text, dests=dests)
        if not res['sent']:
            raise RuntimeError('no destination accepted the message')
        return res

    if args.test:
        text = 'DiaNurFx alert wiring works. Lead is %d min.' % args.lead
        if args.dry_run or not (token and dests):
            print('WOULD SEND:\n%s' % text)
            return 0 if args.dry_run else missing(token, chat)
        res = announce(text)
        print('sent to %d of %d destination(s)' % (res['sent'], res['total']))
        return 0

    events, src = load_events(args.base)

    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    hi = now_ms + args.lead * 60_000
    kinds = {k.strip().upper() for k in args.kinds.split(',')} if args.kinds else None

    due = []
    for ev in events:
        t = ev.get('t')
        if not isinstance(t, (int, float)) or not (now_ms <= t <= hi):
            continue
        kind = (ev.get('kind') or ev.get('label') or '').upper()
        if args.impact and RANK.get((ev.get('impact') or '').lower(), 0)                 < RANK.get(args.impact.lower(), 0):
            continue
        if kinds and kind not in kinds:
            continue
        due.append(ev)

    if not due:
        print('nothing due in the next %d min (checked %d events, %s UTC)'
              % (args.lead, len(events),
                 datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M'))
              + ' [%s]' % src)
        return 0

    state = prune(load_state(), now_ms)
    sent = 0
    for ev in due:
        key = '%d|%s' % (int(ev['t']), ev.get('kind') or ev.get('label') or '?')
        if key in state:
            continue
        mins = max(0, round((ev['t'] - now_ms) / 60_000))
        text = compose(ev, mins, args.local)
        if args.dry_run:
            print('WOULD SEND:\n%s\n' % text)
            continue
        if not (token and dests):
            print('WOULD SEND:\n%s\n' % text)
            return missing(token, chat)
        announce(text)
        # recorded only AFTER a successful send, so a network failure retries
        # on the next run instead of being silently swallowed
        state[key] = datetime.now(timezone.utc).isoformat()
        sent += 1
        print('sent: %s' % key)

    if sent:
        save_state(state)
    return 0


def whoami(token):
    """Identify the bot and capture the chat id for whoever messaged it.

    THE CHAT ID IS NOT THE BOT'S USERNAME, which is the natural guess and is
    wrong: a bot cannot message itself, and `chat_id` wants the id of the
    conversation to send INTO. Telegram will only reveal it after a human has
    written to the bot at least once -- there is no way to look up "the chat
    with my own account" from the token alone, by design.
    """
    if not token:
        print('TELEGRAM_BOT_TOKEN missing from configs/secrets.env', file=sys.stderr)
        return 2
    base = 'https://api.telegram.org/bot%s/' % token

    def api(method):
        with urllib.request.urlopen(base + method, timeout=20) as r:
            return json.loads(r.read().decode())

    me = api('getMe')
    if not me.get('ok'):
        print('token rejected by Telegram: %s' % me, file=sys.stderr)
        return 2
    print('bot: @%s (%s)' % (me['result'].get('username'),
                             me['result'].get('first_name')))

    seen = {}
    for upd in api('getUpdates').get('result', []):
        msg = upd.get('message') or upd.get('channel_post') or {}
        ch = msg.get('chat') or {}
        if ch.get('id'):
            seen[ch['id']] = ch.get('username') or ch.get('title') or ch.get('first_name') or ''

    if not seen:
        have = os.environ.get('TELEGRAM_CHAT_ID')
        print('')
        print('No chat id discoverable from getUpdates.')
        if have:
            # A hand-set id is a legitimate state -- getUpdates only returns
            # PENDING updates, so an id obtained another way, or one whose
            # update was already consumed, shows up as nothing here. Do not
            # imply the setup is broken when it may be complete.
            print('TELEGRAM_CHAT_ID is already set to %s in configs/secrets.env.'
                  % have)
            print('')
            print('CAUTION: Telegram will not let a bot open a conversation. If')
            print('nobody has ever pressed START on @%s, a send to'
                  % me['result'].get('username'))
            print('that id fails with: bot cannot initiate conversation with')
            print('a user. Press START once, then: event_alert.py --test')
            return 0
        print('Open Telegram, search @%s, press START'
              % me['result'].get('username'))
        print('or send it any message. Then run this again.')
        return 2

    for cid, name in seen.items():
        print('  chat id %s  %s' % (cid, name))
    if len(seen) > 1:
        print('')
        print('More than one chat has written to this bot. Nothing was written to')
        print('configs/secrets.env -- pick one and set TELEGRAM_CHAT_ID by hand,')
        print('because guessing which conversation you meant is not for a tool.')
        return 2

    cid = str(next(iter(seen)))
    path = os.path.join(ROOT, 'configs', 'secrets.env')
    text = ''
    if os.path.exists(path):
        with open(path, encoding='utf-8') as fh:
            text = fh.read()
    import re
    if re.search(r'^TELEGRAM_CHAT_ID=', text, re.M):
        text = re.sub(r'^TELEGRAM_CHAT_ID=.*$', 'TELEGRAM_CHAT_ID=' + cid, text, flags=re.M)
    else:
        if text and not text.endswith('\n'):
            text += '\n'
        text += 'TELEGRAM_CHAT_ID=' + cid + '\n'
    with open(path, 'w', encoding='utf-8') as fh:
        fh.write(text)
    print('')
    print('wrote TELEGRAM_CHAT_ID=%s to configs/secrets.env' % cid)
    print('now: python tools/event_alert.py --test')
    return 0


def missing(token, chat):
    print('', file=sys.stderr)
    print('NOT SENT: credentials missing from configs/secrets.env', file=sys.stderr)
    if not token:
        print('  TELEGRAM_BOT_TOKEN  -- talk to @BotFather, /newbot', file=sys.stderr)
    if not chat:
        print('  TELEGRAM_CHAT_ID    -- message your bot, then open', file=sys.stderr)
        print('     https://api.telegram.org/bot<TOKEN>/getUpdates', file=sys.stderr)
        print('     and read result[0].message.chat.id', file=sys.stderr)
    return 2


if __name__ == '__main__':
    sys.exit(main())
