#!/usr/bin/env python
"""
rayo_filters.py -- which signal-time filters make Rayo tickets worth taking?

    python tools/rayo_filters.py                        XAUUSD 15m/30m/1h/4h
    python tools/rayo_filters.py --symbol USDJPY.a      the holdout instrument
    python tools/rayo_filters.py --tfs 30m --arms base long htf

RESEARCH ONLY. Reads bars, writes stdout. Touches no config, no live tool.

WHY RUN THIS AGAIN. Nine signal-time scores were tested against Rayo outcomes
on 2026-09-10 and none passed. Every one of those tests -- and the session,
volatility and trend-age results -- was scored with a 24h horizon that
DISCARDED 40-70% of 15m/30m/1h trades (memory:
rayo-24h-horizon-dropped-unresolved-trades). A filter's verdict can flip when
the trades it removes stop being invisible, so the earlier "none passed" is not
evidence either way. This redoes it with every trade counted and swap charged.

"ACCURACY" MEANS NET R, NOT WIN RATE. A nearer target raises the hit rate and
lowers the money; the rule's own docstring records that. So every arm reports
net R per trade after spread, slippage and swap, total R a year, and the sign in
each of the four registered eras.

THE BAR, STATED BEFORE THE RUN. A filter is a CANDIDATE only if, against the
unfiltered baseline on the same frame, it
  1. raises net R per trade in at least 3 of 4 eras, and
  2. keeps total R a year at least equal to baseline's (a filter that improves
     the average by deleting most of the trades has not improved the account),
  3. on at least two of the gold frames, and
  4. does the same on USDJPY, which took no part in choosing it.
Anything short of that is reported and not proposed.

THE LONG-ONLY TRAP, named in advance. Gold rose roughly threefold over this
window, so a long-only filter is partly a bet on the sample's drift. It gets the
same bar as everything else, and 2017-19 -- when gold went sideways -- is the
era that can refute it.

HOW TRADES ARE COUNTED. One live order per cell, a new ticket only once the
previous resolved; each FILTER CHANGES WHICH TICKETS ENTER THE CURSOR, so each
arm is run as its own sequence rather than by deleting rows from the baseline.
Resolution is memoised per ticket (a ticket's outcome does not depend on the
others), so arms share the expensive 1m walk. Horizon from HORIZON_H (240h for
15m-1h, 720h for 4h); open at the horizon is closed at market and counted.

EVERY FEATURE IS KNOWABLE AT THE TICKET'S BAR. The higher-timeframe trend is
read from the last 4h/1d bar that had CLOSED before the ticket; ATR percentile
from the 500 bars before it; hour from the ticket's own timestamp.
"""

import argparse
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding='utf-8', errors='replace')
    except (AttributeError, ValueError):
        pass

import numpy as np                                             # noqa: E402
import pandas as pd                                            # noqa: E402

import tools.scalper as SC                                     # noqa: E402
import tools.scalp_portfolio as SP                             # noqa: E402
from tools.scalp_portfolio import Cell                         # noqa: E402
from sim.instruments import load                               # noqa: E402

# 3m IS NOT DOWNLOADED, so it is built here from the 1m archive: left-labelled,
# left-closed three-minute buckets on the broker clock the bars are stored in,
# spread taken as the bucket's maximum (the worst quote in it, not the best --
# see memory per-bar-spread-is-the-minutes-best-quote). Research only; nothing
# outside this script sees a 3m frame.
_load_orig = SP.load


def _load_with_3m(symbol, tf, start=None, end=None):
    if tf != '3m':
        return _load_orig(symbol, tf, start, end)
    m = _load_orig(symbol, '1m', start, end)
    agg = {'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'}
    for extra, how in (('volume', 'sum'), ('tick_volume', 'sum'),
                       ('real_volume', 'sum'), ('spread', 'max')):
        if extra in m.columns:
            agg[extra] = how
    return m.resample('3min', label='left', closed='left').agg(agg).dropna(
        subset=['open'])


SP.load = _load_with_3m
SP.TF_MIN.setdefault('3m', 3)

ERAS = [('2017-19', '2017-01-01', '2019-01-01'),
        ('2019-21', '2019-01-01', '2021-01-01'),
        ('2021-23', '2021-01-01', '2023-01-01'),
        ('2023-26', '2023-01-01', '2026-09-14')]

TF_MIN = {'1m': 1, '3m': 3, '5m': 5, '15m': 15, '30m': 30, '1h': 60, '4h': 240}


def ema(s, n):
    return s.ewm(span=n, adjust=False).mean()


def htf_trend(symbol, tf, start, end):
    """(close_ms array, +1/-1 array): EMA20 vs EMA50 on `tf`, per CLOSED bar.

    A bar's close time is its open time plus the frame, and it is only usable
    by a ticket stamped at or after that. `searchsorted(..., 'right') - 1` on
    close times gives exactly the last bar closed by then.
    """
    b = load(symbol, tf, start, end)
    up = (ema(b['close'], 20) > ema(b['close'], 50)).to_numpy()
    open_ms = b.index.values.astype('datetime64[ms]').astype(np.int64)
    close_ms = open_ms + {'15m': 15, '1h': 60, '4h': 240, '1d': 1440}[tf] * 60000
    return close_ms, np.where(up, 1, -1)


