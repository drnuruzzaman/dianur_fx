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
  /* HOW MANY BARS THIS ACTUALLY LOOKED AT, which is not always `lookback`.
     `i0` clamps at 0, so a window longer than the history silently becomes the
     history: on 4h the chart loads 1200 bars (js/util.js BAR_COUNT) and the
     1500-bar tier had been reporting 1500 of them. The replay clips it a second
     way -- its slice ends at the cursor, so 150 bars into a walk every tier is
     150 bars deep whatever it asked for. One field covers both, and it stays
     right if BAR_COUNT changes or history is extended on demand.
     `i - i0`, not `i - i0 + 1`: unclipped it then equals `lookback` exactly,
     so the number a reader sees matches the number the tier is named for. */
  const windowBars = i - i0;
  let winHi = -Infinity, winLo = Infinity;
  for (let k = i0; k <= i; k++) {
    if (bars[k].h > winHi) winHi = bars[k].h;
    if (bars[k].l < winLo) winLo = bars[k].l;
  }
  const allow = Math.max(p.maxDistanceAtr * a, p.maxDistanceRange * (winHi - winLo));
  /* AN INJECTION SEAM, and it changes nothing unless a caller uses it.
     `pivotsFn` defaults to exactly the call that was here, so the shipped
     behaviour and the parity tests are untouched. It exists because the pivot
     definition is the one input to this detector that has never been measured
     -- tools/zone_defn_audit.mjs feeds it a ZigZag instead -- and replicating
     the clustering inside a measurement tool would test a copy of this file
     rather than this file. */
  const { highs, lows } = (p.pivotsFn || findPivots)(bars, p.strengthPivots);

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
      windowBars,
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

/* ---------------------------------------------------------------------------
 * THE TIER LADDER -- one detector, three windows.
 *
 * WHY. `lookback` was 500 on every timeframe and had never been measured.
 * tools/zone_lookback_sweep.mjs swept 100..2000 across six cells and found two
 * things. The window is SCALE-FREE like everything else here -- 5m and 4h,
 * gold and cable all produce ~10 bands at 500, 0.30 ATR wide, within a point
 * of each other -- so the per-timeframe lookback table this was meant to
 * produce is unnecessary. And the shipped cap of six BINDS ON 72-80% OF BARS
 * at lookback 500. Three quarters of the time the chart was showing an
 * arbitrary six out of ten, ordered by a score already retired as a forecast.
 *
 * A ladder answers that better than a bigger cap. Six bands that mean three
 * different distances is a chart; ten bands that all mean the same thing is a
 * mesh. The tiers are the sweep's own three regimes:
 *
 *     near   100    the only window where the cap does not bind (0.6-1.9%);
 *                   two touches, median half an ATR from price -- what is
 *                   underfoot right now
 *     mid    500    today's shipped behaviour, kept as the middle rung so the
 *                   ladder is a superset of what the chart already drew
 *     far   1500    structural; a band still standing after 1500 bars, which
 *                   on 15m is most of a month
 *
 * A TIER IS CLAMPED BY THE HISTORY IT HAS, and says so. `lookback` is what the
 * tier ASKS for; `windowBars` on each zone is what it GOT. They differ on 4h,
 * 1d and 1w -- those load 1200 / 1000 / 800 bars, so the far tier is the whole
 * chart there -- and they differ everywhere early in a replay, whose slice ends
 * at the cursor. Below roughly 300 bars the far tier finds nothing the mid tier
 * has not already claimed and the ladder degrades to two rungs, which is the
 * dedup working rather than failing: fewer bands, not the same band three
 * times. The tooltip quotes `windowBars`, never the request.
 *
 * DEDUP IS BY SHARED PIVOT, and the NARROWEST window wins -- the tier is the
 * SHORTEST memory in which the level is visible. Matching on price instead
 * would split a band that gains a touch and moves its mid; the sweep made that
 * mistake first and invented churn out of it.
 *
 * THE OTHER DIRECTION WAS TRIED FIRST AND IS WRONG. Letting the widest window
 * claim a shared band starves `near` to nothing: the 500-bar window CONTAINS
 * the last 100 bars, so every recent cluster is also in the mid set and the
 * near tier never fires. Measured on the replay -- 4h, 1200 bars -- it
 * returned two `far` and two `mid` and not a single `near`.
 *
 * Narrowest-wins also says the more useful thing. A level a 100-bar memory can
 * already see is one price is working on NOW; a level that needs 1500 bars to
 * appear is old structure well away from price. That is the ladder as a reader
 * wants it -- immediate, medium, structural, spread across the screen by
 * distance -- rather than a re-labelling of the same near cluster.
 *
 * NOT MULTI-TIMEFRAME, still. A 4h band and a 15m band at one price are the
 * same band, so projecting one onto the other double-counts a level. A long
 * window on the chart's OWN bars is the same information without that.
 *
 * NO CLAIM ABOUT DIRECTION IS MADE OR IMPLIED. `far` is not stronger than
 * `near`; across 185,227 approaches nothing about a zone predicts which side
 * price leaves it, and a longer-lived band is no exception. The tier says how
 * far back you have to look to see the level -- a fact about the window, not
 * a forecast.
 */
export const ZONE_TIERS = [
  { tier: 'near', lookback: 100, keep: 2 },
  { tier: 'mid', lookback: 500, keep: 2 },
  { tier: 'far', lookback: 1500, keep: 2 },
];

/** Do two bands come from the same pivot cluster? */
function sharesPivot(a, b) {
  for (const x of a.levels) if (b.levels.includes(x)) return true;
  return false;
}

/**
 * Zones at bar `i` across all three windows, nearest tier first.
 *
 * Returns the same Zone objects `detect` does, each carrying `tier` and
 * `tierRank` (0 near .. 2 far). Callers that do not know about tiers see an
 * ordinary zone array.
 */
export function tieredDetect(bars, i, timeframe, atrArr, params = {}) {
  const out = [];
  // narrowest first, so the shortest window that saw a band is the one that keeps it
  for (let t = 0; t < ZONE_TIERS.length; t++) {
    const { tier, lookback, keep } = ZONE_TIERS[t];
    let zs;
    try {
      zs = detect(bars, i, timeframe, atrArr, { ...params, lookback, maxZones: 999 });
    } catch { continue; }
    let taken = 0;
    for (const z of zs) {
      if (taken >= keep) break;
      if (out.some((o) => sharesPivot(o, z))) continue;
      z.tier = tier;
      z.tierRank = t;
      z.tierLookback = lookback;
      out.push(z);
      taken++;
    }
  }
  return out;
}

/** Tiered zones for a chart's own bars -- the ladder's entry point. */
export function tieredZones(bars, timeframe, params = {}) {
  if (!bars || bars.length < 60) return [];
  return tieredDetect(bars, bars.length - 1, timeframe, atrSeries(bars, 14), params);
}
