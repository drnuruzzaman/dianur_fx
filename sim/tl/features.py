"""
features.py — build the per-candle feature table from the spec.

Two separate data structures, as the spec asks:

  * TRENDLINE objects — one row per line, its own lifecycle and score
    (sim/tl/lines.py -> Trendline.to_row()).
  * PER-CANDLE features — one row per execution bar, referencing lines by id.

15M is the execution timeframe; 1H/4H/D1 are context, projected through
sim/tl/mtf.py so no unclosed higher-timeframe candle can ever be read.

Distances are in ATR of the execution timeframe, not price: 30 points means
something different on gold and on yen, 0.4 ATR does not.
"""

import numpy as np
import pandas as pd

from ..indicators import atr as atr_series
from . import regime as regime_mod
from .engine import Params, TrendlineEngine
from .mtf import MTFContext, TF_MS

CONTEXT_DEFAULT = ('1h', '4h', '1d')


def build_timeframe_state(bars, tf, params: Params = None):
    """
    Run the trendline engine and the regime detector over one timeframe.
    Returns per-bar arrays, all causal, plus every line the engine created.
    """
    eng = TrendlineEngine(tf, TF_MS[tf], params)
    snaps = eng.walk(bars)
    reg, direction, feats = regime_mod.compute(bars)
    close = np.asarray(bars['close'], dtype=float)
    a = feats['atr']

    n = len(bars)
    sup_dist = np.full(n, np.nan)
    res_dist = np.full(n, np.nan)
    near_dist = np.full(n, np.nan)
    strength = np.full(n, np.nan)
    sup_id = np.full(n, None, dtype=object)
    res_id = np.full(n, None, dtype=object)
    sup_px = np.full(n, np.nan)
    res_px = np.full(n, np.nan)
    live_count = np.zeros(n)
    # break events, needed by a breakout strategy: once a resistance line is
    # broken it sits BELOW price, so it stops being offered as resistance and
    # would otherwise vanish from the feature row on the very bar that matters
    broke_res = np.zeros(n)
    broke_sup = np.zeros(n)
    broke_res_px = np.full(n, np.nan)
    broke_sup_px = np.full(n, np.nan)
    broke_res_q = np.full(n, np.nan)
    broke_sup_q = np.full(n, np.nan)

    for i, s in enumerate(snaps):
        atr_i = a[i] if a[i] and not np.isnan(a[i]) else np.nan
        best_strength = 0.0
        # Frozen snapshot scalars only. Reading s.support.quality_score here
        # would give the score the line eventually reached, not the score at
        # this bar — a look-ahead leak that tests/test_lookahead.py catches.
        if s.support_id is not None:
            sup_px[i] = s.support_px
            sup_dist[i] = (close[i] - s.support_px) / atr_i if atr_i else np.nan
            sup_id[i] = s.support_id
            best_strength = max(best_strength, s.support_q)
        if s.resistance_id is not None:
            res_px[i] = s.resistance_px
            res_dist[i] = (s.resistance_px - close[i]) / atr_i if atr_i else np.nan
            res_id[i] = s.resistance_id
            best_strength = max(best_strength, s.resistance_q)
        for role, price, q in s.breaks:
            if role == 'resistance':
                broke_res[i] = 1
                broke_res_px[i] = price
                broke_res_q[i] = q
            else:
                broke_sup[i] = 1
                broke_sup_px[i] = price
                broke_sup_q[i] = q
        candidates = [d for d in (sup_dist[i], res_dist[i]) if not np.isnan(d)]
        near_dist[i] = min(np.abs(candidates)) if candidates else np.nan
        strength[i] = best_strength if best_strength else np.nan
        live_count[i] = s.live_count

    # confirmed swing pivots, for divergence work. `confirmed_i` is the bar the
    # pivot became visible, never the bar it happened on.
    from .pivots import find_pivots
    ph, pl = find_pivots(bars['high'], bars['low'], (params or Params()).strength)
    piv_hi = np.full(n, np.nan)
    piv_lo = np.full(n, np.nan)
    piv_hi_i = np.full(n, np.nan)
    piv_lo_i = np.full(n, np.nan)
    for p in ph:
        if p['confirmed_i'] < n:
            piv_hi[p['confirmed_i']] = p['price']
            piv_hi_i[p['confirmed_i']] = p['i']
    for p in pl:
        if p['confirmed_i'] < n:
            piv_lo[p['confirmed_i']] = p['price']
            piv_lo_i[p['confirmed_i']] = p['i']

    return {
        'snapshots': snaps,
        'regime': reg,
        'resistance_broken': broke_res,
        'support_broken': broke_sup,
        'broken_resistance_price': broke_res_px,
        'broken_support_price': broke_sup_px,
        'broken_resistance_quality': broke_res_q,
        'broken_support_quality': broke_sup_q,
        'pivot_high_price': piv_hi,
        'pivot_low_price': piv_lo,
        'pivot_high_i': piv_hi_i,
        'pivot_low_i': piv_lo_i,
        'trend_direction': direction,
        'support_distance': sup_dist,
        'resistance_distance': res_dist,
        'nearest_trendline_distance': near_dist,
        'trendline_strength': strength,
        'support_trendline_id': sup_id,
        'resistance_trendline_id': res_id,
        'support_price': sup_px,
        'resistance_price': res_px,
        'live_lines': live_count,
        'atr': a,
        'ema_fast': feats['ema_fast'],
        'ema_slow': feats['ema_slow'],
        'range_pos': feats['range_pos'],
    }


