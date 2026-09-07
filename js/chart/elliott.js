/* elliott.js — a causal Elliott wave counter.
 *
 * WHAT THIS IS FOR, because it decides every design choice below: this exists
 * to be FALSIFIED, not to decorate a chart. Elliott counts are notorious for
 * repainting -- the count you are shown today is not the count that was on
 * screen last week, and the label always fits the history it has already seen.
 * The only way to know whether a count carries information is to record what it
 * claimed BEFORE the next bar existed, then score it.
 *
 * So this module never sees the future:
 *
 *   - it reads pivots only through `confirmedI <= upto`, so a swing enters the
 *     count at the bar it was CONFIRMED, several bars after it occurred -- the
 *     same two-clock convention the rest of this project uses;
 *   - `countAsOf(bars, {upto})` is a pure function of `bars[0..upto]`;
 *   - every count carries an INVALIDATION price, which is what makes it a
 *     claim rather than a description.
 *
 * WHAT THE NUMBERS ARE. Each admissible count gets a `score` built from
 * Elliott's guidelines (fib retracements, extension ratios, alternation), and
 * `share` is that score normalised across the surviving counts. THAT IS NOT A
 * PROBABILITY and this file will not call it one. It is a relative weight from
 * an unvalidated scoring function. Turning it into a probability requires
 * measuring, over recorded beliefs, how often a count with share s was the one
 * that survived -- which is what the replay log is for. Until that measurement
 * exists the UI says "uncalibrated" and means it.
 *
 * The HARD RULES are different in kind from the guidelines. They are the three
 * conditions that make a count wrong rather than unlikely, and a count that
 * breaks one is dropped, never merely penalised.
 */

import { findPivots } from './trendlines.js';
import { atrSeries } from './tlengine.js';

/* Fibonacci levels the guidelines are stated in. Not tuned -- these are the
   textbook numbers, and inventing better ones by fitting them to gold would be
   the same mistake as tuning a channel threshold. */
const FIB = {
  w2: [0.5, 0.618, 0.786],       // typical wave-2 retracement of wave 1
  w4: [0.236, 0.382, 0.5],       // typical wave-4 retracement of wave 3
  w3: [1.618, 2.618],            // typical wave-3 extension of wave 1
  w5: [0.618, 1.0],              // typical wave-5 relative to wave 1
};

/** How many alternating pivots to consider. Beyond this the enumeration grows
    without adding counts anyone would read off a chart. */
const MAX_PIVOTS = 14;

/**
 * Alternating high/low pivots, causal.
 *
 * `findPivots` returns highs and lows independently, so a run of three highs
 * with no low between them is possible; a wave count needs strict alternation.
 * Where the sequence repeats a side, the more extreme pivot wins -- a lower low
 * replaces a low, a higher high replaces a high -- which is the same rule a
 * zigzag uses and keeps the sequence anchored on the actual turning points.
 */
export function zigzag(bars, { strength = 3, upto = null } = {}) {
  const end = upto === null ? bars.length - 1 : upto;
  const { highs, lows } = findPivots(bars, strength);
  const all = [];
  for (const p of highs) if (p.confirmedI <= end) all.push({ ...p, kind: 'H' });
  for (const p of lows) if (p.confirmedI <= end) all.push({ ...p, kind: 'L' });
  all.sort((a, b) => a.i - b.i);

  const out = [];
  for (const p of all) {
    const last = out[out.length - 1];
    if (!last) { out.push(p); continue; }
    if (last.kind === p.kind) {
      // same side twice: keep whichever is the true extreme
      const better = p.kind === 'H' ? p.price > last.price : p.price < last.price;
      if (better) out[out.length - 1] = p;
    } else {
      out.push(p);
    }
  }
  return out;
}

const near = (x, targets, tol) => targets.some((t) => Math.abs(x - t) <= tol);

