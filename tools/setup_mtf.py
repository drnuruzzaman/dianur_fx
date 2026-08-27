"""
setup_mtf.py — ONE setup, defined precisely, replayed blind, then anatomised.

THE SETUP. A sequence, not a scoreboard. Each timeframe has one job and the
later stages are only consulted if the earlier ones passed:

    4H   CONTEXT     is structure bullish (or bearish) at this instant?
    1H   LOCATION    is price at a demand zone or a support band?
    15M  TRIGGER     did price SWEEP a prior low and close back above it,
                     then break structure in the context's direction?
    15M  EXECUTION   entry at the break, stop below the sweep, target at the
                     next opposing 1H level

EVERY LEVEL HAS A REASON, which is the point of building it this way:

    entry   the close of the bar that broke structure
    stop    below the sweep low -- the price that says the sweep failed
    target  the next opposing 1H zone -- where the other side is waiting

Not `stop = 1 ATR, target = 2 ATR`. Those numbers are geometry chosen to
backtest well, and the R:R that falls out of a real setup is whatever the levels
say it is: sometimes 1.4, sometimes 4.0, and a setup whose average R:R is 0.8
should be discarded on arithmetic before anyone measures its win rate.

THE ANATOMY IS DERIVED ON ONE ERA AND TESTED ON THE OTHERS. Asking "what do the
winners share" and then building a rule from the answer is how a strategy gets
fitted to its own sample. The features are compared on 2011-2020 only; whatever
that suggests has to survive 2021-2026, which this tool never looks at while
choosing.

CAUSALITY. Higher timeframes are aligned with `mtf.align_index`, which raises
rather than returns if an alignment would expose an unclosed bar. Zones are
detected with `upto` at the trigger bar. The sweep and the break are both read
from bars at or before the trigger.

    python tools/setup_mtf.py XAUUSD.a
"""

import argparse
import gzip
import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sim.tl import supply_demand, zones                            # noqa: E402
from sim.tl.market_structure import MSParams, detect as detect_ms  # noqa: E402
from sim.tl.mtf import align_index                                 # noqa: E402
from sim.tl.pivots import find_pivots                              # noqa: E402
from sim.tl.structure import classify                              # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ERAS = [('1999-2010', 1999, 2010), ('2011-2020', 2011, 2020), ('2021-2026', 2021, 2026)]

#: The setup's own parameters. Each is a definition, not a tuned value.
SWING_STRENGTH = 3          # what counts as a swing to sweep
SWEEP_WINDOW = 12           # bars between the sweep and the break
NEAR_ATR = 1.0              # how close to a zone counts as "at" it
STOP_PAD_ATR = 0.10         # below the sweep low, so the level itself is not the stop
MIN_RR = 1.0                # a setup that cannot pay 1:1 is not taken
HORIZON = 96                # bars before the trade is abandoned


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
                rows.append((int(p[0]), float(p[1]), float(p[2]), float(p[3]),
                             float(p[4]), float(p[7])))
    a = np.array(rows, dtype=float)
    idx = pd.to_datetime(a[:, 0].astype('int64'), unit='s')
    return pd.DataFrame({'open': a[:, 1], 'high': a[:, 2], 'low': a[:, 3],
                         'close': a[:, 4], 'spread': a[:, 5]}, index=idx)


def atr_of(df, length=14):
    h, l, c = df['high'].values, df['low'].values, df['close'].values
    n = len(c)
    tr = np.empty(n)
    tr[0] = h[0] - l[0]
    for i in range(1, n):
        tr[i] = max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
    out = np.full(n, np.nan)
    prev = tr[:length].mean()
    out[length - 1] = prev
    for i in range(length, n):
        prev = (prev * (length - 1) + tr[i]) / length
        out[i] = prev
    return out


