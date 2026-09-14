#!/usr/bin/env python
"""
scalp_portfolio.py — the Rayo Scalper on ONE ACCOUNT, one position at a time.

    python tools/scalp_portfolio.py --grid
    python tools/scalp_portfolio.py --cells tradeable --age-max 20 --risk 0.02

WHY THIS IS NOT tools/scalper.py --backtest. That tool scores ONE CELL and
applies its busy cursor WITHIN that cell, which answers "is this cell's rule any
good". It cannot answer the question that decides what to switch on, because a
real account has one balance and holds one position: when four cells all want a
position at 09:05, three of them do not get one. Adding up per-cell results
counts trades the account could never have taken, and the more cells you enable
the more it overcounts -- so the per-cell table is most wrong exactly where a
decision about enabling cells is being made.

THE QUEUE IS THE MECHANISM, and it is why a signal-quality score can pay without
predicting anything. Every cell posts a pending order on nearly every bar. The
account takes the first one that arrives while it is flat and is then busy until
that trade resolves, so what a filter changes is not only WHICH trades are taken
but WHICH ARE CROWDED OUT. A filter that keeps the same average trade can still
help the account, by declining a mediocre ticket that would have blocked a good
one an hour later. That is the measured mechanism behind `age` -- see
"TREND AGE IS A SIGNAL-QUALITY SCORE" in sim/strategies/rayo.py.

RESOLUTION IS LAZY, AND THAT IS NOT AN OPTIMISATION. Resolving every proposed
ticket and then filtering would be both slower and WRONG-HEADED: the queue
decides what exists. Tickets are walked in time order and only the ones the
account actually accepts are ever resolved. A pass takes seconds once the bars
are in memory, so arms are cheap to compare and there is no cached intermediate
to go stale.

COSTS ARE THE SAME ONES tools/scalper.py CHARGES -- spread plus 0.02 ATR of
slippage each side, over this rule's own risk -- so a single-cell number from
there and a portfolio number from here are quoted on the same basis.

SWAP IS CHARGED PER NIGHT HELD, from the FILL, not from the ticket. In R:

    points x point / risk_price

Lot size and contract size cancel, which is the whole reason this is expressible
in R at all. Getting that wrong once reported +5.19 R of free money on USDJPY.
Wednesday is charged triple (the broker's `swap_rollover_3days_weekday`), and
the day boundary is the BROKER's midnight -- the stored bars are broker time, so
the bar stamps already are that clock.

WHAT IT REPORTS, AND WHY NOT JUST THE RETURN. CAGR alone ranks a strategy that
triples and then halves above one that grows steadily, and on an account this
size the drawdown is what decides whether the final number is reachable by a
human rather than abandoned in month four. P(losing year) comes from a
CALENDAR-MONTH BLOCK bootstrap -- whole months resampled, not individual trades,
because trades inside one trend are not independent and a per-trade bootstrap
would report a confidence interval several times too narrow.
"""

import argparse
import io
import json
import os
import random
import sys
from collections import defaultdict
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import numpy as np                                              # noqa: E402
import pandas as pd                                             # noqa: E402

from sim.instruments import load, spec                          # noqa: E402
from sim.strategies.rayo import tickets                         # noqa: E402
from tools import scalper as SC                                 # noqa: E402

# 1d AND 1w ARE HERE BECAUSE THE SESSION GATE IS OFF. While it was on, every
# daily bar (stamped broker hour 0) was rejected and the registry recorded 1d
# as 'not measurable'; with the gate off those bars produce tickets, and a
# missing key crashed the registration run rather than skipping the row.
TF_MIN = {'1m': 1, '5m': 5, '15m': 15, '30m': 30, '1h': 60, '4h': 240,
          '1d': 1440, '1w': 10080}
DAY_MS = 24 * 3600 * 1000
N_BOOT = 2000


