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
#: The TRACK RECORD, and a different thing from STATE above. STATE is a dedupe
#: ledger: keys only, pruned to the newest 400, and its whole job is answering
#: "have I already sent this?". This is append-only, never pruned, and keeps the
#: full payload of every announced signal so it can be scored forward later.
#: Two files because the two have opposite lifetimes -- pruning a dedupe ledger
#: is correct and pruning a track record destroys it.
JOURNAL = os.path.join(ROOT, 'data', 'signal_journal.jsonl')
BRIDGE = os.environ.get('DNFX_BRIDGE', 'http://127.0.0.1:8765')

ICON = {'buy': '\U0001F7E2', 'sell': '\U0001F534', 'exit': '\U0001F535'}

#: THE CELL'S MEASURED STANDING, carried on every message.
#:
#: Six of the seven enabled cells are at or below their own friction floor
#: (tools/friction_map.py), and the fast ones generate the most alerts while
#: being the least likely to be worth acting on. Dropping them silently would
#: make that judgement in code; leaving them unmarked lets a gold 1m alert
#: arrive looking exactly like the one cell ever measured positive net of it.
#: Marking them keeps the decision in the config -- visible, and yours -- while
#: making the message tell the truth about itself.
#:
#: A cell with no grade is UNGRADED, not assumed fine: the Settings modal can
#: add a cell nobody has measured, and silence there would read as approval.
GRADE_MARK = {
    'validated': '',
    'marginal': '  ' + chr(183) + '  marginal',
    'below': '  ' + chr(183) + '  below friction',
}
UNGRADED = '  ' + chr(183) + '  ungraded'


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


def journal(sig, key):
    """Append one announced signal, with everything a scorer needs later.

    JSONL rather than JSON: an append is one line and one open, so a process
    killed mid-write loses at most the line it was writing rather than the file.
    The dedupe ledger can afford atomic-replace because it is small and
    rewritable; a record that only grows cannot.

    `params` IS RECORDED, and that is what makes honest scoring possible at all.
    The rule's exit is a Donchian channel recomputed on every bar
    (`exit_lo = low.rolling(exit).min().shift(1)`, long exits on a close below
    it), so a snapshot of today's `channel_exit` would be the wrong level
    tomorrow. With the parameters stored, the scorer can rebuild the channel
    from bars and reproduce the rule instead of approximating it.
    """
    row = {
        'key': key,
        'sent_at': datetime.now(timezone.utc).isoformat(),
        'symbol': sig.get('symbol'), 'tf': sig.get('tf'),
        'bar_time': sig.get('bar_time'), 'action': (sig.get('action') or '').lower(),
        'bar_close': sig.get('bar_close'), 'est_entry': sig.get('est_entry'),
        'stop': sig.get('stop'), 'channel_exit': sig.get('channel_exit'),
        'ref_targets': sig.get('ref_targets'), 'atr': sig.get('atr'),
        'lots': sig.get('lots'), 'digits': sig.get('digits'),
        'strategy': sig.get('strategy'), 'params': sig.get('params'),
    }
    try:
        with open(JOURNAL, 'a', encoding='utf-8') as fh:
            fh.write(json.dumps(row, sort_keys=True) + chr(10))
    except OSError as err:
        # NEVER fatal. A signal that went out and was not journalled is a gap in
        # the record; a signal that was not sent because the record could not be
        # written is a gap in the alerting, which is worse.
        print('journal write failed: %s' % err, file=sys.stderr)


def prune(state, keep=400):
    """Keep the newest `keep` keys. The file is a dedupe ledger, not a record."""
    if len(state) <= keep:
        return state
    newest = sorted(state.items(), key=lambda kv: kv[1], reverse=True)[:keep]
    return dict(newest)


def poll(symbol, tf, strategy=None, timeout=90):
    """Ask the bridge what the rule says for one cell.

    `strategy` IS PER CELL AND OPTIONAL. Omitted, the bridge picks the
    timeframe's own validated rule (sim.strategies.strategy_for_tf). Named, that
    cell runs that rule instead.

    THE POINT IS THE INSTRUMENT. The default is chosen per TIMEFRAME and applies
    to every symbol, so USDJPY 4h was handed the rule validated on XAUUSD 4h --
    and USDJPY 4h measured clearly negative under it. An edge belongs to a CELL,
    a (symbol, timeframe) pair, which is how configs/alerts.json is keyed and
    was the one thing the rule choice did not follow.

    Only strategies in sim.strategies.BASELINES can be served: the bridge
    rejects the rest, and `tl_breakout` needs a trendline/regime table it does
    not build live. A bad name comes back as an `error` payload, which is
    reported rather than silently falling through to the default -- a cell that
    quietly ran a different rule than its config says is worse than one that
    does not run.
    """
    args = {'symbol': symbol, 'tf': tf}
    if strategy:
        args['strategy'] = strategy
    q = urllib.parse.urlencode(args)
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