/**
 * Hard rules for a five-wave impulse. Returns null when the count is
 * ADMISSIBLE, or a string naming the rule it breaks.
 *
 * `p` is [p0..p5] as prices in wave order; `dir` is +1 for an up impulse.
 * Waves still in progress pass whatever cannot yet be tested -- an unfinished
 * wave 4 cannot violate the overlap rule until it has a low.
 */
export function checkRules(p, dir) {
  const up = dir > 0;
  const w1 = p[1] - p[0];
  if (p.length > 2) {
    // RULE 1: wave 2 never retraces more than 100% of wave 1.
    if (up ? p[2] <= p[0] : p[2] >= p[0]) return 'wave 2 retraced past the start of wave 1';
  }
  if (p.length > 3) {
    if (up ? p[3] <= p[1] : p[3] >= p[1]) return 'wave 3 did not exceed wave 1';
  }
  if (p.length > 4) {
    // RULE 3: wave 4 does not enter wave 1's price territory.
    if (up ? p[4] <= p[1] : p[4] >= p[1]) return 'wave 4 overlapped wave 1';
  }
  if (p.length > 5) {
    // RULE 2: wave 3 is never the shortest of 1, 3, 5.
    const l1 = Math.abs(w1);
    const l3 = Math.abs(p[3] - p[2]);
    const l5 = Math.abs(p[5] - p[4]);
    if (l3 < l1 && l3 < l5) return 'wave 3 was the shortest of 1/3/5';
    if (up ? p[5] <= p[3] : p[5] >= p[3]) return 'wave 5 did not exceed wave 3';
  }
  return null;
}

/**
 * Guideline evidence for an impulse. Each entry is a MEASUREMENT with a
 * verdict, so the panel can show why a count is preferred rather than only that
 * it is. Weights are deliberately flat -- there is no evidence for a weighting,
 * and inventing one would make the score look more informed than it is.
 */
function impulseEvidence(p, dir) {
  const ev = [];
  const len = (a, b) => Math.abs(p[b] - p[a]);
  const w1 = len(0, 1);
  if (p.length > 2 && w1 > 0) {
    const r = len(1, 2) / w1;
    ev.push({ key: 'w2 retrace', value: r, ok: near(r, FIB.w2, 0.12),
      text: `wave 2 retraced ${(r * 100).toFixed(0)}% of wave 1` });
  }
  if (p.length > 3 && w1 > 0) {
    const r = len(2, 3) / w1;
    ev.push({ key: 'w3 extension', value: r, ok: r >= 1.0,
      text: `wave 3 is ${r.toFixed(2)}x wave 1` });
    ev.push({ key: 'w3 fib', value: r, ok: near(r, FIB.w3, 0.35),
      text: `wave 3 near ${r >= 2.0 ? '2.618' : '1.618'} extension` });
  }
  if (p.length > 4) {
    const w3 = len(2, 3);
    const r = w3 > 0 ? len(3, 4) / w3 : NaN;
    ev.push({ key: 'w4 retrace', value: r, ok: near(r, FIB.w4, 0.12),
      text: `wave 4 retraced ${(r * 100).toFixed(0)}% of wave 3` });
    // ALTERNATION: 2 and 4 should differ in depth. Equal depths are the mark of
    // a count that has been fitted rather than observed.
    const r2 = w1 > 0 ? len(1, 2) / w1 : NaN;
    const diff = Math.abs(r - r2);
    ev.push({ key: 'alternation', value: diff, ok: diff >= 0.15,
      text: diff >= 0.15 ? 'waves 2 and 4 alternate in depth'
        : 'waves 2 and 4 are the same depth' });
  }
  if (p.length > 5 && w1 > 0) {
    const r = len(4, 5) / w1;
    ev.push({ key: 'w5 vs w1', value: r, ok: near(r, FIB.w5, 0.25),
      text: `wave 5 is ${r.toFixed(2)}x wave 1` });
  }
  return ev;
}

/** Corrective ABC: no hard rules beyond B not exceeding the start of A. */
function checkAbc(p, dir) {
  const up = dir > 0;   // dir is the direction of wave A
  if (p.length > 2 && (up ? p[2] <= p[0] : p[2] >= p[0])) {
    return 'wave B retraced past the start of wave A';
  }
  return null;
}

