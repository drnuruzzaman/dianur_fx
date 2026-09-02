"""
exit_sweep.py — should the exit channel be set per instrument and timeframe?

WHY THIS IS A MEASUREMENT AND NOT A SETTING. `donchian.py` locks the exit at
half the entry length, and says why: "letting it vary independently turns one
degree of freedom into two and makes any winner impossible to attribute." That
is a real constraint, not an oversight, and the right way to lift it is to show
the data supports lifting it -- per cell, in both eras, or not at all.

WHAT PROMPTED IT. On XAUUSD 15m the 2-ATR stop is 14.2 points while the exit
channel spans 235 -- a ratio of 16.6, so the stop is hit first essentially
always and the trailing exit never gets to act. The same ratio is 3.2 on 4h,
the cell that was validated. The exit window is held constant in TIME across
timeframes (~40 hours) while the stop scales with the ATR of the bar, and those
two do not move together.

THE SWEEP. Entry length stays at the validated horizon -- 3.3 days of bars, the
one structural finding the earlier sweeps produced -- and only the exit fraction
moves. Each cell is scored per ERA, because with one instrument the eras are the
replication axis, and a fraction is only adopted where it beats 0.5 in BOTH.

    python tools/exit_sweep.py XAUUSD.a 15m,1h,4h
"""

import argparse
import gzip
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ERAS = [('1999-2010', 1999, 2010), ('2011-2020', 2011, 2020), ('2021-2026', 2021, 2026)]

BARS_PER_DAY = {'5m': 288, '15m': 96, '30m': 48, '1h': 24, '4h': 6, '1d': 1}
HORIZON_DAYS = 3.3
ATR_LEN = 14
ATR_MULT = 2.0

#: The fraction of the entry channel the exit uses. 0.5 is the current rule and
#: is included so every other row is read against it rather than against zero.
FRACTIONS = [0.15, 0.25, 0.35, 0.5, 0.7, 1.0]

#: Same frozen friction the other tools charge, per timeframe.
FRICTION_R = {'5m': 0.30, '15m': 0.24, '1h': 0.10, '4h': 0.071, '1d': 0.05}


def load(symbol, tf):
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
                rows.append((int(p[0]), float(p[2]), float(p[3]), float(p[4])))
    a = np.array(rows, dtype=float)
    return a[:, 0], a[:, 1], a[:, 2], a[:, 3]      # ts, high, low, close


def atr_series(high, low, close, length=ATR_LEN):
    n = len(close)
    tr = np.empty(n)
    tr[0] = high[0] - low[0]
    prev_c = close[:-1]
    tr[1:] = np.maximum.reduce([high[1:] - low[1:],
                                np.abs(high[1:] - prev_c),
                                np.abs(low[1:] - prev_c)])
    out = np.full(n, np.nan)
    if n <= length:
        return out
    prev = tr[:length].mean()
    out[length - 1] = prev
    for i in range(length, n):
        prev = (prev * (length - 1) + tr[i]) / length
        out[i] = prev
    return out


def rolling_extreme(x, n, kind):
    """Highest high / lowest low of the PREVIOUS n bars, exclusive of the bar
    itself -- a channel that includes today can never be broken by today."""
    out = np.full(len(x), np.nan)
    if n < 1 or len(x) <= n:
        return out
    from collections import deque
    dq = deque()
    better = (lambda a, b: a >= b) if kind == 'max' else (lambda a, b: a <= b)
    for i in range(len(x)):
        if i >= n:
            out[i] = x[dq[0]]
        while dq and better(x[i], x[dq[-1]]):
            dq.pop()
        dq.append(i)
        if dq[0] <= i - n:
            dq.popleft()
    return out


def run(high, low, close, atr, entry_n, exit_n):
    """
    The rule, walked. Close-triggered entry at the NEXT bar's close (this tool
    has no opens; using the signal close for both would be a free fill), stop
    fixed at entry, exit on a close back through the shorter channel.

    Returns R per trade. R is the stop distance, so the cells are comparable.
    """
    n = len(close)
    up = rolling_extreme(high, entry_n, 'max')
    dn = rolling_extreme(low, entry_n, 'min')
    xhi = rolling_extreme(high, exit_n, 'max')
    xlo = rolling_extreme(low, exit_n, 'min')

    rs = []
    side = 0
    entry_px = stop = risk = 0.0
    for i in range(1, n - 1):
        a = atr[i]
        if not np.isfinite(a) or a <= 0:
            continue
        if side == 0:
            if np.isfinite(up[i]) and close[i] > up[i]:
                side, entry_px = 1, close[i + 1]
                stop = close[i] - ATR_MULT * a
            elif np.isfinite(dn[i]) and close[i] < dn[i]:
                side, entry_px = -1, close[i + 1]
                stop = close[i] + ATR_MULT * a
            if side:
                risk = abs(entry_px - stop)
                if risk <= 0:
                    side = 0
            continue
        # in a position: stop is checked on the bar's range, exit on the close
        if (side > 0 and low[i] <= stop) or (side < 0 and high[i] >= stop):
            rs.append(-1.0)
            side = 0
            continue
        out = ((side > 0 and np.isfinite(xlo[i]) and close[i] < xlo[i])
               or (side < 0 and np.isfinite(xhi[i]) and close[i] > xhi[i]))
        if out:
            rs.append((close[i + 1] - entry_px) * side / risk)
            side = 0
    return np.array(rs)


def boot(x, seed=7, n=2000):
    if len(x) < 10:
        return (float('nan'), float('nan'))
    rng = np.random.default_rng(seed)
    m = x[rng.integers(0, len(x), size=(n, len(x)))].mean(axis=1)
    return tuple(float(v) for v in np.percentile(m, [2.5, 97.5]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('symbol', nargs='?', default='XAUUSD.a')
    ap.add_argument('tfs', nargs='?', default='15m,1h,4h')
    args = ap.parse_args()

    out = {'symbol': args.symbol, 'horizon_days': HORIZON_DAYS,
           'fractions': FRACTIONS, 'cells': []}

    for tf in args.tfs.split(','):
        ts, high, low, close = load(args.symbol, tf)
        atr = atr_series(high, low, close)
        years = 1970 + (ts // (365.2425 * 86400)).astype(int)
        entry_n = max(5, round(HORIZON_DAYS * BARS_PER_DAY[tf]))
        fr = FRICTION_R.get(tf, 0.10)

        for era_name, y0, y1 in ERAS:
            sel = (years >= y0) & (years <= y1)
            if sel.sum() < 2000:
                continue
            i0, i1 = int(np.argmax(sel)), int(len(ts) - np.argmax(sel[::-1]))
            h, l, c, a = high[i0:i1], low[i0:i1], close[i0:i1], atr[i0:i1]
            row = {'tf': tf, 'era': era_name, 'entry_n': entry_n, 'arms': []}
            for f in FRACTIONS:
                exit_n = max(2, int(entry_n * f))
                r = run(h, l, c, a, entry_n, exit_n)
                if len(r) < 20:
                    continue
                lo, hi = boot(r)
                row['arms'].append({
                    'fraction': f, 'exit_n': exit_n, 'trades': len(r),
                    'gross_R': float(r.mean()), 'net_R': float(r.mean() - fr),
                    'lo': lo - fr, 'hi': hi - fr,
                    'win_pct': float((r > 0).mean() * 100),
                })
            if row['arms']:
                out['cells'].append(row)

    print(json.dumps(out, indent=1))


if __name__ == '__main__':
    main()
