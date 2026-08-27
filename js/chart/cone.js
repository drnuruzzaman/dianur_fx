/* cone.js — empirical forecast cones, and the measurement that decides whether
 * they mean anything.
 *
 * A projected path is a claim about where price will be to the tick, and nothing
 * in this project supports that. A CONE is a different kind of object: at this
 * bar, over the history available at this bar, how far did price get 1, 2, ... n
 * bars later? That is a fact about the instrument, not a forecast, and it can be
 * checked -- a 90% band should contain the outcome about 90% of the time.
 *
 * TWO CONES, and the comparison between them is the point:
 *
 *   UNCONDITIONAL   every historical bar contributes. The base rate of movement.
 *   CONDITIONAL     only bars whose STATE resembled this one -- trend, position
 *                   in range, volatility regime, momentum. The claim being
 *                   tested is that the state carries information about the
 *                   dispersion, and `coverage()` is what settles it.
 *
 * A conditional cone that is no better calibrated and no narrower than the
 * unconditional one has learnt nothing, and saying so is the whole reason both
 * are computed rather than only the interesting-sounding one.
 *
 * CAUSALITY. Every sample runs to `upto - ahead`, so the widest step is built
 * from returns that had already finished at the cursor. Sizing a cone from the
 * dispersion of the bars it is drawn over is the future leaking in through the
 * one number nobody would think to check.
 *
 * SCALE. Returns are divided by the ATR AT THAT HISTORICAL BAR and multiplied by
 * the ATR now, so a quiet decade and a violent one contribute the same shape
 * instead of the quiet one flattening the cone.
 */

import { atrSeries } from './tlengine.js';

export const QS = [0.1, 0.25, 0.5, 0.75, 0.9];

/* Fewer than this and a quantile is an anecdote. A step that cannot reach it is
   dropped rather than drawn from what it has -- a cone narrowing at the far end
   because the sample thinned would be exactly backwards. */
const MIN_SAMPLES = 60;

/**
 * The state each historical bar is matched on.
 *
 * Deliberately cheap and deliberately ATR-relative: every component is a ratio
 * or a rank, so a state from 2005 is comparable with one from 2025 and nothing
 * here carries a price level or a date. Elliott's own reading is NOT part of the
 * match -- computing a count at every historical bar costs a full pivot walk per
 * bar, and the scenario mixture below is where the count gets its say.
 */
export function stateSeries(bars, { atrLen = 14, lookback = 60 } = {}) {
  const n = bars.length;
  const atr = atrSeries(bars, atrLen);
  const out = new Array(n).fill(null);
  /* Momentum as an EMA spread, in ATR. Same shape as the MACD sign the panel
     shows, without pulling the indicator module in for one number. */
  const fast = new Float64Array(n);
  const slow = new Float64Array(n);
  const kf = 2 / (12 + 1);
  const ks = 2 / (26 + 1);
  for (let i = 0; i < n; i++) {
    fast[i] = i ? fast[i - 1] + kf * (bars[i].c - fast[i - 1]) : bars[i].c;
    slow[i] = i ? slow[i - 1] + ks * (bars[i].c - slow[i - 1]) : bars[i].c;
  }
  /* A rolling ATR rank needs a window; 250 bars is the same order the rest of
     this project uses for a volatility regime. */
  const VOL_WIN = 250;
  for (let i = lookback; i < n; i++) {
    const a = atr[i];
    if (!(a > 0)) continue;
    let hi = -Infinity;
    let lo = Infinity;
    for (let k = i - lookback + 1; k <= i; k++) {
      if (bars[k].h > hi) hi = bars[k].h;
      if (bars[k].l < lo) lo = bars[k].l;
    }
    const range = hi - lo;
    let rank = 0;
    let seen = 0;
    for (let k = Math.max(atrLen, i - VOL_WIN + 1); k <= i; k++) {
      if (!(atr[k] > 0)) continue;
      seen++;
      if (atr[k] <= a) rank++;
    }
    out[i] = [
      /* where in its recent range price is sitting: 0 at the low, 1 at the high */
      range > 0 ? (bars[i].c - lo) / range : 0.5,
      /* how far the range itself is, in ATR -- a wide range and a narrow one at
         the same position are not the same state */
      Math.min(6, range / a) / 6,
      /* momentum, squashed so one violent bar cannot dominate the distance */
      Math.max(-1, Math.min(1, (fast[i] - slow[i]) / a)),
      /* volatility regime as a rank in [0,1] */
      seen ? rank / seen : 0.5,
      /* trend: where the close sits against the slow EMA, in ATR */
      Math.max(-1, Math.min(1, (bars[i].c - slow[i]) / a)),
    ];
  }
  return { state: out, atr };
}