def sweeps(high, low, close, strength=SWING_STRENGTH):
    """
    Bars where price took out a prior swing and CLOSED back inside.

    A sweep is not a break -- it is a break that failed within the same bar, and
    that distinction is the whole signal: the low was taken, the sellers who
    needed it did not get follow-through, and the close is back above. Pivots
    are read at their CONFIRMED bar, so a swing being swept was visible before
    the sweep.
    """
    n = len(close)
    piv_hi, piv_lo = find_pivots(high, low, strength, close=close)
    lo_level = np.full(n, np.nan)
    hi_level = np.full(n, np.nan)
    cur_lo = np.nan
    cur_hi = np.nan
    by_conf_lo = {}
    by_conf_hi = {}
    for q in piv_lo:
        by_conf_lo.setdefault(int(q['confirmed_i']), []).append(q)
    for q in piv_hi:
        by_conf_hi.setdefault(int(q['confirmed_i']), []).append(q)
    for i in range(n):
        for q in by_conf_lo.get(i, []):
            cur_lo = float(q['price'])
        for q in by_conf_hi.get(i, []):
            cur_hi = float(q['price'])
        lo_level[i] = cur_lo
        hi_level[i] = cur_hi

    up = np.zeros(n, dtype=bool)          # swept a LOW and closed back above
    dn = np.zeros(n, dtype=bool)
    swept_lo = np.full(n, np.nan)
    swept_hi = np.full(n, np.nan)
    for i in range(n):
        if np.isfinite(lo_level[i]) and low[i] < lo_level[i] <= close[i]:
            up[i] = True
            swept_lo[i] = low[i]
        if np.isfinite(hi_level[i]) and high[i] > hi_level[i] >= close[i]:
            dn[i] = True
            swept_hi[i] = high[i]
    return {'up': up, 'dn': dn, 'lo': swept_lo, 'hi': swept_hi}


