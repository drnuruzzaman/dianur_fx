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
  /* 2 rather than 3: a clean double top IS a level a human draws, and score()
     already gives it 11 points against 35 for four touches, so a weak zone
     loses on merit instead of being excluded before it can compete. */
  minTouches: 2,
  minSeparation: 8,
  maxWidthAtr: 1.2,
  maxZones: 6,
  minStrength: 25,
  /* How far price travelled AWAY from a pivot before turning back, in ATR. A
     level that produced 3 ATR bounces is not the same object as one price
     grazed and drifted from, and a raw touch count cannot tell them apart. */
  reactionBars: 20,
  reactionFullAtr: 2.0,
  /* "12 ATR" meant 19% of the actual price range on USDJPY 1h and 54% on
     XAUUSD 4h -- a trader draws what is ON THE CHART, not a count of ATR. The
     allowance is the LARGER of the ATR budget and a fraction of the range price
     actually covered; the ATR term is a floor for unusually quiet windows. */
  maxDistanceAtr: 12,
  maxDistanceRange: 0.75,
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

/* Median excursion away from a zone's pivots, in ATR. Median, not mean, so one
   violent bounce cannot carry a level that otherwise did nothing.
   CAUSAL: every bar read lies between the pivot and `i`, and the window is
   clipped at `i` so a recent pivot is not credited with an excursion that has
   not happened yet. */
function reactionAtr(kept, bars, atr, i, p) {
  const out = [];
  // JS `kept` holds {price, i, kind} objects; the Python mirror uses tuples
  for (const lv of kept) {
    const price = lv.price, k = lv.i, kind = lv.kind;
    const a = k < atr.length ? atr[k] : NaN;
    if (!(Number.isFinite(a) && a > 0)) continue;
    const end = Math.min(i, k + p.reactionBars);
    if (end <= k) continue;
    let move;
    if (kind === 'low') {
      let hi = -Infinity;
      for (let m = k + 1; m <= end; m++) if (bars[m].h > hi) hi = bars[m].h;
      move = hi - price;
    } else {
      let lo = Infinity;
      for (let m = k + 1; m <= end; m++) if (bars[m].l < lo) lo = bars[m].l;
      move = price - lo;
    }
    out.push(Math.max(0, move) / a);
  }
  if (!out.length) return NaN;
  out.sort((x, y) => x - y);
  const mid = out.length >> 1;
  return out.length % 2 ? out[mid] : (out[mid - 1] + out[mid]) / 2;
}

function score(touches, spanBars, widthAtr, lookback, dist, allow, reaction, p) {
  const touchPts = Math.min(28, (touches - 2) * 10 + 9);
  const spanPts = Math.min(15, 15 * (spanBars / Math.max(lookback, 1)));
  const reactPts = (p.reactionFullAtr > 0 && Number.isFinite(reaction))
    ? 17 * Math.min(1, Math.max(0, reaction / p.reactionFullAtr)) : 0;
  const tightPts = p.maxWidthAtr <= 0 ? 0
    : 22 * Math.max(0, 1 - (widthAtr / p.maxWidthAtr));
  // scored against the SAME allowance the filter uses, so a zone that barely
  // survives the cut also scores near zero for closeness
  const proxPts = (allow <= 0 || !Number.isFinite(dist)) ? 0
    : 18 * Math.max(0, 1 - (dist / allow));
  return round2(Math.max(0, Math.min(100,
    touchPts + spanPts + tightPts + reactPts + proxPts)));
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
  let winHi = -Infinity, winLo = Infinity;
  for (let k = i0; k <= i; k++) {
    if (bars[k].h > winHi) winHi = bars[k].h;
    if (bars[k].l < winLo) winLo = bars[k].l;
  }
  const allow = Math.max(p.maxDistanceAtr * a, p.maxDistanceRange * (winHi - winLo));
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
    const dist = Math.abs(lastClose - z.mid);
    if (Number.isFinite(dist) && allow > 0 && dist > allow) continue;
    const reaction = reactionAtr(kept, bars, atrArr, i, p);
    z.reactionAtr = reaction;
    z.atr = a;
    z.strength = score(z.touches, lastI - firstI, widthAtr, p.lookback,
                       dist, allow, reaction, p);
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
