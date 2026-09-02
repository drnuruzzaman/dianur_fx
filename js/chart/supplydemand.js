/* supplydemand.js — zones found from IMPULSE ORIGINS, not pivot clusters.
 *
 * A port of sim/tl/supply_demand.py, compared zone-for-zone in
 * tests/test_sd_parity.py.
 *
 * zones.js asks "where has price turned repeatedly?" and clusters confirmed
 * pivots. This asks "where did price leave from in a hurry?" — the quiet base a
 * large directional move departed from. They find genuinely different things: a
 * pivot cluster needs price to have visited a level SEVERAL times, an impulse
 * origin can be a level price visited ONCE and ran from.
 *
 * MEASURED, before being drawn. Against the same placebo as pivot-cluster
 * zones, across three disjoint eras: +5.55 / +6.87 / +3.83 pp, versus
 * +5.50 / +5.00 / +5.09 pp for pivot clusters — statistically indistinguishable
 * on roughly a third the sample. Two unrelated detectors converging on the same
 * ~5pp is what makes the effect look structural rather than fitted.
 *
 * FRESHNESS is the property pivot-clustering cannot express: a pivot-cluster
 * zone is DEFINED by repeated touches, so "untested" is meaningless there. Here
 * a zone starts fresh and is consumed by use. Fresh beat tested in all three
 * eras (+6.16 vs +4.93, +7.21 vs +6.53, +4.28 vs +3.39) — the direction
 * replicates, the gap is small, and none of the differences would clear
 * significance alone. Shown, not weighted heavily.
 *
 * Impulse SIZE is deliberately not used for ranking beyond its scoring share:
 * big impulses won in two eras and lost badly in the third (+4.60 vs +9.14),
 * so the sign does not replicate.
 */

import { atrSeries } from './tlengine.js';

export const DEMAND = 'demand';   // base a rally left; expected to support
export const SUPPLY = 'supply';   // base a decline left; expected to resist

export const DEFAULT_SD_PARAMS = {
  impulseAtr: 2.5,      // net move that counts as a departure
  impulseBars: 6,       // over at most this many bars
  minAgree: 0.6,        // fraction of impulse bars agreeing in direction
  baseRangeAtr: 0.8,    // a base bar's range must be under this
  maxBaseBars: 8,       // longer than this is a range, not a base
  minBaseBars: 1,
  maxWidthAtr: 2.5,     // a base wider than this is not one level
  touchAtr: 0.15,       // how close a return counts as a touch
  maxDistanceAtr: 12,
  maxZones: 8,
  lookback: 600,
};

const round2 = (v) => Math.round(v * 100) / 100;

export class SDZone {
  constructor(o) { Object.assign(this, o); }
  get mid() { return 0.5 * (this.low + this.high); }
  /** Never revisited — the property pivot-clustering cannot express. */
  get fresh() { return this.touches === 0; }
  contains(p) { return p >= this.low && p <= this.high; }
  distanceAtr(price, atr) {
    if (!atr) return NaN;
    if (this.contains(price)) return 0;
    const d = price < this.low ? this.low - price : price - this.high;
    return d / atr;
  }
}

function score(z, p, distAtr) {
  const impPts = Math.min(40, 40 * (z.impulseAtr / (p.impulseAtr * 2)));
  const tightPts = 20 * Math.max(0, 1 - z.widthAtr / Math.max(p.maxWidthAtr, 1e-9));
  const freshPts = z.touches === 0 ? 20 : Math.max(0, 20 - 7 * z.touches);
  const proxPts = (p.maxDistanceAtr <= 0 || !Number.isFinite(distAtr)) ? 0
    : 20 * Math.max(0, 1 - distAtr / p.maxDistanceAtr);
  return round2(Math.max(0, Math.min(100, impPts + tightPts + freshPts + proxPts)));
}

/**
 * WHERE A BASE IS CREATED, AND NOTHING ABOUT WHAT HAPPENED AFTER.
 *
 * `detect` below answers "what zones are worth drawing at bar `upto`", and to
 * do that it scans FORWARD from each base: counting touches, marking the zone
 * broken, dropping it if it was broken by `upto`, scoring it by distance from
 * `upto`. All of that is correct for a panel and fatal for a backtest -- a walk
 * that asked `detect` once per series would only ever see the zones that
 * survived to the end, which is survivorship bias in its purest form and would
 * make any strategy built on it look brilliant.
 *
 * This is the creation half alone: an impulse ends at bar i, the base behind it
 * is measured, and the zone is returned stamped with the bar it became knowable
 * on. Nothing here reads a bar after `confirmedI`. A caller that wants to know
 * whether a zone is still alive at some later bar must work that out from the
 * bars it is allowed to see -- which is exactly what js/chart/smcretest.js
 * does.
 *
 * The scan is the same one `detect` runs, over the whole series rather than a
 * `lookback` window; tests/test_smcretest.py holds the two to the same bases.
 */
