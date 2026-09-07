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
    kind = ev.get('kind') or ev.get('label') or 'release'
    bits = ['⚠️  *%s* in %d min' % (kind, mins)]
    meaning = MEANING.get(kind)
    if meaning:
        bits.append('_%s_' % meaning)
    bits.append('%s UTC' % when.strftime('%a %d %b %H:%M'))
    if local_tz:
        bits.append('%s Brisbane' % when.astimezone(BNE).strftime('%a %d %b %H:%M'))
    exp = EXPANSION.get(kind)
    if exp:
        bits.append('measured volatility after: %.2fx the hour before' % exp)
    elif ev.get('impact'):
        bits.append('vendor impact: %s (not measured here)' % ev['impact'])
    return '\n'.join(bits)


def send(token, chat, text):
    """Send to every configured chat. `chat` may be a comma-separated list.

    ONE FAILURE MUST NOT SILENCE THE REST. If a group has removed the bot, the
    private chat should still get its release warning, so each destination is
    attempted independently and the errors are collected rather than raised on
    the first one. It raises only if EVERY destination failed -- that is a real
    outage and the caller must not record the alert as sent.
    """
    chats = [c.strip() for c in str(chat).split(',') if c.strip()]
    ok, errs = 0, []
    for c in chats:
        data = urllib.parse.urlencode({
            'chat_id': c, 'text': text, 'parse_mode': 'Markdown',
            'disable_web_page_preview': 'true',
        }).encode()
        try:
            req = urllib.request.Request(API % token, data=data)
            with urllib.request.urlopen(req, timeout=20) as r:
                body = json.loads(r.read().decode())
            if body.get('ok'):
                ok += 1
            else:
                errs.append('%s: %s' % (c, body))
        except Exception as err:                              # noqa: BLE001
            errs.append('%s: %s' % (c, err))
    if errs:
        print('telegram: %d of %d failed -- %s'
              % (len(errs), len(chats), '; '.join(errs)), file=sys.stderr)
    if not ok:
        raise RuntimeError('every destination failed: %s' % '; '.join(errs))
    return ok


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
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

    if args.test:
        text = 'DiaNurFx alert wiring works. Lead is %d min.' % args.lead
        if args.dry_run or not (token and chat):
            print('WOULD SEND:\n%s' % text)
            return 0 if args.dry_run else missing(token, chat)
        send(token, chat, text)
        print('sent')
        return 0

    with open(CAL, encoding='utf-8') as fh:
        events = json.load(fh)

    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    hi = now_ms + args.lead * 60_000
    kinds = {k.strip().upper() for k in args.kinds.split(',')} if args.kinds else None

    due = []
    for ev in events:
        t = ev.get('t')
        if not isinstance(t, (int, float)) or not (now_ms <= t <= hi):
            continue
        kind = (ev.get('kind') or ev.get('label') or '').upper()
        if args.impact and (ev.get('impact') or '').lower() != args.impact.lower():
            continue
        if kinds and kind not in kinds:
            continue
        due.append(ev)

    if not due:
        print('nothing due in the next %d min (checked %d events, %s UTC)'
              % (args.lead, len(events),
                 datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')))
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
        if not (token and chat):
            print('WOULD SEND:\n%s\n' % text)
            return missing(token, chat)
        send(token, chat, text)
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