function abcEvidence(p) {
  const ev = [];
  const a = Math.abs(p[1] - p[0]);
  if (p.length > 2 && a > 0) {
    const r = Math.abs(p[2] - p[1]) / a;
    ev.push({ key: 'B retrace', value: r, ok: r >= 0.382 && r <= 0.886,
      text: `B retraced ${(r * 100).toFixed(0)}% of A` });
  }
  if (p.length > 3 && a > 0) {
    const r = Math.abs(p[3] - p[2]) / a;
    ev.push({ key: 'C vs A', value: r, ok: near(r, [0.618, 1.0, 1.618], 0.2),
      text: `C is ${r.toFixed(2)}x A` });
  }
  return ev;
}

/* What the count says happens NEXT, which is the only part that can be scored
   against later bars. `dir` is the impulse direction. */
function outlook(kind, waveNow, dir) {
  if (kind === 'impulse') {
    // inside 1, 3 or 5 the count expects the impulse direction to continue
    if (waveNow === 1 || waveNow === 3 || waveNow === 5) return 'continuation';
    if (waveNow === 2 || waveNow === 4) return 'correction';
    return 'reversal';           // the impulse is complete
  }
  if (waveNow === 'A' || waveNow === 'C') return 'correction';
  if (waveNow === 'B') return 'continuation';
  return 'reversal';
}

/**
 * The invalidation price: the level that KILLS this count.
 *
 * Every count must have one. A count with no level that would refute it is not
 * a forecast, and the whole point of the replay log is to record claims that
 * later bars can settle.
 */
function invalidationFor(kind, waveNow, p, dir) {
  const up = dir > 0;
  if (kind === 'impulse') {
    if (waveNow === 1) return p[0];                    // below the origin
    if (waveNow === 2) return p[0];                    // rule 1
    if (waveNow === 3) return p[2];                    // 3 cannot undercut 2
    if (waveNow === 4) return p[1];                    // rule 3, the overlap
    if (waveNow === 5) return p[4];
    return up ? p[5] : p[5];
  }
  if (waveNow === 'B') return p[0];
  if (waveNow === 'C') return p[1];
  return p[0];
}

/** Fib projection for where the wave in progress is expected to end. */
function targetZone(kind, waveNow, p, dir) {
  const up = dir > 0;
  const sgn = up ? 1 : -1;
  const w1 = Math.abs(p[1] - p[0]);
  if (kind === 'impulse' && waveNow === 3 && p.length >= 3) {
    return [p[2] + sgn * w1 * 1.618, p[2] + sgn * w1 * 2.618];
  }
  if (kind === 'impulse' && waveNow === 5 && p.length >= 5) {
    return [p[4] + sgn * w1 * 0.618, p[4] + sgn * w1 * 1.0];
  }
  if (kind === 'impulse' && waveNow === 2 && p.length >= 2) {
    return [p[1] - sgn * w1 * 0.5, p[1] - sgn * w1 * 0.786];
  }
  if (kind === 'impulse' && waveNow === 4 && p.length >= 4) {
    const w3 = Math.abs(p[3] - p[2]);
    return [p[3] - sgn * w3 * 0.236, p[3] - sgn * w3 * 0.5];
  }
  return null;
}

/**
 * THE PROJECTED PATH: where this count says price goes next, drawn as bars.
 *
 * A wave label on its own cannot be scored -- "wave 3" is not a forecast, it is
 * a name. What can be scored is a PATH: at bar +12 the count expects this
 * price, at bar +30 this one. Drawing it forward, before the bars exist, is what
 * turns the count into something the next thirty bars can settle.
 *
 * PRICE comes from the fib relationships the guidelines are already stated in,
 * so nothing new is invented here. TIME comes from the durations this count's
 * own completed waves actually took -- the median of them, per remaining leg.
 * That is a weak estimator and it is deliberately the weakest part of the
 * projection: Elliott says a great deal about proportion in price and almost
 * nothing testable about proportion in time. The scoring reports price error
 * against the projected path and does not pretend the timing is a claim.
 *
 * Returns points RELATIVE to the as-of bar: {ahead, price, label}.
 */
