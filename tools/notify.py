#!/usr/bin/env python
"""
notify.py -- ONE place that decides where an alert goes, and puts it there.

    python tools/notify.py --list              show every destination + why
    python tools/notify.py --test tg-1         send a test message to one
    python tools/notify.py --test-all          ...to every enabled destination
    python tools/notify.py --status            which channels have credentials

WHY THIS EXISTS. Four tools announced things (signal_alert, event_alert,
scalper, telegram_bot) and three of them had their own copy of "read
TELEGRAM_CHAT_ID, POST to api.telegram.org". So the answer to "where do alerts
go?" was spread across three files and one environment variable, adding a
second channel meant editing all three, and adding a group meant editing a
gitignored file by hand on the machine. The destination list is now DATA, in
configs/alerts.json, editable from Settings -> Destinations.

THE SPLIT BETWEEN THE TWO FILES IS DELIBERATE:

    configs/alerts.json    WHERE to send: channel, label, target, which kinds
    configs/secrets.env    HOW to authenticate: bot token, WhatsApp token

A chat id is an address, not a credential -- it is worthless without the token,
and it has to be in the file the UI writes or it cannot be managed from the UI
at all. The token stays in the gitignored file and NEVER reaches the browser:
`/notify/status` answers only whether each credential is present, never its
value, for the same reason tools/_secrets.py refuses to print one.

KINDS. Every alert carries one: `signals` (entries and exits from
signal_alert.py), `news` (macro releases from event_alert.py), `scalper` (Rayo
tickets from scalper.py --notify). A destination lists the kinds it wants, so
one group can take gold entries while another takes only the calendar.

ONE FAILURE MUST NOT SILENCE THE REST -- the property inherited from
event_alert.send and the reason this fans out rather than looping in the caller.
If a group has removed the bot, the private chat still gets its alert. `deliver`
reports per-destination results and the caller decides; it raises nothing.

AND A PARTIAL SEND COUNTS AS SENT. signal_alert.py records a signal in its
dedupe ledger when `sent` is non-zero, so a destination that was down MISSES
that message rather than everyone receiving it again on the next poll. That is
the deliberate choice: a duplicate signal is worse than a missed copy of one,
because a duplicate can be acted on twice.

BACK COMPAT, AND IT IS NOT A MIGRATION. With no `destinations` in alerts.json
this synthesises one Telegram destination per id in TELEGRAM_CHAT_ID, receiving
every kind -- exactly what the old code did. Nothing is written: the env var
stays authoritative until somebody saves a destination list from the UI, so an
install that never opens Settings keeps working and an install that does gets
the env list pre-filled as its starting point.

WHATSAPP IS NOT TELEGRAM, and the difference is not a detail:

  * The official Cloud API CANNOT SEND TO A GROUP OR A CHANNEL. There is no
    group endpoint and no channel endpoint; `to` is a phone number. "Add a
    WhatsApp group" is not something this or any code can do through Meta's API
    today, and a Channel -- the broadcast product behind the Updates tab -- is
    further out still: Meta publishes no way to post to one programmatically,
    for anybody, at any tier.
  * A `whatsapp.com/channel/...` or `chat.whatsapp.com/...` link is an INVITE
    link, the same kind of thing as Telegram's `t.me/+hash`: an invite code, not
    an address. Internally a channel is a "newsletter" and its address is a JID
    like `120363...@newsletter`, which the invite code does not contain. Both
    link forms are refused BY NAME in normalize_target, because the digit
    scraper underneath will otherwise reduce one to a phone number that belongs
    to somebody else -- the channel link tested during this work became
    `+00297220`. A `...@newsletter` target IS passed through untouched, for a
    provider that can use one; whether it can is the provider's claim to make,
    not this file's.
  * Free-form text only reaches a number that messaged you in the last 24
    HOURS (error 131047 otherwise). An alert at 03:00 is exactly the case that
    fails, so the cloud provider sends an approved TEMPLATE when
    WHATSAPP_TEMPLATE is set, with the message as its one body variable.
  * Providers that do support groups (whapi.cloud and similar) drive WhatsApp
    Web, are not Meta products, and can get a number banned. `provider=whapi`
    is here because the alternative was pretending the option does not exist,
    and it is labelled unofficial everywhere it appears.

NEITHER WHATSAPP PATH HAS BEEN RUN AGAINST A LIVE ACCOUNT from this project --
there are no WhatsApp credentials on this machine. The request shapes follow the
published APIs, and `--test` on a real destination is the only thing that turns
that into evidence. The Telegram path is the one with a track record.
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import _secrets                                     # noqa: E402

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding='utf-8', errors='replace')
    except (AttributeError, ValueError):
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ALERTS = os.path.join(ROOT, 'configs', 'alerts.json')

TG_API = 'https://api.telegram.org/bot%s/sendMessage'
TG_PHOTO = 'https://api.telegram.org/bot%s/sendPhoto'
GRAPH = 'https://graph.facebook.com/v21.0/%s/messages'

#: Every kind of thing that gets announced. A destination with no `kinds` key
#: takes all of them -- see BACK COMPAT above.
KINDS = ('signals', 'news', 'scalper')

CHANNELS = ('telegram', 'whatsapp')


def load_config(path=ALERTS):
    try:
        with open(path, encoding='utf-8') as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _from_env():
    """The old TELEGRAM_CHAT_ID list, as destinations. See BACK COMPAT."""
    raw = os.environ.get('TELEGRAM_CHAT_ID') or ''
    out = []
    for n, chat in enumerate([c.strip() for c in raw.split(',') if c.strip()], 1):
        out.append({'id': 'env-%d' % n, 'channel': 'telegram',
                    'label': 'TELEGRAM_CHAT_ID #%d' % n, 'target': chat,
                    'enabled': True, 'kinds': list(KINDS), 'from_env': True})
    return out


def destinations(cfg=None, kind=None, enabled_only=True):
    """Every destination, or every destination that wants `kind`.

    THE ENV FALLBACK IS ALL-OR-NOTHING. If alerts.json carries a
    `destinations` LIST -- even an empty one -- that list is the answer and
    TELEGRAM_CHAT_ID is ignored. Merging the two would mean a chat removed in
    the UI kept receiving alerts from the environment, and the only way to
    silence it would be to find a file the UI does not show.
    """
    cfg = load_config() if cfg is None else cfg
    dests = cfg.get('destinations')
    if not isinstance(dests, list):
        dests = _from_env()
    out = []
    for d in dests:
        if not isinstance(d, dict) or not d.get('target'):
            continue
        if enabled_only and d.get('enabled') is False:
            continue
        if kind is not None:
            kinds = d.get('kinds')
            # Absent means "unspecified, so everything" (the env fallback and
            # hand-written files). An explicit [] means "nothing", which the UI
            # shows as a warning rather than treating as a default.
            if isinstance(kinds, list) and kind not in kinds:
                continue
        out.append(d)
    return out


def credentials():
    """What is configured, as booleans only. Never returns a secret value."""
    _secrets.load()
    provider = (os.environ.get('WHATSAPP_PROVIDER') or 'cloud').strip().lower()
    env = _from_env()
    return {
        'telegram': {
            'token': bool(os.environ.get('TELEGRAM_BOT_TOKEN')),
            'env_chats': len(env),
            # THE ADDRESSES, so the UI can offer "import these" rather than
            # making somebody retype a chat id from a gitignored file. A chat
            # id is not a credential -- it addresses a conversation and is
            # useless without the token, which is NOT in this dict at any
            # depth. That distinction is the whole reason this endpoint can
            # exist at all.
            'env_targets': [d['target'] for d in env],
        },
        'whatsapp': {
            'provider': provider,
            'token': bool(os.environ.get('WHATSAPP_TOKEN')),
            'phone_id': bool(os.environ.get('WHATSAPP_PHONE_ID')),
            'template': (os.environ.get('WHATSAPP_TEMPLATE') or '') or None,
            'groups': provider != 'cloud',
        },
    }


def _post_json(url, payload, headers=None, timeout=20):
    data = json.dumps(payload).encode('utf-8')
    head = {'Content-Type': 'application/json'}
    head.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=head)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode() or '{}')


def _http_error(err):
    """An API's own error text, not just '400 Bad Request'.

    Every one of these APIs explains the refusal in the BODY -- a wrong chat
    id, a group the bot was removed from, a 24-hour window that has closed --
    and urllib puts none of that in the exception's string. Reading it is the
    difference between a fixable message and 'HTTP Error 400'.
    """
    try:
        body = err.read().decode('utf-8', 'replace')[:400]
    except Exception:                                          # noqa: BLE001
        body = ''
    return 'HTTP %s %s' % (getattr(err, 'code', '?'), body or err)


#: getChat answers are cached for the life of the process, keyed on the
#: resolved address. A channel is not renamed twice in a minute, and the panel
#: asks once per row every time it opens -- without this, leaving Settings open
#: and switching tabs would talk to Telegram for no new information.
_NAMES = {}


def describe(channel, target, timeout=10, refresh=False):
    """What is this chat CALLED? Read-only; sends nothing to it.

    WHY A LOOKUP RATHER THAN A TYPED NAME. The panel used to label rows
    `Telegram 1` and `Telegram 2`, which is the one thing about a destination
    nobody needs told -- what a reader wants to see beside a chat id is the
    name of the group it belongs to. Telegram already knows it, so asking is
    better than making somebody type it and better than a number.

    `getChat` IS THE WHOLE ANSWER and it needs no extra rights: a bot may call
    it for any chat it is IN, which is exactly the set of chats worth being a
    destination. Its reply carries `title` for a group or channel, and
    `first_name` for a private chat -- so a private chat is named after the
    person rather than reading as untitled.

    IT CAN FAIL AND THAT IS NOT AN ERROR. The bot may not be a member yet, the
    token may be absent, the network may be down. Every one of those returns
    ok:false with the reason and leaves the address alone: a name is a
    convenience, and refusing to show a destination because its name could not
    be fetched would be the tail wagging the dog.

    NOT AVAILABLE ON WHATSAPP. Neither the Cloud API nor the unofficial
    providers expose a contact or group name for a number you hold -- there is
    no equivalent call -- so a WhatsApp row keeps whatever it is called here.
    """
    if channel != 'telegram':
        return {'ok': False, 'error': 'no name lookup for %s' % channel}
    try:
        resolved = normalize_target(channel, target)
    except Exception as err:                                   # noqa: BLE001
        return {'ok': False, 'error': str(err)}
    if not refresh and resolved in _NAMES:
        return _NAMES[resolved]

    _secrets.load()
    token = os.environ.get('TELEGRAM_BOT_TOKEN')
    if not token:
        return {'ok': False, 'error': 'TELEGRAM_BOT_TOKEN missing from '
                                      'configs/secrets.env'}
    url = ('https://api.telegram.org/bot%s/getChat?%s'
           % (token, urllib.parse.urlencode({'chat_id': resolved})))
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            body = json.loads(r.read().decode())
    except urllib.error.HTTPError as err:
        out = {'ok': False, 'error': _http_error(err)}
        _NAMES[resolved] = out
        return out
    except Exception as err:                                   # noqa: BLE001
        # NOT CACHED. A timeout says nothing about the chat, and caching it
        # would keep the row nameless for the life of the process over one bad
        # moment on the network.
        return {'ok': False, 'error': str(err)}
    if not body.get('ok'):
        out = {'ok': False, 'error': str(body.get('description') or body)}
        _NAMES[resolved] = out
        return out

    chat = body.get('result') or {}
    name = (chat.get('title')
            or ' '.join(x for x in (chat.get('first_name'), chat.get('last_name')) if x)
            or (('@' + chat['username']) if chat.get('username') else None)
            or str(chat.get('id') or resolved))
    out = {'ok': True, 'title': name, 'type': chat.get('type'),
           'username': chat.get('username'), 'id': chat.get('id'),
           'target': resolved}
    _NAMES[resolved] = out
    return out

def send_telegram(target, text, token=None):
    token = token or os.environ.get('TELEGRAM_BOT_TOKEN')
    if not token:
        raise RuntimeError('TELEGRAM_BOT_TOKEN missing from configs/secrets.env')
    data = urllib.parse.urlencode({
        'chat_id': target, 'text': text, 'parse_mode': 'Markdown',
        'disable_web_page_preview': 'true',
    }).encode()
    try:
        req = urllib.request.Request(TG_API % token, data=data)
        with urllib.request.urlopen(req, timeout=20) as r:
            body = json.loads(r.read().decode())
    except urllib.error.HTTPError as err:
        raise RuntimeError(_http_error(err))
    if not body.get('ok'):
        raise RuntimeError(str(body))
    return body


def _multipart(fields, files):
    """A multipart/form-data body. Returns (content_type, bytes).

    Hand-rolled because this module talks to Telegram with urllib and nothing
    else, and sendPhoto is the only call in the project that needs a file part.
    Pulling in `requests` for one endpoint would put a third-party import on
    the scheduled-task path, which runs headless under pythonw.
    """
    CRLF = chr(13) + chr(10)          # written with chr() on purpose: a
    # patch script twice turned a literal \r\n in this file into a REAL
    # carriage return inside the string, which is a syntax error at best and
    # a malformed request body at worst.
    boundary = '----dianurfx%s' % uuid.uuid4().hex
    out = []
    for k, v in fields.items():
        out.append(('--%s%s' % (boundary, CRLF)).encode())
        out.append(('Content-Disposition: form-data; name="%s"%s%s'
                    % (k, CRLF, CRLF)).encode())
        out.append(('%s%s' % (v, CRLF)).encode('utf-8'))
    for k, (fname, blob, ctype) in files.items():
        out.append(('--%s%s' % (boundary, CRLF)).encode())
        out.append(('Content-Disposition: form-data; name="%s"; filename="%s"%s'
                    % (k, fname, CRLF)).encode())
        out.append(('Content-Type: %s%s%s' % (ctype, CRLF, CRLF)).encode())
        out.append(blob)
        out.append(CRLF.encode())
    out.append(('--%s--%s' % (boundary, CRLF)).encode())
    return 'multipart/form-data; boundary=%s' % boundary, b''.join(out)


#: Telegram truncates a photo caption past this. The scalper ticket is ~250
#: characters, so this is a guard against a future longer message silently
#: losing its tail rather than a limit anyone is near.
TG_CAPTION_MAX = 1024


def send_telegram_photo(target, png, caption, token=None):
    """One photo with the ticket as its caption.

    ONE MESSAGE, NOT TWO. Sending the text and then the image would put them in
    separate bubbles that a busy group can interleave with other traffic, and a
    chart floating next to somebody else's message is worse than no chart. The
    caption binds them.
    """
    token = token or os.environ.get('TELEGRAM_BOT_TOKEN')
    if not token:
        raise RuntimeError('TELEGRAM_BOT_TOKEN missing from configs/secrets.env')
    if len(caption.encode('utf-8')) > TG_CAPTION_MAX:
        caption = caption[:TG_CAPTION_MAX - 20].rstrip() + chr(10) + '...'
    ctype, body = _multipart(
        {'chat_id': str(target), 'caption': caption, 'parse_mode': 'Markdown'},
        {'photo': ('signal.png', png, 'image/png')})
    try:
        req = urllib.request.Request(TG_PHOTO % token, data=body,
                                     headers={'Content-Type': ctype})
        with urllib.request.urlopen(req, timeout=40) as r:
            out = json.loads(r.read().decode())
    except urllib.error.HTTPError as err:
        raise RuntimeError(_http_error(err))
    if not out.get('ok'):
        raise RuntimeError(str(out))
    return out


def send_whatsapp(target, text):
    """Cloud API by default; `whapi` for the unofficial group-capable path."""
    provider = (os.environ.get('WHATSAPP_PROVIDER') or 'cloud').strip().lower()
    token = os.environ.get('WHATSAPP_TOKEN')
    if not token:
        raise RuntimeError('WHATSAPP_TOKEN missing from configs/secrets.env')

    if provider == 'whapi':
        base = (os.environ.get('WHAPI_BASE') or 'https://gate.whapi.cloud').rstrip('/')
        try:
            body = _post_json(base + '/messages/text', {'to': target, 'body': text},
                              {'Authorization': 'Bearer ' + token})
        except urllib.error.HTTPError as err:
            raise RuntimeError(_http_error(err))
        if body.get('sent') is False or body.get('error'):
            raise RuntimeError(str(body))
        return body

    phone_id = os.environ.get('WHATSAPP_PHONE_ID')
    if not phone_id:
        raise RuntimeError('WHATSAPP_PHONE_ID missing from configs/secrets.env')
    # '@' or a g.us JID ONLY. This used to flag any hyphen, which caught
    # `+61-412-345-678` -- a perfectly good number written the way people write
    # numbers. normalize_target() strips that formatting before this sees it,
    # so a hyphen here no longer means anything and testing for it refused
    # valid targets.
    if '@' in str(target) or 'g.us' in str(target).lower():
        # Said plainly rather than sent and refused with a Meta error code that
        # explains nothing. There is no group endpoint on the Cloud API.
        raise RuntimeError('the WhatsApp Cloud API cannot send to a group or a '
                           'channel -- `to` is a phone number in E.164 form and '
                           'there is no other endpoint. An unofficial provider '
                           '(WHATSAPP_PROVIDER=whapi) is the only route, and '
                           'only if that provider supports it')

    template = os.environ.get('WHATSAPP_TEMPLATE')
    if template:
        lang = os.environ.get('WHATSAPP_TEMPLATE_LANG') or 'en'
        payload = {'messaging_product': 'whatsapp', 'to': target,
                   'type': 'template',
                   'template': {'name': template, 'language': {'code': lang},
                                'components': [{'type': 'body', 'parameters': [
                                    {'type': 'text', 'text': text[:1024]}]}]}}
    else:
        payload = {'messaging_product': 'whatsapp', 'to': target, 'type': 'text',
                   'text': {'preview_url': False, 'body': text[:4096]}}
    try:
        body = _post_json(GRAPH % phone_id, payload,
                          {'Authorization': 'Bearer ' + token})
    except urllib.error.HTTPError as err:
        extra = ''
        if not template:
            extra = ('  (free-form text only reaches a number that messaged you '
                     'in the last 24h -- set WHATSAPP_TEMPLATE for alerts)')
        raise RuntimeError(_http_error(err) + extra)
    if body.get('error'):
        raise RuntimeError(str(body['error']))
    return body


def normalize_target(channel, target):
    """Turn what a person would PASTE into what the API wants.

    Returns the address to send to. Raises RuntimeError, with the reason and
    the way out, for the shapes that cannot address a chat at all.

    WHY THIS IS NOT LEFT TO THE READER. Nobody has a Telegram numeric chat id
    to hand; what they have is the thing they can copy -- `@goldsignals`, or
    the `t.me/...` link out of the channel's own share menu. Requiring the id
    meant a detour through `getUpdates` for every group, and getting it wrong
    fails as "chat not found", which reads like a permissions problem.

    WHAT TELEGRAM ACCEPTS AS `chat_id`, and it is only these two:

        -1001234567890      the numeric id, for anything
        @publicname         the @username, for a PUBLIC channel or group only

    So a link is converted to one of those, and the links that cannot be:

      * `t.me/+AbCd...` and `t.me/joinchat/...` are INVITE links. They carry a
        one-time invite hash, NOT an address -- nothing in the Bot API resolves
        one, by design, and it would still be the wrong answer because a bot
        cannot join by link. Use the numeric id.
      * `t.me/c/1234567890/55` IS resolvable: that path form appears in private
        supergroups and channels, and the id is `-100` + that number. Converted
        here rather than made the reader's arithmetic problem.

    A PUBLIC @USERNAME STILL NEEDS THE BOT INSIDE THE CHANNEL as a member (an
    administrator, for a channel). Resolving the address and being allowed to
    post are different questions and only the first one is answered here.

    WHATSAPP takes a phone number in E.164 and nothing else on the Cloud API,
    so `wa.me/61412345678`, `+61 412 345 678` and `0412 345 678` all reduce to
    digits -- except the last one, which cannot be: a national number has no
    country code and guessing one would send a message to a stranger in
    whichever country the guess landed in. That is refused.
    """
    t = str(target or '').strip()
    if not t:
        raise RuntimeError('destination has no target')

    if channel == 'telegram':
        low = t.lower()
        for host in ('https://', 'http://'):
            if low.startswith(host):
                t, low = t[len(host):], low[len(host):]
        for host in ('t.me/', 'telegram.me/', 'telegram.dog/'):
            if low.startswith(host):
                rest = t[len(host):].strip('/')
                if rest.startswith('+') or rest.lower().startswith('joinchat/'):
                    raise RuntimeError(
                        'that is a Telegram INVITE link, which is a one-time '
                        'hash rather than an address -- no bot can resolve or '
                        'join one. Use the numeric chat id (-100...), or the '
                        '@username if the channel is public')
                if rest.lower().startswith('c/'):
                    # t.me/c/<internal id>/<message> -> -100<internal id>
                    parts = [p for p in rest.split('/') if p]
                    if len(parts) >= 2 and parts[1].isdigit():
                        return '-100' + parts[1]
                    raise RuntimeError('cannot read a chat id out of %r' % target)
                name = rest.split('/')[0].split('?')[0]
                if not name:
                    raise RuntimeError('no channel name in %r' % target)
                return '@' + name.lstrip('@')
        if t.startswith('@'):
            return t
        # A bare id, or a bare username typed without the @. Digits (with an
        # optional leading -) are an id; anything else is treated as a username,
        # because a username is the only other thing Telegram will accept.
        bare = t[1:] if t.startswith('-') else t
        if bare.isdigit():
            return t
        return '@' + t

    if channel == 'whatsapp':
        low = t.lower()
        if '@' in t or 'g.us' in low:
            return t                       # a JID; the group check handles it
        for host in ('https://', 'http://'):
            if low.startswith(host):
                t, low = t[len(host):], low[len(host):]
        # THE INVITE LINKS, REFUSED BY NAME AND BEFORE ANY DIGITS ARE READ.
        # Both of these carry an invite CODE, not an address -- the same
        # distinction Telegram's t.me/+hash has -- and the digit scraper below
        # would happily reduce one to a phone number that belongs to somebody
        # else. Measured, not imagined: the channel link in this comment
        # produced `+00297220`, a number nobody chose, which the sender would
        # then have messaged. A refusal is the only safe answer.
        for host, what in (
                ('whatsapp.com/channel/', 'CHANNEL'),
                ('chat.whatsapp.com/', 'GROUP')):
            if host in low:
                raise RuntimeError(
                    'that is a WhatsApp %s INVITE link. It carries an invite '
                    'code rather than an address, and the official Cloud API '
                    'cannot post to a channel or a group at all -- `to` is a '
                    'phone number and there is no other endpoint. See '
                    'tools/notify.py for the whole story' % what)
        for host in ('wa.me/', 'api.whatsapp.com/send', 'web.whatsapp.com/send',
                     'whatsapp://send'):
            if low.startswith(host):
                tail = t[len(host):]
                if 'phone=' in tail:
                    tail = tail.split('phone=', 1)[1].split('&')[0]
                t = tail.strip('/?&')
                break
        # ANY LETTER MEANS IT IS NOT A NUMBER. Without this the scraper turns
        # `chat.whatsapp.com/AbCdEf123` into `+123` -- it finds digits in
        # anything, which is precisely why it must not be the last word.
        if any(ch.isalpha() for ch in t):
            raise RuntimeError('%r is not a phone number. WhatsApp needs a '
                               'number in E.164 form, e.g. +61412345678' % target)
        digits = ''.join(ch for ch in t if ch.isdigit())
        if not digits:
            raise RuntimeError('no phone number in %r' % target)
        # E.164 IS 15 DIGITS AT MOST and no country has fewer than 7 with its
        # code. A length outside that is not a phone number, whatever it is.
        if not 7 <= len(digits) <= 15:
            raise RuntimeError('%r has %d digits; a phone number in E.164 form '
                               'has 7 to 15' % (target, len(digits)))
        if t.strip().startswith('0'):
            raise RuntimeError(
                'that looks like a national number (leading 0). WhatsApp needs '
                'the country code -- e.g. +61412345678, not 0412345678. '
                'Guessing a country would message a stranger')
        return '+' + digits

    return t


def send_one(dest, text, image=None):
    """Send to a single destination. Raises RuntimeError with a readable why.

    `image` is PNG bytes or None. WHATSAPP IGNORES IT, deliberately: the Cloud
    API will not accept raw bytes, only a publicly reachable media URL or a
    prior upload against a phone-number id, and this project has neither. A
    WhatsApp destination therefore gets the text it always got rather than a
    silent failure -- and the caller is told, so it never looks like the image
    was sent.
    """
    _secrets.load()
    channel = (dest.get('channel') or 'telegram').strip().lower()
    if channel not in CHANNELS:
        raise RuntimeError('unknown channel %r (expected one of %s)'
                           % (channel, ', '.join(CHANNELS)))
    # NORMALISED HERE, IN THE SENDER, not in the browser. The UI shows what a
    # pasted link will become, but the page is one of several callers -- the
    # scheduled tools read the same file -- and a rule enforced only in the UI
    # is a rule a hand-edited config does not have to follow.
    target = normalize_target(channel, dest.get('target'))
    if channel == 'telegram':
        if image:
            return send_telegram_photo(target, image, text)
        return send_telegram(target, text)
    return send_whatsapp(target, text)


def deliver(kind, text, cfg=None, dests=None, dry=False, image=None):
    """Fan `text` out to every destination that wants `kind`.

    Returns {'sent': n, 'failed': n, 'results': [...], 'total': n}. It does not
    raise: one dead group must not stop the others, and the caller needs to
    know that SOMETHING got through, which an exception cannot express.
    """
    if dests is None:
        dests = destinations(cfg, kind)
    out = {'sent': 0, 'failed': 0, 'total': len(dests), 'results': []}
    for d in dests:
        name = d.get('label') or d.get('id') or d.get('target')
        if dry:
            out['results'].append({'id': d.get('id'), 'label': name,
                                   'ok': True, 'dry': True})
            out['sent'] += 1
            continue
        try:
            send_one(d, text, image=image)
            out['sent'] += 1
            out['results'].append({'id': d.get('id'), 'label': name, 'ok': True,
                                   'image': bool(image) and
                                   (d.get('channel') or 'telegram') == 'telegram'})
        except Exception as err:                               # noqa: BLE001
            out['failed'] += 1
            out['results'].append({'id': d.get('id'), 'label': name,
                                   'ok': False, 'error': str(err)})
            print('notify: %s (%s) failed -- %s'
                  % (name, d.get('channel'), err), file=sys.stderr)
    if not dests:
        # A configuration with nowhere to send is not an error here, but it is
        # the single most likely reason "the alerts stopped" -- so it says so
        # every time rather than only when somebody runs --list.
        print('notify: no destination wants %r -- configure Settings -> '
              'Destinations' % kind, file=sys.stderr)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--list', action='store_true', help='show every destination')
    ap.add_argument('--status', action='store_true', help='which credentials exist')
    ap.add_argument('--names', action='store_true',
                    help='with --list, ask Telegram what each chat is called')
    ap.add_argument('--test', metavar='ID', help='send a test message to one id')
    ap.add_argument('--test-all', action='store_true',
                    help='send a test message to every enabled destination')
    ap.add_argument('--kind', default='signals', choices=KINDS,
                    help='which kind --test-all should fan out as')
    ap.add_argument('--text', default=None, help='override the test message')
    args = ap.parse_args()

    _secrets.load()
    cfg = load_config()
    cred = credentials()

    if args.status or not (args.list or args.test or args.test_all):
        print('credentials (presence only, never values):')
        print('  telegram  token %s, TELEGRAM_CHAT_ID holds %d chat(s)'
              % ('yes' if cred['telegram']['token'] else 'NO',
                 cred['telegram']['env_chats']))
        w = cred['whatsapp']
        print('  whatsapp  provider %s, token %s, phone id %s, template %s'
              % (w['provider'], 'yes' if w['token'] else 'NO',
                 'yes' if w['phone_id'] else 'NO', w['template'] or 'none'))
        if not w['groups']:
            print('            the Cloud API cannot send to groups; numbers only')

    if args.list or not (args.test or args.test_all or args.status):
        rows = destinations(cfg, enabled_only=False)
        src = 'configs/alerts.json' if isinstance(cfg.get('destinations'), list) \
            else 'TELEGRAM_CHAT_ID (no destinations in alerts.json yet)'
        print()
        print('%d destination(s) from %s' % (len(rows), src))
        for d in rows:
            kinds = d.get('kinds')
            print('  [%s] %-8s %-10s %-22s %s'
                  % ('x' if d.get('enabled') is not False else ' ',
                     d.get('id') or '?', d.get('channel') or 'telegram',
                     d.get('label') or '', 'all kinds' if not isinstance(kinds, list)
                     else (', '.join(kinds) or 'NOTHING -- receives no alerts')))
            if args.names:
                # NOT BY DEFAULT: --list is the offline answer to "where do
                # alerts go", and one network call per row would make the
                # cheapest command here fail when the network does.
                who = describe(d.get('channel') or 'telegram', d.get('target'))
                print('           %s' % (('%s (%s)' % (who['title'], who.get('type')))
                                         if who.get('ok')
                                         else 'name unavailable: %s' % who.get('error')))
        if not rows:
            print('  none -- nothing will be announced anywhere')

    if args.test or args.test_all:
        text = args.text or ('DiaNurFx test -- if you can read this, this '
                             'destination is wired up correctly.')
        if args.test:
            rows = [d for d in destinations(cfg, enabled_only=False)
                    if str(d.get('id')) == args.test]
            if not rows:
                print('no destination with id %r' % args.test, file=sys.stderr)
                return 2
        else:
            rows = destinations(cfg, args.kind)
        res = deliver(args.kind, text, cfg=cfg, dests=rows)
        print()
        for r in res['results']:
            print('  %-22s %s' % (r['label'], 'sent' if r['ok'] else r['error']))
        print('%d sent, %d failed' % (res['sent'], res['failed']))
        return 0 if res['sent'] and not res['failed'] else 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
