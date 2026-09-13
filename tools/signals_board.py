#!/usr/bin/env python
"""
signals_board.py — every watched cell on one screen, for MANUAL execution.

    python tools/signals_board.py                 # the enabled cells
    python tools/signals_board.py --all           # including disabled ones
    python tools/signals_board.py --entries       # only cells asking for a trade

WHY THIS AND NOT signal_now.py. That file is the real instrument: it reads your
live position, renders the full instruction, and refuses to guess when it cannot
tell what is held. This is a BOARD, not a replacement — eight cells at a glance
so you can see which one is asking for something, then run signal_now.py on that
cell for the instruction you would actually trade from.

THE CELL LIST COMES FROM configs/alerts.json, deliberately. That file is what
the scheduled alerter reads, so a cell shown here is a cell that will alert, and
a cell that alerts is shown here. Keeping a second list in this file is how the
board and the alerts would quietly disagree about what is being tested.

IT SHOWS THE GRADE ON EVERY ROW, for the same reason the Telegram message does:
`below` means the cell was measured under its own friction floor, and a row that
looks identical to a validated one while being nothing of the kind is how a
forward test quietly becomes a way to lose money on cells you already knew about.
"""
import argparse
import io
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ALERTS = os.path.join(ROOT, 'configs', 'alerts.json')

#: the app's timeframe ladder, mirroring TF in js/util.js
LADDER = ['1m', '5m', '15m', '30m', '1h', '4h', '1d', '1w']


def cells(include_disabled=False):
    with io.open(ALERTS, encoding='utf-8') as fh:
        d = json.load(fh)
    out = []
    for c in d.get('signals', {}).get('watch', []):
        if not include_disabled and not c.get('enabled'):
            continue
        out.append((c.get('symbol'), c.get('tf'), c.get('grade') or '-'))
    # Symbol, then UP the timeframe ladder. alerts.json is in registration
    # order, which is a history of the file rather than a way to read a board,
    # and a plain sort puts 15m before 1h before 4h before 5m. Same order as the
    # Signal Board tab -- see the `rung` comment in js/ui/signalboard.js.
    return sorted(out, key=lambda r: (r[0], LADDER.index(r[1])
                                      if r[1] in LADDER else len(LADDER)))


def ask(base, sym, tf, timeout, position='flat'):
    """One cell, asked on a FLAT basis by default.

    /signal reads the live position off the terminal unless told otherwise, and
    for an order ticket that is right -- a breakout is an entry only when flat.
    For a BOARD it is wrong: eleven cells share two account positions, so every
    gold row reported the one `long` sitting in MetaTrader and no gold cell
    could ever say BUY while any gold was open. The board reports the market;
    what to do about it is the reader's call. `--held` restores the old
    behaviour for when you want the instruction rather than the survey.
    """
    q = urllib.parse.urlencode(
        dict({'symbol': sym, 'tf': tf}, **({'position': position} if position else {})))
    try:
        with urllib.request.urlopen('%s/signal?%s' % (base, q), timeout=timeout) as r:
            return json.load(r)
    except Exception as exc:                                    # noqa: BLE001
        return {'error': str(exc)[:60]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--base', default='http://127.0.0.1:8765')
    ap.add_argument('--all', action='store_true', help='include disabled cells')
    ap.add_argument('--entries', action='store_true',
                    help='only rows whose action is a trade')
    ap.add_argument('--timeout', type=int, default=60)
    ap.add_argument('--held', action='store_true',
                    help='ask against the LIVE position instead of flat')
    args = ap.parse_args()

    rows = cells(args.all)
    if not rows:
        print('no cells in %s' % ALERTS)
        return 1

    hdr = '%-10s %-4s %-10s %-9s %-6s %11s %11s %11s'
    print(hdr % ('symbol', 'tf', 'grade', 'N', 'signal',
                 'bar close', 'buy above', 'sell below'))
    print('-' * 80)
    asking = []
    for sym, tf, grade in rows:
        d = ask(args.base, sym, tf, args.timeout,
                position=None if args.held else 'flat')
        if d.get('error'):
            print('%-10s %-4s %-10s  %s' % (sym, tf, grade, d['error']))
            continue
        act = d.get('action') or '-'
        if args.entries and act not in ('buy', 'sell', 'enter'):
            continue
        p = d.get('params') or {}
        n = '%s/%s' % (p.get('entry'), p.get('exit'))
        num = lambda v: ('%.3f' % v) if isinstance(v, (int, float)) else '-'
        print(hdr % (sym, tf, grade, n,
                     act if act in ('buy', 'sell') else '-',
                     num(d.get('bar_close')), num(d.get('upper')),
                     num(d.get('lower'))))
        if act in ('buy', 'sell'):
            asking.append((sym, tf, act))
        sys.stdout.flush()

    if asking:
        print('\nasking for action:')
        for sym, tf, act in asking:
            print('  %-10s %-4s %-6s ->  python tools/signal_now.py --symbol %s --tf %s'
                  % (sym, tf, act, sym, tf))
    else:
        print('\nno cell is signalling: every one is inside its channel.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