export function projectPath(count, { minLeg = 6 } = {}) {
  if (!count || count.kind !== 'impulse') return [];
  const p = count.pivots.map((x) => x.price);
  const idx = count.pivots.map((x) => x.i);
  const dir = count.dir;
  const sgn = dir > 0 ? 1 : -1;
  const w1 = Math.abs(p[1] - p[0]);
  if (!(w1 > 0)) return [];

  /* Durations of the legs already complete. A leg still forming is excluded --
     its length is not known yet, and including the partial would bias every
     projected leg short. */
  const legs = [];
  for (let k = 1; k < idx.length; k++) legs.push(idx[k] - idx[k - 1]);
  const dur = legs.length
    ? Math.max(minLeg, Math.round(legs.slice().sort((a, b) => a - b)[Math.floor(legs.length / 2)]))
    : minLeg;

  const out = [];
  let ahead = 0;
  const add = (price, label, bars) => {
    ahead += Math.max(minLeg, Math.round(bars));
    out.push({ ahead, price, label });
  };

  const w = count.waveNow;
  /* Each remaining leg, in order, at its textbook proportion. Wave 3 is given
     1.618 of wave 1 rather than the midpoint of its whole 1.618-2.618 zone: the
     lower bound is the one the guideline actually names, and projecting to the
     middle of a range would be quietly optimistic. */
  if (w <= 1) add(p[0] + sgn * w1, '1', dur);
  if (w <= 2 && p.length > 1) {
    const from = w <= 1 ? p[0] + sgn * w1 : p[1];
    add(from - sgn * w1 * 0.618, '2', dur * 0.8);
  }
  if (w <= 3) {
    const base = p.length > 2 ? p[2] : p[0];
    add(base + sgn * w1 * 1.618, '3', dur * 1.4);
  }
  if (w <= 4) {
    const three = out.length ? out[out.length - 1].price
      : (p.length > 3 ? p[3] : p[0] + sgn * w1 * 2.618);
    const w3 = Math.abs(three - (p.length > 2 ? p[2] : p[0]));
    add(three - sgn * w3 * 0.382, '4', dur * 0.9);
  }
  if (w <= 5) {
    const four = out.length ? out[out.length - 1].price
      : (p.length > 4 ? p[4] : p[0]);
    add(four + sgn * w1 * 1.0, '5', dur);
  }
  return out;
}

/**
 * How far price actually ran from the projected path, in ATR at the time of the
 * belief. Reported per point AND as a terminal error, because a path can be
 * right about where price ends up and wrong about everything in between.
 */
export function scoreProjection(belief, bars) {
  const c = belief.counts && belief.counts[0];
  if (!c || !c.projection || !c.projection.length) return null;
  if (!Number.isFinite(belief.atr) || belief.atr <= 0) return null;
  const pts = [];
  for (const pt of c.projection) {
    const j = belief.asOfI + pt.ahead;
    if (j > bars.length - 1) break;
    pts.push({ ...pt, actual: bars[j].c,
      errAtr: (bars[j].c - pt.price) / belief.atr });
  }
  if (!pts.length) return null;
  const abs = pts.map((x) => Math.abs(x.errAtr));
  return {
    points: pts,
    meanAbsAtr: abs.reduce((a, b) => a + b, 0) / abs.length,
    terminalAtr: pts[pts.length - 1].errAtr,
    covered: pts.length, of: c.projection.length,
  };
}

