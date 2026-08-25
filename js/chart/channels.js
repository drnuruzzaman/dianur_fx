/* channels.js — parallel channels, derived from the lines the engine found.
 *
 * A port of sim/tl/channels.py, compared channel-for-channel in
 * tests/test_channel_parity.py. Same pairing rule, same containment measure,
 * same scoring arithmetic, same dedupe — so a channel drawn on the chart is a
 * channel the Python side would report.
 *
 * TWO KINDS:
 *   paired     two independently confirmed rails that happen to run parallel
 *   projected  one confirmed rail plus a parallel copy pushed out to the
 *              furthest opposite extreme in its span — how a channel is drawn
 *              by hand, and a weaker claim, so it carries a scoring penalty
 *
 * CONTAINMENT is what stops this finding channels everywhere: two roughly
 * parallel lines can always be drawn, and what makes them a channel is that
 * price stayed between them.
 */

import { Direction, Role, Status, TF_MS, Trendline, TrendlineEngine, atrSeries }
  from './tlengine.js';

export const SLOPE_TOL_ATR_PER_BAR = 0.035;

export const DEFAULT_CHANNEL_PARAMS = {
  slopeTol: SLOPE_TOL_ATR_PER_BAR,
  minWidthAtr: 1.0,
  maxWidthAtr: 8.0,
  minContainment: 0.75,
  minOverlapBars: 20,
  minTouchesEach: 2,
  dedupeAtr: 0.5,
  allowProjected: true,
  maxChannels: 3,
};

const round2 = (v) => Math.round(v * 100) / 100;

export class Channel {
  constructor(o) { Object.assign(this, o); }

  lowerAt(t) { return this.lower.valueAt(t); }
  upperAt(t) { return this.upper.valueAt(t); }
  /** The dashed centre line every hand-drawn channel carries. */
  medianAt(t) { return 0.5 * (this.lowerAt(t) + this.upperAt(t)); }

  /** 0 at the lower rail, 1 at the upper; outside the corridor it leaves [0,1]. */
  positionAt(t, price) {
    const lo = this.lowerAt(t), hi = this.upperAt(t);
    if (!Number.isFinite(lo) || !Number.isFinite(hi) || hi <= lo) return NaN;
    return (price - lo) / (hi - lo);
  }

  get type() {
    if (this.direction === Direction.UP) return 'ascending_channel';
    if (this.direction === Direction.DOWN) return 'descending_channel';
    return 'horizontal_channel';
  }
}

function overlap(a, b) {
  const a0 = a.pivot1.i, a1 = a.pivot2.i, b0 = b.pivot1.i, b1 = b.pivot2.i;
  return [Math.max(Math.min(a0, a1), Math.min(b0, b1)),
          Math.min(Math.max(a0, a1), Math.max(b0, b1))];
}

/**
 * Fraction of CLOSES inside the corridor, plus how often each rail was touched
 * by a bar EXTREME. Same split as the line engine: a wick through a rail tests
 * it, a close through it fails it.
 */
function containment(lower, upper, bars, times, i0, i1) {
  if (i1 <= i0) return [0, 0, 0];
  let inside = 0, tLo = 0, tHi = 0, n = 0;
  for (let j = i0; j <= i1; j++) {
    const t = times[j];
    const lo = lower.valueAt(t), hi = upper.valueAt(t);
    if (!Number.isFinite(lo) || !Number.isFinite(hi) || hi <= lo) continue;
    n++;
    const w = hi - lo;
    const c = bars[j].c;
    if (c >= lo && c <= hi) inside++;
    if (Math.abs(bars[j].l - lo) <= 0.10 * w) tLo++;
    if (Math.abs(bars[j].h - hi) <= 0.10 * w) tHi++;
  }
  return [n ? inside / n : 0, tLo, tHi];
}