def build(bars_by_tf: dict, exec_tf='15m', context=CONTEXT_DEFAULT,
          params: Params = None, strict=True):
    """
    The full per-candle feature table.

    bars_by_tf: {'15m': df, '1h': df, '4h': df, '1d': df}
    Returns (features DataFrame, {tf: state}, lines DataFrame).
    """
    if exec_tf not in bars_by_tf:
        raise KeyError(f'no bars for the execution timeframe {exec_tf}')
    states = {}
    for tf in (exec_tf, *context):
        if tf in bars_by_tf:
            states[tf] = build_timeframe_state(bars_by_tf[tf], tf, params)

    ex = bars_by_tf[exec_tf]
    out = pd.DataFrame({
        'timestamp': ex.index,
        'open': np.asarray(ex['open'], float),
        'high': np.asarray(ex['high'], float),
        'low': np.asarray(ex['low'], float),
        'close': np.asarray(ex['close'], float),
    }).set_index('timestamp')

    # execution timeframe: read directly
    s = states[exec_tf]
    prefix = exec_tf.replace('m', 'm').lower()
    for name in ('trend_direction', 'regime', 'support_distance', 'resistance_distance',
                 'nearest_trendline_distance', 'trendline_strength',
                 'support_trendline_id', 'resistance_trendline_id'):
        out[f'{prefix}_{name}'] = s[name]
    for name in ('resistance_broken', 'support_broken', 'broken_resistance_price',
                 'broken_support_price', 'broken_resistance_quality',
                 'broken_support_quality', 'pivot_high_price', 'pivot_low_price',
                 'pivot_high_i', 'pivot_low_i'):
        out[f'{prefix}_{name}'] = s[name]
    out[f'{prefix}_support_price'] = s['support_price']
    out[f'{prefix}_resistance_price'] = s['resistance_price']
    out[f'{prefix}_range_pos'] = s['range_pos']
    out[f'{prefix}_atr'] = s['atr']
    out[f'{prefix}_ema_fast'] = s['ema_fast']
    out[f'{prefix}_ema_slow'] = s['ema_slow']

    # context timeframes: projected through the closed-bar-only alignment
    ctx = MTFContext(exec_tf, ex.index, strict=strict)
    for tf in context:
        if tf not in states:
            continue
        st = states[tf]
        htf = bars_by_tf[tf]
        cols = {
            'trend_direction': st['trend_direction'],
            'regime': st['regime'],
            'support_distance': st['support_distance'],
            'resistance_distance': st['resistance_distance'],
            'nearest_trendline_distance': st['nearest_trendline_distance'],
            'trendline_strength': st['trendline_strength'],
            'support_trendline_id': st['support_trendline_id'],
            'resistance_trendline_id': st['resistance_trendline_id'],
            'support_price': st['support_price'],
            'resistance_price': st['resistance_price'],
            'resistance_broken': st['resistance_broken'],
            'support_broken': st['support_broken'],
        }
        ctx.add(tf, htf.index, cols)
        key = 'd1' if tf == '1d' else tf
        for name in cols:
            out[f'{key}_{name}'] = ctx.get(tf, name)
        out[f'{key}_bar_index'] = ctx.get(tf, '_htf_bar_index')

    # distances to a context line must be re-expressed against the CURRENT
    # execution price: the HTF value was computed at the HTF close, and price has
    # moved since. Recomputing here keeps the projection honest.
    ex_atr = states[exec_tf]['atr']
    for tf in context:
        key = 'd1' if tf == '1d' else tf
        if f'{key}_support_price' not in out:
            continue
        with np.errstate(invalid='ignore'):
            out[f'{key}_support_distance'] = (out['close'] - out[f'{key}_support_price']) / ex_atr
            out[f'{key}_resistance_distance'] = (out[f'{key}_resistance_price'] - out['close']) / ex_atr
            out[f'{key}_nearest_trendline_distance'] = np.minimum(
                out[f'{key}_support_distance'].abs(), out[f'{key}_resistance_distance'].abs())

    lines = pd.DataFrame([l.to_row()
                          for tf in states
                          for l in _all_lines(states[tf]['snapshots'])])
    return out, states, lines


def _all_lines(snapshots):
    seen, out = set(), []
    for s in snapshots:
        for l in s.live:
            if l.id not in seen:
                seen.add(l.id)
                out.append(l)
    return out
