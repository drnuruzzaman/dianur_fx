#!/usr/bin/env python
"""
scalper.py — a rule-based scalping signal generator with a TP ladder.

    python tools/scalper.py --live                     # the ticket right now
    python tools/scalper.py --backtest --from 2026-01-01
    python tools/scalper.py --backtest --mode fade --tf 5m

WHAT IT PRODUCES. A tradeable ticket in the shape a signal room posts:

    XAUUSD.a 5m   BUY STOP 4392.72
      SL  4389.14
      TP1 4395.74   TP2 4397.74   TP3 4401.64

THE RULE, stated so it can be argued with rather than tuned in the dark:

    STRUCTURE   swing high / low = the highest high and lowest low of the last
                `--swing` closed bars, the level a scalper would draw.
    TREND       EMA(fast) against EMA(slow) on the execution frame.
    BREAK MODE  trend up   -> BUY STOP a touch above the swing high
                trend down -> SELL STOP a touch below the swing low
                Joining the move. This is what a momentum scalper does.
    FADE MODE   trend up   -> BUY LIMIT at the swing low
                trend down -> SELL LIMIT at the swing high
                Buying the pullback. This is what the panel that prompted the
                build appeared to be doing on half its signals.
    STOP        `--stop-atr` x ATR(14) from the entry.
    TARGETS     0.9R / 1.5R / 2.4R -- deliberately the ratios read off that
                panel, TP1 UNDER 1R included, so the comparison is like for like.
    EXPIRY      a pending order unfilled after `--expire` bars is cancelled.

WHY IT IS A TICKET GENERATOR AND NOT A sim.Strategy. The engine in sim/ fills at
the next bar's open and carries ONE stop and ONE target; it has no pending
orders and no ladder, and bending it to take them would put a second fill model
next to the one every measured result in this project already rests on. Instead
this emits the same JSONL that tools/score_external.py reads -- the scorer built
to grade somebody else's signals -- so our scalper is graded by exactly the
machinery, and exactly the standard, applied to theirs.

THE BACKTEST RESOLVES ON 1m BARS, not on the signal frame. A 5m bar that
contains both the stop and TP1 cannot say which came first; the 1m bars inside
it usually can. Where even 1m cannot separate them the trade is scored as the
LOSS and counted as `ambiguous`, because the alternative is a backtest that
quietly resolves its own ties in its favour.
"""
import argparse
import io
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import numpy as np                                              # noqa: E402
import pandas as pd                                             # noqa: E402

OUT = os.path.join(ROOT, 'data', 'scalper_signals.jsonl')

#: which ladder rung the backtest exits on, set from --exit-tp. A cell so the
#: resolver can read it without threading another argument through every call.
EXIT_TP = [1]
JOURNAL = os.path.join(ROOT, 'data', 'scalper_journal.jsonl')
NOTIFY_STATE = os.path.join(ROOT, 'data', 'scalper_notified.json')

#: pre-registered expectation per cell, read from configs/alerts.json so the
#: message and the forward test can never quote different numbers.
EXPECTED = {}

#: The cells a MESSAGE may be sent for. Journalling and notifying are different
#: questions and used to be one: `--registered` looped every ENABLED cell and
#: both recorded and announced it, so AUDUSD 15m went out to Telegram carrying
#: "expected -0.0499 R per fill" -- a signal the measurement says not to take,
#: formatted exactly like one it does.
#:
#: Everything enabled is still JOURNALLED. That is not an oversight: the 5m
#: cells are controls, registered to lose, and a control whose outcomes are
#: never recorded cannot do its job of proving the scoring can still fail.
TRADEABLE = set()


#: The trend-age gate, read from configs/alerts.json `scalper.quality`.
#: {} until load_registry() has run, and an empty dict means NO GATE -- a
#: config that fails to parse must not silently start filtering signals.
QUALITY = {}


def age_gate(t):
    """Should this ticket be silenced for being a stale trend? (go, why)

    THE GATE IS ON THE MESSAGE, NOT ON THE RULE. `sim/strategies/rayo.py` still
    proposes the ticket and `--journal` still records it, so the cell keeps
    being scored on everything it would have taken. Only the Telegram message is
    withheld. That matters twice over: every expected_net_r in the registry was
    measured UNGATED, and the gate is not a per-cell improvement anyway -- it
    makes each gold cell individually worse and earns its place by
    de-correlating cells in a one-position queue.

    Measured on that queue at stop 5.0, ten tradeable cells: +20.7% -> +23.7%
    CAGR, P(losing year) 22% -> 16%, drawdown unchanged.
    """
    if not QUALITY.get('enforce'):
        return True, None
    cap = QUALITY.get('max_age')
    if cap is None or t.get('age') is None:
        return True, None
    if t['age'] <= cap:
        return True, None
    return False, ('trend age %d bars > %d -- stale trend, journalled not sent'
                   % (t['age'], cap))


