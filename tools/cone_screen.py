"""
cone_screen.py — does the forecast cone tell you which trades not to take?

THE QUESTION. `DISPLACEMENT_V1` is the best hypothesis in this project and it
still does not clear its costs: gross 0.05-0.17 R against friction 0.07-0.24 R.
The gap is small. If some of those trades ask price to travel further than it
normally travels in the holding period, they were never going to work, and
removing them should raise the gross number without touching the detector.

The cone answers exactly that, and it is the only forecast object here with
measured calibration -- nominal 80 delivers 77-81 out to 24 bars on XAUUSD 4H.
So each trade is scored at ENTRY against the distribution of moves available at
that moment:

    target percentile   how unusual a move the 2.0 ATR target requires
    stop percentile     how ordinary a move the 1.0 ATR stop sits inside

A target at the 92nd percentile is arithmetic, not a forecast. A stop at the
30th is inside the noise. Both are checkable before the trade.

HOW IT STAYS HONEST

  ONE INSTRUMENT, ONE TIMEFRAME AT A TIME. Pooling was the confound that
  produced a +0.784 correlation out of nothing earlier here.

  ERAS ARE THE REPLICATION AXIS. With a single instrument there are no other
  instruments to replicate across, so a result must hold in 1999-2010,
  2011-2020 and 2021-2026 independently. A filter that only works in one era is
  a filter fitted to that era.

  THE THRESHOLD IS NOT TUNED PER CELL. One sweep of thresholds is reported for
  every cell and era, so a reader can see whether any single value works
  everywhere -- rather than the best value per cell, which always looks good.

  THE COST MODEL IS THE FROZEN ONE. `DISPLACEMENT_V1.friction_model`, taken from
  the spec rather than re-derived, so this is comparable with everything already
  measured.

    python tools/cone_screen.py XAUUSD.a 4h
"""

import argparse
import gzip
import io
import json
import os
import subprocess
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sim.tl.experiments import DISPLACEMENT_V1                     # noqa: E402
from sim.tl.market_structure import MSParams, detect as detect_ms  # noqa: E402
from sim.tl.strategy import structural_triggers                    # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: Disjoint by construction, and the same three used everywhere else here.
ERAS = [('1999-2010', 1999, 2010), ('2011-2020', 2011, 2020), ('2021-2026', 2021, 2026)]


def load_bars(symbol, tf):
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
                rows.append((int(p[0]) * 1000, float(p[1]), float(p[2]),
                             float(p[3]), float(p[4])))
    a = np.array(rows, dtype=float)
    return {'t': a[:, 0], 'o': a[:, 1], 'h': a[:, 2], 'l': a[:, 3], 'c': a[:, 4]}


def atr_series(high, low, close, length=14):
    """Wilder, matching js/chart/tlengine.js so a cone here means what it means
    in the panel."""
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


def barrier_race(high, low, close, atr, horizon, stop_atr, target_atr):
    """
    For every bar, would this trade's TARGET have been touched before its STOP?

    THE ENDPOINT DISTRIBUTION IS THE WRONG OBJECT, and the first version of this
    tool used it. A cone says where price is likely to BE in 96 bars; a trade
    with two barriers is decided by which one it TOUCHES first, and those are
    different questions. Measured: the 2.0 ATR target sits around the 20-35th
    percentile of the 96-bar move, so almost no trade was ever screened out for
    an unreachable target -- the screen kept 77-100% of trades and changed
    nothing. The trades were not failing because the target was far. They were
    failing on the path.

    So this is a first-passage race, run once per bar per side, on the same
    pessimistic convention the simulator uses: the stop is checked first on any
    bar where both barriers are in range.

    Returns +1 target first, -1 stop first, 0 neither within the horizon.
    """
    n = len(close)
    out = {1: np.zeros(n, dtype=np.int8), -1: np.zeros(n, dtype=np.int8)}
    for side in (1, -1):
        res = out[side]
        for i in range(n - 1):
            a = atr[i]
            if not np.isfinite(a) or a <= 0:
                continue
            entry = close[i]
            stop = entry - side * stop_atr * a
            target = entry + side * target_atr * a
            end = min(n, i + horizon + 1)
            for j in range(i + 1, end):
                if (side > 0 and low[j] <= stop) or (side < 0 and high[j] >= stop):
                    res[i] = -1
                    break
                if (side > 0 and high[j] >= target) or (side < 0 and low[j] <= target):
                    res[i] = 1
                    break
    return out