def trend_at(ref, ms):
    close_ms, sign = ref
    i = int(np.searchsorted(close_ms, ms, side='right')) - 1
    return int(sign[i]) if i >= 0 else 0


def features(cell, symbol, start, end):
    """Signal-time features for every ticket, keyed by ticket ms."""
    m15 = htf_trend(symbol, '15m', start, end)
    h1 = htf_trend(symbol, '1h', start, end)
    h4 = htf_trend(symbol, '4h', start, end)
    d1 = htf_trend(symbol, '1d', start, end)
    atrs = np.array([t['atr'] for t in cell.tk], dtype=float)
    feats = {}
    window = []
    for k, t in enumerate(cell.tk):
        side = 1 if t['side'] == 'buy' else -1
        lo = max(0, k - 500)
        hist = atrs[lo:k]
        pct = float((hist < t['atr']).mean()) if hist.size >= 100 else 0.5
        hr = pd.Timestamp(t['ms'], unit='ms').hour
        feats[t['ms']] = {
            'side': side,
            'age': t.get('age'),
            'm15': trend_at(m15, t['ms']) * side,    # +1 = with the 15m trend
            'h1': trend_at(h1, t['ms']) * side,      # +1 = with the 1h trend
            'h4': trend_at(h4, t['ms']) * side,      # +1 = with the 4h trend
            'd1': trend_at(d1, t['ms']) * side,      # +1 = with the daily trend
            'atr_pct': pct,
            'hour': hr,                             # stored bars: broker time
            'tf': cell.tf,
        }
    return feats


ARMS = {
    'base':     ('every ticket', lambda f: True),
    'long':     ('buys only', lambda f: f['side'] > 0),
    'short':    ('sells only', lambda f: f['side'] < 0),
    'age10':    ('trend age <= 10 bars', lambda f: f['age'] is not None and f['age'] <= 10),
    'age20':    ('trend age <= 20 bars', lambda f: f['age'] is not None and f['age'] <= 20),
    'htf':      ('with the 4h trend', lambda f: f['h4'] > 0),
    'd1':       ('with the daily trend', lambda f: f['d1'] > 0),
    'htfd1':    ('with 4h AND daily', lambda f: f['h4'] > 0 and f['d1'] > 0),
    'volhi':    ('ATR in top half of last 500', lambda f: f['atr_pct'] >= 0.5),
    'vollo':    ('ATR in bottom half', lambda f: f['atr_pct'] < 0.5),
    'active':   ('broker 09:00-21:00 (London+NY)', lambda f: 9 <= f['hour'] < 21),
    # added AFTER the first gold run, as the combination of the two arms that
    # passed there -- so on gold it is chosen, not tested; USDJPY is the test.
    # THE LIVE RULE (sim/strategies/rayo.py trend_frames): 1m-1h agree with
    # 1h AND 4h, 4h agrees with the daily, 1d/1w only their own trend.
    'live':     ('LIVE: 1h+4h / 4h:daily / d1:own',
                 lambda f: (f['d1'] > 0) if f['tf'] == '4h'
                 else True if f['tf'] in ('1d', '1w')
                 else (f['h1'] > 0 and f['h4'] > 0)),
    'h1':       ('with the 1h trend only', lambda f: f['h1'] > 0),
    'm15':      ('with the 15m trend only', lambda f: f['m15'] > 0),
    'h1h4':     ('with 1h AND 4h', lambda f: f['h1'] > 0 and f['h4'] > 0),
    'htfd1act': ('4h+daily trend AND 09-21', lambda f: f['h4'] > 0 and f['d1'] > 0
                                                     and 9 <= f['hour'] < 21),
}


class Runner:
    def __init__(self, cell, horizon_ms):
        self.cell, self.h = cell, horizon_ms
        self.memo = {}

    def resolve(self, t):
        k = t['ms']
        if k not in self.memo:
            self.memo[k] = SC.resolve(t, self.cell.M, self.cell.expire_ms, self.h)
        return self.memo[k]

    def arm(self, keep, flat_hour=None, spread_pts=None, swap=True):
        """One cursor over the kept tickets.

        `flat_hour` (BROKER clock, e.g. 23.5): a trade still running at that
        time of day is closed at that minute's close, so it never holds through
        the broker's midnight rollover and never pays swap. It is applied BEFORE
        the cursor moves on, because an earlier exit frees the cell for an
        earlier next ticket. Stored 1m bars are broker time, so the hour is read
        straight off their timestamps (memory: stored-bars-are-broker-time).

        `spread_pts` replaces the captured spread, to model a cheaper account.
        """
        out, busy = [], -1
        c = self.cell
        ms_arr, _, _, C = c.M
        day = 86400000
        for t in c.tk:
            if t['ms'] < busy or not keep(t['ms']):
                continue
            r = self.resolve(t)
            if not r:
                continue
            if r['outcome'] == 'expired' or r.get('r') is None:
                busy = r.get('end_ms', t['ms'])
                continue
            fill = r.get('fill_ms') or t['ms']
            gross, end, swap_on = r['r'], r['end_ms'], True
            if flat_hour is not None:
                cut = fill - fill % day + int(flat_hour * 3600000)
                if cut <= fill:
                    cut += day
                if cut < end:
                    i = int(np.searchsorted(ms_arr, cut, side='left'))
                    if i < len(ms_arr) and ms_arr[i] < end:
                        side = 1 if t['side'] == 'buy' else -1
                        gross = (C[i] - t['entry']) * side / t['risk']
                        end, swap_on = int(ms_arr[i]), False
            busy = end
            cost = c.cost_r(t)
            if spread_pts is not None:
                cost = (c.point * spread_pts + 2 * 0.02 * t['atr']) / t['risk']
            sw = c.swap_r(t, fill, end) if (swap_on and swap) else 0.0
            out.append((t['ms'], gross, gross - cost + sw))
        return out