def load_registry(path=None):
    """EXPECTED and TRADEABLE from configs/alerts.json. Returns the watch list.

    Read for EVERY run, not only `--registered`. A single-cell `--live --notify`
    used to leave both empty, so it quoted no expectation and answered "may I
    message this?" with silence -- which the old code read as yes.
    """
    cfg = path or os.path.join(ROOT, 'configs', 'alerts.json')
    try:
        with io.open(cfg, encoding='utf-8') as fh:
            watch = (json.load(fh).get('scalper') or {}).get('watch') or []
    except (OSError, ValueError):
        return []
    QUALITY.clear()
    QUALITY.update((json.load(io.open(cfg, encoding='utf-8')).get('scalper')
                    or {}).get('quality') or {})
    for c in watch:
        key = (c.get('symbol'), c.get('tf'))
        if c.get('expected_net_r') is not None:
            EXPECTED[key] = c['expected_net_r']
        if c.get('tradeable'):
            TRADEABLE.add(key)
    return watch

def _spec_digits(symbol, tf, default=2):
    """The instrument's decimal places, for formatting a price as a price."""
    try:
        from sim.instruments import spec
        return int(spec(symbol, tf).get('digits', default))
    except Exception:                       # noqa: BLE001 -- display must not fail
        return default


# THE RULE LIVES IN sim/strategies/rayo.py, not here. This file is the harness
# -- fills, the ladder race, expiry, costs -- and a copy of the rule beside a
# copy of the scorer is how the two quietly stop describing the same thing.
from sim.strategies.rayo import TPS, tickets, breakeven_r         # noqa: E402


def resolve(tk, M, expire_ms, horizon_ms, be_r=None):
    """Fill on 1m bars, then race the stop against the ladder. Ties go to the loss.

    NUMPY AND A BOUNDED WINDOW. The first version filtered the whole 1m frame
    per ticket -- three million rows times thousands of tickets -- and did not
    finish in ten minutes. searchsorted for the start, then a slice no longer
    than `horizon_ms`; a scalp that has not resolved in a day is `open` and
    saying so is more honest than following it for two years anyway.
    """
    ms, H, L, C = M
    side = 1 if tk['side'] == 'buy' else -1
    entry, sl, tps = tk['entry'], tk['sl'], tk['tp']
    risk = abs(entry - sl)
    t0 = tk['ms']
    i0 = int(np.searchsorted(ms, t0, side='left'))
    i1 = int(np.searchsorted(ms, t0 + horizon_ms, side='right'))
    if i0 >= len(ms) or i1 <= i0:
        return None

    # ---- 1. the fill, inside the expiry window ----
    iexp = int(np.searchsorted(ms, t0 + expire_ms, side='right'))
    fs, fe = i0, min(iexp, i1)
    touch = np.flatnonzero((L[fs:fe] <= entry) & (H[fs:fe] >= entry))
    if not touch.size:
        return {'outcome': 'expired', 'r': None, 'end_ms': int(ms[min(fe, len(ms)-1)])}
    f = fs + int(touch[0])
    # FILL TIME, REPORTED. Overnight financing is charged per night
    # HELD, and the nights between the ticket being posted and the order
    # actually filling are not held. tools/scalp_portfolio.py needs the
    # difference; every other caller ignores the extra key.
    fill_ms = int(ms[f])

    # ---- 2. the race ----
    hs, he = f, i1
    tgt = tps[min(max(EXIT_TP[0], 1), len(tps)) - 1]
    if side > 0:
        sl_hit = np.flatnonzero(L[hs:he] <= sl)
        tp_hit = np.flatnonzero(H[hs:he] >= tgt)
    else:
        sl_hit = np.flatnonzero(H[hs:he] >= sl)
        tp_hit = np.flatnonzero(L[hs:he] <= tgt)
    i_sl = hs + int(sl_hit[0]) if sl_hit.size else None
    i_tp = hs + int(tp_hit[0]) if tp_hit.size else None

    # ---- 2b. BREAK-EVEN (be_r, in R; see sim/strategies/rayo.py BREAKEVEN) --
    # Once the favourable excursion reaches be_r, the stop is the ENTRY from
    # the NEXT minute on: the minute that triggers it cannot also be stopped by
    # it, because nothing inside a 1m bar says which came first. A trade that
    # hits its original stop or TP1 before the trigger is untouched.
    be_ms = None
    if be_r:
        fav = (H[hs:he] - entry) if side > 0 else (entry - L[hs:he])
        trig = np.flatnonzero(fav >= be_r * risk)
        i_tr = hs + int(trig[0]) if trig.size else None
        first_end = min(x for x in (i_sl, i_tp, he) if x is not None)
        if i_tr is not None and i_tr < first_end:
            be_ms = int(ms[i_tr])
            b0 = i_tr + 1
            be_hit = (np.flatnonzero(L[b0:he] <= entry) if side > 0
                      else np.flatnonzero(H[b0:he] >= entry))
            i_be = b0 + int(be_hit[0]) if be_hit.size else None
            if i_be is not None and (i_tp is None or i_be < i_tp):
                return {'outcome': 'be', 'r': 0.0, 'ambiguous': False,
                        'fill_ms': fill_ms, 'end_ms': int(ms[i_be]), 'be_ms': be_ms}
            i_sl = None                    # the original stop can no longer be hit

    if i_sl is not None and i_tp is not None and i_sl == i_tp:
        return {'outcome': 'sl', 'r': -1.0, 'ambiguous': True,
                'fill_ms': fill_ms, 'end_ms': int(ms[i_sl])}
    if i_sl is not None and (i_tp is None or i_sl < i_tp):
        return {'outcome': 'sl', 'r': -1.0, 'ambiguous': False,
                'fill_ms': fill_ms, 'end_ms': int(ms[i_sl])}
    if i_tp is not None:
        out = {'outcome': 'tp%d' % EXIT_TP[0],
               'r': round(abs(tgt - entry) / risk, 4),
               'ambiguous': False, 'fill_ms': fill_ms, 'end_ms': int(ms[i_tp])}
        if be_ms is not None:
            out['be_ms'] = be_ms
        return out
    out = {'outcome': 'open', 'r': round((C[he - 1] - entry) * side / risk, 4),
           'ambiguous': False, 'fill_ms': fill_ms, 'end_ms': int(ms[he - 1])}
    if be_ms is not None:
        out['be_ms'] = be_ms
    return out