function score(cont, tLo, tHi, bars, widthAtr, kind) {
  const contPts = 45 * Math.max(0, Math.min(1, (cont - 0.5) / 0.5));
  const both = Math.min(tLo, tHi);
  const touchPts = Math.min(25, both * 6);
  const spanPts = Math.min(15, bars / 12);
  let widthPts;
  if (widthAtr <= 0) widthPts = 0;
  else if (widthAtr < 2) widthPts = 15 * (widthAtr / 2);
  else if (widthAtr <= 5) widthPts = 15;
  else widthPts = Math.max(0, 15 * (1 - (widthAtr - 5) / 7));
  const penalty = kind === 'projected' ? 18 : 0;
  return round2(Math.max(0, Math.min(100,
    contPts + touchPts + spanPts + widthPts - penalty)));
}

function parallelCopy(src, priceAtT, tAnchor, newId, role) {
  const t = new Trendline({
    id: newId, timeframe: src.timeframe, role, direction: src.direction,
    pivot1: { t: tAnchor, price: priceAtT, i: src.pivot1.i },
    pivot2: { t: tAnchor, price: priceAtT, i: src.pivot2.i },
    slope: src.slope, intercept: priceAtT,
    createdAt: src.createdAt, spanBars: src.spanBars,
    atrAtCreation: src.atrAtCreation,
  });
  /* The JS constructor hard-sets status and touches (it is built for the
     engine's own creation path, where every line starts as a CANDIDATE with two
     anchors). Python's Trendline is a dataclass and takes them as arguments, so
     they have to be assigned here or the two sides disagree about whether the
     projected rail is tradeable at all. */
  t.status = Status.CONFIRMED;
  t.touches = 1;
  return t;
}

/** Same corridor from different anchors: compared by where the rails sit NOW. */
function duplicate(ch, existing, tNow, atr, tolAtr) {
  const lo = ch.lowerAt(tNow), hi = ch.upperAt(tNow);
  const tol = tolAtr * atr;
  for (const o of existing) {
    if (Math.abs(o.lowerAt(tNow) - lo) <= tol
        && Math.abs(o.upperAt(tNow) - hi) <= tol) return true;
  }
  return false;
}

function build(kind, lower, upper, timeframe, times, i0, i1, widthAtr, cont,
               tLo, tHi, projectedSide) {
  const slope = 0.5 * (lower.slope + upper.slope);
  const nBars = i1 - i0 + 1;
  return new Channel({
/* The overlap window alone does NOT identify a channel: two different rail
     PAIRS can share the same i0..i1 and did -- observed on 15m gold as two
     corridors both calling themselves `15m-CH-P-1165-1382` with upper rails 29
     points apart. Nothing keys on the id today, so it was invisible; the first
     Map keyed by it would silently drop a corridor. The rails' own anchors
     disambiguate. */
    id: `${timeframe}-CH-${kind === 'paired' ? 'P' : 'J'}-${i0}-${i1}`
      + `-${lower.pivot1.i}.${upper.pivot1.i}`,
    timeframe, kind, direction: lower.direction, lower, upper, slope,
    tStart: times[i0], tEnd: times[i1], widthAtr, containment: cont,
    touchesLower: tLo, touchesUpper: tHi, bars: nBars,
    projectedSide,
    qualityScore: score(cont, tLo, tHi, nBars, widthAtr, kind),
  });
}

function projected(src, roleOther, bars, times, i, atr, timeframe, p, existing) {
  let i0 = Math.min(src.pivot1.i, src.pivot2.i);
  let i1 = Math.min(Math.max(src.pivot1.i, src.pivot2.i), i);
  if (i1 - i0 < p.minOverlapBars) return [];
  const wantHigh = roleOther === Role.RESISTANCE;
  let bestD = 0, bestJ = null;
  for (let j = i0; j <= i1; j++) {
    const v = src.valueAt(times[j]);
    if (!Number.isFinite(v)) continue;
    const d = wantHigh ? bars[j].h - v : v - bars[j].l;
    if (d > bestD) { bestD = d; bestJ = j; }
  }
  if (bestJ === null || bestD <= 0) return [];
  const widthAtr = bestD / atr;
  if (widthAtr < p.minWidthAtr || widthAtr > p.maxWidthAtr) return [];

  const tAnchor = times[bestJ];
  const px = wantHigh ? bars[bestJ].h : bars[bestJ].l;
  const clone = parallelCopy(src, px, tAnchor, src.id + '-par', roleOther);
  const lower = wantHigh ? src : clone;
  const upper = wantHigh ? clone : src;

  const [cont, tLo, tHi] = containment(lower, upper, bars, times, i0, i1);
  if (cont < p.minContainment) return [];
  if (Math.min(tLo, tHi) < p.minTouchesEach) return [];
  const ch = build('projected', lower, upper, timeframe, times, i0, i1,
                   widthAtr, cont, tLo, tHi, wantHigh ? 'upper' : 'lower');
  /* `existing` is the caller's running list, passed by reference: the caller
     extends it with what we return, so appending here too would double-add. */
  if (duplicate(ch, existing, times[i], atr, p.dedupeAtr)) return [];
  return [ch];
}

