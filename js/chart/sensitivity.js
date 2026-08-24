/* sensitivity.js — per-instrument detection and validation thresholds.
 *
 *     sensitivity = f(pivot_window, ATR_prominence, volatility_regime)
 *
 * A port of sim/tl/sensitivity.py, compared field-for-field in
 * tests/test_sensitivity_parity.py.
 *
 * WHAT THIS IS AND IS NOT, because the measurements are unusually clear:
 *
 * An ablation over three disjoint eras (1999-2010, 2011-2020, 2021-2026) took
 * the three components apart. Pooled placebo-adjusted edge, 70k -> 34k paired
 * approaches:
 *
 *     default (strength 3, no prominence)      -0.17 pp   z -0.70
 *     wider window ALONE                       -0.60 pp   z -2.31
 *     prominence ALONE                         -0.32 pp   z -1.17
 *     window + prominence                      +0.64 pp   z +2.12
 *     + volatility regime          <-- shipped +0.85 pp   z +2.48
 *
 * Neither the wider window nor the prominence bar helps on its own; both are
 * WORSE than doing nothing. They only work together, and the reason is
 * mechanical: prominence is measured over a +/-strength window, so at
 * strength 3 the depth measure spans 7 bars and barely discriminates. The wider
 * window is what makes the prominence measurement mean something. Changing one
 * without the other is not a partial improvement, it is a regression.
 *
 * PER-SIDE ASYMMETRY IS OFF BY DEFAULT. Support lines carry a measured
 * +2.37 pp placebo-adjusted edge against resistance's +0.20 pp (z 6.96 vs 0.57,
 * 71k approaches), so the two sides genuinely behave differently. Acting on it
 * does NOT replicate: the asymmetric arm contributed +1.10 pp in one era,
 * -0.85 in the next and -0.13 in the third, and it was the only variant that
 * turned a positive era negative. Knowing the sides differ is not the same as
 * knowing how to exploit it. The parameters are kept, defaulted off.
 *
 * AND THE HONEST CEILING: +0.85 pp makes the detector LESS BAD, not good. The
 * default arm is negative; this cancels that. Nothing here is an edge.
 */

import { atrSeries } from './tlengine.js';
import { findPivots } from './trendlines.js';

/* Base pivot window per timeframe. Higher timeframes carry fewer, larger
   swings, so a wider window costs little and rejects more noise. */
export const BASE_STRENGTH = {
  '1m': 3, '5m': 3, '15m': 3, '30m': 4,
  '1h': 4, '4h': 5, '1d': 5, '1w': 5,
};

export const CALM = 'calm', NORMAL = 'normal', HIGH = 'high', EXTREME = 'extreme';

export const DEFAULT_SENSITIVITY_PARAMS = {
  prominencePct: 40,        // drop the least prominent N% of swings
  atrWindow: 14,
  volLookback: 500,
  baseTolAtr: 0.32,
  baseMinQuality: 90,
  /* Both OFF: see the header. Set to 6 and 0.90 to re-enable the asymmetry. */
  resistanceQualityBonus: 0,
  resistanceTolScale: 1.0,
  minPivots: 40,
};

/** Where current ATR sits in its own recent distribution. */
export function volRegime(atr, i, lookback) {
  const lo = Math.max(0, i - lookback + 1);
  const seg = [];
  for (let j = lo; j <= i; j++) if (Number.isFinite(atr[j])) seg.push(atr[j]);
  if (seg.length < 30 || !Number.isFinite(atr[i])) return [NORMAL, 50];
  let below = 0;
  for (const v of seg) if (v < atr[i]) below++;
  const pct = (below / seg.length) * 100;
  if (pct >= 90) return [EXTREME, pct];
  if (pct >= 75) return [HIGH, pct];
  if (pct <= 25) return [CALM, pct];
  return [NORMAL, pct];
}

/**
 * Base window by timeframe, widened when the market is fast.
 *
 * The window grows rather than the threshold: widening it asks price to travel
 * further in TIME, which is what distinguishes a turn from a spike.
 */
export function strengthFor(tf, regime) {
  let s = BASE_STRENGTH[tf] === undefined ? 3 : BASE_STRENGTH[tf];
  if (regime === HIGH) s += 1;
  else if (regime === EXTREME) s += 2;
  return s;
}