def md_escape(text):
    """Neutralise Telegram Markdown in text WE DID NOT WRITE.

    The rule's `instruction` is quoted verbatim, and it contains tags like
    `channel_exit` and `breakout_up`. A single underscore opens italics in
    Telegram's legacy Markdown, so one tag made the message's delimiters odd and
    the API answered 400 "can't parse entities" -- every EXIT alert failed this
    way for a day while the task reported success, because a buy body happens to
    contain no underscore and an exit body always does.

    Only DYNAMIC text is escaped. The bold symbol and the italic bar stamp are
    formatting this file chose and must survive.
    """
    for ch in ('_', '*', '`', '['):
        text = text.replace(ch, chr(92) + ch)
    return text


def compose(sig, grade=None):
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
    # On the header line rather than a row of its own: this is a property of
    # the CELL, not another price, and the message was deliberately trimmed
    # to the numbers you act on.
    mark = '' if grade == 'validated' else GRADE_MARK.get(grade, UNGRADED)

    if act == 'exit':
        bits = ['%s *%s*  EXIT%s' % (icon, name, mark)]
        if sig.get('instruction'):
            bits.append(md_escape(sig['instruction']))
        return '\n'.join(bits + [_bar_line(sig)])

    d = int(sig.get('digits') or 2)
    px = lambda v: ('%.*f' % (d, v)) if isinstance(v, (int, float)) else str(v)

    bits = ['*%s*  %s%s' % (name, act.upper(), mark)]
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
            yield sym, tf, w.get('grade'), w.get('strategy')


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
            print('    [%s] %-10s %-4s %-10s %-14s %s'
                  % ('x' if on else ' ', w.get('symbol'), w.get('tf'),
                     w.get('grade') or 'UNGRADED',
                     w.get('strategy') or '(tf default)', w.get('note') or ''))
        if not any_on:
            print('  NOTHING ENABLED -- every cell is switched off')
        return 0

    _secrets.load()
    token = os.environ.get('TELEGRAM_BOT_TOKEN')
    chat = os.environ.get('TELEGRAM_CHAT_ID')

    state = load_state()
    fired = failed = 0
    for sym, tf, grade, strat in cells(cfg):
        try:
            sig = poll(sym, tf, strat)
        except (urllib.error.URLError, OSError, ValueError) as err:
            # One unreachable cell must not stop the rest: a 4h signal is worth
            # more than a tidy exit.
            print('%s %s: bridge failed (%s)' % (sym, tf, err), file=sys.stderr)
            continue
        if sig.get('error'):
            # Reported, never swallowed. See poll(): a cell that silently ran a
            # different rule than its config names is worse than one that did
            # not run at all.
            print('%s %s: %s' % (sym, tf, sig['error']), file=sys.stderr)
            failed += 1
            continue
        act = (sig.get('action') or '').lower()
        if act not in cfg['alert_on']:
            print('%s %s: %s (%s)' % (sym, tf, act, sig.get('state')))
            continue
        key = '%s|%s|%s|%s' % (sym, tf, sig.get('bar_time'), act)
        if key in state:
            print('%s %s: %s already sent' % (sym, tf, act))
            continue
        text = compose(sig, grade)
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
            failed += 1
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
        journal(sig, key)
        print('sent: %s' % key)

    # `fired` no longer gates the save -- each send writes its own -- so it is
    # only worth printing, and a run that sent nothing says so rather than
    # ending in silence that reads the same as a crash.
    print('%d sent' % fired if fired else 'nothing to send')
    # A FAILED SEND MUST REACH THE SCHEDULER. This returned 0 no matter what,
    # so Task Scheduler showed LastTaskResult 0 for a full day while every exit
    # alert was being rejected by Telegram. "It ran fine" and "it delivered
    # nothing" looked identical from outside, which is the worst property an
    # alerting job can have.
    if failed:
        print('%d send(s) FAILED — not recorded, will retry next run' % failed,
              file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
