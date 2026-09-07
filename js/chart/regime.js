/* regime.js — market regime per timeframe: trending up, trending down,
 * sideways, or in transition.
 *
 * A port of sim/tl/regime.py, parity-tested in tests/test_structure_parity.py.
 * Three causal readings, combined:
 *
 *   SLOPE    EMA(fast) vs EMA(slow), normalised by ATR so it means the same
 *            thing on gold and on yen.
 *   BREADTH  where price sits inside its own recent range. A market pinned
 *            mid-range is sideways however the EMAs look.
 *   ENERGY   ATR now vs ATR over a longer window. A contracting range that is
 *            still directional is TRANSITION, not TREND.
 */

import { atrSeries } from './tlengine.js';

export const TRENDING_UP = 'trending_up';
export const TRENDING_DOWN = 'trending_down';
export const SIDEWAYS = 'sideways';
export const TRANSITION = 'transition';

export const DIR_UP = 'up', DIR_DOWN = 'down', DIR_FLAT = 'flat';

/* EMA seeded with an SMA of the first `length` values — the same explicit
   seeding as sim/indicators.py, because the default recursive seed differs by
   enough to move a regime boundary. */
function ema(values, length) {
  const out = new Array(values.length).fill(NaN);
  if (values.length < length) return out;
  const k = 2 / (length + 1);
  let prev = 0;
  for (let j = 0; j < length; j++) prev += values[j];
  prev /= length;
  out[length - 1] = prev;
  for (let i = length; i < values.length; i++) {
    prev = values[i] * k + prev * (1 - k);
    out[i] = prev;
  }
  return out;
}

export function compute(bars, {
  fast = 21, slow = 50, rangeWindow = 40, atrLen = 14,
  slopeAtr = 0.35, band = 0.22, squeeze = 0.75,
} = {}) {
  const n = bars.length;
  const close = bars.map((b) => b.c);
  const ef = ema(close, fast);
  const es = ema(close, slow);
  const a = atrSeries(bars, atrLen);

  const sep = new Array(n).fill(NaN);
  for (let i = 0; i < n; i++) if (a[i] > 0) sep[i] = (ef[i] - es[i]) / a[i];

  const pos = new Array(n).fill(NaN);
  for (let i = rangeWindow - 1; i < n; i++) {
    let hi = -Infinity, lo = Infinity;
    for (let j = i - rangeWindow + 1; j <= i; j++) {
      if (bars[j].h > hi) hi = bars[j].h;
      if (bars[j].l < lo) lo = bars[j].l;
    }
    pos[i] = hi === lo ? 0.5 : (close[i] - lo) / (hi - lo);
  }

  const w = atrLen * 4;
  const energy = new Array(n).fill(NaN);
  for (let i = w - 1; i < n; i++) {
    let sum = 0, cnt = 0;
    for (let j = i - w + 1; j <= i; j++) if (Number.isFinite(a[j])) { sum += a[j]; cnt++; }
    const longAtr = cnt ? sum / cnt : NaN;
    if (longAtr > 0) energy[i] = a[i] / longAtr;
  }

  const regime = new Array(n).fill(TRANSITION);
  const direction = new Array(n).fill(DIR_FLAT);
  for (let i = 0; i < n; i++) {
    const s = sep[i], p = pos[i], e = energy[i];
    if (!Number.isFinite(s) || !Number.isFinite(p)) continue;
    direction[i] = s > 0.08 ? DIR_UP : s < -0.08 ? DIR_DOWN : DIR_FLAT;

    const strong = Math.abs(s) >= slopeAtr;
    const midRange = p >= 0.5 - band && p <= 0.5 + band;
    const contracting = Number.isFinite(e) && e < squeeze;

    if (strong && !midRange && !contracting) regime[i] = s > 0 ? TRENDING_UP : TRENDING_DOWN;
    else if (!strong && midRange) regime[i] = SIDEWAYS;
    else regime[i] = TRANSITION;
  }

  return { regime, direction, emaFast: ef, emaSlow: es, atr: a,
           emaSepAtr: sep, rangePos: pos, energy };
}

/* ------------------------------------------------------------------------
 * THE SAME READINGS, ON INDEPENDENT AXES.
 *
 * WHY. The four labels above are mutually exclusive, and markets are not. A
 * bull trend IS simultaneously in a pullback, at some volatility, possibly on a
 * structure event -- and forcing one label discards the other three readings.
 * That is not a theoretical complaint: `trending_up` covers 50% of this rule's
 * trades on 15m and 51% on 5m, which makes it a bucket so broad it cannot
 * separate anything, and measuring it as an entry gate returned p = 0.334
 * against a best-bucket null. A coarse bucket is one honest explanation for
 * that result, and this is the way to test it.
 *
 * NOTHING ABOVE CHANGES. `compute` and `latest` keep their four states and
 * their exact thresholds, because five surfaces read them -- read.js,
 * segments.js, rulepanel.js, strategyreplay.js, trendread.js -- and a silent
 * change to what `sideways` means would move the chart while nothing said so.
 * These are additional views over the SAME three causal series.
 *
 * EVENT IS DELIBERATELY NOT HERE. BOS and CHoCH live in marketstructure.js, a
 * channel break is what donchian.js already computes, and a sweep is
 * liquidity.js. Re-deriving any of them here would be a second implementation
 * of a detector that already has one, which is how the ATR divergence got in.
 * The event axis is composed at the dataset layer from those three.
 * ---------------------------------------------------------------------- */