def _safe_print(text):
    """Print text that may contain emoji on a cp1252 console.

    The message carries the same coloured circles signal_alert.py posts, and a
    Windows console encodes to cp1252 -- so a dry run died on the first emoji.
    Telegram gets UTF-8 bytes either way; this only protects the local echo.
    tools/_run_quiet.py already opens its log with errors='replace', so the
    scheduled path was never affected.
    """
    try:
        print(text)
    except UnicodeEncodeError:
        enc = sys.stdout.encoding or 'ascii'
        print(text.encode(enc, 'replace').decode(enc, 'replace'))


def _notify_state():
    try:
        with io.open(NOTIFY_STATE, encoding='utf-8') as fh:
            return json.load(fh)
    except Exception:                                       # noqa: BLE001
        return {}


def _notify_save(st):
    try:
        os.makedirs(os.path.dirname(NOTIFY_STATE), exist_ok=True)
        tmp = NOTIFY_STATE + '.tmp'
        with io.open(tmp, 'w', encoding='utf-8') as fh:
            json.dump(st, fh, indent=1)
        os.replace(tmp, NOTIFY_STATE)
    except OSError as exc:
        print('  ! could not save notify state: %s' % exc, file=sys.stderr)


def should_notify(key, t, st):
    """Has the ticket changed enough to be worth a message?

    NOT ONCE PER BAR. The rule reposts a pending order on every bar, so four
    cells on 30m and 1h would push roughly six messages an hour that mostly say
    the same thing, and a feed like that gets muted -- at which point the real
    signal is missed too.

    A message goes out when the ticket is MATERIALLY different: no previous one,
    the side flipped, or the entry moved by more than half the risk. Half the
    risk is the threshold because a level that has drifted less than that is the
    same trade at a slightly different price, and re-placing the order for it
    would cost more in spread than the drift is worth.
    """
    prev = st.get(key)
    if not prev:
        return True, 'first ticket'
    if prev.get('side') != t['side']:
        return True, 'side flipped %s -> %s' % (prev.get('side'), t['side'])
    moved = abs(float(t['entry']) - float(prev.get('entry', 0)))
    if moved > 0.5 * float(t['risk']):
        return True, 'entry moved %.2f (> half the %.2f risk)' % (moved, t['risk'])

    # THE STOP IS PART OF THE TICKET, AND COMPARING ONLY THE ENTRY MISSED IT.
    # The entry is `swing +/- 0.10 ATR` and does not depend on the stop width,
    # so when the rule moved from 4.0 to 5.0 ATR on 2026-09-14 every SL and all
    # three targets moved about 25% -- and this function called those tickets
    # "unchanged" and sent nothing. A reader holding an order placed from an
    # older message would have kept a stop the rule no longer asks for, and
    # would never have been told. A parameter change is exactly when the alert
    # matters most.
    prev_sl = prev.get('sl')
    if prev_sl is None:
        return True, 'no stop recorded for the last message -- re-announcing'
    sl_moved = abs(float(t['sl']) - float(prev_sl))
    if sl_moved > 0.25 * float(t['risk']):
        return True, ('stop moved %.5g (> a quarter of the %.5g risk)'
                      % (sl_moved, t['risk']))
    return False, 'unchanged'


SCORED = os.path.join(ROOT, 'data', 'scalper_scored.jsonl')


def _jsonl(path):
    out = []
    if not os.path.exists(path):
        return out
    with io.open(path, encoding='utf-8') as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    pass
    return out


