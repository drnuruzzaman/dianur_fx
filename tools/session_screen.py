"""
session_screen.py — DISPLACEMENT_V1 on XAUUSD, priced with the REAL spread and
split by session.

WHY THIS AND NOT ANOTHER STATE MODEL. Every edge measured in this project is
0.05-0.17 R gross against 0.07-0.24 R friction. The binding constraint has never
been direction; it has been cost. And cost per unit of opportunity is not
constant across the day:

    XAUUSD 1h, last 8 years, median spread against median bar range
        16:00 UTC   5.0 points / 919 points   0.54%
        23:00 UTC   7.0 points / 246 points   2.84%

The spread barely moves. The available move varies 3.7x. So the same trade taken
at 23:00 costs 5.3x what it costs at 16:00, per unit of move -- and ATR
normalisation only partly absorbs that, because ATR is a rolling 14-bar average
that smooths across exactly the cycle that matters.

TWO THINGS THIS DOES DIFFERENTLY

  REAL FRICTION, PER TRADE. Every bar carries its own spread in the data and it
  has never been used: the experiments charge a frozen constant. Here each trade
  is charged `spread_at_entry / risk_distance + 2 * slippage`, which is the same
  model the spec names, evaluated rather than assumed.

  SESSIONS ARE DEFINED BY THE CLOCK, NOT BY THE RESULT. The buckets below are the
  conventional FX sessions, fixed before looking. Deriving them from the cost
  table -- "cheap hours are the ones under 1%" -- would be fitting the split to
  the data it is about to be tested on, and the per-hour table is printed
  separately so the convention can be judged.

    python tools/session_screen.py XAUUSD.a 1h,4h
"""

import argparse
import gzip
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sim.tl.experiments import DISPLACEMENT_V1                     # noqa: E402
from sim.tl.market_structure import MSParams, detect as detect_ms  # noqa: E402
from sim.tl.strategy import structural_triggers                    # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ERAS = [('1999-2010', 1999, 2010), ('2011-2020', 2011, 2020), ('2021-2026', 2021, 2026)]

#: Conventional FX sessions in UTC, fixed before the run. Gold trades the London
#: and New York clocks; the overlap is where its volume is.
SESSIONS = [
    ('asia',    0, 7),
    ('london',  7, 13),
    ('overlap', 13, 17),
    ('ny-late', 17, 21),
    ('off',     21, 24),
]


def load_bars(symbol, tf):
    """Bars WITH the spread column, which the rest of this project drops."""
    d = os.path.join(ROOT, 'data', 'bars', symbol, tf)
    rows = []
    for f in sorted(os.listdir(d)):
        if not f.endswith('.csv.gz'):
            continue
        with gzip.open(os.path.join(d, f), 'rt') as fh:
            for line in fh:
                if not line or line[0] == 't':
                    continue
                p = line.split(',')
                rows.append((int(p[0]), float(p[1]), float(p[2]), float(p[3]),
                             float(p[4]), float(p[7])))
    a = np.array(rows, dtype=float)
    return {'ts': a[:, 0], 'o': a[:, 1], 'h': a[:, 2], 'l': a[:, 3],
            'c': a[:, 4], 'spread': a[:, 5]}


def atr_series(high, low, close, length=14):
    n = len(close)
    tr = np.empty(n)
    tr[0] = high[0] - low[0]
    for i in range(1, n):
        tr[i] = max(high[i] - low[i], abs(high[i] - close[i - 1]),
                    abs(low[i] - close[i - 1]))
    out = np.full(n, np.nan)
    if n < length:
        return out
    prev = tr[:length].mean()
    out[length - 1] = prev
    for i in range(length, n):
        prev = (prev * (length - 1) + tr[i]) / length
        out[i] = prev
    return out


def session_of(hour):
    for name, a, b in SESSIONS:
        if a <= hour < b:
            return name
    return 'off'