const quantiles = (sorted, qs) => {
  const q = {};
  for (const p of qs) {
    q[p] = sorted[Math.min(sorted.length - 1, Math.max(0, Math.round(p * (sorted.length - 1))))];
  }
  return q;
};

/**
 * Build both cones at `upto`.
 *
 * `k` is how many historical analogues the conditional cone keeps. Too few and
 * the quantiles are noise; too many and it converges on the unconditional cone
 * and stops being conditional at all. 400 of a few thousand candidates is the
 * middle, and `coverage()` is what says whether the choice was any good.
 */
export function cones(bars, {
  upto = null, steps = 24, k = 400, atrLen = 14, lookback = 2500,
  precomputed = null,
} = {}) {
  const end = upto === null ? bars.length - 1 : Math.min(upto, bars.length - 1);
  const { state, atr } = precomputed || stateSeries(bars, { atrLen });
  const a = atr[end];
  const here = state[end];
  if (!(a > 0) || !here || end < 200) return null;

  /* Candidates: every bar with a state, far enough back that its own forward
     window closed before the cursor. */
  const from = Math.max(60, end - lookback);
  const cand = [];
  for (let i = from; i < end - steps; i++) {
    const s = state[i];
    if (!s) continue;
    let d = 0;
    for (let j = 0; j < s.length; j++) { const dd = s[j] - here[j]; d += dd * dd; }
    cand.push({ i, d });
  }
  if (cand.length < MIN_SAMPLES * 2) return null;
  cand.sort((x, y) => x.d - y.d);
  const near = cand.slice(0, Math.min(k, cand.length));

  const build = (idx) => {
    const bands = [];
    for (let ahead = 1; ahead <= steps; ahead++) {
      const moves = [];
      for (const c of idx) {
        const i = c.i;
        if (i + ahead > end) continue;
        const ai = atr[i];
        if (!(ai > 0)) continue;
        moves.push((bars[i + ahead].c - bars[i].c) / ai);
      }
      if (moves.length < MIN_SAMPLES) break;
      moves.sort((x, y) => x - y);
      const q = quantiles(moves, QS);
      const price = {};
      for (const p of QS) price[p] = bars[end].c + q[p] * a;
      bands.push({ ahead, n: moves.length, atrQ: q, q: price });
    }
    return bands;
  };

  const conditional = build(near);
  const unconditional = build(cand);
  if (!conditional.length || !unconditional.length) return null;
  return {
    asOfI: end, close: bars[end].c, atr: a, k: near.length,
    candidates: cand.length,
    conditional, unconditional,
  };
}

/**
 * How often price has historically travelled at least this far, in this
 * direction, within `horizon` bars.
 *
 * This is what turns a target into a statement. A level is not "the target" --
 * it is a distance, and the distance either sits inside the range price
 * normally covers in the holding period or it does not. Measured on bars whose
 * own window closed before `upto`, so it says nothing the cursor could not know.
 */
export function reachRate(bars, {
  upto = null, horizon = 24, distAtr = 0, dir = 1, atrLen = 14,
  lookback = 2500, precomputed = null,
} = {}) {
  const end = upto === null ? bars.length - 1 : Math.min(upto, bars.length - 1);
  const { atr } = precomputed || stateSeries(bars, { atrLen });
  const hi = end - horizon;
  const lo = Math.max(atrLen + 1, hi - lookback);
  if (hi - lo < MIN_SAMPLES) return null;
  let n = 0;
  let hit = 0;
  for (let i = lo; i < hi; i++) {
    const a = atr[i];
    if (!(a > 0)) continue;
    n++;
    if (((bars[i + horizon].c - bars[i].c) / a) * dir >= distAtr) hit++;
  }
  return n >= MIN_SAMPLES ? { n, rate: hit / n } : null;
}