def registry_expected():
    with io.open(os.path.join(ROOT, 'configs', 'alerts.json'), encoding='utf-8') as fh:
        watch = (json.load(fh).get('scalper') or {}).get('watch') or []
    return {(c['symbol'], c['tf']): (c.get('expected_net_r') or 0.0) for c in watch}


def cells_from_registry(which):
    """The cell list, read from configs/alerts.json rather than typed here.

    `tradeable` is the 10 cells the measurement says are worth real money;
    `enabled` is whatever is switched on right now, which is a fact about the
    config and not about the rule.
    """
    with io.open(os.path.join(ROOT, 'configs', 'alerts.json'), encoding='utf-8') as fh:
        watch = (json.load(fh).get('scalper') or {}).get('watch') or []
    if which == 'tradeable':
        sel = [c for c in watch if c.get('tradeable')]
    elif which == 'enabled':
        sel = [c for c in watch if c.get('enabled')]
    elif which == 'enabled-tradeable':
        sel = [c for c in watch if c.get('enabled') and c.get('tradeable')]
    else:
        want = set()
        for part in which.split(','):
            sym, _, tf = part.strip().partition(':')
            want.add((sym, tf))
        sel = [c for c in watch if (c.get('symbol'), c.get('tf')) in want]
    return [(c['symbol'], c['tf']) for c in sel]


_M1 = {}


def _m1(symbol, start, end):
    key = (symbol, start, end)
    if key not in _M1:
        f = load(symbol, '1m', start, end).rename(
            columns={'high': 'h', 'low': 'l', 'close': 'c'})
        _M1[key] = (f.index.view('int64') // 10 ** 6,
                    f['h'].to_numpy(float), f['l'].to_numpy(float),
                    f['c'].to_numpy(float))
    return _M1[key]


class Cell(object):
    """One symbol/timeframe: its tickets, its 1m bars, its costs and its swap."""

    def __init__(self, symbol, tf, start, end, session, stop_atr, swing,
                 fast, slow, expire, expected=0.0, spread_mult=1.0):
        self.symbol, self.tf = symbol, tf
        # READ FROM THE REGISTRY, NOT MEASURED HERE. `expectation` priority has
        # to rank cells by something knowable BEFORE the trade, and the only
        # such number is the one already registered in configs/alerts.json.
        # Computing it from this run's own outcomes would be hindsight.
        self.expected = expected
        bars = load(symbol, tf, start, end)
        self.tk = tickets(bars, 'break', swing, fast, slow, stop_atr, session)
        for t in self.tk:
            t['symbol'] = symbol
            t['tf'] = tf
        # ONE COPY OF THE 1m FRAME PER SYMBOL, shared by that symbol's cells.
        # Ten cells are five instruments at two resolutions, and loading the
        # minute bars per CELL doubled a 300 MB working set for nothing.
        self.M = _m1(symbol, start, end)
        self.expire_ms = expire * TF_MIN[tf] * 60 * 1000

        sp = spec(symbol, tf)
        pts = (sp.get('spread_points_now') or sp.get('spread_floor_points')
               or (30.0 if 'XAU' in symbol.upper() else 10.0))
        # SPREAD IS THE ONE COST THAT IS NOT FIXED. `spread_points_now` is
        # what the broker quoted on the day it was captured; it widens on
        # news, at the roll, and on a worse account. A cell whose edge does
        # not survive a wider one is not tradeable, it is lucky.
        self.spread_px = sp['point'] * float(pts) * float(spread_mult)
        self.spread_mult = float(spread_mult)
        self.point = sp['point']
        self.swap_long = float(sp.get('swap_long_points') or 0.0)
        self.swap_short = float(sp.get('swap_short_points') or 0.0)
        self.roll_wd = int(sp.get('swap_rollover_3days_weekday', 3))

    def cost_r(self, t):
        return (self.spread_px + 2 * 0.02 * t['atr']) / t['risk']

    def swap_r(self, t, fill_ms, exit_ms):
        """Financing in R. LOT SIZE CANCELS, so this never goes via money."""
        per_night = (self.swap_long if t['side'] == 'buy' else self.swap_short)
        if not per_night:
            return 0.0
        d0 = datetime.fromtimestamp(fill_ms / 1000.0, timezone.utc).date()
        d1 = datetime.fromtimestamp(exit_ms / 1000.0, timezone.utc).date()
        charge = 0.0
        for n in range((d1 - d0).days):
            day = d0 + pd.Timedelta(days=n + 1).to_pytimedelta()
            charge += 3.0 if day.weekday() == self.roll_wd else 1.0
        # NEGATIVE `per_night` IS A CHARGE, so it lowers the result; the sign
        # is the broker's and is not flipped here.
        return charge * per_night * self.point / t['risk']


