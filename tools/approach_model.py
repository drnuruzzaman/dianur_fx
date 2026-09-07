#!/usr/bin/env python
"""
PHASE 12 and PHASE 21 -- does anything here carry information, and is it tradeable?

    python tools/approach_model.py [--horizon 20] [--tf 15m]

TWO QUESTIONS, ONE HARNESS.

  PHASE 12  Model A (zone features) against Model B (zone + liquidity). If B
            does not beat A out of sample, liquidity is redundant AFTER the
            zone features are known -- which is a stronger statement than the
            geometric overlap test, because two detectors can be geometrically
            independent and still carry the same information.

  PHASE 21  Sweep the decision threshold and check it walk-forward. A threshold
            picked on the data it is then scored on is the oldest way to
            manufacture an edge, so every number below is out of sample.

WALK-FORWARD, NOT A RANDOM SPLIT. Rows are ordered by time and cut into folds;
each fold is predicted by a model fitted only on the rows BEFORE it. A shuffled
split would let a model learn from the same afternoon it is scored on -- zone
approaches cluster in time, so neighbouring rows are close to duplicates.

LOGISTIC REGRESSION, FITTED BY GRADIENT DESCENT IN PLAIN PYTHON. No sklearn in
this project's dependencies, and a linear model is the right instrument
regardless: with ~10 features and a suspected-zero effect, the thing to
establish is whether ANY signal exists, not to squeeze the last point out of it.
A tree ensemble that found something a linear model could not would be a reason
to look harder, not a result.

THE BASELINE IS THE ONE THAT MATTERS. `base` predicts the training majority
class for every row. A model that cannot beat it has learned nothing, and with
a 56/35 class split that bar is high -- accuracy alone would look impressive at
56% while knowing nothing at all.
"""

import argparse
import collections
import json
import math
import os
import random
import statistics
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, 'data', 'research', 'approaches.jsonl')

ZONE_FEATURES = ['zone_width_atr', 'touch_count', 'touch_density', 'reaction_atr',
                 'zone_age_bars', 'zone_recency_bars', 'zone_span_bars',
                 'distance_from_price_atr', 'strength', 'break_count']
# `bars_since_break` is deliberately NOT in that list: it is null for every zone
# price has never crossed, and design() drops any row with a null feature, so
# including it would silently restrict the model to already-broken zones and
# change what the AUC is even measuring. It is bucketed in approach_phases.py,
# where a missing value just makes a smaller table.
LIQ_FEATURES = ['liq_count', 'liq_nearest_atr', 'liq_present']
#: Higher-frame confluence. `htf_zone_dist_atr` is null only where the HTF
#: series was unavailable, which is no cell in the shipped CELLS list, so it
#: does not silently restrict the sample the way `bars_since_break` would.
HTF_FEATURES = ['htf_zone_present', 'htf_zone_dist_atr', 'htf_zone_overlap',
                'htf_zone_count']
CTX_FEATURES = ['ema_sep_atr', 'range_pos', 'prior_vol_ratio']


def load(tf=None, pivot='fixed'):
    rows = []
    with open(SRC, encoding='utf-8') as fh:
        for line in fh:
            r = json.loads(line)
            if tf and r['timeframe'] != tf:
                continue
            if pivot and r['pivot_definition'] != pivot:
                continue
            rows.append(r)
    rows.sort(key=lambda r: r['t'])
    return rows


def design(rows, feats, hz):
    """(X, y) with y = 1 when price went back the way it came."""
    X, y = [], []
    for r in rows:
        lab = r.get('y_first_%d' % hz)
        if lab not in ('toward', 'through'):
            continue                        # `neither` is not a direction
        row = []
        ok = True
        for f in feats:
            v = r.get(f)
            if v is None or (isinstance(v, float) and math.isnan(v)):
                ok = False
                break
            row.append(float(v))
        if not ok:
            continue
        X.append(row)
        y.append(1 if lab == 'toward' else 0)
    return X, y


def standardise(X, mu=None, sd=None):
    n = len(X[0])
    if mu is None:
        mu = [statistics.fmean(c) for c in zip(*X)]
        sd = [(statistics.pstdev(c) or 1.0) for c in zip(*X)]
    Z = [[(row[j] - mu[j]) / sd[j] for j in range(n)] for row in X]
    return Z, mu, sd


def fit(X, y, epochs=120, lr=0.3, l2=1e-3):
    n, d = len(X), len(X[0])
    w = [0.0] * d
    b = 0.0
    for _ in range(epochs):
        gw = [0.0] * d
        gb = 0.0
        for i in range(n):
            z = b + sum(w[j] * X[i][j] for j in range(d))
            p = 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, z))))
            e = p - y[i]
            gb += e
            for j in range(d):
                gw[j] += e * X[i][j]
        b -= lr * gb / n
        for j in range(d):
            w[j] -= lr * (gw[j] / n + l2 * w[j])
    return w, b