/**
 * CALIBRATION: does a 70% forecast happen 70% of the time?
 *
 * Accuracy answers "how often is the top pick right", which a model can score
 * well on while its numbers mean nothing -- always saying 99% and being right
 * 60% of the time is 60% accurate and badly wrong about itself. Calibration is
 * the harder and more useful question, and it is the one that decides whether
 * `share` may ever be printed as a probability.
 *
 * Rows are bucketed by the confidence the forecast CLAIMED for the class it
 * named. A bucket is reported with its own n, because a bucket of four is a
 * coincidence rather than a measurement.
 *
 * The BRIER SCORE is the summary: mean squared error over the three-class
 * forecast, lower better. It is quoted against the CLIMATOLOGY -- the same
 * score for a forecast that ignores the chart and always predicts the base
 * rates of this sample. A model that cannot beat climatology has told you
 * nothing you did not already know from counting outcomes.
 */
export function calibration(rows, { edges = [0, 0.35, 0.45, 0.55, 0.65, 0.75, 1.01] } = {}) {
  const CLASSES = ['continuation', 'correction', 'reversal'];
  const usable = rows.filter((r) => r.scenario && r.expected && r.actual);
  if (!usable.length) return null;

  const base = {};
  for (const c of CLASSES) base[c] = usable.filter((r) => r.actual === c).length / usable.length;

  let brier = 0;
  let brierBase = 0;
  for (const r of usable) {
    for (const c of CLASSES) {
      const y = r.actual === c ? 1 : 0;
      brier += ((r.scenario[c] || 0) - y) ** 2;
      brierBase += ((base[c] || 0) - y) ** 2;
    }
  }
  brier /= usable.length;
  brierBase /= usable.length;

  const buckets = [];
  for (let i = 0; i < edges.length - 1; i++) {
    const lo = edges[i], hi = edges[i + 1];
    const inBucket = usable.filter((r) => {
      const p = r.scenario[r.expected] || 0;
      return p >= lo && p < hi;
    });
    if (!inBucket.length) continue;
    const claimed = inBucket.reduce((a, r) => a + (r.scenario[r.expected] || 0), 0) / inBucket.length;
    const happened = inBucket.filter((r) => r.actual === r.expected).length / inBucket.length;
    buckets.push({ lo, hi, n: inBucket.length, claimed, happened, gap: happened - claimed });
  }
  return { n: usable.length, buckets, brier, brierBase, base,
           skill: brierBase > 0 ? 1 - brier / brierBase : null };
}

/**
 * REPAINTING, measured.
 *
 * The complaint against Elliott is not that a finished chart looks wrong -- a
 * finished chart always looks right, because the count was fitted to it. The
 * complaint is that the count you were shown at the time kept being replaced.
 * A replay preserves that history, so it can be counted:
 *
 *   flips        how often the primary reading changed between consecutive bars
 *   dirFlips     how often it changed DIRECTION, which is the change that would
 *                have reversed a trade rather than relabelled one
 *   survival     how many bars a primary reading lasted, median
 *   invalidated  how often a count's own stated level was traded through
 *
 * `beliefs` must be in cursor order and evenly spaced; `stride` says how many
 * bars apart, so survival can be reported in bars rather than in samples.
 */
export function stability(beliefs, { stride = 1 } = {}) {
  const seq = beliefs.filter((b) => b.counts && b.counts.length);
  if (seq.length < 2) return null;
  let flips = 0;
  let dirFlips = 0;
  const runs = [];
  let run = 1;
  for (let i = 1; i < seq.length; i++) {
    const a = seq[i - 1].counts[0];
    const b = seq[i].counts[0];
    const same = a.kind === b.kind && a.dir === b.dir && a.waveNow === b.waveNow;
    if (same) { run++; continue; }
    flips++;
    if (a.dir !== b.dir) dirFlips++;
    runs.push(run);
    run = 1;
  }
  runs.push(run);
  runs.sort((x, y) => x - y);
  return {
    n: seq.length, stride,
    flipRate: flips / (seq.length - 1),
    dirFlipRate: dirFlips / (seq.length - 1),
    medianRunBars: runs[Math.floor(runs.length / 2)] * stride,
    longestRunBars: runs[runs.length - 1] * stride,
  };
}