/**
 * SCENARIO-CONDITIONAL CONES, and their mixture.
 *
 * The idea is the one worth testing: a wave-3 continuation and a wave-4
 * correction are different futures, so blending them into one band before
 * looking is throwing away the only thing the count claims to know. Build a cone
 * per scenario from the historical bars that RESOLVED into that scenario, then
 * combine them by the count's own weights.
 *
 * A historical bar's scenario is read in the CURRENT count's directional frame:
 * `(close[i+h] - close[i]) / atr[i] * dir`, bucketed at the same +/-0.75 ATR the
 * scorer uses. So "continuation" means "moved the way this count is pointing",
 * which is the question the count is actually asking.
 *
 * MIXING QUANTILES IS NOT AVERAGING THEM. The P10 of a mixture is not the mean
 * of the components' P10s -- that identity does not hold for any quantile except
 * by coincidence, and using it produces a band that is too narrow exactly when
 * the scenarios disagree, which is when the width matters. The mixture is built
 * by pooling the samples with per-class weights and taking WEIGHTED quantiles of
 * the pool, which is the mixture distribution by construction.
 */
export const SCENARIOS = ['continuation', 'correction', 'reversal'];

function classify(bars, atr, i, ahead, dir, moveAtr) {
  const ai = atr[i];
  if (!(ai > 0) || i + ahead > bars.length - 1) return null;
  const signed = ((bars[i + ahead].c - bars[i].c) / ai) * dir;
  if (signed >= moveAtr) return 'continuation';
  if (signed <= -moveAtr) return 'reversal';
  return 'correction';
}

/** Weighted quantiles of {v, w} samples. `w` need not sum to one. */
export function weightedQuantiles(samples, qs) {
  const xs = samples.slice().sort((a, b) => a.v - b.v);
  const total = xs.reduce((a, x) => a + x.w, 0);
  const out = {};
  if (!(total > 0)) return out;
  let k = 0;
  let acc = 0;
  for (const q of qs.slice().sort((a, b) => a - b)) {
    const target = q * total;
    while (k < xs.length - 1 && acc + xs[k].w < target) { acc += xs[k].w; k++; }
    out[q] = xs[k].v;
  }
  return out;
}

/**
 * Per-scenario cones plus the weighted mixture.
 *
 * `weights` is the count's scenario distribution. A scenario with too few
 * historical analogues is dropped and its weight redistributed -- drawing a band
 * from twenty samples would put a confident-looking edge on nothing.
 */
export function scenarioCones(bars, {
  upto = null, steps = 24, dir = 1, weights = null, refHorizon = null,
  atrLen = 14, lookback = 2500, moveAtr = 0.75, precomputed = null,
} = {}) {
  const end = upto === null ? bars.length - 1 : Math.min(upto, bars.length - 1);
  const { atr } = precomputed || stateSeries(bars, { atrLen });
  const a = atr[end];
  if (!(a > 0) || end < 200) return null;
  const href = refHorizon || steps;

  /* Every candidate, labelled by what it resolved into at the reference
     horizon. Candidates stop at `end - steps` so the longest step of every cone
     is built from a window that closed before the cursor. */
  const from = Math.max(60, end - lookback);
  const byClass = { continuation: [], correction: [], reversal: [] };
  for (let i = from; i < end - steps; i++) {
    const cls = classify(bars, atr, i, href, dir, moveAtr);
    if (cls) byClass[cls].push(i);
  }

  const usable = SCENARIOS.filter((c) => byClass[c].length >= MIN_SAMPLES);
  if (!usable.length) return null;

  /* Weights over the scenarios that HAVE support, renormalised. A weight on a
     scenario with no analogues is a weight on nothing. */
  const w = {};
  let wsum = 0;
  for (const c of usable) {
    const raw = weights && Number.isFinite(weights[c]) ? weights[c] : 1 / usable.length;
    w[c] = Math.max(0, raw);
    wsum += w[c];
  }
  if (!(wsum > 0)) for (const c of usable) { w[c] = 1 / usable.length; wsum = 1; }
  for (const c of usable) w[c] /= wsum;

  const per = {};
  const mixture = [];
  for (let ahead = 1; ahead <= steps; ahead++) {
    const pooled = [];
    let ok = true;
    for (const c of usable) {
      const moves = [];
      for (const i of byClass[c]) {
        const ai = atr[i];
        if (!(ai > 0) || i + ahead > end) continue;
        moves.push((bars[i + ahead].c - bars[i].c) / ai);
      }
      if (moves.length < MIN_SAMPLES) { ok = false; break; }
      moves.sort((x, y) => x - y);
      const q = quantiles(moves, QS);
      const price = {};
      for (const p of QS) price[p] = bars[end].c + q[p] * a;
      (per[c] ||= []).push({ ahead, n: moves.length, atrQ: q, q: price });
      /* Each class contributes its samples at its own weight, so the pool IS
         the mixture rather than an approximation of it. */
      const each = w[c] / moves.length;
      for (const m of moves) pooled.push({ v: m, w: each });
    }
    if (!ok) break;
    const q = weightedQuantiles(pooled, QS);
    const price = {};
    for (const p of QS) price[p] = bars[end].c + q[p] * a;
    mixture.push({ ahead, n: pooled.length, atrQ: q, q: price });
  }
  if (!mixture.length) return null;
  return {
    asOfI: end, close: bars[end].c, atr: a, dir, weights: w,
    support: Object.fromEntries(SCENARIOS.map((c) => [c, byClass[c].length])),
    per, mixture,
  };
}