def choose(batch, priority, rnd):
    """Which of several simultaneous tickets the one free slot goes to.

    THE QUEUE IS SATURATED, so this function, not the entry rule, decides most
    of what the account holds. Every cell posts a pending order on nearly every
    bar; 30m and 1h bars close together on the hour; so at most decision points
    several cells are asking at once and exactly one can be served.

    `arrival` IS THE INCUMBENT AND IT IS ARBITRARY. Sorting by timestamp alone
    leaves simultaneous tickets in whatever order the cells were listed, and a
    stable sort then hands every tie to the same cell for nine years: XAUUSD 30m
    took 1938 of 2218 trades, 87% of the account, purely for being first in
    configs/alerts.json. That is not a property of gold. It is why `random` is
    here -- as a control that measures the size of the artefact, not as a
    proposal.
    """
    if len(batch) == 1 or priority == 'arrival':
        return batch[0]
    if priority == 'random':
        return batch[rnd.randrange(len(batch))]
    if priority == 'age':
        return min(batch, key=lambda x: (x[2].get('age', 0), x[0]))
    if priority == 'expectation':
        return max(batch, key=lambda x: (x[1].expected, -x[2].get('age', 0)))
    if priority == 'slow':
        return max(batch, key=lambda x: (TF_MIN[x[1].tf], -x[2].get('age', 0)))
    raise ValueError(priority)


def run_arm(cells, age_max, horizon_ms=DAY_MS, priority='arrival', seed=7,
            slots=1, one_per_symbol=True):
    """Walk every cell's tickets in ONE time-ordered queue, flat-or-busy.

    TICKETS ARE BATCHED BY TIMESTAMP, not taken one at a time, because the
    account does not choose between a ticket now and a ticket later -- it
    chooses between the ones in front of it. Every ticket sharing the earliest
    free timestamp is a candidate and `choose` picks one; the rest are gone,
    not queued, which is what a pending order that was never placed means.

    Returns the list of taken trades, each with its net R and its clock.
    """
    rnd = random.Random(seed)
    q = []
    for c in cells:
        for t in c.tk:
            q.append((t['ms'], c, t))
    q.sort(key=lambda x: x[0])

    # OPEN POSITIONS, NOT A SINGLE CURSOR. `slots` is the number the account
    # may hold at once. It is the constraint this whole file exists to measure:
    # with one slot, ten cells take the same 2,200 trades as one cell does, so
    # every filter is really choosing WHICH cell owns the account rather than
    # adding anything to it.
    out, open_pos, i = [], [], 0
    while i < len(q):
        now = q[i][0]
        open_pos = [o for o in open_pos if o[0] > now]
        if len(open_pos) >= slots:
            i += 1
            continue
        j = i
        while j < len(q) and q[j][0] == q[i][0]:
            j += 1
        # THE GATE IS APPLIED HERE, AT THE QUEUE, and that is the whole point.
        # Declining a ticket does not only remove its own result -- it leaves
        # the account flat for whatever arrives next.
        batch = [x for x in q[i:j]
                 if age_max is None or x[2].get('age', 0) <= age_max]
        i = j
        # ONE POSITION PER INSTRUMENT, by default. Two slots filled with gold
        # 30m and gold 1h is not diversification -- the two agree on direction
        # most of the time -- it is the same bet at double size, which would
        # make a multi-slot account look good for the wrong reason.
        while batch and len(open_pos) < slots:
            held = {o[1] for o in open_pos}
            cand = [x for x in batch
                    if not (one_per_symbol and x[1].symbol in held)]
            if not cand:
                break
            ms, c, t = choose(cand, priority, rnd)
            batch = [x for x in batch if x[2] is not t]
            r = SC.resolve(t, c.M, c.expire_ms, horizon_ms)
            if not r:
                # A ticket the 1m frame cannot even reach is not a decision the
                # account made; the slot is still free for the next candidate.
                continue
            open_pos.append((r.get('end_ms', ms), c.symbol))
            if r['outcome'] not in ('sl', 'tp1', 'tp2', 'tp3'):
                continue                   # expired or still open: no money moved
            net = r['r'] - c.cost_r(t) + c.swap_r(t, r['fill_ms'], r['end_ms'])
            out.append({'ms': ms, 'end_ms': r['end_ms'], 'symbol': c.symbol,
                        'tf': c.tf, 'side': t['side'], 'age': t.get('age'),
                        'gross': r['r'], 'net': net, 'outcome': r['outcome']})
    out.sort(key=lambda t: t['end_ms'])
    return out


