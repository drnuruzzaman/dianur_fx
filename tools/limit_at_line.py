"""
ENTER AT THE LINE, NOT NEAR IT.

Every bounce measured in this project enters wherever price happened to be when
the approach fired -- within `near_atr` of the line, 0.20 ATR off it on average.
With symmetric 1.0 ATR barriers that risks 1.20 to make 0.80, R:R 0.67, and at
the measured hold rate it is negative before a cent of cost. The geometry, not
the signal, is what loses.

A resting limit AT the line removes that penalty: risk 1.00, reward 1.00. It
also removes entry slippage, because a limit does not slip.

THE CATCH IS THE FILL, and it is adversely selected in a way that is exactly
computable rather than merely worrying:

    a BREAK must pass through the line, so every loser fills
    a HOLD fills only if price came all the way to the line before turning

So the fill filter keeps all the bad outcomes and only some of the good ones.
Whether the geometry gain survives that is the whole question, and it cannot be
reasoned out -- the hold rate among FILLED approaches has to be measured.

Three arms, same approaches, same lines:

  near    entry where the approach fired, dist ATR off the line   (as measured)
  limit   entry at the line, filled trades only
  per_op  the limit arm charged for its misses: unfilled = no trade, 0 R

`per_op` is the honest one. Reporting `limit` alone would be the same mistake as
pooling only the cells that survived a filter.
"""
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, '.')

from sim.indicators import atr as atr_series
from sim.instruments import load, spec
from sim.tl.engine import Params, TrendlineEngine
from sim.tl.mtf import TF_MS

NEAR_ATR, FAR_ATR = 0.4, 1.5
STOP_ATR, TARGET_ATR = 1.0, 1.0
HORIZON = 48
SLIP = 0.02
ERAS = [('1999-2010', '1999-01-01', '2010-12-31'),
        ('2011-2020', '2011-01-01', '2020-12-31'),
        ('2021-2026', '2021-01-01', None)]
# XAUUSD ONLY. The per-instrument-per-timeframe discipline this project settled
# on means the four instruments were never one population; with a single
# instrument the ERAS are the replication axis instead.
SYMS = ('XAUUSD.a',)
TFS = ('1h', '4h')