def resting(bars, tf_ms, journal, scored, now_ms, exit_tp=1):
    """The cell's state under FIXED 12: ('pending', row, bars_left),
    ('open', row, fill_ms) or ('flat', None, None).

    THE TWIN OF js/chart/rayorule.js run() IN LEDGER MODE, so the Telegram
    message, the right-rail panel, the TP bands and the Strategy Replay all
    name the same order. A journalled ticket is taken when the cell is free,
    rests with its own levels for `expire_bars` bars counted from its own bar,
    fills when a bar trades through the entry, then races its stop against
    TP1 (a tie is the loss); the next ticket is taken only after that. A ticket
    tools/score_scalper.py has resolved fills and ends on the bars it named.

    `bars` are the bridge's bars for the cell INCLUDING the forming one (a fill
    can happen there); only CLOSED bars post a ticket. `journal` and `scored`
    are this cell's rows at this stop and mode.

    Re-issuing the order every bar measured worse on all 8 gold frames
    (tools/rayo_reissue_eval.py, logs/rayo_reissue_eval.txt).
    """
    if not bars:
        return ('flat', None, None)
    by_ms = {int(j['ms']): j for j in journal if j.get('ms') is not None}
    sc_by_id = {r.get('id'): r for r in scored}
    rows = {j.get('id'): j for j in journal}
    # Inside the scored span only a ticket the scorer TOOK is taken; past it,
    # any journalled ticket, under the same cursor (as rayorule.js).
    scored_until = max((int(r.get('ms', 0)) for r in scored), default=float('-inf'))
    k = max(1, min(3, int(exit_tp or 1))) - 1
    first = int(bars[0]['t'])

    pos, order, free_from = None, None, float('-inf')
    # WHAT WAS LIVE BEFORE THE WINDOW. The bridge serves a few hundred bars;
    # the ledger says what the cell was doing when they begin.
    prior = sorted((r for r in scored if int(r.get('ms', 0)) < first),
                   key=lambda r: r['ms'])
    if prior:
        last = prior[-1]
        if last.get('outcome') == 'open' and last.get('id') in rows:
            pos = {'row': rows[last['id']], 'sc': last}
        elif last.get('end_ms'):
            free_from = int(last['end_ms'])

    for i, b in enumerate(bars):
        t0 = int(b['t'])
        closed = t0 + tf_ms <= now_ms
        if pos is None and order is not None:
            sc = order['sc']
            gone = ((sc.get('outcome') == 'expired' and t0 + tf_ms > int(sc.get('end_ms') or 0))
                    if sc else i >= order['exp_i'])
            if gone:
                order = None
                free_from = max(free_from, t0)
        for pas in (0, 1):
            freed = free_from
            if pos is None and order is None and t0 >= free_from and closed:
                j = by_ms.get(t0)
                if j is not None and (j.get('id') in sc_by_id or t0 > scored_until):
                    order = {'row': j, 'exp_i': i + int(j.get('expire_bars') or 12),
                             'sc': sc_by_id.get(j.get('id'))}
            if order is not None and pos is None:
                sc, e = order['sc'], float(order['row']['entry'])
                if sc:
                    fm = sc.get('fill_ms')
                    fills = (sc.get('outcome') != 'expired' and fm is not None
                             and t0 <= int(fm) < t0 + tf_ms)
                else:
                    fills = float(b['l']) <= e <= float(b['h'])
                if fills:
                    pos = {'row': order['row'], 'sc': sc, 'fill_ms': t0}
                    order = None
            if pos is not None:
                row, sc = pos['row'], pos['sc']
                long = row['side'] == 'buy'
                entry = float(row['entry'])
                sl, tgt = float(row['sl']), float(row['tp'][k])
                # BREAK-EVEN (row['breakeven_r']): once a bar's favourable
                # excursion reaches the trigger, the stop is the entry from the
                # NEXT bar -- the bar-level twin of resolve()'s next-minute rule.
                if pos.get('be'):
                    sl = entry
                hit_sl = float(b['l']) <= sl if long else float(b['h']) >= sl
                hit_tp = float(b['h']) >= tgt if long else float(b['l']) <= tgt
                ber = row.get('breakeven_r')
                if ber and not pos.get('be'):
                    fav = (float(b['h']) - entry) if long else (entry - float(b['l']))
                    if fav >= float(ber) * abs(entry - float(row['sl'])) and not hit_sl:
                        pos['be'] = True
                        pos['be_ms'] = t0
                if (sc and sc.get('be_ms') is not None and not pos.get('be')
                        and int(sc['be_ms']) < t0 + tf_ms):
                    pos['be'], pos['be_ms'] = True, int(sc['be_ms'])
                if sc and sc.get('outcome') != 'open':
                    em = sc.get('end_ms')
                    ends = em is not None and t0 <= int(em) < t0 + tf_ms
                    hit_sl = ends and sc.get('outcome') == 'sl'
                    hit_tp = ends and sc.get('outcome') != 'sl'
                if hit_sl or hit_tp:
                    em = sc.get('end_ms') if sc else None
                    free_from = t0 if (em is not None and int(em) == t0) else t0 + 1
                    pos = None
            if pas or free_from != t0 or freed == t0:
                break

    if pos is not None:
        return ('open', pos['row'], {
            'fill_ms': (pos['sc'] or {}).get('fill_ms') or pos.get('fill_ms'),
            'be': bool(pos.get('be')), 'be_ms': pos.get('be_ms')})
    if order is not None:
        return ('pending', order['row'], max(0, order['exp_i'] - (len(bars) - 1)))
    return ('flat', None, None)