export function bases(bars, atr, params = {}) {
  const p = { ...DEFAULT_SD_PARAMS, ...params };
  const n = bars.length;
  const out = [];
  for (let i = 1; i < n; i++) {
    const a = atr[i];
    if (!Number.isFinite(a) || a <= 0) continue;

    let best = null;
    for (let k = 2; k <= p.impulseBars; k++) {
      const j = i - k + 1;
      if (j <= 1) break;
      const move = bars[i].c - bars[j - 1].c;
      if (Math.abs(move) < p.impulseAtr * a) continue;
      const d = move > 0 ? 1 : -1;
      let agree = 0;
      for (let m = j; m <= i; m++) if ((bars[m].c - bars[m - 1].c) * d > 0) agree++;
      if (agree / k < p.minAgree) continue;
      best = { j, d, imp: Math.abs(move) / a };
      break;                       // shortest qualifying impulse wins
    }
    if (!best) continue;
    const { j, d, imp } = best;

    const b1 = j - 1;
    let b0 = b1;
    while (b0 > 1 && (b1 - b0 + 1) < p.maxBaseBars
           && (bars[b0].h - bars[b0].l) <= p.baseRangeAtr * atr[b0]) {
      b0--;
    }
    b0++;
    if (b1 - b0 + 1 < p.minBaseBars || b1 < b0) continue;
    let lo = Infinity, hi = -Infinity;
    for (let m = b0; m <= b1; m++) {
      if (bars[m].l < lo) lo = bars[m].l;
      if (bars[m].h > hi) hi = bars[m].h;
    }
    const widthAtr = (hi - lo) / a;
    if (!(widthAtr > 0) || widthAtr > p.maxWidthAtr) continue;

    out.push({ kind: d > 0 ? DEMAND : SUPPLY, low: lo, high: hi,
               confirmedI: i, baseI0: b0, baseI1: b1,
               widthAtr, impulseAtr: imp, atr: a });
  }
  return out;
}

/**
 * Zones knowable at bar `upto`, strongest first.
 * A zone is confirmed at the END of its impulse, never at its base, so nothing
 * here is visible before the move that created it has finished.
 */
export function detect(bars, tf, atr, params = {}, upto = null) {
  const p = { ...DEFAULT_SD_PARAMS, ...params };
  const n = bars.length;
  const iEnd = upto === null ? n - 1 : Math.min(upto, n - 1);
  const start = Math.max(1, iEnd - p.lookback);
  const zones = [];
  let seq = 0;

  for (let i = start; i <= iEnd; i++) {
    const a = atr[i];
    if (!Number.isFinite(a) || a <= 0) continue;

    // --- 1. does an impulse END at bar i? ---
    let best = null;
    for (let k = 2; k <= p.impulseBars; k++) {
      const j = i - k + 1;
      if (j <= start) break;
      const move = bars[i].c - bars[j - 1].c;
      if (Math.abs(move) < p.impulseAtr * a) continue;
      const d = move > 0 ? 1 : -1;
      let agree = 0;
      for (let m = j; m <= i; m++) if ((bars[m].c - bars[m - 1].c) * d > 0) agree++;
      if (agree / k < p.minAgree) continue;
      best = { j, d, imp: Math.abs(move) / a };
      break;                       // shortest qualifying impulse wins
    }
    if (!best) continue;
    const { j, d, imp } = best;

    // --- 2. walk back for the base ---
    const b1 = j - 1;
    let b0 = b1;
    while (b0 > start && (b1 - b0 + 1) < p.maxBaseBars
           && (bars[b0].h - bars[b0].l) <= p.baseRangeAtr * atr[b0]) {
      b0--;
    }
    b0++;
    if (b1 - b0 + 1 < p.minBaseBars || b1 < b0) continue;
    let lo = Infinity, hi = -Infinity;
    for (let m = b0; m <= b1; m++) {
      if (bars[m].l < lo) lo = bars[m].l;
      if (bars[m].h > hi) hi = bars[m].h;
    }
    const widthAtr = (hi - lo) / a;
    if (!(widthAtr > 0) || widthAtr > p.maxWidthAtr) continue;

    seq++;
    const z = new SDZone({
      id: `${tf}-SD-${seq}`, timeframe: tf,
      kind: d > 0 ? DEMAND : SUPPLY, low: lo, high: hi,
      baseI0: b0, baseI1: b1, impulseI1: i, confirmedI: i,
      tBase: bars[b0].t, tConfirmed: bars[i].t,
      impulseAtr: imp, baseBars: b1 - b0 + 1, widthAtr,
      touches: 0, broken: false, strength: 0, atr: a,
    });

    // --- 3. how has it been used SINCE it formed? ---
    for (let m = i + 1; m <= iEnd; m++) {
      const am = (Number.isFinite(atr[m]) && atr[m] > 0) ? atr[m] : a;
      if (bars[m].l <= hi + p.touchAtr * am && bars[m].h >= lo - p.touchAtr * am) {
        z.touches++;
      }
      if (z.kind === DEMAND && bars[m].c < lo - p.touchAtr * am) { z.broken = true; break; }
      if (z.kind === SUPPLY && bars[m].c > hi + p.touchAtr * am) { z.broken = true; break; }
    }
    if (z.broken) continue;

    const dist = z.distanceAtr(bars[iEnd].c, atr[iEnd]);
    if (Number.isFinite(dist) && dist > p.maxDistanceAtr) continue;
    z.strength = score(z, p, dist);
    zones.push(z);
  }

  zones.sort((x, y) => y.strength - x.strength);
  const out = [];
  const aEnd = Number.isFinite(atr[iEnd]) ? atr[iEnd] : 0;
  for (const z of zones) {
    // two bases at the same price are one zone
    if (out.some((k) => Math.abs(z.mid - k.mid) <= 0.5 * aEnd)) continue;
    out.push(z);
    if (out.length >= p.maxZones) break;
  }
  return out;
}

/** Zones for a chart's own bars — the entry point main.js uses. */
export function liveSDZones(bars, tf, params = {}) {
  if (!bars || bars.length < 80) return [];
  return detect(bars, tf, atrSeries(bars, 14), params);
}