def walk(trades, equity, f):
    """Stake set at ENTRY from the balance then; profit booked at EXIT.

    NOT `bal *= (1 + f*r)` DOWN A SORTED LIST. That is only right when one
    trade closes before the next opens. With more than one slot, positions
    overlap, and compounding them as if they were sequential silently sizes the
    second trade off a balance that already contains the first one's profit --
    money the account did not have when it placed the order.
    """
    ev = []
    for t in trades:
        ev.append((t['ms'], 0, t))
        ev.append((t['end_ms'], 1, t))
    ev.sort(key=lambda x: (x[0], x[1]))
    peak = bal = equity
    dd = 0.0
    stake = {}
    for _, kind, t in ev:
        if kind == 0:
            stake[id(t)] = f * bal
        else:
            bal += stake.pop(id(t), f * bal) * t['net']
            if bal <= 0:
                return 0.0, 1.0
            peak = max(peak, bal)
            dd = max(dd, (peak - bal) / peak)
    return bal, dd


def by_month(trades):
    m = defaultdict(list)
    for t in trades:
        d = datetime.fromtimestamp(t['ms'] / 1000.0, timezone.utc)
        m['%04d-%02d' % (d.year, d.month)].append(t['net'])
    return m


def p_losing_year(trades, f, n=N_BOOT, seed=7):
    """Twelve months resampled with replacement, compounded. Blocks, not trades."""
    months = list(by_month(trades).values())
    if len(months) < 12:
        return None
    rnd = random.Random(seed)
    bad = 0
    for _ in range(n):
        bal = 1.0
        for _ in range(12):
            for r in months[rnd.randrange(len(months))]:
                bal *= (1.0 + f * r)
        bad += (bal < 1.0)
    return 100.0 * bad / n


def summarise(label, trades, equity, f, years):
    if not trades:
        return {'arm': label, 'n': 0}
    final, dd = walk(trades, equity, f)
    cagr = ((final / equity) ** (1.0 / years) - 1.0) * 100.0 if final > 0 else -100.0
    nets = [t['net'] for t in trades]
    return {
        'arm': label, 'n': len(trades),
        'win': 100.0 * sum(1 for t in trades if t['gross'] > 0) / len(trades),
        'mean_r': sum(nets) / len(nets),
        'total_r': sum(nets),
        'r_per_year': sum(nets) / years,
        'final': final, 'cagr': cagr, 'dd': 100.0 * dd,
        'p_lose': p_losing_year(trades, f),
    }


HEAD = ('%-26s %6s %6s %9s %9s %10s %8s %7s %8s'
        % ('arm', 'trades', 'win%', 'mean R', 'R/yr', 'final', 'CAGR%', 'maxDD%', 'P(loss)'))


