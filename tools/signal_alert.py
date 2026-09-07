#!/usr/bin/env python
"""
Announce a rule signal to Telegram, for whatever cells configs/alerts.json lists.

    python tools/signal_alert.py --list       what is watched, and why
    python tools/signal_alert.py --dry-run    poll now, print, send nothing
    python tools/signal_alert.py              poll and send

THE CELLS ARE CONFIGURATION, NOT CODE. `configs/alerts.json` is read on every
run, so adding or dropping an instrument or timeframe takes effect on the next
poll with nothing restarted. A cell can be disabled rather than deleted, which
keeps the note explaining why it was ever watched.

ONE SIGNAL IS ANNOUNCED ONCE. Every send is keyed on
(symbol, timeframe, bar time, action) in `data/signal_alerted.json`, so a task
polling every minute against a 4h cell sends one message per signal and not two
hundred and forty. The key includes the BAR TIME rather than the wall clock:
the same signal is reported by the bridge on every poll until the bar closes,
and keying on anything else would either spam or miss the next one.

`hold` IS NEVER ANNOUNCED. It is the answer on almost every bar of every cell,
and a channel that receives it stops being read.

THE BRIDGE DOES THE THINKING. `/signal` is the rule's own statement, computed in
Python on the same code the backtests run -- this file decides WHEN to ask and
WHO to tell, and composes nothing the rule did not say. The `instruction` field
is quoted verbatim for that reason.

NOT A TRADE INSTRUCTION. The bridge is read-only and so is this. It reports what
the rule says; every measurement in the README says the rule is at or below
friction on all but one cell, and that one's interval spans zero.
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
from tools import _secrets                                     # noqa: E402
from tools.event_alert import BNE, send                        # noqa: E402

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding='utf-8', errors='replace')
    except (AttributeError, ValueError):
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = os.path.join(ROOT, 'configs', 'alerts.json')
STATE = os.path.join(ROOT, 'data', 'signal_alerted.json')
BRIDGE = os.environ.get('DNFX_BRIDGE', 'http://127.0.0.1:8765')

ICON = {'buy': '\U0001F7E2', 'sell': '\U0001F534', 'exit': '\U0001F535'}


def load_config():
    """The signals half of configs/alerts.json.

    JSON RATHER THAN YAML, and the reason is the Settings modal. The UI writes
    this file, and writing YAML from the browser would either need a serialiser
    on the server or would destroy every comment in the file on the first save.
    The per-cell `note` survives because it is DATA, not a comment -- which is
    the only kind of explanation a machine-written file can keep.

    A MISSING OR BROKEN FILE WATCHES NOTHING. It does not fall back to a
    built-in list: a silent default here would keep announcing cells nobody
    could see in the UI, which is worse than silence.
    """
    try:
        with open(CONFIG, encoding='utf-8') as fh:
            cfg = json.load(fh) or {}
    except (OSError, ValueError) as err:
        print('cannot read %s (%s)' % (CONFIG, err), file=sys.stderr)
        return {'alert_on': [], 'watch': []}
    sig = cfg.get('signals') or {}
    return {'alert_on': sig.get('alert_on') or ['buy', 'sell', 'exit'],
            'watch': sig.get('watch') or []}


def load_state():
    try:
        with open(STATE, encoding='utf-8') as fh:
            return json.load(fh)
    except (OSError, ValueError):
        # A corrupt state file costs at most one duplicate message; raising here
        # would cost every signal until someone noticed.
        return {}


def save_state(state):
    tmp = STATE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as fh:
        json.dump(state, fh, indent=1, sort_keys=True)
    os.replace(tmp, STATE)


def prune(state, keep=400):
    """Keep the newest `keep` keys. The file is a dedupe ledger, not a record."""
    if len(state) <= keep:
        return state
    newest = sorted(state.items(), key=lambda kv: kv[1], reverse=True)[:keep]
    return dict(newest)


def poll(symbol, tf, timeout=90):
    q = urllib.parse.urlencode({'symbol': symbol, 'tf': tf})
    with urllib.request.urlopen(BRIDGE + '/signal?' + q, timeout=timeout) as r:
        return json.loads(r.read().decode())


#: Broker tickers to the names a reader uses. `.a` is Pepperstone's suffix and
#: means nothing to anyone reading a message.
PRETTY = {'XAUUSD': 'XAU/USD', 'XAGUSD': 'XAG/USD', 'EURUSD': 'EUR/USD',
          'GBPUSD': 'GBP/USD', 'USDJPY': 'USD/JPY', 'AUDUSD': 'AUD/USD',
          'USDCAD': 'USD/CAD', 'AUDJPY': 'AUD/JPY'}


def pretty(symbol):
    base = (symbol or '').replace('.a', '').upper()
    return PRETTY.get(base, base)


def compose(sig):
    """The signal in the shape a reader wants it, from fields the rule computed.

    NOTHING HERE IS INVENTED. Signal is the close that triggered, Entry is the
    rule's `est_entry`, SL is its `stop`, and TP1/TP2/TP3 are its `ref_targets`
    -- 1R, 2R and 3R from the entry, priced off the same stop distance the rule
    sized on.

    WHAT "TP" MEANS HERE, because the label is doing work it has not earned.
    `sim/signal.py` calls those levels REFERENCE and says outright that quoting
    them as TP1/TP2/TP3 "would be reporting a rule that was never measured" --
    the rule has no take-profit, it exits on the channel. `tools/tp_sweep.py`
    measured a 1R cap on the validated cell and it turned +43.7 net R into
    -2.1: a trend rule is paid from the tail and a cap is a bet against it.

    The labels are used anyway because the CHART already uses them, captioned
    "in the way -- not targets", and one vocabulary across the two surfaces is
    worth more than a second one that is more precise in isolation. The rule's
    own exit is printed on its own line so nothing has to be inferred.

    TWO PRICES, NOT ONE, and they differ for a reason. The rule decides on a
    CLOSE and fills at the NEXT OPEN, so the trigger price is known and the
    entry is not: `est_entry` carries the modelled spread and slippage. Showing
    only one of them would hide the cost of getting in.
    """
    act = (sig.get('action') or '').lower()
    icon = ICON.get(act, '⚪')
    name = '%s %s' % (pretty(sig.get('symbol')), sig.get('tf'))

    if act == 'exit':
        bits = ['%s *%s*  EXIT' % (icon, name)]
        if sig.get('instruction'):
            bits.append(sig['instruction'])
        return '\n'.join(bits + [_bar_line(sig)])

    d = int(sig.get('digits') or 2)
    px = lambda v: ('%.*f' % (d, v)) if isinstance(v, (int, float)) else str(v)

    bits = ['*%s*  %s' % (name, act.upper())]
    # THE TRIGGER CLOSE IS NOT SHOWN, by request. It was there to make the gap
    # between the price that DECIDED and the price you GET visible: the rule
    # fires on a close and fills at the next open, so `est_entry` carries
    # modelled spread and slippage and is always the worse of the two. That
    # cost has not gone away, it is only no longer printed, and `bar_close` is
    # still in the payload for anyone reconciling a fill.
    if sig.get('est_entry') is not None:
        bits.append('\U0001F7E2   Entry   %s' % px(sig['est_entry']))
    for n, (_r, price) in enumerate(sig.get('ref_targets') or [], 1):
        bits.append('\U0001F535   TP%d     %s' % (n, px(price)))
    if sig.get('stop') is not None:
        bits.append('\U0001F534   SL      %s' % px(sig['stop']))

    if sig.get('lots') is not None:
        # a row, not a footnote: size is the number a reader acts on and it was
        # asked for in the same column as the prices
        bits.append('⚪   Lots    %s' % sig['lots'])
    if sig.get('channel_exit') is not None:
        # the rule's ACTUAL exit, which is none of the TPs above
        bits.append('_rule exits on close %s %s_'
                    % ('<' if act == 'buy' else '>', px(sig['channel_exit'])))
    bits.append(_bar_line(sig))
    return '\n'.join(bits)


def _bar_line(sig):
    """Bar stamp in Brisbane with UTC beside it.

    Both, because this project has already shipped one bug from conflating the
    calendar's UTC with the broker's clock, and a bar stamp with no zone is how
    that happens again.
    """
    bt = sig.get('bar_time')
    if not bt:
        return ''
    try:
        when = datetime.strptime(bt, '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)
    except ValueError:
        return 'bar %s UTC' % bt
    return '_bar %s AEST  (%s UTC)_' % (when.astimezone(BNE).strftime('%a %d %b %H:%M'),
                                        when.strftime('%H:%M'))


def cells(cfg):
    for w in cfg.get('watch') or []:
        if not isinstance(w, dict) or not w.get('enabled', True):
            continue
        sym, tf = w.get('symbol'), str(w.get('tf') or '')
        if sym and tf:
            yield sym, tf, w.get('note') or ''


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--dry-run', action='store_true',
                    help='poll and print; send nothing, record nothing')
    ap.add_argument('--list', action='store_true',
                    help='show the configured cells and exit')
    args = ap.parse_args()

    cfg = load_config()

    if args.list:
        print('configs/alerts.json')
        print('  alert on: %s' % ', '.join(cfg['alert_on']))
        print('  watching:')
        any_on = False
        for w in cfg.get('watch') or []:
            on = w.get('enabled', True)
            any_on = any_on or on
            print('    [%s] %-10s %-4s  %s' % ('x' if on else ' ',
                                               w.get('symbol'), w.get('tf'),
                                               w.get('note') or ''))
        if not any_on:
            print('  NOTHING ENABLED -- every cell is switched off')
        return 0

    _secrets.load()
    token = os.environ.get('TELEGRAM_BOT_TOKEN')
    chat = os.environ.get('TELEGRAM_CHAT_ID')

    state = load_state()
    fired = 0
    for sym, tf, _note in cells(cfg):
        try:
            sig = poll(sym, tf)
        except (urllib.error.URLError, OSError, ValueError) as err:
            # One unreachable cell must not stop the rest: a 4h signal is worth
            # more than a tidy exit.
            print('%s %s: bridge failed (%s)' % (sym, tf, err), file=sys.stderr)
            continue
        act = (sig.get('action') or '').lower()
        if act not in cfg['alert_on']:
            print('%s %s: %s (%s)' % (sym, tf, act, sig.get('state')))
            continue
        key = '%s|%s|%s|%s' % (sym, tf, sig.get('bar_time'), act)
        if key in state:
            print('%s %s: %s already sent' % (sym, tf, act))
            continue
        text = compose(sig)
        if args.dry_run:
            print('WOULD SEND:\n%s\n' % text)
            continue
        if not (token and chat):
            print('WOULD SEND:\n%s\n' % text)
            print('TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID missing from '
                  'configs/secrets.env', file=sys.stderr)
            return 2
        try:
            send(token, chat, text)
        except Exception as err:                               # noqa: BLE001
            print('%s %s: send failed (%s)' % (sym, tf, err), file=sys.stderr)
            continue
        # recorded only after a successful send, so a network failure retries
        state[key] = datetime.now(timezone.utc).isoformat()
        fired += 1

        # WRITTEN NOW, NOT AT THE END OF THE LOOP. This used to save once after
        # every cell had been polled, which left a window in which a message had
        # gone out but nothing on disk said so -- and anything that ended the
        # process inside that window made the next run send it again.
        #
        # That window was not theoretical. `poll()` allows 90s PER CELL and
        # there are seven enabled, so a slow bridge can run this past ten
        # minutes, while the scheduled task's ExecutionTimeLimit terminates it.
        # A duplicate signal is worst exactly when the bridge is struggling,
        # which is when you can least afford to distrust the feed.
        #
        # The cost is one small atomic write per SENT message -- not per poll,
        # and `hold` is the answer on almost every bar of every cell, so in
        # practice this writes only when something actually happened.
        save_state(prune(state))
        print('sent: %s' % key)

    # `fired` no longer gates the save -- each send writes its own -- so it is
    # only worth printing, and a run that sent nothing says so rather than
    # ending in silence that reads the same as a crash.
    print('%d sent' % fired if fired else 'nothing to send')
    return 0


if __name__ == '__main__':
    sys.exit(main())