/**
 * COVERAGE AND INTERVAL SCORE -- whether the stated uncertainty matches reality.
 *
 * Coverage alone is not enough and the reason is worth stating: a cone from -10
 * ATR to +10 ATR has perfect coverage and says nothing. The INTERVAL SCORE
 * (Gneiting-Raftery) charges for width and adds a penalty proportional to how
 * far outside the interval the outcome fell, so it cannot be gamed by widening.
 * Lower is better, and it is reported in ATR so it is comparable across cells.
 *
 *   score = width + (2/alpha) * (miss below) + (2/alpha) * (miss above)
 */
export function intervalScore(lo, hi, actual, alpha) {
  let s = hi - lo;
  if (actual < lo) s += (2 / alpha) * (lo - actual);
  if (actual > hi) s += (2 / alpha) * (actual - hi);
  return s;
}

/**
 * Score a set of recorded cones against what happened.
 *
 * `rows` are {bands, close, atr, asOfI}; the bars are the truth. Everything is
 * reported per horizon, because a cone can be honest at four bars and hopeless
 * at thirty-two and an average over both would hide it.
 */
export function coverage(rows, bars, { horizons = [1, 2, 4, 8, 16, 24] } = {}) {
  const out = [];
  for (const h of horizons) {
    let n = 0;
    let in90 = 0;
    let in50 = 0;
    let w90 = 0;
    let w50 = 0;
    let is90 = 0;
    let medErr = 0;
    for (const r of rows) {
      const band = r.bands.find((b) => b.ahead === h);
      const j = r.asOfI + h;
      if (!band || j > bars.length - 1 || !(r.atr > 0)) continue;
      const actual = bars[j].c;
      n++;
      if (actual >= band.q[0.1] && actual <= band.q[0.9]) in90++;
      if (actual >= band.q[0.25] && actual <= band.q[0.75]) in50++;
      w90 += (band.q[0.9] - band.q[0.1]) / r.atr;
      w50 += (band.q[0.75] - band.q[0.25]) / r.atr;
      is90 += intervalScore(band.q[0.1], band.q[0.9], actual, 0.2) / r.atr;
      medErr += Math.abs(actual - band.q[0.5]) / r.atr;
    }
    if (!n) continue;
    out.push({
      horizon: h, n,
      /* The 10-90 band is an 80% interval, not 90 -- naming it after its edges
         would overstate what it claims by ten points. */
      cover80: in90 / n, cover50: in50 / n,
      width80Atr: w90 / n, width50Atr: w50 / n,
      intervalScoreAtr: is90 / n,
      medianAbsErrAtr: medErr / n,
    });
  }
  return out;
}