def find_setups(sym, era=None, require_context=True, require_location=True):
    """
    Every candidate, with the gates RECORDED rather than only applied.

    The first version of the anatomy was useless and the reason is worth
    keeping: it gated on context, location and trigger, and then asked what
    separated its winners from its losers. Every surviving trade had all three
    by construction, so there was nothing left to vary -- the largest gap was
    8pp and it pointed the wrong way. To ask what a gate is WORTH, the
    population has to include the trades that failed it.
    """
    m15 = load(sym, '15m')
    h1 = load(sym, '1h')
    h4 = load(sym, '4h')
    if era:
        _, y0, y1 = era
        m15 = m15[(m15.index.year >= y0) & (m15.index.year <= y1)]
        h1 = h1[(h1.index.year >= y0 - 1) & (h1.index.year <= y1)]
        h4 = h4[(h4.index.year >= y0 - 1) & (h4.index.year <= y1)]
    if len(m15) < 5000 or len(h1) < 500 or len(h4) < 300:
        return []

    a15 = atr_of(m15)
    a1 = atr_of(h1)
    h15, l15, c15, o15 = (m15['high'].values, m15['low'].values,
                          m15['close'].values, m15['open'].values)

    # CONTEXT: 4h structural bias, aligned so no unclosed 4h bar is ever read
    h4bias = classify(h4, strength=3)['bias']
    map4 = align_index(m15.index, '15m', h4.index, '4h')
    map1 = align_index(m15.index, '15m', h1.index, '1h')

    # TRIGGER parts on 15m
    sw = sweeps(h15, l15, c15)
    ev, _ = detect_ms({'high': h15, 'low': l15, 'close': c15}, MSParams(strength=3),
                      atr=a15, times=(m15.index.astype('int64') // 1_000_000).values)
    breaks = {}
    for e in ev:
        breaks.setdefault(int(e.i), []).append(e)

    spread15 = m15['spread'].values
    out = []
    for i, evs in sorted(breaks.items()):
        if i < 300 or i >= len(c15) - HORIZON - 1:
            continue
        a = a15[i]
        if not np.isfinite(a) or a <= 0:
            continue
        for e in evs:
            side = 1 if e.direction == 'bullish' else -1

            # --- 4H CONTEXT ---------------------------------------------- #
            k4 = map4[i]
            if k4 < 0:
                continue
            bias = h4bias[k4]
            context_ok = ((side > 0 and bias == 'up')
                          or (side < 0 and bias == 'down'))
            if require_context and not context_ok:
                continue

            # --- 15M SWEEP, before the break ------------------------------ #
            lo_w = max(0, i - SWEEP_WINDOW)
            arr = sw['up' if side > 0 else 'dn'][lo_w:i + 1]
            if not arr.any():
                continue
            j = lo_w + int(np.where(arr)[0][-1])
            sweep_px = sw['lo'][j] if side > 0 else sw['hi'][j]
            if not np.isfinite(sweep_px):
                continue

            # --- 1H LOCATION ---------------------------------------------- #
            k1 = map1[i]
            if k1 < 200:
                continue
            h1cut = h1.iloc[:k1 + 1]
            t1 = (h1cut.index.astype('int64') // 1_000_000).values
            zs = zones.detect({'high': h1cut['high'].values, 'low': h1cut['low'].values,
                               'close': h1cut['close'].values},
                              k1, '1h', a1[:k1 + 1], times=t1)
            sd = supply_demand.detect({'high': h1cut['high'].values,
                                       'low': h1cut['low'].values,
                                       'close': h1cut['close'].values},
                                      '1h', a1[:k1 + 1], upto=k1, times=t1)
            px = c15[i]
            # A `Zone` is a BAND, not a level, and it has no kind: whether it is
            # support or resistance is decided by which side of it price is on.
            # For a long, "at support" means price is in the band or just above
            # its top -- the band has to be under the trade, or it is not what is
            # holding it up.
            wantsd = 'demand' if side > 0 else 'supply'
            at_zone = None
            for z in zs:
                edge = z.high if side > 0 else z.low
                below = (px >= z.low) if side > 0 else (px <= z.high)
                if below and abs(px - edge) <= NEAR_ATR * a:
                    at_zone = ('sr', float(edge), float(z.strength))
                    break
            if at_zone is None:
                for z in sd:
                    if z.kind != wantsd or z.broken:
                        continue
                    # the PROXIMAL edge is the one price meets first
                    edge = z.high if side > 0 else z.low
                    if abs(px - edge) <= NEAR_ATR * a:
                        at_zone = ('sd', float(edge), float(z.strength))
                        break
            location_ok = at_zone is not None
            if require_location and not location_ok:
                continue

            # --- TARGET: the next OPPOSING 1H level ----------------------- #
            # The opposing side is whatever sits AHEAD of the trade: its near
            # edge is where the other side starts, so that is the target rather
            # than the middle or the far side of the band.
            oppsd = 'supply' if side > 0 else 'demand'
            cands = []
            for z in zs:
                near_edge = z.low if side > 0 else z.high
                if (near_edge - px) * side > 0:
                    cands.append(float(near_edge))
            for z in sd:
                if z.kind != oppsd or z.broken:
                    continue
                near_edge = z.low if side > 0 else z.high
                if (near_edge - px) * side > 0:
                    cands.append(float(near_edge))
            ahead = [v for v in cands if (v - px) * side > 0]
            if not ahead:
                continue
            target = min(ahead) if side > 0 else max(ahead)

            # --- LEVELS, each with its reason ----------------------------- #
            entry = float(o15[i + 1])          # tradeable: the next bar's open
            stop = float(sweep_px - side * STOP_PAD_ATR * a)
            risk = (entry - stop) * side
            reward = (target - entry) * side
            if risk <= 0 or reward <= 0:
                continue
            rr = reward / risk
            if rr < MIN_RR:
                continue

            # --- outcome -------------------------------------------------- #
            r = None
            mfe = mae = 0.0
            held = 0
            for k in range(i + 1, min(len(c15), i + HORIZON + 1)):
                held = k - i
                mfe = max(mfe, (h15[k] - entry) * side / risk)
                mae = min(mae, (l15[k] - entry) * side / risk)
                if (side > 0 and l15[k] <= stop) or (side < 0 and h15[k] >= stop):
                    r = -1.0
                    break
                if (side > 0 and h15[k] >= target) or (side < 0 and l15[k] <= target):
                    r = rr
                    break
            if r is None:
                k = min(len(c15) - 1, i + HORIZON)
                r = ((c15[k] - entry) * side) / risk

            fric = spread15[i] * 0.01 / risk + 2 * 0.02 * a / risk
            out.append({
                'i': int(i), 't': str(m15.index[i]), 'side': int(side),
                'era': era[0] if era else 'all',
                'context_bias': str(bias), 'context_ok': bool(context_ok),
                'location_ok': bool(location_ok),
                'location_kind': at_zone[0] if at_zone else 'none',
                'location_score': at_zone[2] if at_zone else 0.0,
                'sweep_bars_before': int(i - j),
                'trigger_kind': str(e.kind),
                'entry': entry, 'stop': stop, 'target': float(target),
                'rr': float(rr), 'risk_atr': float(risk / a),
                'result_R': float(r), 'net_R': float(r - fric),
                'mfe_R': float(mfe), 'mae_R': float(mae),
                'bars_held': int(held), 'friction_R': float(fric),
                'hour': int(m15.index[i].hour),
            })
    return out


def summarise(rows, label):
    if not rows:
        return {'cell': label, 'n': 0}
    net = np.array([x['net_R'] for x in rows])
    res = np.array([x['result_R'] for x in rows])
    rr = np.array([x['rr'] for x in rows])
    rng = np.random.default_rng(7)
    if len(net) >= 10:
        idx = rng.integers(0, len(net), size=(2000, len(net)))
        lo, hi = np.percentile(net[idx].mean(axis=1), [2.5, 97.5])
    else:
        lo = hi = float('nan')
    return {'cell': label, 'n': len(rows),
            'gross_R': float(res.mean()), 'net_R': float(net.mean()),
            'lo': float(lo), 'hi': float(hi),
            'win_pct': float((res > 0).mean() * 100),
            'avg_rr': float(rr.mean()),
            'avg_mfe': float(np.mean([x['mfe_R'] for x in rows])),
            'avg_mae': float(np.mean([x['mae_R'] for x in rows])),
            'avg_bars': float(np.mean([x['bars_held'] for x in rows]))}


def anatomy(rows):
    """What separates winners from losers -- reported, not acted on."""
    if len(rows) < 40:
        return None
    win = [x for x in rows if x['result_R'] > 0]
    lose = [x for x in rows if x['result_R'] <= 0]
    feats = {
        '4H context aligned': lambda x: x['context_ok'],
        '1H at location': lambda x: x['location_ok'],
        'rr >= 2': lambda x: x['rr'] >= 2,
        'rr >= 3': lambda x: x['rr'] >= 3,
        'location is S/R': lambda x: x['location_kind'] == 'sr',
        'location is supply/demand': lambda x: x['location_kind'] == 'sd',
        'trigger is bos': lambda x: x['trigger_kind'] == 'bos',
        'sweep within 4 bars': lambda x: x['sweep_bars_before'] <= 4,
        'risk under 1 ATR': lambda x: x['risk_atr'] < 1.0,
        'long': lambda x: x['side'] > 0,
    }
    out = []
    for name, f in feats.items():
        w = np.mean([f(x) for x in win]) if win else float('nan')
        l = np.mean([f(x) for x in lose]) if lose else float('nan')
        out.append({'feature': name, 'winners_pct': 100 * w, 'losers_pct': 100 * l,
                    'gap_pp': 100 * (w - l)})
    out.sort(key=lambda x: -abs(x['gap_pp']))
    return {'n_win': len(win), 'n_lose': len(lose), 'features': out}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('symbol', nargs='?', default='XAUUSD.a')
    args = ap.parse_args()

    out = {'symbol': args.symbol, 'setup': 'mtf_sweep_bos', 'cells': []}
    per_era = {}
    wide = {}
    for era in ERAS:
        rows = find_setups(args.symbol, era)
        per_era[era[0]] = rows
        out['cells'].append(summarise(rows, era[0]))
        # the ungated population: same geometry, gates recorded not enforced
        wide[era[0]] = find_setups(args.symbol, era, require_context=False,
                                   require_location=False)
    # THE ANATOMY IS DERIVED ON ONE ERA ONLY, over the population that VARIES.
    out['anatomy_2011_2020'] = anatomy(wide.get('2011-2020', []))
    # and what each gate is worth, on that same single era
    d = wide.get('2011-2020', [])
    out['gate_value_2011_2020'] = []
    for name, f in [('no gates', lambda x: True),
                    ('context only', lambda x: x['context_ok']),
                    ('location only', lambda x: x['location_ok']),
                    ('both gates', lambda x: x['context_ok'] and x['location_ok'])]:
        out['gate_value_2011_2020'].append(summarise([x for x in d if f(x)], name))
    # THE TEST: the same four arms on the era the choice never saw
    t = wide.get('2021-2026', [])
    out['gate_value_2021_2026'] = []
    for name, f in [('no gates', lambda x: True),
                    ('context only', lambda x: x['context_ok']),
                    ('location only', lambda x: x['location_ok']),
                    ('both gates', lambda x: x['context_ok'] and x['location_ok'])]:
        out['gate_value_2021_2026'].append(summarise([x for x in t if f(x)], name))
    print(json.dumps(out, indent=1, default=float))


if __name__ == '__main__':
    main()