/**
 * Enumerate the counts admissible at `upto`.
 *
 * The enumeration is deliberately small: every alternating pivot in the recent
 * window is tried as the ORIGIN of an impulse and of a correction, in both
 * directions, and the resulting counts are filtered by the hard rules. That is
 * enough to produce the two or three readings a human would argue over, and
 * stopping there is the point -- an engine that offers thirty counts has not
 * said anything.
 */
export function countAsOf(bars, { upto = null, strength = 3, atrLen = 14 } = {}) {
  const end = upto === null ? bars.length - 1 : Math.min(upto, bars.length - 1);
  const empty = { asOfI: end, asOfT: null, pivots: [], counts: [], scenario: null };
  if (end < 30) return empty;

  const atr = atrSeries(bars.slice(0, end + 1), atrLen);
  const piv = zigzag(bars, { strength, upto: end });
  if (piv.length < 3) return { ...empty, asOfT: bars[end].t };
  return countFrom(piv.slice(-MAX_PIVOTS),
    { close: bars[end].c, atr: atr[end], end, t: bars[end].t });
}

/**
 * The enumeration itself, given an alternating pivot window.
 *
 * Split out of `countAsOf` for one reason: fitting the weights means running
 * this at tens of thousands of bars, and `countAsOf` recomputes the whole pivot
 * set and the whole ATR series on every call -- fine for one bar on a chart,
 * quadratic over a series. A caller that walks forward can keep both
 * incrementally and hand the window in. `tests/test_elliott.py` checks the two
 * paths agree, because a fast path that quietly disagrees with the app is worse
 * than no fast path.
 */
export function countFrom(window, { close, atr: a, end, t }) {
  const empty = { asOfI: end, asOfT: t, pivots: [], counts: [], scenario: null };
  if (!window || window.length < 3) return empty;

  const counts = [];
  for (let s = 0; s + 2 < window.length; s++) {
    const seq = window.slice(s);
    const dir = seq[0].kind === 'L' ? 1 : -1;      // an up impulse starts at a low
    const prices = seq.map((x) => x.price);

    /* IMPULSE, complete or in progress. `k` is how many wave ends are present;
       the wave in progress is the one after the last confirmed pivot. */
    for (let k = 2; k <= Math.min(6, seq.length); k++) {
      const p = prices.slice(0, k);
      const bad = checkRules(p, dir);
      if (bad) continue;
      const waveNow = k - 1 === 5 ? 6 : k;          // 6 == impulse complete
      if (waveNow > 5) continue;
      const ev = impulseEvidence(p, dir);
      const ok = ev.filter((e) => e.ok).length;
      counts.push({
        kind: 'impulse', dir, waveNow,
        pivots: seq.slice(0, k).map((x) => ({ i: x.i, t: x.t, price: x.price })),
        label: `impulse ${dir > 0 ? 'up' : 'down'} — wave ${waveNow}`,
        evidence: ev, ruleBreak: null,
        score: ok + 1 + 0.35 * (k - 2),             // longer admissible counts say more
        invalidation: invalidationFor('impulse', waveNow, p, dir),
        target: targetZone('impulse', waveNow, p, dir),
        outlook: outlook('impulse', waveNow, dir),
      });
    }

    /* CORRECTION A-B-C in the opposite direction. */
    for (let k = 2; k <= Math.min(4, seq.length); k++) {
      const p = prices.slice(0, k);
      if (checkAbc(p, dir)) continue;
      const waveNow = ['A', 'B', 'C'][k - 2] || 'C';
      const ev = abcEvidence(p);
      const ok = ev.filter((e) => e.ok).length;
      counts.push({
        kind: 'abc', dir, waveNow,
        pivots: seq.slice(0, k).map((x) => ({ i: x.i, t: x.t, price: x.price })),
        label: `correction ${dir > 0 ? 'up' : 'down'} — wave ${waveNow}`,
        evidence: ev, ruleBreak: null,
        score: ok + 0.6 + 0.2 * (k - 2),            // below impulses of equal fit
        invalidation: invalidationFor('abc', waveNow, p, dir),
        target: null,
        outlook: outlook('abc', waveNow, dir),
      });
    }
  }

  /* A count whose invalidation price has ALREADY been traded through is dead on
     arrival -- it describes a sequence the bars have refuted. */
  const alive = counts.filter((c) => {
    if (!Number.isFinite(c.invalidation)) return false;
    return c.dir > 0 ? close > c.invalidation : close < c.invalidation;
  });

  /* Prefer counts anchored in the recent past: a five-wave sequence that ended
     300 bars ago is true and useless. Decay is in ATR-normalised BARS rather
     than in price, so it means the same thing on every instrument. */
  const scored = alive.map((c) => {
    const last = c.pivots[c.pivots.length - 1];
    const age = end - last.i;
    const decay = 1 / (1 + age / 60);
    return { ...c, age, score: c.score * decay };
  }).sort((x, y) => y.score - x.score);

  /* Keep the readings a person would actually weigh, one per (kind, waveNow,
     direction) so the list is three DIFFERENT arguments rather than three
     alignments of the same one. */
  const seen = new Set();
  const top = [];
  for (const c of scored) {
    const key = `${c.kind}:${c.dir}:${c.waveNow}`;
    if (seen.has(key)) continue;
    seen.add(key);
    top.push(c);
    if (top.length === 3) break;
  }

  const total = top.reduce((s, c) => s + c.score, 0) || 1;
  for (const c of top) c.share = c.score / total;
  /* Projected only for the counts that survive selection: a path for a reading
     nobody sees is cost without a claim. */
  for (const c of top) c.projection = projectPath(c);

  /* Scenario weights are the counts' shares grouped by what they EXPECT, which
     is the only summary that can be scored against later bars. */
  const scenario = { continuation: 0, correction: 0, reversal: 0 };
  for (const c of top) scenario[c.outlook] += c.share;

  return {
    asOfI: end,
    asOfT: t,
    close,
    atr: a,
    /* Attached by the caller: the cone is a property of the SERIES rather than
       of the count, and it is measured in js/chart/cone.js. */
    cone: null,
    pivots: window.map((x) => ({ i: x.i, t: x.t, price: x.price, kind: x.kind })),
    counts: top,
    scenario,
  };
}