export const BULL = 'bull', BEAR = 'bear', NEUTRAL = 'neutral';
export const IMPULSE = 'impulse', PULLBACK = 'pullback', CORRECTION = 'correction';
export const RANGE = 'range', TRANSITIONAL = 'transitional';
export const VOL_LOW = 'low', VOL_NORMAL = 'normal', VOL_HIGH = 'high',
             VOL_EXTREME = 'extreme';

/* Every threshold in one place and none of them claimed to be optimal. They
   are starting values to be swept, which is the only honest status for a
   constant nobody has measured yet. */
export const DIMS_CONFIG = {
  dirGap: 0.5,          // |EMA21-EMA50| / ATR above which a direction is called
  impulseAtr: 0.5,      // give-back from the running extreme, in ATR
  correctionAtr: 1.5,   // beyond this it is a correction, not a pullback
  extremeLookback: 40,  // bars the running extreme is measured over
  volLow: 0.75, volHigh: 1.25, volExtreme: 2.0,   // ATR-now / ATR-56 bands
  band: 0.22,           // mid-range half-width, as in compute()
};

/**
 * Direction, phase and volatility per bar, as separate axes.
 *
 * PHASE IS MEASURED AS GIVE-BACK FROM A RUNNING EXTREME, in ATR: how far price
 * has come off the highest high of the last `extremeLookback` bars when the
 * direction is bull, and off the lowest low when bear. Shallow is an impulse,
 * middling is a pullback, deep is a correction. The extreme window ENDS at the
 * current bar, so nothing here reads forward.
 *
 * A direction is required before a phase means anything: with no direction
 * there is no trend to pull back from, so the phase is RANGE when price is
 * pinned mid-range and TRANSITIONAL otherwise.
 */
export function dimensions(bars, opts = {}) {
  const p = { ...DIMS_CONFIG, ...opts };
  const r = compute(bars, opts);
  const n = bars.length;
  const direction = new Array(n).fill(NEUTRAL);
  const phase = new Array(n).fill(TRANSITIONAL);
  const volatility = new Array(n).fill(VOL_NORMAL);
  const giveBack = new Array(n).fill(NaN);

  for (let i = 0; i < n; i++) {
    const sep = r.emaSepAtr[i], pos = r.rangePos[i], e = r.energy[i], a = r.atr[i];
    if (!Number.isFinite(sep)) continue;

    direction[i] = sep >= p.dirGap ? BULL : sep <= -p.dirGap ? BEAR : NEUTRAL;

    volatility[i] = !Number.isFinite(e) ? VOL_NORMAL
      : e < p.volLow ? VOL_LOW
      : e < p.volHigh ? VOL_NORMAL
      : e < p.volExtreme ? VOL_HIGH : VOL_EXTREME;

    if (direction[i] === NEUTRAL) {
      phase[i] = Number.isFinite(pos) && pos >= 0.5 - p.band && pos <= 0.5 + p.band
        ? RANGE : TRANSITIONAL;
      continue;
    }
    if (!(a > 0)) continue;

    const lo = Math.max(0, i - p.extremeLookback + 1);
    let ext = direction[i] === BULL ? -Infinity : Infinity;
    for (let k = lo; k <= i; k++) {
      if (direction[i] === BULL) { if (bars[k].h > ext) ext = bars[k].h; }
      else if (bars[k].l < ext) ext = bars[k].l;
    }
    const g = direction[i] === BULL ? (ext - bars[i].c) / a : (bars[i].c - ext) / a;
    giveBack[i] = g;
    phase[i] = g < p.impulseAtr ? IMPULSE
      : g < p.correctionAtr ? PULLBACK : CORRECTION;
  }

  return { direction, phase, volatility, giveBackAtr: giveBack,
           emaSepAtr: r.emaSepAtr, rangePos: r.rangePos, energy: r.energy,
           atr: r.atr, regime: r.regime };
}

/** The dimensional read for the final bar. */
export function latestDimensions(bars, opts = {}) {
  if (!bars || bars.length < 60) return null;
  const d = dimensions(bars, opts);
  const i = bars.length - 1;
  return { direction: d.direction[i], phase: d.phase[i],
           volatility: d.volatility[i], giveBackAtr: d.giveBackAtr[i],
           emaSepAtr: d.emaSepAtr[i], rangePos: d.rangePos[i], energy: d.energy[i] };
}

/** Just the final bar — what the Trend read panel needs. */
export function latest(bars, opts = {}) {
  if (!bars || bars.length < 60) return null;
  const r = compute(bars, opts);
  const i = bars.length - 1;
  return { regime: r.regime[i], direction: r.direction[i],
           rangePos: r.rangePos[i], energy: r.energy[i], emaSepAtr: r.emaSepAtr[i] };
}