def run_cell(bars, tf, sym, era):
    high = np.asarray(bars['high'], float)
    low = np.asarray(bars['low'], float)
    close = np.asarray(bars['close'], float)
    atr = atr_series(bars, 14)
    n = len(close)
    rows = []
    armed = {}
    geom = {}

    def on_bar(i, snap, live):
        a = atr[i]
        if not np.isfinite(a) or a <= 0 or i + HORIZON >= n:
            return
        for ln in live:
            if not ln.is_tradeable:
                continue
            v = ln.value_at(int(bars.index[i].value // 1_000_000))
            if not np.isfinite(v) or v <= 0:
                continue
            lid = ln.id
            geom.setdefault(lid, {})[i] = v
            dist = abs(close[i] - v) / a
            if dist >= FAR_ATR:
                armed[lid] = True
                continue
            if dist > NEAR_ATR or not armed.get(lid, False):
                continue
            armed[lid] = False

            side = 1 if ln.role.value == 'support' else -1
            seen = geom[lid]
            ks = sorted(seen)
            slope = ((seen[ks[-1]] - seen[ks[0]]) / max(1, ks[-1] - ks[0])
                     if len(ks) > 1 else 0.0)

            # barriers anchored on the LINE, as in diagnostics.py: the outcome
            # is a fact about the level, independent of where entry sits.
            up_b = TARGET_ATR * a if side > 0 else STOP_ATR * a
            dn_b = STOP_ATR * a if side > 0 else TARGET_ATR * a

            filled = False
            outcome = None
            # i + 1, as every other resolver here does. Starting at `i` let the
            # SIGNAL BAR's own high/low resolve the outcome: a bar closing 0.2
            # ATR above a support having already printed a high 1 ATR above it
            # scored an instant hold. That is look-ahead inside the bar, and it
            # lifted the hold rate to 79% and the expectancy to +0.27 R.
            for j in range(i + 1, min(n - 1, i + HORIZON) + 1):
                lv = v + slope * (j - i)
                # a limit at the line fills the moment the bar trades through it
                if not filled and low[j] <= lv <= high[j]:
                    filled = True
                if high[j] >= lv + up_b:
                    outcome = 'hold' if side > 0 else 'break'
                    break
                if low[j] <= lv - dn_b:
                    outcome = 'break' if side > 0 else 'hold'
                    break
            if outcome is None:
                continue
            rows.append({'era': era, 'symbol': sym, 'tf': tf, 'i': i,
                         'dist_atr': dist, 'held': int(outcome == 'hold'),
                         'filled': int(filled), 'atr': a, 'side': side})

    eng = TrendlineEngine(tf, TF_MS[tf], Params(), record_tradeable=True)
    eng.walk(bars, on_bar=on_bar)
    return rows


def r_of(held, dist, sp, a, slip_mult):
    """R for one approach. Entry sits `dist` ATR off the line."""
    risk = (STOP_ATR + dist) * a
    reward = (TARGET_ATR - dist) * a
    if risk <= 0 or reward <= 0:
        return np.nan
    spread = float(sp.get('spread_points_now') or 0) * sp['point']
    cost = spread / risk + slip_mult * SLIP * a / risk
    return (reward / risk if held else -1.0) - cost


all_rows = []
for era, start, end in ERAS:
    for sym in SYMS:
        for tf in TFS:
            try:
                bars = load(sym, tf, start, end)
            except Exception:
                continue
            if len(bars) < 400:
                continue
            r = run_cell(bars, tf, sym, era)
            all_rows.extend(r)
            print('  %s %s %s -> %d approaches' % (era, sym, tf, len(r)), flush=True)

df = pd.DataFrame(all_rows)
df.to_csv('runs/struct/limit_at_line.csv', index=False)
print('\n%d approaches' % len(df))

specs = {(s, t): spec(s, t) for s in SYMS for t in TFS}


def arm_r(g, mode):
    out = []
    for _, x in g.iterrows():
        sp = specs[(x.symbol, x.tf)]
        if mode == 'near':
            out.append(r_of(x.held, x.dist_atr, sp, x.atr, 2))
        elif mode == 'limit':
            if not x.filled:
                continue
            out.append(r_of(x.held, 0.0, sp, x.atr, 1))
        elif mode == 'per_op':
            out.append(r_of(x.held, 0.0, sp, x.atr, 1) if x.filled else 0.0)
    return np.array([v for v in out if np.isfinite(v)])


def ci(x, n=3000, seed=5):
    if len(x) < 30:
        return (np.nan, np.nan)
    rng = np.random.default_rng(seed)
    m = [rng.choice(x, len(x), True).mean() for _ in range(n)]
    return tuple(np.percentile(m, [2.5, 97.5]))


def ci_bar(g, mode, n=2000, seed=5):
    """
    Resample BARS, not rows. Up to 19 lines can be approached on one bar and
    91% of those groups share an outcome, so rows are ~2.2x duplicated and an
    i.i.d. interval on them is fiction.
    """
    keys = list(g.groupby(['era', 'symbol', 'tf', 'i']).indices.items())
    if len(keys) < 30:
        return (np.nan, np.nan), 0
    idx = [v for _, v in keys]
    rng = np.random.default_rng(seed)
    out = []
    for _ in range(n):
        pick = rng.integers(0, len(idx), len(idx))
        rows = np.concatenate([idx[j] for j in pick])
        x = arm_r(g.iloc[rows], mode)
        if len(x):
            out.append(x.mean())
    return tuple(np.percentile(out, [2.5, 97.5])), len(idx)


print('\n' + '=' * 74)
print('FILL RATE, and it is adversely selected as predicted')
print('=' * 74)
print('  overall fill rate        %.1f%%' % (100 * df.filled.mean()))
print('  of BREAKS  (losers)      %.1f%%' % (100 * df[df.held == 0].filled.mean()))
print('  of HOLDS   (winners)     %.1f%%' % (100 * df[df.held == 1].filled.mean()))
print('  hold rate, all           %.1f%%' % (100 * df.held.mean()))
print('  hold rate, FILLED only   %.1f%%' % (100 * df[df.filled == 1].held.mean()))

print('\n' + '=' * 74)
print('EXPECTANCY PER ARM  (R, after spread and slippage)')
print('=' * 74)
print('%-10s %9s %8s %11s %24s' % ('arm', 'rows', 'bars', 'net_R', 'CI (by bar)'))
for mode in ('near', 'limit', 'per_op'):
    x = arm_r(df, mode)
    (lo, hi), nb = ci_bar(df, mode)
    print('%-10s %9d %8d %+11.4f   [%+.4f, %+.4f]'
          % (mode, len(x), nb, x.mean(), lo, hi))

print('\nby era (per_op, the honest arm):')
for era, _, _ in ERAS:
    g = df[df.era == era]
    if len(g) < 50:
        continue
    x = arm_r(g, 'per_op')
    (lo, hi), nb = ci_bar(g, 'per_op')
    print('  %-12s bars=%6d  %+.4f  CI [%+.4f, %+.4f]' % (era, nb, x.mean(), lo, hi))

print('\nwrote runs/struct/limit_at_line.csv')