def path_rate(race, i, side, horizon, lookback=2500, min_samples=60):
    """
    The historical rate at which this geometry reached its target first, over
    bars whose own race had already FINISHED by bar i.

    That cutoff is the whole causality guarantee: a bar `horizon` back or less
    has not resolved yet at `i`, and including it would be reading the same
    future the trade is about to walk into.
    """
    hi = i - horizon
    lo = max(0, hi - lookback)
    if hi - lo < min_samples:
        return None
    seg = race[side][lo:hi]
    decided = seg != 0
    if decided.sum() < min_samples:
        return None
    return {
        'n_path': int(decided.sum()),
        'p_target_first': float((seg[decided] == 1).mean()),
        'p_timeout': float((seg == 0).mean()),
    }


def cone_percentiles(close, atr, i, horizon, target_px, stop_px, side,
                     lookback=2500, min_samples=60):
    """
    Where the target and the stop sit in the distribution of moves available at
    bar `i`, measured on bars that had already resolved by then.

    Returns the fraction of historical `horizon`-bar moves that reached at least
    as far as the target, in the trade's direction -- so a small number means an
    unusual demand. The stop is the mirror: the fraction that fell at least as
    far the other way.
    """
    lo = max(20, i - lookback)
    hi = i - horizon                       # its window must have closed by `i`
    if hi - lo < min_samples:
        return None
    a_i = atr[i]
    if not np.isfinite(a_i) or a_i <= 0:
        return None

    a = atr[lo:hi]
    c0 = close[lo:hi]
    c1 = close[lo + horizon:hi + horizon]
    ok = np.isfinite(a) & (a > 0)
    if ok.sum() < min_samples:
        return None
    moves = ((c1[ok] - c0[ok]) / a[ok]) * side          # in the trade's direction

    need_target = abs(target_px - close[i]) / a_i
    need_stop = abs(close[i] - stop_px) / a_i
    return {
        'n': int(ok.sum()),
        'p_reach_target': float((moves >= need_target).mean()),
        'p_hit_stop': float((moves <= -need_stop).mean()),
        'target_atr': float(need_target),
        'stop_atr': float(need_stop),
    }


def simulate(bars, spec, tf, era=None):
    """Every displacement trade in the window, with its cone score at entry."""
    t, o, h, l, c = bars['t'], bars['o'], bars['h'], bars['l'], bars['c']
    n = len(c)
    atr = atr_series(h, l, c)

    years = np.array([int(x) for x in
                      np.datetime_as_string(bars['t'].astype('datetime64[ms]'), unit='Y')])
    if era:
        _, y0, y1 = era
        sel = (years >= y0) & (years <= y1)
        if sel.sum() < 500:
            return []
        i0, i1 = int(np.argmax(sel)), int(n - np.argmax(sel[::-1]))
    else:
        i0, i1 = 0, n

    # `detect` takes a frame-like of high/low/close plus explicit times, so the
    # numpy arrays are handed over directly rather than round-tripped through
    # pandas for one call.
    race = barrier_race(h, l, c, atr, spec.horizon_bars,
                        spec.stop_atr, spec.target_atr)

    ev, _ = detect_ms({'high': h, 'low': l, 'close': c},
                      MSParams(strength=spec.structure_strength),
                      atr=atr, times=t.astype('int64'))
    trig = structural_triggers(ev, h, l, c, atr, n,
                               displacement_atr=spec.displacement_atr)

    out = []
    for i, side in sorted(trig.items()):
        if i < max(i0, 300) or i >= i1 - spec.horizon_bars:
            continue
        a = atr[i]
        if not np.isfinite(a) or a <= 0:
            continue
        entry = c[i]
        stop = entry - side * spec.stop_atr * a
        target = entry + side * spec.target_atr * a

        # walk forward; stop is checked first on the same bar, which is the
        # pessimistic convention used throughout this project
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
            r = ((c[j] - entry) * side) / (spec.stop_atr * a)

        cone = cone_percentiles(c, atr, i, spec.horizon_bars, target, stop, side)
        path = path_rate(race, i, side, spec.horizon_bars)
        if cone is None or path is None:
            continue
        out.append({'i': i, 'side': side, 'gross_R': float(r), **cone, **path})
    return out