/**
 * Channels visible at bar `i`, best first.
 * `lines` is the live tradeable population; `bars` is the chart's own array.
 */
export function detect(lines, bars, atrArr, i, timeframe, params = {}) {
  const p = { ...DEFAULT_CHANNEL_PARAMS, ...params };
  const a = (i < atrArr.length && Number.isFinite(atrArr[i])) ? atrArr[i] : 0;
  if (a <= 0) return [];

  const times = bars.map((b) => b.t);
  const sups = lines.filter((l) => l.role === Role.SUPPORT && l.isTradeable);
  const ress = lines.filter((l) => l.role === Role.RESISTANCE && l.isTradeable);
  const tNow = times[i];
  const tfMs = times.length > 1 && times[1] > times[0] ? times[1] - times[0] : 1;

  const out = [];

  // ---- 1. paired ------------------------------------------------------- //
  for (const s of sups) {
    for (const r of ress) {
      if (Math.abs(s.slope - r.slope) * tfMs > p.slopeTol * a) continue;
      const [o0, o1raw] = overlap(s, r);
      const o1 = Math.min(o1raw, i);
      if (o1 - o0 < p.minOverlapBars) continue;
      const loNow = s.valueAt(tNow), hiNow = r.valueAt(tNow);
      if (!Number.isFinite(loNow) || !Number.isFinite(hiNow)) continue;
      const widthAtr = (hiNow - loNow) / a;
      if (widthAtr < p.minWidthAtr || widthAtr > p.maxWidthAtr) continue;
      const [cont, tLo, tHi] = containment(s, r, bars, times, o0, o1);
      if (cont < p.minContainment) continue;
      if (Math.min(tLo, tHi) < p.minTouchesEach) continue;
      const ch = build('paired', s, r, timeframe, times, o0, o1, widthAtr,
                       cont, tLo, tHi, null);
      if (duplicate(ch, out, tNow, a, p.dedupeAtr)) continue;
      out.push(ch);
    }
  }

  // ---- 2. projected ---------------------------------------------------- //
  if (p.allowProjected) {
    for (const src of sups) {
      out.push(...projected(src, Role.RESISTANCE, bars, times, i, a, timeframe, p, out));
    }
    for (const src of ress) {
      out.push(...projected(src, Role.SUPPORT, bars, times, i, a, timeframe, p, out));
    }
  }

  out.sort((x, y) => y.qualityScore - x.qualityScore);
  return out.slice(0, p.maxChannels);
}

/**
 * Channels for a chart's own bars — the entry point main.js uses.
 *
 * Runs the engine itself rather than accepting `liveLines()` output: that
 * function returns flattened plain objects for the renderer (`kind`, `p1`, no
 * `isTradeable`), while the pairing logic needs real Trendline instances. This
 * is the same construction tests/test_channel_parity.py exercises, so what the
 * chart draws is what the test compares.
 */
export function liveChannels(bars, timeframe, { limitBars = 1500, params = {},
                                                channelParams = {} } = {}) {
  if (!bars || bars.length < 80) return [];
  const slice = limitBars && bars.length > limitBars ? bars.slice(-limitBars) : bars;
  const eng = new TrendlineEngine(timeframe, TF_MS[timeframe] || 900e3, params);
  const snaps = eng.walk(slice);
  const last = snaps[snaps.length - 1];
  const live = last.live.filter((l) => l.isTradeable);
  if (!live.length) return [];
  return detect(live, slice, atrSeries(slice, 14), slice.length - 1,
                timeframe, channelParams);
}