def summary(tr, years):
    if not tr:
        return None
    nets = [x[2] for x in tr]
    eras = []
    for _, a, b in ERAS:
        lo = int(pd.Timestamp(a, tz='UTC').value // 10 ** 6)
        hi = int(pd.Timestamp(b, tz='UTC').value // 10 ** 6)
        sel = [x[2] for x in tr if lo <= x[0] < hi]
        eras.append(sum(sel) / len(sel) if sel else None)
    return {'n': len(tr), 'win': 100.0 * sum(1 for x in tr if x[1] > 0) / len(tr),
            'net': sum(nets) / len(nets), 'ryr': sum(nets) / years, 'eras': eras}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--symbol', default='XAUUSD.a')
    ap.add_argument('--tfs', nargs='+', default=['15m', '30m', '1h', '4h'])
    ap.add_argument('--arms', nargs='+', default=list(ARMS))
    ap.add_argument('--from', dest='start', default='2017-01-01')
    ap.add_argument('--to', dest='end', default='2026-09-14')
    ap.add_argument('--horizon-h', type=float, default=None,
                    help='hours a filled trade may stay open before it is closed '
                         'at market (default HORIZON_H for the frame)')
    ap.add_argument('--no-swap', action='store_true',
                    help='charge no overnight financing')
    ap.add_argument('--flat-hour', type=float, default=None,
                    help='close any open trade at this BROKER hour (e.g. 23.5)')
    ap.add_argument('--spread-pts', type=float, default=None,
                    help='charge this spread in points instead of the captured one')
    args = ap.parse_args()
    years = (pd.Timestamp(args.end) - pd.Timestamp(args.start)).days / 365.25

    print('%s %s..%s  stop 5 ATR, TP1 exit, spread+slip+swap, nothing dropped'
          '  | flat at %s | spread %s | swap %s'
          % (args.symbol, args.start, args.end,
             ('%.1fh broker' % args.flat_hour) if args.flat_hour is not None else 'off',
             ('%.0f pts' % args.spread_pts) if args.spread_pts is not None else 'captured',
             'off' if args.no_swap else 'on'))
    for tf in args.tfs:
        sys.stderr.write('%s %s: loading\n' % (args.symbol, tf))
        cell = Cell(args.symbol, tf, args.start, args.end, None, 5.0, 20, 20, 50, 12)
        feats = features(cell, args.symbol, args.start, args.end)
        hz = args.horizon_h if args.horizon_h is not None else SC.HORIZON_H.get(tf, 240)
        run = Runner(cell, int(hz * 3600 * 1000))
        base = None
        print()
        print('%s %s' % (args.symbol, tf))
        print('%-32s %6s %5s %8s %7s | %8s %8s %8s %8s | %s'
              % ('arm', 'n', 'win%', 'net R', 'R/yr', *[e[0] for e in ERAS],
                 'vs base: eras up / R/yr'))
        for key in args.arms:
            label, fn = ARMS[key]
            sys.stderr.write('  %s\n' % key)
            s = summary(run.arm(lambda ms: fn(feats[ms]), args.flat_hour,
                                args.spread_pts, not args.no_swap), years)
            if s is None:
                print('%-32s no trades' % label)
                continue
            if key == 'base':
                base = s
            cmp = ''
            if base and key != 'base':
                up = sum(1 for a, b in zip(s['eras'], base['eras'])
                         if a is not None and b is not None and a > b)
                verdict = 'CANDIDATE' if up >= 3 and s['ryr'] >= base['ryr'] else ''
                cmp = '%d/4  %+6.1f  %s' % (up, s['ryr'] - base['ryr'], verdict)
            print('%-32s %6d %4.0f%% %+8.4f %+7.1f | %s | %s'
                  % (label, s['n'], s['win'], s['net'], s['ryr'],
                     ' '.join(('%+8.4f' % e) if e is not None else '       -'
                              for e in s['eras']), cmp))
    return 0


if __name__ == '__main__':
    sys.exit(main())