/** Prominence of every pivot in ATR units, split by side: [highs, lows]. */
export function prominenceValues(bars, strength, upto = null, atr = null) {
  const n = upto === null ? bars.length : Math.min(upto + 1, bars.length);
  const a = atr || atrSeries(bars, 14);
  const { highs, lows } = findPivots(bars.slice(0, n), strength);

  const prom = (pivots, isHigh) => {
    const out = [];
    for (const p of pivots) {
      const i = p.i;
      const av = a[i];
      if (!Number.isFinite(av) || av <= 0) continue;
      const lo = Math.max(0, i - strength);
      const hi = Math.min(n, i + strength + 1);
      let d;
      if (isHigh) {
        let mn = Infinity;
        for (let j = lo; j < hi; j++) if (bars[j].l < mn) mn = bars[j].l;
        d = bars[i].h - mn;
      } else {
        let mx = -Infinity;
        for (let j = lo; j < hi; j++) if (bars[j].h > mx) mx = bars[j].h;
        d = mx - bars[i].l;
      }
      out.push(d / av);
    }
    return out;
  };
  return [prom(highs, true), prom(lows, false)];
}

/* Linear-interpolation percentile, matching numpy.percentile's default so the
   two languages agree on the threshold rather than on the method. */
export function percentile(values, pct) {
  if (!values.length) return 0;
  const v = [...values].sort((a, b) => a - b);
  if (v.length === 1) return v[0];
  const idx = (pct / 100) * (v.length - 1);
  const lo = Math.floor(idx), hi = Math.ceil(idx);
  if (lo === hi) return v[lo];
  return v[lo] + (v[hi] - v[lo]) * (idx - lo);
}

/**
 * Derive both sides' thresholds from this instrument's own behaviour.
 * Reads only bars at or before `upto` (default: the last bar).
 */
export function calibrate(bars, timeframe, symbol = '', params = {}, upto = null) {
  const p = { ...DEFAULT_SENSITIVITY_PARAMS, ...params };
  const i = upto === null ? bars.length - 1 : Math.min(upto, bars.length - 1);
  const atr = atrSeries(bars, p.atrWindow);

  const [regime, pct] = volRegime(atr, i, p.volLookback);
  const strength = strengthFor(timeframe, regime);
  const [hiProm, loProm] = prominenceValues(bars, strength, i, atr);

  const bar = (vals) => (vals.length < p.minPivots ? 0
    : percentile(vals, p.prominencePct));
  const hiBar = bar(hiProm), loBar = bar(loProm);

  /* A swing HIGH builds a resistance line; a swing LOW builds support. Naming
     the mapping is worth the line — getting it backwards is silent and the
     result still looks plausible. */
  return {
    symbol, timeframe, volRegime: regime, atrPct: pct,
    support: {
      side: 'support', strength, minProminenceAtr: loBar,
      tolAtr: p.baseTolAtr, minQuality: p.baseMinQuality,
    },
    resistance: {
      side: 'resistance', strength, minProminenceAtr: hiBar,
      tolAtr: p.baseTolAtr * p.resistanceTolScale,
      minQuality: Math.min(100, p.baseMinQuality + p.resistanceQualityBonus),
    },
    nPivots: hiProm.length + loProm.length,
  };
}

/**
 * Prominence of ONE bar treated as a pivot, in ATR units.
 *
 * Used by liveLines to ask whether a line's anchors would clear the strict
 * calibration's bar, without re-running the engine at that calibration. The
 * engine itself filters at the pivot stage, so it has no need of this -- which
 * is why there is no Python counterpart and nothing to keep in parity.
 */
export function pivotProminence(bars, i, strength, isHigh, atr) {
  const a = atr[i];
  if (!Number.isFinite(a) || a <= 0) return -1;
  const lo = Math.max(0, i - strength);
  const hi = Math.min(bars.length, i + strength + 1);
  if (isHigh) {
    let mn = Infinity;
    for (let j = lo; j < hi; j++) if (bars[j].l < mn) mn = bars[j].l;
    return (bars[i].h - mn) / a;
  }
  let mx = -Infinity;
  for (let j = lo; j < hi; j++) if (bars[j].h > mx) mx = bars[j].h;
  return (mx - bars[i].l) / a;
}

/** The side block for a role, matching Sensitivity.for_role in Python. */
export function forRole(sens, role) {
  return role === 'support' ? sens.support : sens.resistance;
}