def predict(X, w, b):
    out = []
    for row in X:
        z = b + sum(w[j] * row[j] for j in range(len(w)))
        out.append(1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, z)))))
    return out


def auc(y, p):
    pairs = sorted(zip(p, y))
    pos = sum(y)
    neg = len(y) - pos
    if not pos or not neg:
        return float('nan')
    rank, i, s = 0.0, 0, 0.0
    # average ranks over ties
    while i < len(pairs):
        j = i
        while j + 1 < len(pairs) and pairs[j + 1][0] == pairs[i][0]:
            j += 1
        r = (i + j) / 2.0 + 1
        for k in range(i, j + 1):
            if pairs[k][1] == 1:
                s += r
        i = j + 1
    return (s - pos * (pos + 1) / 2.0) / (pos * neg)


def walk_forward(rows, feats, hz, folds=5):
    """Out-of-sample probabilities for every row a model could score."""
    X, y = design(rows, feats, hz)
    if len(X) < folds * 200:
        return None
    size = len(X) // folds
    oos_p, oos_y, base_hits = [], [], 0
    for f in range(1, folds):
        tr = slice(0, f * size)
        te = slice(f * size, (f + 1) * size if f < folds - 1 else len(X))
        Xtr, ytr = X[tr], y[tr]
        Xte, yte = X[te], y[te]
        Ztr, mu, sd = standardise(Xtr)
        Zte, _, _ = standardise(Xte, mu, sd)
        w, b = fit(Ztr, ytr)
        p = predict(Zte, w, b)
        oos_p += p
        oos_y += yte
        maj = 1 if sum(ytr) * 2 >= len(ytr) else 0
        base_hits += sum(1 for v in yte if v == maj)
    acc = sum(1 for pp, yy in zip(oos_p, oos_y) if (pp >= 0.5) == (yy == 1)) / len(oos_y)
    return {'n': len(oos_y), 'auc': auc(oos_y, oos_p), 'acc': 100 * acc,
            'base': 100 * base_hits / len(oos_y), 'p': oos_p, 'y': oos_y}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--horizon', type=int, default=20)
    ap.add_argument('--tf', default=None)
    ap.add_argument('--pivot', default='fixed')
    args = ap.parse_args()

    rows = load(args.tf, args.pivot)
    print('rows %d  tf=%s  pivots=%s  horizon=%d'
          % (len(rows), args.tf or 'all', args.pivot, args.horizon))
    print('')

    sets = [('A  zone only', ZONE_FEATURES),
            ('B  zone + liquidity', ZONE_FEATURES + LIQ_FEATURES),
            ('C  zone + liq + context', ZONE_FEATURES + LIQ_FEATURES + CTX_FEATURES),
            ('D  + higher frame', ZONE_FEATURES + LIQ_FEATURES + CTX_FEATURES
                                  + HTF_FEATURES)]
    print('PHASE 12  walk-forward, 5 folds, each fold predicted from earlier rows only')
    print('%-26s %8s %7s %8s %8s' % ('model', 'n', 'AUC', 'acc%', 'base%'))
    print('-' * 62)
    results = {}
    for name, feats in sets:
        r = walk_forward(rows, feats, args.horizon)
        if not r:
            print('%-26s too few rows' % name)
            continue
        results[name] = r
        print('%-26s %8d %7.4f %8.1f %8.1f' % (name, r['n'], r['auc'], r['acc'], r['base']))
    print('')
    print('AUC 0.50 is a coin flip. acc% below base% means the model is worse than')
    print('always guessing the training majority.')

    # ---- PHASE 21: threshold sweep, out of sample -------------------------
    best = (results.get('D  + higher frame') or results.get('C  zone + liq + context')
            or results.get('A  zone only'))
    if best:
        print('')
        print('PHASE 21  threshold sweep on the out-of-sample probabilities')
        print('%-10s %9s %9s %9s' % ('p >=', 'taken', 'hit%', 'lift vs base'))
        print('-' * 42)
        basep = 100.0 * sum(best['y']) / len(best['y'])
        for thr in (0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80):
            sel = [(pp, yy) for pp, yy in zip(best['p'], best['y']) if pp >= thr]
            if len(sel) < 50:
                print('%-10.2f %9d  too few' % (thr, len(sel)))
                continue
            hit = 100.0 * sum(yy for _, yy in sel) / len(sel)
            print('%-10.2f %9d %9.1f %9.1f' % (thr, len(sel), hit, hit - basep))
        print('')
        print('`lift` is against the unconditional rate of the same label (%.1f%%).' % basep)
        print('A threshold that takes fewer trades at the same hit rate has found')
        print('nothing -- it is a smaller sample of the same population.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
