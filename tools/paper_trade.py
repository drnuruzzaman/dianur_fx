#!/usr/bin/env python
"""
paper_trade.py — run the validated rule forward on live data. READ ONLY.

    python tools/paper_trade.py --once            # for a scheduler
    python tools/paper_trade.py --watch 300       # poll every 5 minutes

WHAT THIS IS FOR. Every cost in the gold 4h Donchian result is now measured
against ticks rather than assumed, and the rule has passed four gates in and out
of sample. What has never been tested is the gap between the SIMULATOR and
REALITY: whether the bars the bridge serves live match the bars on disk, whether
a signal fires when the backtest says it should, and whether the fill the model
predicts is available when the bar actually opens. A backtest cannot answer any
of those about itself.

IT CANNOT TRADE. There is no order_send in this file, no call to any endpoint
that could place one, and the bridge does not expose one. It writes a journal and
nothing else. That is deliberate: the whole project's safety property is that
nothing in it can move money, and a paper-trading tool is exactly where that
would erode.

WHAT IT RECORDS, and why each field is there rather than derivable later:

  the DECISION      bar time, close, the stop, the state before and after. The
                    bar the decision was based on, so it can be re-derived
                    against the historical file later and any divergence found.
                    Recorded in BOTH clocks -- see below.
  the LIVE QUOTE    bid, ask and spread at the moment of the decision. This is
                    the only thing that cannot be reconstructed afterwards, and
                    it is what makes a fill comparison possible at all.
  the PREDICTED FILL what the simulator would have charged. Written BEFORE the
                    bar opens, so it is a forecast on the record rather than a
                    number fitted to the outcome.
  the ACTUAL OPEN   filled in on a later poll, once the bar exists.

THREE CLOCKS, and every row carries the two that matter.

    at              LOCAL time, with its offset. For a person reading the log.
    bar_time        UTC. What /bars serves: the bridge subtracts the +3h server
                    offset from MetaTrader's timestamps.
    bar_time_server BROKER SERVER time. What data/bars/ stores, deliberately --
                    the offset moves with the broker's DST, so correcting twenty
                    years of history with one constant would be wrong for half
                    of it.

Both bar clocks are written because they are the join key to two different
records, and confusing them is not hypothetical: comparing the live bars to the
downloaded files on timestamp gave ZERO overlap out of 579 identical bars, purely
because one grid was 01/05/09/13/17/21 and the other 00/04/08/12/16/20.

The paper position is tracked so the sequence is real -- a rule that is flat
cannot take an entry -- but the P&L is secondary. The divergence columns are the
deliverable.
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sim.core import BarView, LONG, SHORT, FLAT
from sim.instruments import spec
from sim.strategies import BASELINES

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAPER = os.path.join(ROOT, 'runs', 'paper')
SLIPPAGE_ATR = 0.02


def get(base, path, timeout=20):
    with urllib.request.urlopen('%s%s' % (base, path), timeout=timeout) as fh:
        return json.load(fh)


def now_iso():
    return datetime.now().astimezone().isoformat(timespec='seconds')


def load_state(path):
    if os.path.exists(path):
        try:
            return json.load(open(path, encoding='utf-8'))
        except Exception:                                 # noqa: BLE001
            pass
    return {'position': None, 'pending': None, 'last_bar_t': None, 'trades': []}


def save_json(path, doc):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.part'
    with open(tmp, 'w', encoding='utf-8') as fh:
        json.dump(doc, fh, indent=1, default=str)
    os.replace(tmp, path)          # never leave a half-written state file


def journal(path, event):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'a', encoding='utf-8') as fh:
        fh.write(json.dumps(event, default=str) + '\n')


def bars_frame(rows):
    df = pd.DataFrame(rows)
    df['ts'] = pd.to_datetime(df['t'], unit='ms')
    return df.set_index('ts')[['o', 'h', 'l', 'c']].rename(
        columns={'o': 'open', 'h': 'high', 'l': 'low', 'c': 'close'})


def poll(base, symbol, tf, strategy, state, jpath, verbose=True):
    """One decision cycle. Returns the event it wrote, or None."""
    health = get(base, '/health')
    if not health.get('connected'):
        ev = {'at': now_iso(), 'kind': 'bridge_down',
              'error': health.get('error') or 'not connected'}
        journal(jpath, ev)
        return ev

    payload = get(base, '/bars?symbol=%s&tf=%s&count=600' % (symbol, tf))
    rows = payload.get('bars') or []
    if len(rows) < 100:
        ev = {'at': now_iso(), 'kind': 'too_few_bars', 'bars': len(rows)}
        journal(jpath, ev)
        return ev
    quote = get(base, '/quotes?symbols=%s' % symbol)['quotes'].get(symbol) or {}

    df = bars_frame(rows)
    # THE LAST BAR IS STILL FORMING. The rule decides on a CLOSE, so the newest
    # bar is dropped -- acting on it would be reading a price that is not final,
    # which is the live equivalent of look-ahead.
    closed = df.iloc[:-1]
    last_t = closed.index[-1]

    if state.get('last_bar_t') == str(last_t):
        if verbose:
            print('  %s  no new closed bar (last %s)' % (now_iso(), last_t))
        return None

    strat = BASELINES[strategy]()
    series = {k: np.asarray(v, dtype=float) for k, v in strat.prepare(closed).items()}
    arrays = (closed['open'].to_numpy(float), closed['high'].to_numpy(float),
              closed['low'].to_numpy(float), closed['close'].to_numpy(float),
              np.zeros(len(closed)), np.zeros(len(closed)),
              closed.index.to_numpy())
    i = len(closed) - 1

    pos = state.get('position')
    held = None
    if pos:
        class _Held:
            side = pos['side']
            entry_i = 0
        held = _Held()
    intent = strat.on_bar(BarView(arrays, series, i), held)

    sp = spec(symbol, tf)
    a = float(series['atr'][i])
    bar = closed.iloc[-1]
    ev = {
        'at': now_iso(),
        'kind': 'decision',
        'symbol': symbol, 'tf': tf, 'strategy': strategy,
        # both clocks: bar_time is UTC (what /bars serves), bar_time_server
        # is what data/bars/ stores. One is the join key to the live record,
        # the other to the historical files.
        'bar_time': str(last_t),
        'bar_time_server': str(last_t + pd.Timedelta(
            milliseconds=int(health.get('time_offset_ms') or 0))),
        'server_offset_ms': int(health.get('time_offset_ms') or 0),
        'bar_open': float(bar.open), 'bar_high': float(bar.high),
        'bar_low': float(bar.low), 'bar_close': float(bar.close),
        'atr': round(a, 6),
        'state_before': 'flat' if not pos else ('long' if pos['side'] > 0 else 'short'),
        'live_bid': quote.get('bid'), 'live_ask': quote.get('ask'),
        'live_spread_pts': (round((quote['ask'] - quote['bid']) / sp['point'], 1)
                            if quote.get('ask') and quote.get('bid') else None),
        'action': 'hold' if intent is None else
                  ('exit' if intent.side == FLAT else
                   'buy' if intent.side == LONG else 'sell'),
        'stop': None, 'predicted_fill': None, 'actual_open': None,
        'fill_error_pts': None,
    }

    if intent is not None and intent.side != FLAT:
        side = 1 if intent.side == LONG else -1
        spread = float(sp.get('spread_points_now') or 0) * sp['point']
        slip = SLIPPAGE_ATR * a
        # what the simulator WOULD charge at the next open. Written now, before
        # that bar exists, so it is a forecast on the record.
        ev['stop'] = round(float(intent.stop), sp['digits'])
        ev['predicted_fill_formula'] = 'next_open + %.4f (spread) + %.4f (slip)' % (
            spread, slip) if side > 0 else 'next_open - %.4f (slip)' % slip
        ev['predicted_offset'] = round(spread + slip if side > 0 else -slip, 6)
        state['pending'] = {'side': side, 'stop': ev['stop'],
                            'signal_bar': str(last_t),
                            'predicted_offset': ev['predicted_offset']}
    elif intent is not None and intent.side == FLAT:
        state['pending'] = {'side': 0, 'signal_bar': str(last_t),
                            'predicted_offset': 0.0}

    # settle a pending order against the bar that has now closed
    prev = state.get('pending_prev')
    if prev and prev.get('signal_bar') != str(last_t):
        open_px = float(bar.open)
        pred = open_px + prev.get('predicted_offset', 0.0)
        ev['actual_open'] = open_px
        ev['predicted_fill'] = round(pred, sp['digits'])
        if prev['side'] != 0:
            state['position'] = {'side': prev['side'], 'entry': pred,
                                 'stop': prev['stop'], 'since': str(last_t)}
        else:
            state['position'] = None

    state['pending_prev'] = state.get('pending')
    state['last_bar_t'] = str(last_t)
    journal(jpath, ev)
    if verbose:
        print('  %s  bar %s UTC / %s server  %s%s'
              % (ev['at'], ev['bar_time'][-8:], ev['bar_time_server'][-8:],
                 ev['action'].upper(),
                 '' if ev['stop'] is None else '  stop %s' % ev['stop']))
    return ev


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--base', default='http://127.0.0.1:8765')
    ap.add_argument('--symbol', default='XAUUSD.a')
    ap.add_argument('--tf', default='4h')
    ap.add_argument('--strategy', default='donchian', choices=sorted(BASELINES))
    ap.add_argument('--once', action='store_true', help='one cycle, for a scheduler')
    ap.add_argument('--watch', type=int, default=0, help='seconds between polls')
    args = ap.parse_args()

    spath = os.path.join(PAPER, 'state_%s_%s_%s.json'
                         % (args.symbol.replace('.', ''), args.tf, args.strategy))
    jpath = os.path.join(PAPER, 'journal_%s_%s_%s.jsonl'
                         % (args.symbol.replace('.', ''), args.tf, args.strategy))
    state = load_state(spath)

    print('PAPER, read only. %s %s %s  ->  %s'
          % (args.symbol, args.tf, args.strategy, os.path.relpath(jpath, ROOT)))
    print('this tool cannot place an order; the bridge exposes none.\n')

    interval = args.watch or 0
    while True:
        try:
            poll(args.base, args.symbol, args.tf, args.strategy, state, jpath)
            save_json(spath, state)
        except (urllib.error.URLError, TimeoutError) as exc:
            print('  %s  bridge unreachable: %s' % (now_iso(), exc))
            journal(jpath, {'at': now_iso(), 'kind': 'unreachable',
                            'error': str(exc)})
        if args.once or not interval:
            break
        time.sleep(interval)

    pos = state.get('position')
    print('\nposition: %s' % ('flat' if not pos else
                              '%s from %s, stop %s'
                              % ('LONG' if pos['side'] > 0 else 'SHORT',
                                 pos['since'], pos['stop'])))
    print('pending : %s' % (state.get('pending') or 'none'))
    print('state   : %s' % os.path.relpath(spath, ROOT))


if __name__ == '__main__':
    main()
