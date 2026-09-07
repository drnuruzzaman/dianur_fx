#!/usr/bin/env python
"""
signal_now.py — what the rule says to do, now. For MANUAL execution.

    python tools/signal_now.py                     # ask once, print it
    python tools/signal_now.py --once --notify      # for the scheduler
    python tools/signal_now.py --watch 300          # poll every 5 minutes

READ ONLY, and structurally so. This file contains no order_send, and the
bridge exposes no endpoint that could place one -- its only trade-adjacent
calls are `/positions` and `/orders`, which READ, and `order_calc_margin`,
which is a calculator. You place the order yourself in MetaTrader.

WHY IT READS YOUR LIVE POSITION. The rule's answer depends on what is held: a
channel break is an entry only when flat, and the channel exit only applies when
not. If this tool assumed flat it would tell you to BUY into a long you already
have, every bar, for the whole length of a trend. So it reads /positions and
reports which side it found. When it cannot tell, it says so and refuses to
guess rather than printing a confident wrong instruction.

WHAT IT WILL NOT DO. It will not tell you a take-profit, because the validated
rule does not have one -- see sim/signal.py. It quotes R multiples as
orientation and labels them as not part of the rule.

DEDUPLICATION. A signal is emitted once per bar. Polling every 15 minutes on a
4h chart means ~16 looks per bar, and a tool that shouted on all of them would
train you to ignore it. State lives in runs/paper/signal_state_*.json.
"""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sim.fx import FX
from sim.instruments import account_currency, spec
from sim.signal import evaluate
from sim.strategies import BASELINES

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'runs', 'paper')


def get(base, path, timeout=20):
    with urllib.request.urlopen('%s%s' % (base, path), timeout=timeout) as fh:
        return json.load(fh)


def now_iso():
    return datetime.now().astimezone().isoformat(timespec='seconds')


def toast(title, body):
    """Windows notification. Best effort -- a failed toast must never take the
    signal down with it, so the text has already been printed by the time this
    is called."""
    try:
        ps = (
            '[Windows.UI.Notifications.ToastNotificationManager, '
            'Windows.UI.Notifications, ContentType=WindowsRuntime] > $null;'
            '$t=[Windows.UI.Notifications.ToastNotificationManager]::'
            'GetTemplateContent(2);'
            '$x=$t.GetElementsByTagName("text");'
            '$x.Item(0).AppendChild($t.CreateTextNode(%s)) > $null;'
            '$x.Item(1).AppendChild($t.CreateTextNode(%s)) > $null;'
            '[Windows.UI.Notifications.ToastNotificationManager]::'
            'CreateToastNotifier("DiaNurFx").Show('
            '[Windows.UI.Notifications.ToastNotification]::new($t))'
            % (json.dumps(title), json.dumps(body))
        )
        subprocess.run(['powershell', '-NoProfile', '-Command', ps],
                       capture_output=True, timeout=25)
    except Exception as exc:                              # noqa: BLE001
        print('  (notification failed: %s)' % exc)


def live_position(base, symbol):
    """(side, note, legs). side is +1/-1/0, or None for UNKNOWN.

    Never assume flat on an error: assuming flat makes the tool emit an ENTRY,
    which is the expensive direction to be wrong in.
    """
    try:
        rows = get(base, '/positions').get('positions') or []
    except Exception as exc:                              # noqa: BLE001
        return None, 'could not read /positions: %s' % exc, 0
    mine = [p for p in rows if (p.get('symbol') or '') == symbol]
    if not mine:
        return 0, 'flat (no open position on %s)' % symbol, 0
    net = 0.0
    for p in mine:
        # The bridge serves side as the STRING 'buy'/'sell'. An earlier version
        # of this read an integer `type` field, which does not exist, so
        # `.get('type', 0)` defaulted every leg to BUY -- a short would have
        # been reported as a long and the rule asked the wrong question.
        s = str(p.get('side') or '').lower()
        if s not in ('buy', 'sell'):
            return None, 'unrecognised side %r on ticket %s' % (
                p.get('side'), p.get('ticket')), len(mine)
        vol = float(p.get('volume') or 0)
        net += vol if s == 'buy' else -vol
    if len(mine) > 1:
        legs = ', '.join('%s %.2f' % (str(p.get('side')).upper(),
                                      float(p.get('volume') or 0))
                         for p in mine[:6])
        note = '%d positions (%s%s), net %+.2f lots' % (
            len(mine), legs, ', ...' if len(mine) > 6 else '', net)
    else:
        note = '%s %.2f lots from %s' % (
            'LONG' if net > 0 else 'SHORT', abs(net),
            mine[0].get('price_open'))
    if abs(net) < 1e-9:
        return 0, 'hedged flat (%s)' % note, len(mine)
    return (1 if net > 0 else -1), note, len(mine)