def simulate(bars, spec, point, era=None):
    ts, h, l, c = bars['ts'], bars['h'], bars['l'], bars['c']
    spread_pts = bars['spread']
    n = len(c)
    atr = atr_series(h, l, c)
    years = 1970 + (ts // (365.2425 * 86400)).astype(int)
    hours = ((ts // 3600) % 24).astype(int)

    if era:
        _, y0, y1 = era
        sel = (years >= y0) & (years <= y1)
        if sel.sum() < 500:
            return []
        i0, i1 = int(np.argmax(sel)), int(n - np.argmax(sel[::-1]))
    else:
        i0, i1 = 0, n

    ev, _ = detect_ms({'high': h, 'low': l, 'close': c},
                      MSParams(strength=spec.structure_strength),
                      atr=atr, times=(ts * 1000).astype('int64'))
    trig = structural_triggers(ev, h, l, c, atr, n,
                               displacement_atr=spec.displacement_atr)

    out = []
    for i, side in sorted(trig.items()):
        if i < max(i0, 200) or i >= i1 - spec.horizon_bars:
            continue
        a = atr[i]
        if not np.isfinite(a) or a <= 0:
            continue
        entry = c[i]
        risk = spec.stop_atr * a
        stop = entry - side * risk
        target = entry + side * spec.target_atr * a

        r = None
        for j in range(i + 1, min(n, i + spec.horizon_bars + 1)):
            if (side > 0 and l[j] <= stop) or (side < 0 and h[j] >= stop):
                r = -1.0
                break
            if (side > 0 and h[j] >= target) or (side < 0 and l[j] <= target):
                r = spec.target_atr / spec.stop_atr
                break
        if r is None:
            j = min(n - 1, i + spec.horizon_bars)
            r = ((c[j] - entry) * side) / risk

        # THE REAL COST OF THIS TRADE, from this bar's own spread.
        sp = spread_pts[i] * point
        fr = sp / risk + 2 * spec.slippage_atr / spec.stop_atr
        out.append({'i': i, 'hour': int(hours[i]), 'session': session_of(hours[i]),
                    'gross_R': float(r), 'friction_R': float(fr),
                    'net_R': float(r - fr), 'spread_pts': float(spread_pts[i]),
                    'atr': float(a)})
    return out


def stats(rows, boots=2000, seed=7):
    if not rows:
        return None
    net = np.array([x['net_R'] for x in rows])
    gross = np.array([x['gross_R'] for x in rows])
    fr = np.array([x['friction_R'] for x in rows])
    rng = np.random.default_rng(seed)
    if len(net) >= 10:
        idx = rng.integers(0, len(net), size=(boots, len(net)))
        lo, hi = np.percentile(net[idx].mean(axis=1), [2.5, 97.5])
    else:
        lo = hi = float('nan')
    return {'n': len(rows), 'gross_R': float(gross.mean()),
            'friction_R': float(fr.mean()), 'net_R': float(net.mean()),
            'lo': float(lo), 'hi': float(hi),
            'win_pct': float((gross > 0).mean() * 100)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('symbol', nargs='?', default='XAUUSD.a')
    ap.add_argument('tfs', nargs='?', default='1h,4h')
    ap.add_argument('--point', type=float, default=0.01)   # gold quotes in cents
    args = ap.parse_args()
    spec = DISPLACEMENT_V1

    out = {'symbol': args.symbol, 'spec': spec.name, 'point': args.point,
           'sessions': [f'{n} {a:02d}-{b:02d}' for n, a, b in SESSIONS], 'cells': []}

    for tf in args.tfs.split(','):
        bars = load_bars(args.symbol, tf)
        allrows = simulate(bars, spec, args.point)
        cell = {'tf': tf, 'all': stats(allrows), 'by_session': {}, 'by_hour': {},
                'by_era': {}}
        for name, _, _ in SESSIONS:
            s = stats([x for x in allrows if x['session'] == name])
            if s:
                cell['by_session'][name] = s
        for hh in range(24):
            s = stats([x for x in allrows if x['hour'] == hh])
            if s and s['n'] >= 20:
                cell['by_hour'][hh] = s
        # replication: the same session split, inside each era
        for era in ERAS:
            rows = simulate(bars, spec, args.point, era)
            if len(rows) < 30:
                continue
            cell['by_era'][era[0]] = {
                'all': stats(rows),
                'sessions': {n: stats([x for x in rows if x['session'] == n])
                             for n, _, _ in SESSIONS},
            }
        out['cells'].append(cell)

    print(json.dumps(out, indent=1))


if __name__ == '__main__':
    main()