def compose_scalper(symbol, tf, t, expected=None):
    """The ticket, in the same shape signal_alert.py posts."""
    pretty = symbol.replace('.a', '').replace('XAUUSD', 'XAU/USD')                    .replace('USDJPY', 'USD/JPY')
    d = 2 if 'XAU' in symbol.upper() else 3
    f = '%%.%df' % d
    out = ['*%s %s*  %s %s  ·  rayo scalper'
           % (pretty, tf, t['side'].upper(), t['order'].upper())]
    out.append('🟢   Entry   ' + f % t['entry'])
    for i, tp in enumerate(t['tp'], start=1):
        out.append('🔵   TP%d     ' % i + f % tp)
    out.append('🔴   SL      ' + f % t['sl'])
    left = t.get('bars_left', t.get('expire_bars', 12))
    out.append('_pending: fills only if price trades through the entry within '
               '%d bar%s -- levels fixed, not re-issued_' % (left, '' if left == 1 else 's'))
    # IN THE TEXT AS WELL AS ON THE IMAGE, and the duplication is deliberate:
    # the chart render is non-fatal, so a ticket can go out text-only, and that
    # is exactly the message that must not be the one without a warning.
    from tools.chartshot import RISK_TEXT
    out.append('_%s_' % RISK_TEXT)
    # NO EXPECTATION IN THE MESSAGE, by request. It read as noise on a ticket
    # -- "expected -0.0499 R per fill (median of four sub-eras)" is a sentence
    # about a nine-year backtest, not about this trade, and it was appearing on
    # signals the reader could do nothing with.
    #
    # THE GUARD IT USED TO PROVIDE IS NOW STRUCTURAL, which is why removing it
    # is safe: a message is only composed for a cell marked `tradeable`, so the
    # negative-expectation cells that made this line necessary no longer
    # produce a message at all. `expected` stays in the signature, unused, so
    # the omission reads as a decision rather than an oversight.
    return chr(10).join(out)