def line(s):
    if not s.get('n'):
        return '%-26s %6s   -- no trades --' % (s['arm'], 0)
    return ('%-26s %6d %5.1f%% %+9.4f %+9.1f %10.0f %+7.1f %6.1f%% %7s'
            % (s['arm'], s['n'], s['win'], s['mean_r'], s['r_per_year'],
               s['final'], s['cagr'], s['dd'],
               '-' if s['p_lose'] is None else '%.0f%%' % s['p_lose']))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--cells', default='tradeable',
                    help="'tradeable', 'enabled', 'enabled-tradeable', or "
                         "SYM:tf,SYM:tf")
    ap.add_argument('--age-max', default='none',
                    help="max trend age in bars, or 'none'")
    ap.add_argument('--risk', type=float, default=0.02)
    ap.add_argument('--equity', type=float, default=10000.0)
    ap.add_argument('--from', dest='start', default='2017-01-01')
    ap.add_argument('--to', dest='end', default='2026-09-01')
    ap.add_argument('--session', default='none', metavar='LO-HI')
    ap.add_argument('--stop-atr', type=float, default=5.0)
    ap.add_argument('--swing', type=int, default=20)
    ap.add_argument('--fast', type=int, default=20)
    ap.add_argument('--slow', type=int, default=50)
    ap.add_argument('--expire', type=int, default=12)
    ap.add_argument('--exit-tp', type=int, default=1, choices=(1, 2, 3))
    ap.add_argument('--priority', default='arrival',
                    choices=('arrival', 'random', 'age', 'expectation', 'slow'),
                    help='who gets the one free slot when several cells ask at '
                         'once. `arrival` is the incumbent and is arbitrary')
    ap.add_argument('--grid', action='store_true',
                    help='the decision table: cell set x age gate')
    ap.add_argument('--queue-grid', action='store_true',
                    help='the priority table: who gets the slot x age gate')
    ap.add_argument('--slots', type=int, default=1,
                    help='concurrent positions the account may hold')
    ap.add_argument('--stop-sweep', action='store_true',
                    help='where the stop wants to be ON AN ACCOUNT, by sub-era')
    ap.add_argument('--robust', action='store_true',
                    help='PRE-REGISTERED: is 2 slots a property of the queue or '
                         'an artefact of one cell set? cells x tie-break x stop')
    ap.add_argument('--eras', action='store_true',
                    help='net R per year by sub-era, for the slot arms')
    ap.add_argument('--slot-grid', action='store_true',
                    help='slots x age gate, at MATCHED TOTAL RISK')
    args = ap.parse_args()

    SC.EXIT_TP[0] = args.exit_tp
    session = SC.parse_session(args.session)

    years = ((pd.Timestamp(args.end) - pd.Timestamp(args.start)).days / 365.25)

    EXP = registry_expected()

    def build(names):
        out = []
        for sym, tf in names:
            sys.stderr.write('  loading %s %s ...\n' % (sym, tf))
            out.append(Cell(sym, tf, args.start, args.end, session,
                            args.stop_atr, args.swing, args.fast, args.slow,
                            args.expire, EXP.get((sym, tf), 0.0)))
        return out

    if args.grid:
        four = [('XAUUSD.a', '30m'), ('XAUUSD.a', '1h'),
                ('USDJPY.a', '30m'), ('USDJPY.a', '1h')]
        ten = cells_from_registry('tradeable')
        built = {}
        for k in ten:
            sys.stderr.write('  loading %s %s ...\n' % k)
            built[k] = Cell(k[0], k[1], args.start, args.end, session,
                            args.stop_atr, args.swing, args.fast, args.slow,
                            args.expire, EXP.get(k, 0.0))
        print('\nRayo Scalper on one account, one position at a time')
        print('%s .. %s   risk %.1f%% of balance   start %.0f\n'
              % (args.start, args.end, 100 * args.risk, args.equity))
        print(HEAD)
        print('-' * len(HEAD))
        for name, sel in (('4 cells (XAU+JPY)', four), ('10 cells (all tradeable)', ten)):
            cs = [built[k] for k in sel if k in built]
            for gate, glab in ((None, 'no gate'), (20, 'age<=20')):
                print(line(summarise('%s, %s' % (name, glab),
                                     run_arm(cs, gate, priority=args.priority),
                                     args.equity, args.risk, years)))
        return 0

    if args.queue_grid:
        ten = cells_from_registry('tradeable')
        built = []
        for k in ten:
            sys.stderr.write('  loading %s %s ...\n' % k)
            built.append(Cell(k[0], k[1], args.start, args.end, session,
                              args.stop_atr, args.swing, args.fast, args.slow,
                              args.expire, EXP.get(k, 0.0)))
        print('\nWHO GETS THE ONE FREE SLOT -- 10 tradeable cells, one position')
        print('%s .. %s   risk %.1f%% of balance   start %.0f\n'
              % (args.start, args.end, 100 * args.risk, args.equity))
        print(HEAD)
        print('-' * len(HEAD))
        for pr in ('arrival', 'random', 'age', 'expectation', 'slow'):
            for gate, glab in ((None, 'no gate'), (20, 'age<=20')):
                print(line(summarise('%s, %s' % (pr, glab),
                                     run_arm(built, gate, priority=pr),
                                     args.equity, args.risk, years)))
        return 0

    if args.slot_grid:
        ten = cells_from_registry('tradeable')
        built = []
        for k in ten:
            sys.stderr.write('  loading %s %s ...\n' % k)
            built.append(Cell(k[0], k[1], args.start, args.end, session,
                              args.stop_atr, args.swing, args.fast, args.slow,
                              args.expire, EXP.get(k, 0.0)))
        print('\nHOW MANY POSITIONS AT ONCE -- 10 tradeable cells')
        print('MATCHED TOTAL RISK: %0.1f%% split across the slots, so 3 slots '
              'risk %0.2f%% each.' % (100 * args.risk, 100 * args.risk / 3))
        print('Comparing slots at the SAME risk per trade would just be '
              'leverage wearing a hat.\n')
        print('%s .. %s   start %.0f\n' % (args.start, args.end, args.equity))
        print(HEAD)
        print('-' * len(HEAD))
        for n in (1, 2, 3, 4):
            for gate, glab in ((None, 'no gate'), (20, 'age<=20')):
                print(line(summarise('%d slot%s, %s' % (n, '' if n == 1 else 's', glab),
                                     run_arm(built, gate, priority=args.priority,
                                             slots=n),
                                     args.equity, args.risk / n, years)))
        return 0

    if args.stop_sweep:
        # THE COST FRACTION IS (spread + slip) / (stop_atr x ATR), so a wider
        # stop monotonically cuts the charge -- that is settled, and is how this
        # rule went positive at all. What was NEVER swept is where the optimum
        # sits ON AN ACCOUNT rather than per cell: a wider stop also holds the
        # trade longer, which pays more swap and takes fewer trades per year,
        # and the one-position queue turns "fewer trades" into a real cost.
        # 4.0 was chosen on per-cell net R. This asks the portfolio.
        GY = [('XAUUSD.a', '30m'), ('XAUUSD.a', '1h'),
              ('USDJPY.a', '30m'), ('USDJPY.a', '1h')]
        ALL = cells_from_registry('tradeable')
        ERAS = [('2017-19', '2017-01-01', '2019-01-01'),
                ('2019-21', '2019-01-01', '2021-01-01'),
                ('2021-23', '2021-01-01', '2023-01-01'),
                ('2023-26', '2023-01-01', '2026-09-01')]
        print('\nWHERE THE STOP WANTS TO BE, ON AN ACCOUNT (1 slot, no gate)')
        print('risk %0.1f%% of balance; R/yr by sub-era on the right\n'
              % (100 * args.risk))
        print('%-24s %6s %9s %8s %7s %7s   %8s %8s %8s %8s'
              % ('arm', 'trades', 'mean R', 'CAGR', 'maxDD', 'P(los)',
                 *[e[0] for e in ERAS]))
        print('-' * 108)
        for cname, names in (('gold+yen', GY), ('all 10', ALL)):
            for stop in (4.0, 4.5, 5.0, 5.5, 6.0, 7.0):
                sys.stderr.write('  %s stop %.1f ...\n' % (cname, stop))
                cs = [Cell(sym, tf, args.start, args.end, session, stop,
                           args.swing, args.fast, args.slow, args.expire,
                           EXP.get((sym, tf), 0.0)) for sym, tf in names]
                tr = run_arm(cs, None, priority=args.priority, slots=1)
                sm = summarise('x', tr, args.equity, args.risk, years)
                cols = []
                for _, a0, b0 in ERAS:
                    lo = int(pd.Timestamp(a0).value // 10 ** 6)
                    hi = int(pd.Timestamp(b0).value // 10 ** 6)
                    sel = [t for t in tr if lo <= t['ms'] < hi]
                    yrs = (pd.Timestamp(b0) - pd.Timestamp(a0)).days / 365.25
                    cols.append('%+8.1f' % (sum(t['net'] for t in sel) / yrs
                                            if sel else 0.0))
                print('%-24s %6d %+9.4f %+7.1f%% %6.1f%% %6s   %s'
                      % ('%s, stop %.1f' % (cname, stop), sm['n'], sm['mean_r'],
                         sm['cagr'], sm['dd'],
                         '-' if sm['p_lose'] is None else '%.0f%%' % sm['p_lose'],
                         ' '.join(cols)))
            print()
        return 0

    if args.robust:
        # THE BAR WAS WRITTEN DOWN BEFORE THIS RAN. 2 slots at matched total
        # risk must beat 1 slot on CAGR AND not worsen maxDD in every arm:
        # gold/yen alone, EUR/GBP/AUD alone (no part in choosing it), under
        # RANDOM tie-breaking rather than the config-order accident, and at
        # stop 3.0 and 5.0 ATR as well as 4.0. Anything less and the effect
        # belongs to this configuration rather than to the queue.
        GY = [('XAUUSD.a', '30m'), ('XAUUSD.a', '1h'),
              ('USDJPY.a', '30m'), ('USDJPY.a', '1h')]
        MAJ = [c for c in cells_from_registry('tradeable') if c not in GY]
        ALL = cells_from_registry('tradeable')

        def build_at(names, stop):
            out = []
            for sym, tf in names:
                out.append(Cell(sym, tf, args.start, args.end, session, stop,
                                args.swing, args.fast, args.slow, args.expire,
                                EXP.get((sym, tf), 0.0)))
            return out

        print('\nIS TWO SLOTS A PROPERTY OF THE QUEUE, OR OF THIS CELL SET?')
        print('matched total risk %0.1f%%; 2 slots risk half each. '
              'PASS = more CAGR and no more drawdown.\n'
              % (100 * args.risk))
        print('%-34s %9s %9s   %9s %9s   %s'
              % ('arm', '1sl CAGR', '1sl DD', '2sl CAGR', '2sl DD', 'verdict'))
        print('-' * 92)
        rows = []
        for stop in (3.0, 4.0, 5.0):
            for cname, names in (('gold+yen (4)', GY),
                                 ('EUR/GBP/AUD (6)', MAJ),
                                 ('all tradeable (10)', ALL)):
                for pr in (('arrival', 'random') if stop == 4.0 else ('arrival',)):
                    sys.stderr.write('  stop %.1f  %s  %s ...\n' % (stop, cname, pr))
                    cs = build_at(names, stop)
                    a1 = summarise('x', run_arm(cs, None, priority=pr, slots=1),
                                   args.equity, args.risk, years)
                    a2 = summarise('x', run_arm(cs, None, priority=pr, slots=2),
                                   args.equity, args.risk / 2, years)
                    ok = (a2['cagr'] > a1['cagr']) and (a2['dd'] <= a1['dd'])
                    rows.append(ok)
                    print('%-34s %+8.1f%% %8.1f%%   %+8.1f%% %8.1f%%   %s'
                          % ('stop %.1f, %s, %s' % (stop, cname, pr),
                             a1['cagr'], a1['dd'], a2['cagr'], a2['dd'],
                             'PASS' if ok else 'fail'))
        print('\n%d of %d arms pass.' % (sum(rows), len(rows)))
        return 0

    if args.eras:
        # THE ERA BOUNDARY IS 2021-01-01, not 12-31, and these are the four
        # windows every registered cell in this project was graded on. A result
        # that only exists pooled is a result that has not been tested.
        ERAS = [('2017-19', '2017-01-01', '2019-01-01'),
                ('2019-21', '2019-01-01', '2021-01-01'),
                ('2021-23', '2021-01-01', '2023-01-01'),
                ('2023-26', '2023-01-01', '2026-09-01')]
        ten = cells_from_registry('tradeable')
        built = []
        for k in ten:
            sys.stderr.write('  loading %s %s ...\n' % k)
            built.append(Cell(k[0], k[1], args.start, args.end, session,
                              args.stop_atr, args.swing, args.fast, args.slow,
                              args.expire, EXP.get(k, 0.0)))
        ARMS = [('1 slot, no gate', 1, None), ('2 slots, no gate', 2, None),
                ('2 slots, age<=20', 2, 20), ('3 slots, age<=20', 3, 20)]
        runs = {}
        for lab, n, gate in ARMS:
            runs[lab] = (run_arm(built, gate, priority=args.priority, slots=n), n)
        print('\nNET R PER YEAR, BY SUB-ERA -- the account is one run; the '
              'trades are bucketed by entry')
        print('\n%-20s %10s %10s %10s %10s   %10s'
              % ('arm', *[e[0] for e in ERAS], 'worst'))
        print('-' * 78)
        for lab, n, gate in ARMS:
            tr, _ = runs[lab]
            row, vals = [], []
            for _, a0, b0 in ERAS:
                lo = int(pd.Timestamp(a0).value // 10 ** 6)
                hi = int(pd.Timestamp(b0).value // 10 ** 6)
                sel = [t for t in tr if lo <= t['ms'] < hi]
                yrs = (pd.Timestamp(b0) - pd.Timestamp(a0)).days / 365.25
                v = sum(t['net'] for t in sel) / yrs if sel else 0.0
                row.append('%+10.1f' % v)
                vals.append(v)
            print('%-20s %s   %+10.1f' % (lab, ' '.join(row), min(vals)))
        print('\nR per year is quoted at 1R per trade, so it is comparable '
              'across slot counts;')
        print('the money each arm makes divides that R by the slots it is '
              'spread over.')
        return 0

    names = cells_from_registry(args.cells)
    if not names:
        print('no cells matched %r' % args.cells)
        return 1
    age = None if str(args.age_max).lower() in ('none', '', 'off') else int(args.age_max)
    cs = build(names)
    tr = run_arm(cs, age, priority=args.priority, slots=args.slots)
    print('\n%s   %d cell(s)   risk %.1f%%   %s .. %s\n'
          % (args.cells, len(cs), 100 * args.risk, args.start, args.end))
    print(HEAD)
    print('-' * len(HEAD))
    print(line(summarise('age %s' % (age if age is not None else 'any'),
                         tr, args.equity, args.risk, years)))
    per = defaultdict(list)
    for t in tr:
        per['%s %s' % (t['symbol'], t['tf'])].append(t['net'])
    print('\nwhat the queue actually took, by cell:')
    for k in sorted(per, key=lambda k: -sum(per[k])):
        v = per[k]
        print('  %-14s %5d trades  mean %+0.4f R  total %+7.1f R'
              % (k, len(v), sum(v) / len(v), sum(v)))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