def load_state(path):
    if os.path.exists(path):
        try:
            return json.load(open(path, encoding='utf-8'))
        except Exception:                                 # noqa: BLE001
            pass
    return {'last_emitted_bar': None, 'last_action': None}


def save_json(path, doc):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.part'
    with open(tmp, 'w', encoding='utf-8') as fh:
        json.dump(doc, fh, indent=1, default=str)
    os.replace(tmp, path)


def append(path, doc):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'a', encoding='utf-8') as fh:
        fh.write(json.dumps(doc, default=str) + '\n')


def cycle(args, state, jpath, verbose=True):
    health = get(args.base, '/health')
    if not health.get('connected'):
        print('  %s  bridge not connected: %s'
              % (now_iso(), health.get('error') or '?'))
        return None

    payload = get(args.base, '/bars?symbol=%s&tf=%s&count=600'
                  % (args.symbol, args.tf))
    rows = payload.get('bars') or []
    if len(rows) < 120:
        print('  %s  only %d bars; need 120+' % (now_iso(), len(rows)))
        return None

    df = pd.DataFrame(rows)
    df['ts'] = pd.to_datetime(df['t'], unit='ms')
    df = df.set_index('ts')[['o', 'h', 'l', 'c']].rename(
        columns={'o': 'open', 'h': 'high', 'l': 'low', 'c': 'close'})
    # THE LAST BAR IS STILL FORMING. Deciding on it is look-ahead: the close
    # moves, and the "signal" can appear and vanish within the same bar.
    closed = df.iloc[:-1]

    if args.position is None:
        side, note, legs = live_position(args.base, args.symbol)
        if side is None:
            print('  %s  position UNKNOWN — %s' % (now_iso(), note))
            print('  refusing to guess: the rule\'s answer depends on what is '
                  'held.')
            return None
        if legs > 1:
            # The rule's state model is ONE position with ONE 2-ATR stop. An
            # account holding several legs at different prices is not that, and
            # netting them into 'long' or 'short' is a lossy guess about a state
            # the rule never created. Reported rather than hidden: while this
            # says 'short', entry signals are suppressed and the channel exit
            # shown belongs to a position the rule did not open.
            print('  %s  WARNING: %d separate %s legs are open.'
                  % (now_iso(), legs, args.symbol))
            print('  The rule assumes ONE position with ONE stop. Netting these'
                  ' into a single')
            print('  side is an approximation, and entry signals stay suppressed'
                  ' while it holds.')
            print('  Use --position flat to ask what the rule would say from '
                  'scratch.')
    else:
        side = {'flat': 0, 'long': 1, 'short': -1}[args.position]
        note = 'OVERRIDDEN by --position %s (broker not consulted)' % args.position

    sp = spec(args.symbol, args.tf)
    offset = int(health.get('time_offset_ms') or 0)
    last_t = closed.index[-1]
    try:
        equity = float((get(args.base, '/account') or {}).get('equity') or 0)
    except Exception:                                     # noqa: BLE001
        equity = 0.0

    strat = BASELINES[args.strategy]()
    sig = evaluate(
        closed, strat, sp,
        equity=equity or None, risk_pct=args.risk,
        fx=FX.build(account_currency()),
        position_side=side,
        bar_time_server=last_t + pd.Timedelta(milliseconds=offset),
        symbol=args.symbol, tf=args.tf,
    )
    if sig is None:
        print('  %s  not enough bars for %s' % (now_iso(), args.strategy))
        return None

    fresh = state.get('last_emitted_bar') != sig.bar_time
    if verbose and (fresh or sig.action == 'hold' or args.always):
        print('\n%s   broker position: %s' % (now_iso(), note))
        print(sig.instruction())
        if sig.action != 'hold':
            print('  bar        %s UTC / %s server'
                  % (sig.bar_time[-8:], (sig.bar_time_server or ' ' * 8)[-8:]))
            print('  YOU place this order. This tool cannot and does not.')

    if sig.action != 'hold' and fresh:
        rec = dict(vars(sig))
        rec['at'] = now_iso()
        rec['broker_position'] = note
        rec['equity'] = equity
        append(jpath, rec)
        if args.notify:
            toast('%s %s — %s' % (sig.symbol, sig.tf, sig.action.upper()),
                  'stop %.2f   size %s lots   no TP (channel exit)'
                  % (sig.stop or 0.0,
                     'n/a' if sig.lots is None else '%.2f' % sig.lots))
        state['last_emitted_bar'] = sig.bar_time
        state['last_action'] = sig.action

    # A HEARTBEAT ON EVERY CYCLE, including a hold.
    #
    # Under a scheduler this is the only evidence the poll happened at all. The
    # journal deliberately records actionable signals only -- a 15-minute poll on
    # a 4h chart would otherwise write ~16 identical HOLD rows per bar and bury
    # the entries. But that left a task exiting 0 with nothing on disk, which is
    # indistinguishable from a task that silently stopped firing. So the state
    # file carries the last poll: if `checked_at` is hours stale, the schedule is
    # broken, and that is worth knowing before a missed entry tells you.
    state['checked_at'] = now_iso()
    state['checked_bar'] = sig.bar_time
    state['checked_action'] = sig.action
    state['checked_state'] = sig.state
    state['broker_position'] = note
    save_json(os.path.join(OUT, args.state), state)
    return sig


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--base', default='http://127.0.0.1:8765')
    ap.add_argument('--symbol', default='XAUUSD.a')
    ap.add_argument('--tf', default='4h')
    ap.add_argument('--strategy', default='donchian', choices=sorted(BASELINES))
    ap.add_argument('--risk', type=float, default=0.5, help='%% of equity')
    ap.add_argument('--once', action='store_true')
    ap.add_argument('--watch', type=int, default=0, help='seconds between polls')
    ap.add_argument('--position', choices=('flat', 'long', 'short'),
                    default=None,
                    help='override what is held instead of reading /positions; '
                         'use flat to ask what the rule says from scratch')
    ap.add_argument('--notify', action='store_true',
                    help='Windows notification on a fresh entry/exit')
    ap.add_argument('--always', action='store_true',
                    help='print even when the bar was already reported')
    args = ap.parse_args()

    tag = '%s_%s_%s' % (args.symbol.replace('.', ''), args.tf, args.strategy)
    args.state = 'signal_state_%s.json' % tag
    jpath = os.path.join(OUT, 'signals_%s.jsonl' % tag)
    state = load_state(os.path.join(OUT, args.state))

    print('SIGNALS for MANUAL execution — %s %s %s, risk %.2f%%'
          % (args.symbol, args.tf, args.strategy, args.risk))
    print('this tool cannot place an order; the bridge exposes none.')
    print('-> %s' % os.path.relpath(jpath, ROOT))

    while True:
        try:
            cycle(args, state, jpath)
        except (urllib.error.URLError, TimeoutError) as exc:
            print('  %s  bridge unreachable: %s' % (now_iso(), exc))
        if args.once or not args.watch:
            break
        time.sleep(args.watch)


if __name__ == '__main__':
    main()