def do_live(args):
    """One cell: fetch bars, print the ticket, journal it if asked.

    A FUNCTION so `--registered` can loop cells IN THIS PROCESS. It used to
    spawn a subprocess per cell, which broke logging: tools/_run_quiet.py
    redirects this process's stdout to logs/scalper.log and a child gets the
    OS handles instead, so every scheduled run wrote an empty log and a
    failure would have been silent.
    """
    q = urllib.parse.urlencode({'symbol': args.symbol, 'tf': args.tf,
                                'count': 400, 'months': 0})
    with urllib.request.urlopen('%s/bars?%s' % (args.base, q), timeout=120) as r:
        rows = json.load(r).get('bars') or []
    all_rows = list(rows)                  # the forming bar included, for fills
    df = pd.DataFrame(rows)
    df['ts'] = pd.to_datetime(df['t'], unit='ms')
    df = df.set_index('ts')[['o', 'h', 'l', 'c']].rename(
        columns={'o': 'open', 'h': 'high', 'l': 'low', 'c': 'close'})
    df = df.iloc[:-1]                      # the forming bar decides nothing
    modes = ['break', 'fade'] if args.mode == 'both' else [args.mode]

    # ---------------------------------------------------------------- #
    # THE FORWARD RECORD.                                              #
    # ---------------------------------------------------------------- #
    # ONE LINE PER BAR, deduped on the bar -- not one per poll. A 30m cell
    # polled every five minutes proposes the same ticket six times, and six
    # copies of one idea is the error that made the first backtest look
    # plausible. The RAW record is kept complete on purpose and the
    # one-live-order-at-a-time discipline is applied at SCORING time
    # (`score_external.py --serial`), so the journal can never be accused of
    # having quietly dropped the signals that went badly.
    seen = set()
    if args.journal and os.path.exists(JOURNAL):
        with io.open(JOURNAL, encoding='utf-8') as fh:
            for line in fh:
                line = line.strip()
                if line:
                    d = json.loads(line)
                    seen.add('%s|%s|%s|%s' % (d.get('symbol'), d.get('tf'),
                                              d.get('mode'), d.get('t')))

    for m in modes:
        tk = tickets(df, m, args.swing, args.fast, args.slow, args.stop_atr,
                     parse_session(args.session))
        if not tk:
            print('%s: no ticket' % m)
            continue
        t = tk[-1]
        # %.2f IS A GOLD FORMAT. It printed EURUSD entries as "1.09", which
        # on a 5-digit instrument is not a price. The digits come from the
        # instrument spec, the same place the chart and the message do.
        _f = '%%.%df' % _spec_digits(args.symbol, args.tf)
        print(('\n%s %s   %s %s ' + _f + '        [%s]')
              % (args.symbol, args.tf, t['side'].upper(),
                 t['order'].upper(), t['entry'], m))
        print(('  SL  ' + _f) % t['sl'])
        print(('  TP1 ' + _f + '   TP2 ' + _f + '   TP3 ' + _f) % tuple(t['tp']))
        print('  bar %s   expires in %d bars' % (t['t'], args.expire))
        print('  YOU place this order. This tool cannot and does not.')

        if args.journal:
            key = '%s|%s|%s|%s' % (args.symbol, args.tf, m, t['t'])
            if key in seen:
                print('  (already journalled)')
            else:
                # A UNIQUE ID. `t['id']` is the index of the ticket within its
                # generation run, so all four cells journalled the same '347' and
                # no signal could be referred to afterwards.
                rec = dict(t, id='%s-%s-%s-%s' % (args.symbol.replace('.', ''),
                                                  args.tf, m, t['t'][:16]),
                           symbol=args.symbol, tf=args.tf,
                           expire_bars=args.expire, exit_tp=args.exit_tp,
                           stop_atr=args.stop_atr,
                           breakeven_r=breakeven_r(args.tf),
                           logged_at=datetime.now(timezone.utc).isoformat())
                os.makedirs(os.path.dirname(JOURNAL), exist_ok=True)
                with io.open(JOURNAL, 'a', encoding='utf-8') as fh:
                    fh.write(json.dumps(rec) + chr(10))
                seen.add(key)
                print('  -> journalled to %s' % os.path.relpath(JOURNAL, ROOT))
        # ---------------------------------------------------------------- #
        # THE MESSAGE: FIXED 12, NOT THE LATEST TICKET.                    #
        # ---------------------------------------------------------------- #
        # A message goes out ONCE per ticket, when it becomes the order the
        # cell has RESTING under the live scorer's rule -- taken when the cell
        # is free, left at its own levels for 12 bars. While it rests, fills,
        # or its trade runs, later tickets are not announced. This replaced
        # `should_notify` (a message whenever the latest ticket moved by half
        # the risk), which was the re-issue rule in message form; re-issue
        # measured worse on all 8 gold frames (logs/rayo_reissue_eval.txt).
        # The right-rail panel and TP bands show the same order.
        if args.notify:
            frame_min = {'1m': 1, '3m': 3, '5m': 5, '15m': 15, '30m': 30, '1h': 60,
                         '2h': 120, '4h': 240, '1d': 1440}.get(args.tf)
            mine = lambda r: (r.get('symbol') == args.symbol and r.get('tf') == args.tf
                              and (r.get('mode') or 'break') == m
                              and r.get('stop_atr') == args.stop_atr)
            jrows = [r for r in _jsonl(JOURNAL) if mine(r)]
            if not any(int(r.get('ms', -1)) == int(t['ms']) for r in jrows):
                # not journalled this run (--journal off): the fresh ticket
                # still counts as posted on its bar
                jrows.append(dict(t, id='%s-%s-%s-%s' % (args.symbol.replace('.', ''),
                                                         args.tf, m, t['t'][:16]),
                                  expire_bars=args.expire))
            srows = [r for r in _jsonl(SCORED) if mine(r)]
            now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
            state, rt, extra = (resting(all_rows, frame_min * 60000, jrows, srows,
                                        now_ms, args.exit_tp)
                                if frame_min else ('flat', None, None))
            nkey = '%s|%s|%s' % (args.symbol, args.tf, m)
            st = _notify_state()
            if (state == 'open' and extra and extra.get('be')
                    and ((args.symbol, args.tf) in TRADEABLE or args.notify_untradeable)
                    and (st.get(nkey + '|be') or {}).get('id') != rt.get('id')):
                # MOVE THE STOP TO ENTRY -- once per ticket. The bridge is
                # read-only, so this is an instruction to the reader; the rule,
                # the scorer and the chart already count the stop as moved.
                _f = '%%.%df' % _spec_digits(args.symbol, args.tf)
                pretty = (args.symbol.replace('.a', '').replace('XAUUSD', 'XAU/USD')
                          .replace('USDJPY', 'USD/JPY'))
                text = chr(10).join([
                    '*%s %s*  %s  ·  rayo scalper',
                    '🟡   MOVE SL TO ENTRY  ' + _f,
                    '_the trade reached +%sR -- stop to break-even; TP1 unchanged_'])
                text = text % (pretty, args.tf, rt['side'].upper(), float(rt['entry']),
                               ('%g' % float(rt.get('breakeven_r') or 0)))
                if args.dry_run:
                    print('  WOULD SEND (break-even, ticket %s):' % rt.get('id'))
                    _safe_print(text)
                else:
                    try:
                        from tools import notify
                        res = notify.deliver('scalper', text)
                        print('  -> break-even sent to %d destination(s)' % res['sent'])
                        if res['sent']:
                            st[nkey + '|be'] = {'id': rt.get('id'),
                                                'at': datetime.now(timezone.utc).isoformat()}
                            _notify_save(st)
                    except Exception as exc:                # noqa: BLE001
                        print('  ! break-even notify failed: %s' % exc, file=sys.stderr)
            elif state != 'pending':
                print('  (no message: %s)' % (
                    ('a trade is open from ticket %s%s' % (rt.get('id'),
                     ', stop at break-even' if extra and extra.get('be') else ''))
                    if state == 'open' else 'no ticket resting'))
            elif (args.symbol, args.tf) not in TRADEABLE and not args.notify_untradeable:
                print('  (no message: %s %s is journalled, not traded -- '
                      'expected %s R per fill)'
                      % (args.symbol, args.tf,
                         ('%+.4f' % EXPECTED[(args.symbol, args.tf)])
                         if (args.symbol, args.tf) in EXPECTED else 'un-measured'))
            elif not age_gate(rt)[0]:
                print('  (no message: %s)' % age_gate(rt)[1])
            elif (st.get(nkey) or {}).get('id') == rt.get('id'):
                print('  (no message: ticket %s already announced, %d bar(s) left)'
                      % (rt.get('id'), extra))
            else:
                why = 'resting ticket %s, %d bar(s) left' % (rt.get('id'), extra)
                exp = EXPECTED.get((args.symbol, args.tf))
                text = compose_scalper(args.symbol, args.tf,
                                       dict(rt, expire_bars=args.expire,
                                            bars_left=extra), exp)

                # THE CHART OF THE FRAME THE SIGNAL IS ON, drawn from the very
                # bars the rule just read -- not re-fetched, so the picture
                # cannot disagree with the prices in the caption.
                #
                # NEVER FATAL. A message with no chart is worth far more than
                # no message, so a rendering failure is reported and stepped
                # over.
                png = None
                if not args.no_chart:
                    try:
                        from tools.chartshot import render
                        digits = 2 if 'XAU' in args.symbol.upper() else (
                            3 if 'JPY' in args.symbol.upper() else 5)
                        png = render(df, dict(rt), args.symbol, args.tf,
                                     digits=digits, expected=exp)
                    except Exception as exc:                # noqa: BLE001
                        print('  ! chart not drawn (%s) -- sending text only'
                              % exc, file=sys.stderr)

                if args.dry_run:
                    print('  WOULD SEND (%s)%s:'
                          % (why, ' +chart %dkB' % (len(png) // 1024)
                             if png else ' (no chart)'))
                    _safe_print(text)
                else:
                    try:
                        # ROUTED: destinations live in configs/alerts.json and
                        # a Scalper ticket goes only to the ones that asked for
                        # `scalper` (tools/notify.py).
                        from tools import notify
                        res = notify.deliver('scalper', text, image=png)
                        if res['sent']:
                            print('  -> %d destination(s) (%s)'
                                  % (res['sent'], why))
                        else:
                            print('  ! nothing accepted the ticket',
                                  file=sys.stderr)
                    except Exception as exc:                # noqa: BLE001
                        print('  ! notify failed: %s' % exc, file=sys.stderr)
                # RECORDED AFTER THE ATTEMPT so a failed send is retried next
                # poll, and NOT AFTER A DRY RUN, so a preview cannot cancel the
                # real message.
                if not args.dry_run:
                    st[nkey] = {'id': rt.get('id'), 'side': rt['side'],
                                'entry': rt['entry'], 'sl': rt['sl'],
                                'risk': rt['risk'],
                                'at': datetime.now(timezone.utc).isoformat()}
                    _notify_save(st)
    return 0


def parse_session(text):
    """'none' -> None, '7-21' -> (7, 21). Raises rather than silently trading."""
    if not text or text.strip().lower() in ('none', 'off', '24h', ''):
        return None
    try:
        lo, hi = (int(p) for p in text.replace(':', '-').split('-')[:2])
    except ValueError:
        raise SystemExit('--session wants LO-HI on the broker clock, or none')
    if not (0 <= lo < hi <= 24):
        raise SystemExit('--session hours must satisfy 0 <= LO < HI <= 24')
    return (lo, hi)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--symbol', default='XAUUSD.a')
    ap.add_argument('--tf', default='30m')
    ap.add_argument('--mode', default='break', choices=('break', 'fade', 'both'))
    ap.add_argument('--swing', type=int, default=20)
    ap.add_argument('--fast', type=int, default=20)
    ap.add_argument('--slow', type=int, default=50)
    ap.add_argument('--stop-atr', type=float, default=5.0)
    ap.add_argument('--expire', type=int, default=12, help='bars, then cancel')
    # THE SESSION GATE IS OFF BY DEFAULT, matching rayo.DEFAULTS. It is exposed
    # because the +42% R/yr the window measured is only reproducible if this run
    # can be told to put it back: --session 7-21. Any comparison against a
    # pre-2026-09-10 number must pass it, or it compares two different rules.
    ap.add_argument('--session', default='none', metavar='LO-HI',
                    help="broker-clock hours to trade, e.g. 7-21; "
                         "'none' (default) trades around the clock")
    ap.add_argument('--exit-tp', type=int, default=1, choices=(1, 2, 3),
                    help='which ladder rung the backtest exits on')
    ap.add_argument('--from', dest='start', default='2024-01-01')
    ap.add_argument('--to', dest='end', default='2026-12-31')
    ap.add_argument('--live', action='store_true')
    ap.add_argument('--backtest', action='store_true')
    ap.add_argument('--registered', action='store_true',
                    help='run every enabled cell in configs/alerts.json '
                         '`scalper.watch` -- what the scheduled task calls')
    ap.add_argument('--dry-run', action='store_true',
                    help='print the message, send nothing')
    ap.add_argument('--no-chart', action='store_true',
                    help='send the ticket as text only, without the chart image')
    ap.add_argument('--notify-untradeable', action='store_true',
                    help='send messages for journal-only and control cells too. '
                         'For testing the delivery path; not for live use.')
    ap.add_argument('--notify', action='store_true',
                    help='Telegram the ticket when it MATERIALLY changes')
    ap.add_argument('--journal', action='store_true',
                    help='append the live ticket to data/scalper_journal.jsonl')
    ap.add_argument('--base', default='http://127.0.0.1:8765')
    args = ap.parse_args()

    from sim.instruments import load

    # ONE CONFIG, NOT A LIST IN A .cmd FILE. The cells being forward tested live
    # in configs/alerts.json beside the Donchian ones, so registering a cell and
    # scheduling it cannot come apart -- the same reason both boards read that
    # file rather than keeping their own list.
    load_registry()          # EXPECTED and TRADEABLE, whichever mode this is

    if args.registered:
        watch = load_registry()
        cells = [(c['symbol'], c['tf']) for c in watch if c.get('enabled')]
        print('%d enabled cell(s); %d tradeable, the rest journal only'
              % (len(cells), sum(1 for c in cells if c in TRADEABLE)))
        if not cells:
            print('no enabled cells in scalper.watch')
            return 1
        # IN-PROCESS, NOT subprocess. Spawning children looked tidier and broke
        # the one thing the scheduled task has left: tools/_run_quiet.py
        # redirects THIS process's stdout to logs/scalper.log, and a child gets
        # the OS handles, not the redirect -- so every run wrote an empty log
        # and a failure would have been completely silent.
        rc = 0
        for sym, tf in cells:
            args.symbol, args.tf = sym, tf
            rc |= (do_live(args) or 0)
        return rc


    if args.live:
        return do_live(args)

    if not args.backtest:
        ap.error('pass --live or --backtest')

    bars = load(args.symbol, args.tf, args.start, args.end)
    m1 = load(args.symbol, '1m', args.start, args.end)
    m1 = m1.rename(columns={'high': 'h', 'low': 'l', 'close': 'c'})
    m1['ms'] = m1.index.view('int64') // 10 ** 6
    tf_min = {'1m': 1, '5m': 5, '15m': 15, '30m': 30, '1h': 60}[args.tf]
    expire_ms = args.expire * tf_min * 60 * 1000

    horizon_ms = 24 * 3600 * 1000          # unresolved after a day is `open`

    EXIT_TP[0] = args.exit_tp
    modes = ['break', 'fade'] if args.mode == 'both' else [args.mode]
    print('%s %s   swing %d  ema %d/%d  stop %.1f ATR  TP %s  expire %d bars'
          % (args.symbol, args.tf, args.swing, args.fast, args.slow,
             args.stop_atr, '/'.join('%.1fR' % k for k in TPS), args.expire))
    print('%s .. %s   resolved on 1m bars, ties scored as the loss\n'
          % (args.start, args.end))
    # THE COST, PER TRADE, FROM THIS RULE'S OWN RISK. Spread plus 0.02 ATR of
    # slippage each side, over the 1.5 ATR this rule risks -- NOT the 2 ATR the
    # Donchian cells risk, so the floor here is correspondingly higher. A
    # scalper posting six tickets a day pays this six times a day, and printing
    # the gross number without it is the exact flattery this project refuses.
    from sim.instruments import spec as _spec
    _sp = _spec(args.symbol, args.tf)
    _pts = (_sp.get('spread_points_now') or _sp.get('spread_floor_points')
            or (30.0 if 'XAU' in args.symbol.upper() else 10.0))
    spread_px = _sp['point'] * float(_pts)

    print('%-6s %7s %8s %8s %6s %5s %9s %8s %9s %10s'
          % ('mode', 'posted', 'filled', 'expired', 'win', 'amb',
             'gross R', 'cost R', 'NET R', 'total net'))
    print('-' * 88)
    M = (m1['ms'].to_numpy(np.int64), m1['h'].to_numpy(float),
         m1['l'].to_numpy(float), m1['c'].to_numpy(float))
    saved = []
    for m in modes:
        tk = tickets(bars, m, args.swing, args.fast, args.slow, args.stop_atr,
                     parse_session(args.session))
        for t in tk:
            t['symbol'] = args.symbol

        # ONE LIVE ORDER AT A TIME, which is how the thing being imitated works
        # and the only honest way to count. `tickets` proposes a setup on EVERY
        # qualifying bar, so an uptrend that lasts a day proposes the same buy
        # stop a hundred times; scoring all of them counts one idea a hundred
        # times over and turns a single good break into a hundred winners.
        # A new ticket is posted only once the previous one has resolved.
        res, busy = [], -1
        for t in tk:
            if t['ms'] < busy:
                continue
            r = resolve(t, M, expire_ms, horizon_ms)
            if not r:
                continue
            res.append((t, r))
            busy = r.get('end_ms', t['ms'])
        exp = sum(1 for _, r in res if r['outcome'] == 'expired')
        cl = [(t, r) for t, r in res if r['outcome'] in ('sl', 'tp%d' % args.exit_tp)]
        rs = [r['r'] for _, r in cl]
        costs = [(spread_px + 2 * 0.02 * t['atr']) / t['risk'] for t, _ in cl]
        nets = [x - c for x, c in zip(rs, costs)]
        amb = sum(1 for _, r in cl if r.get('ambiguous'))
        w = sum(1 for x in rs if x > 0)
        print('%-6s %7d %8d %8d %5s%% %5d %+9.4f %8.4f %+9.4f %+10.1f'
              % (m, len(tk), len(cl), exp,
                 '%.0f' % (100.0 * w / len(rs)) if rs else '-', amb,
                 (sum(rs) / len(rs)) if rs else 0.0,
                 (sum(costs) / len(costs)) if costs else 0.0,
                 (sum(nets) / len(nets)) if nets else 0.0, sum(nets)))
        saved += [dict(t, result=r) for t, r in res]

    with io.open(OUT, 'w', encoding='utf-8') as fh:
        for x in saved:
            fh.write(json.dumps(x) + '\n')
    print('\nwrote %s' % os.path.relpath(OUT, ROOT))
    print('gross R excludes costs. cost R is the spread (%.2f) plus 0.02 ATR a '
          'side, over' % spread_px)
    print('this rule %.1f ATR risk. NET R is what the account would have seen.'
          % args.stop_atr)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