#: Friction is TAKEN FROM THE FROZEN BASELINE, not re-derived.
#:
#: The model is `spread/stop_price + 2*slippage_atr/stop_atr`, and its spread
#: half needs a per-instrument, per-era spread series that is not on disk for
#: gold going back to 1999. Re-deriving half of it and quietly dropping the
#: other half would understate costs by roughly a third -- the slippage term
#: alone is 0.040 R against the 0.071 R the 4h baseline actually measured.
#:
#: So the measured number is used, and the comparison this tool exists to make
#: is unaffected either way: screening changes the GROSS side, and every row is
#: charged the same friction.
FRICTION_R = {'15m': 0.24, '1h': 0.10, '4h': 0.071, '1d': 0.05}


def friction_R(spec, tf):
    return FRICTION_R.get(tf, 0.10)


def report(trades, spec, tf, label, boots=2000, seed=7):
    if not trades:
        return None
    g = np.array([x['gross_R'] for x in trades])
    fr = friction_R(spec, tf)
    # A CI ON EVERY ROW, because a screen that keeps 4% of the trades will
    # produce a spectacular mean and a meaningless one. Without the interval
    # beside it, `+0.750` reads as a discovery rather than as twelve trades.
    rng = np.random.default_rng(seed)
    if len(g) >= 5:
        idx = rng.integers(0, len(g), size=(boots, len(g)))
        means = g[idx].mean(axis=1)
        lo, hi = np.percentile(means, [2.5, 97.5])
    else:
        lo = hi = float('nan')
    return {
        'cell': label, 'n': len(trades),
        'gross_R': float(g.mean()),
        'net_R': float(g.mean() - fr),
        'net_lo': float(lo - fr), 'net_hi': float(hi - fr),
        'win_pct': float((g > 0).mean() * 100),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('symbol', nargs='?', default='XAUUSD.a')
    ap.add_argument('tfs', nargs='?', default='15m,1h,4h')
    ap.add_argument('--thresholds', default='0.0,0.15,0.20,0.25,0.30')
    args = ap.parse_args()

    spec = DISPLACEMENT_V1
    ths = [float(x) for x in args.thresholds.split(',')]
    out = {'symbol': args.symbol, 'spec': spec.name,
           'friction_R': FRICTION_R, 'cells': []}

    for tf in args.tfs.split(','):
        bars = load_bars(args.symbol, tf)
        for era in ERAS:
            trades = simulate(bars, spec, tf, era)
            if len(trades) < 30:
                continue
            row = {'tf': tf, 'era': era[0], 'all': report(trades, spec, tf, 'all'),
                   'screened': []}
            for th in ths:
                kept = [x for x in trades if x['p_reach_target'] >= th]
                r = report(kept, spec, tf, f'endpoint>={th:.2f}')
                if r:
                    r['threshold'] = th
                    r['kept_pct'] = 100.0 * len(kept) / len(trades)
                    row['screened'].append(r)
            row['path'] = []
            for th in [0.30, 0.34, 0.38, 0.42]:
                kept = [x for x in trades if x['p_target_first'] >= th]
                r = report(kept, spec, tf, f'path>={th:.2f}')
                if r:
                    r['threshold'] = th
                    r['kept_pct'] = 100.0 * len(kept) / len(trades)
                    row['path'].append(r)
            out['cells'].append(row)

    print(json.dumps(out, indent=1))


if __name__ == '__main__':
    main()
