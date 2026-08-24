/* zones.js — horizontal support/resistance BANDS, not lines.
 *
 * A port of sim/tl/zones.py, compared zone-for-zone in tests/test_zone_parity.py.
 *
 * A line is a claim about one price. A zone is a claim about a region, and it is
 * the more honest object: price does not turn at 1.16847, it turns somewhere in
 * a band a few tenths of an ATR wide — which is how every hand-annotated chart
 * draws it ("Supply", "Demand", "Seller Zone", "Buyer Zone").
 *
 * ROLE IS ASSIGNED FROM PRICE, not from the pivot type. A zone built from swing
 * highs is resistance while price is below it and support once price is above
 * it; hard-coding the role from its pivots would throw that flip away.
 */

import { atrSeries } from './tlengine.js';
import { findPivots } from './trendlines.js';

export const SUPPORT = 'support';
export const RESISTANCE = 'resistance';

export const DEFAULT_ZONE_PARAMS = {
  strengthPivots: 3,
  lookback: 500,
  clusterAtr: 0.35,
  minTouches: 3,
  minSeparation: 8,
  maxWidthAtr: 1.2,
  maxZones: 6,
  minStrength: 25,
  maxDistanceAtr: 12,
};

const round2 = (v) => Math.round(v * 100) / 100;

export class Zone {
  constructor(o) { Object.assign(this, o); }

  get mid() { return 0.5 * (this.low + this.high); }

  contains(price) { return price >= this.low && price <= this.high; }

  /** Resistance below, support above; inside the band the nearer edge decides. */
  roleAt(price) {
    if (price < this.low) return RESISTANCE;
    if (price > this.high) return SUPPORT;
    return (price - this.low) > (this.high - price) ? SUPPORT : RESISTANCE;
  }

  distanceAtr(price, atr) {
    if (!atr) return NaN;
    if (this.contains(price)) return 0;
    const d = price < this.low ? this.low - price : price - this.high;
    return d / atr;
  }
}

function score(touches, spanBars, widthAtr, lookback, distAtr, p) {
  const touchPts = Math.min(35, (touches - 2) * 12 + 11);
  const spanPts = Math.min(20, 20 * (spanBars / Math.max(lookback, 1)));
  const tightPts = p.maxWidthAtr <= 0 ? 0
    : 25 * Math.max(0, 1 - (widthAtr / p.maxWidthAtr));
  const proxPts = (p.maxDistanceAtr <= 0 || !Number.isFinite(distAtr)) ? 0
    : 20 * Math.max(0, 1 - (distAtr / p.maxDistanceAtr));
  return round2(Math.max(0, Math.min(100, touchPts + spanPts + tightPts + proxPts)));
}

/** Agglomerate a price-sorted list, breaking wherever the gap exceeds `tol`. */
function cluster(levels, tol) {
  if (!levels.length) return [];
  const sorted = [...levels].sort((a, b) => a.price - b.price);
  const groups = [[sorted[0]]];
  for (const lv of sorted.slice(1)) {
    const g = groups[groups.length - 1];
    if (lv.price - g[g.length - 1].price <= tol) g.push(lv);
    else groups.push([lv]);
  }
  return groups;
}

/**
 * Zones visible at bar `i`, strongest first. Only pivots CONFIRMED by bar `i`
 * are used, so a swing is never counted before it became visible.
 */
export function detect(bars, i, timeframe, atrArr, params = {}) {
  const p = { ...DEFAULT_ZONE_PARAMS, ...params };
  const a = (i < atrArr.length && Number.isFinite(atrArr[i])) ? atrArr[i] : 0;
  if (a <= 0) return [];

  const lastClose = bars[i].c;
  const i0 = Math.max(0, i - p.lookback);
  const { highs, lows } = findPivots(bars, p.strengthPivots);

  /* findPivots does not carry confirmedI — it is shared with the batch scorer
     and must stay byte-identical for the other parity tests — so the confirming
     bar is derived here: it is i + strength by definition. */
  const conf = (q) => q.i + p.strengthPivots <= i && q.i >= i0;
  const levels = [
    ...highs.filter(conf).map((q) => ({ price: q.price, i: q.i, kind: 'high' })),
    ...lows.filter(conf).map((q) => ({ price: q.price, i: q.i, kind: 'low' })),
  ];
  if (levels.length < p.minTouches) return [];

  const times = bars.map((b) => b.t);
  const out = [];
  let seq = 0;

  for (const g of cluster(levels, p.clusterAtr * a)) {
    const gs = [...g].sort((x, y) => x.i - y.i);
    const kept = [];
    for (const lv of gs) {
      if (!kept.length || lv.i - kept[kept.length - 1].i >= p.minSeparation) kept.push(lv);
    }
    if (kept.length < p.minTouches) continue;
    const prices = kept.map((x) => x.price);
    const lo = Math.min(...prices), hi = Math.max(...prices);
    const widthAtr = (hi - lo) / a;
    if (widthAtr > p.maxWidthAtr) continue;
    const firstI = kept[0].i, lastI = kept[kept.length - 1].i;
    seq++;
    const z = new Zone({
      id: `${timeframe}-Z-${seq}`, timeframe, low: lo, high: hi,
      touches: kept.length, firstI, lastI,
      firstT: times[firstI], lastT: times[lastI],
      widthAtr,
      levels: prices.map((x) => Math.round(x * 1e10) / 1e10),
      fromHighs: kept.filter((x) => x.kind === 'high').length,
      fromLows: kept.filter((x) => x.kind === 'low').length,
    });
    const dist = z.distanceAtr(lastClose, a);
    if (Number.isFinite(dist) && dist > p.maxDistanceAtr) continue;
    z.strength = score(z.touches, lastI - firstI, widthAtr, p.lookback, dist, p);
    if (z.strength < p.minStrength) continue;
    out.push(z);
  }

  out.sort((x, y) => y.strength - x.strength);
  return out.slice(0, p.maxZones);
}

/** Zones for a chart's own bars — the entry point main.js uses. */
export function liveZones(bars, timeframe, params = {}) {
  if (!bars || bars.length < 60) return [];
  return detect(bars, bars.length - 1, timeframe, atrSeries(bars, 14), params);
}
