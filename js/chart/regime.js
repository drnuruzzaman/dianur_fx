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

/** Just the final bar — what the Trend read panel needs. */
export function latest(bars, opts = {}) {
  if (!bars || bars.length < 60) return null;
  const r = compute(bars, opts);
  const i = bars.length - 1;
  return { regime: r.regime[i], direction: r.direction[i],
           rangePos: r.rangePos[i], energy: r.energy[i], emaSepAtr: r.emaSepAtr[i] };
}