/**
 * Score a recorded belief against what price actually did.
 *
 * The outcome is measured in ATR AT THE TIME OF THE BELIEF, not in price, so
 * one instrument's verdict means the same as another's. `horizon` bars ahead is
 * a choice, and it is recorded with the result rather than hidden in it.
 */
export function scoreBelief(belief, bars, { horizon = 24, moveAtr = 0.75 } = {}) {
  const i = belief.asOfI;
  const j = Math.min(bars.length - 1, i + horizon);
  if (j <= i || !Number.isFinite(belief.atr) || belief.atr <= 0) return null;

  const dir = belief.counts?.[0]?.dir || 1;
  const move = (bars[j].c - belief.close) / belief.atr;
  const signed = move * dir;                 // positive == the count's direction

  let actual = 'correction';
  if (signed >= moveAtr) actual = 'continuation';
  else if (signed <= -moveAtr) actual = 'reversal';

  /* Invalidation is checked on WICKS over the whole horizon, not on the closing
     price: a count is dead the moment price trades through the level, and
     asking only where it closed would let a count survive a day it did not. */
  let invalidated = false;
  const lv = belief.counts?.[0]?.invalidation;
  if (Number.isFinite(lv)) {
    for (let k = i + 1; k <= j; k++) {
      if (dir > 0 ? bars[k].l < lv : bars[k].h > lv) { invalidated = true; break; }
    }
  }

  const expected = belief.counts?.[0]?.outlook || null;
  return {
    horizon, moveAtr, moveAtrActual: signed,
    expected, actual, hit: expected === actual,
    invalidated,
    claimedShare: belief.counts?.[0]?.share ?? null,
  };
}
