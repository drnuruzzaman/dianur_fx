/* elliott_fit.mjs — fit the counter's weights on recorded beliefs, walk-forward.
 *
 * THE PROBLEM THIS ANSWERS. `js/chart/elliott.js` scores a count by counting how
 * many guidelines it satisfies, with FLAT weights, because there was no evidence
 * for any other weighting and inventing one would have made the score look more
 * informed than it was. Measured, that scorer is not merely uninformative: its
 * confidence is INVERTED. On XAUUSD 1H the 75-100% bucket claimed 90% and
 * delivered 25%, and the Brier score lost to climatology by 66%.
 *
 * So: learn the weights from what actually followed, and see whether a fitted
 * scorer can beat counting outcomes. If it cannot, the guidelines carry no
 * usable signal in this form and that is the finding.
 *
 * HOW IT STAYS HONEST
 *
 *   WALK-FORWARD. Weights that predict bar i are fitted only on samples whose
 *   outcome was already known at i -- which is i - horizon, not i. A model
 *   trained on labels from the last `horizon` bars is trained on the future.
 *
 *   PER INSTRUMENT, PER TIMEFRAME. Pooling instruments was the confound that
 *   produced a +0.784 correlation out of nothing earlier in this project. Each
 *   cell is fitted and scored alone.
 *
 *   THE SAME ENGINE. Features come from `countFrom` in the app's own module, not
 *   from a reimplementation, so a fitted weight means the same thing in the
 *   panel as it did here. The pivot walk is incremental only because
 *   `countAsOf` recomputes everything per bar and that is quadratic over 66,000
 *   bars; `tests/test_elliott.py` checks the two paths agree.
 *
 *   usage:  node tools/elliott_fit.mjs XAUUSD.a 1h [horizon] [from-year]
 */

import fs from 'node:fs';
import path from 'node:path';
import zlib from 'node:zlib';
import { countFrom, calibration } from '../js/chart/elliott.js';
import { findPivots } from '../js/chart/trendlines.js';
import { atrSeries } from '../js/chart/tlengine.js';

const ROOT = path.resolve(path.dirname(new URL(import.meta.url).pathname).replace(/^\/([A-Za-z]:)/, '$1'), '..');
const MAX_PIVOTS = 14;          // must match elliott.js
const STRENGTH = 3;

/* ------------------------------------------------------------------- data */

function loadBars(symbol, tf, fromYear) {
  const dir = path.join(ROOT, 'data', 'bars', symbol, tf);
  const files = fs.readdirSync(dir).filter((f) => f.endsWith('.csv.gz')).sort();
  const bars = [];
  for (const f of files) {
    if (fromYear && Number(f.slice(0, 4)) < fromYear) continue;
    const text = zlib.gunzipSync(fs.readFileSync(path.join(dir, f))).toString('utf8');
    for (const line of text.split('\n')) {
      if (!line || line[0] === 't') continue;
      const p = line.split(',');
      bars.push({ t: Number(p[0]) * 1000, o: +p[1], h: +p[2], l: +p[3], c: +p[4] });
    }
  }
  return bars;
}

/* ------------------------------------------------------- incremental walk */

/**
 * Yield a belief at every `stride`-th bar without recomputing the world.
 *
 * Pivots are found once for the whole series -- `findPivots` is causal and each
 * pivot carries the bar it was CONFIRMED at, so admitting them in confirmation
 * order is the same filter `zigzag` applies, done once instead of per bar.
 */
function* walk(bars, { stride, warmup }) {
  const atr = atrSeries(bars, 14);
  const { highs, lows } = findPivots(bars, STRENGTH);
  const all = [];
  for (const p of highs) all.push({ ...p, kind: 'H' });
  for (const p of lows) all.push({ ...p, kind: 'L' });
  all.sort((a, b) => a.confirmedI - b.confirmedI || a.i - b.i);

  let next = 0;
  const zz = [];                       // the alternating window, grown in place
  const pending = [];

  for (let i = 0; i < bars.length; i++) {
    while (next < all.length && all[next].confirmedI <= i) pending.push(all[next++]);
    if (pending.length) {
      /* Confirmation order is not bar order -- a low confirmed late can belong
         before a high confirmed early -- so the newly admitted pivots are
         merged by bar index, then folded in with zigzag's own rule. */
      pending.sort((a, b) => a.i - b.i);
      for (const p of pending) {
        const last = zz[zz.length - 1];
        if (!last) { zz.push(p); continue; }
        if (last.kind === p.kind) {
          const better = p.kind === 'H' ? p.price > last.price : p.price < last.price;
          if (better) zz[zz.length - 1] = p;
        } else if (p.i > last.i) {
          zz.push(p);
        }
      }
      pending.length = 0;
      if (zz.length > MAX_PIVOTS * 3) zz.splice(0, zz.length - MAX_PIVOTS * 3);
    }
    if (i < warmup || i % stride) continue;
    if (zz.length < 3) continue;
    const b = countFrom(zz.slice(-MAX_PIVOTS),
      { close: bars[i].c, atr: atr[i], end: i, t: bars[i].t });
    if (b.counts.length) yield b;
  }
}

/* ---------------------------------------------------------------- labels */

const CLASSES = ['continuation', 'correction', 'reversal'];

/** What price did over `horizon`, in ATR at the time of the belief. */
function outcome(bars, b, horizon, moveAtr = 0.75) {
  const j = b.asOfI + horizon;
  if (j > bars.length - 1 || !(b.atr > 0)) return null;
  const dir = b.counts[0].dir;
  const signed = ((bars[j].c - b.close) / b.atr) * dir;
  if (signed >= moveAtr) return 'continuation';
  if (signed <= -moveAtr) return 'reversal';
  return 'correction';
}

/* --------------------------------------------------------------- features
 *
 * One row per COUNT, not per bar: the question the model is asked is "will the
 * class this count names actually happen", which is a property of the count.
 * Per-bar shares are then the softmax of the counts at that bar.
 *
 * Every feature is either a rule outcome or a shape ratio the guidelines are
 * already stated in. Nothing here is a price or a date, so a weight fitted on
 * gold in 2005 means the same thing on gold in 2025.
 */
const EV_KEYS = ['w2 retrace', 'w3 extension', 'w3 fib', 'w4 retrace', 'alternation',
                 'w5 vs w1', 'B retrace', 'C vs A'];

function features(c) {
  const f = [1];                                   // bias
  f.push(c.kind === 'impulse' ? 1 : 0);
  for (const w of [1, 2, 3, 4, 5]) f.push(c.waveNow === w ? 1 : 0);
  for (const w of ['A', 'B', 'C']) f.push(c.waveNow === w ? 1 : 0);
  for (const k of EV_KEYS) {
    const e = c.evidence.find((x) => x.key === k);
    f.push(e ? 1 : 0);                             // present at all
    f.push(e && e.ok ? 1 : 0);                     // and satisfied
  }
  f.push(Math.min(1, (c.age || 0) / 60));          // staleness, capped
  f.push(Math.min(1, c.pivots.length / 6));        // how much of a count it is
  f.push(c.dir > 0 ? 1 : 0);
  return f;
}

/* ------------------------------------------------- logistic regression, SGD */

function fit(rows, dim, { epochs = 60, lr = 0.08, l2 = 1e-4 } = {}) {
  const w = new Float64Array(dim);
  for (let e = 0; e < epochs; e++) {
    for (const r of rows) {
      let z = 0;
      for (let k = 0; k < dim; k++) z += w[k] * r.x[k];
      const p = 1 / (1 + Math.exp(-z));
      const g = p - r.y;
      for (let k = 0; k < dim; k++) w[k] -= lr * (g * r.x[k] + l2 * w[k]);
    }
  }
  return w;
}

/**
 * SOFTMAX REGRESSION, three classes, trained on cross-entropy.
 *
 * Three independent binary models still had to be normalised to make a
 * distribution, and normalising three small probabilities inflates every one of
 * them -- which is the last of the artefactual overconfidence. A multinomial
 * trained on cross-entropy produces a distribution by construction, so what it
 * reports is what it learnt rather than what division did to it.
 */
function fitSoftmax(rows, dim, K, { epochs = 60, lr = 0.08, l2 = 1e-4 } = {}) {
  const W = Array.from({ length: K }, () => new Float64Array(dim));
  const p = new Float64Array(K);
  for (let e = 0; e < epochs; e++) {
    for (const r of rows) {
      let max = -Infinity;
      for (let c = 0; c < K; c++) {
        let z = 0;
        for (let k = 0; k < dim; k++) z += W[c][k] * r.x[k];
        p[c] = z;
        if (z > max) max = z;
      }
      let sum = 0;
      for (let c = 0; c < K; c++) { p[c] = Math.exp(p[c] - max); sum += p[c]; }
      for (let c = 0; c < K; c++) {
        const g = p[c] / sum - (r.y === c ? 1 : 0);
        for (let k = 0; k < dim; k++) W[c][k] -= lr * (g * r.x[k] + l2 * W[c][k]);
      }
    }
  }
  return W;
}

function predictSoftmax(W, x, T = 1) {
  const K = W.length;
  const z = new Array(K);
  let max = -Infinity;
  for (let c = 0; c < K; c++) {
    let s2 = 0;
    for (let k = 0; k < x.length; k++) s2 += W[c][k] * x[k];
    z[c] = s2 / T;
    if (z[c] > max) max = z[c];
  }
  let sum = 0;
  for (let c = 0; c < K; c++) { z[c] = Math.exp(z[c] - max); sum += z[c]; }
  return z.map((v) => v / sum);
}

/**
 * TEMPERATURE SCALING, the decisive test.
 *
 * A model can be badly calibrated and still useful: if it RANKS correctly and
 * merely exaggerates, dividing the logits by one number fixes it. If it cannot
 * be fixed by one number, the exaggeration was not the problem -- the ordering
 * carries nothing and no amount of rescaling will make the forecast informative.
 *
 * `T` is fitted on a VALIDATION window that the weights never saw and that sits
 * entirely before the test block, so this is not the test set calibrating
 * itself. Searched rather than optimised: one parameter over a bounded range is
 * not worth a solver.
 */
function fitTemperature(W, rows) {
  let best = 1;
  let bestLoss = Infinity;
  for (let T = 0.25; T <= 12; T += 0.05) {
    let loss = 0;
    for (const r of rows) {
      const p = predictSoftmax(W, r.x, T);
      loss -= Math.log(Math.max(1e-12, p[r.y]));
    }
    if (loss < bestLoss) { bestLoss = loss; best = T; }
  }
  return best;
}

const predict = (w, x) => {
  let z = 0;
  for (let k = 0; k < w.length; k++) z += w[k] * x[k];
  return 1 / (1 + Math.exp(-z));
};

/* ------------------------------------------------------------------- main */

const [, , SYMBOL = 'XAUUSD.a', TF = '1h', HZ = '24', FROM = '0'] = process.argv;
const horizon = Number(HZ);
const fromYear = Number(FROM) || 0;
const stride = 5;

const bars = loadBars(SYMBOL, TF, fromYear);
process.stderr.write(`${SYMBOL} ${TF}: ${bars.length} bars\n`);

/* Collect every belief and its outcome first. The walk is the expensive part
   and it is deterministic, so it is done once and reused by both the flat
   scorer and every walk-forward refit. */
const samples = [];
for (const b of walk(bars, { stride, warmup: 300 })) {
  const y = outcome(bars, b, horizon);
  if (!y) continue;
  samples.push({
    i: b.asOfI,
    counts: b.counts.map((c) => ({
      outlook: c.outlook, score: c.score, share: c.share, x: features(c),
    })),
    actual: y,
  });
}
process.stderr.write(`${samples.length} beliefs with a settled outcome\n`);

const DIM = samples[0].counts[0].x.length;

/* PER-BAR FEATURES, for the three-class head below.
 *
 * The per-count head asks "will this count's class happen" and then normalises
 * across counts -- which is where the first fit's overconfidence came from.
 * Dividing three unlikely probabilities by their own sum forces them to add to
 * one, so a bar where nothing is likely still reports 90% for something. That
 * is an artefact of the aggregation, not a fact about the market, and it was
 * inflating every bucket.
 *
 * The fix is to predict the OUTCOME CLASS directly: one binary model per class,
 * trained on the bar, each free to say "unlikely" without another class having
 * to absorb the leftover. */
function barFeatures(s) {
  const f = s.counts[0].x.slice();               // the primary count's shape
  for (const cls of CLASSES) {
    /* how much of the reading points at this class, and whether anything does */
    const share = s.counts.filter((c) => c.outlook === cls)
      .reduce((a, c) => a + c.share, 0);
    f.push(share);
    f.push(share > 0 ? 1 : 0);
  }
  f.push(s.counts.length / 3);
  return f;
}

/* WALK-FORWARD. Refit every `block` samples on everything whose outcome was
   known by then -- `horizon / stride` samples back, not up to the boundary,
   because a sample's label is only readable `horizon` bars after it. */
const block = 250;
const rows = [];
const lag = Math.ceil(horizon / stride);
const BDIM = barFeatures(samples[0]).length;
let w = null;                  // per-count head
let W = null;                  // the three-class head
let temp = 1;                  // its temperature, fitted on held-out rows
const out = [];
const outClass = [];

for (let start = block; start < samples.length; start += block) {
  const trainEnd = start - lag;
  if (trainEnd > 0) {
    const train = [];
    for (let k = 0; k < trainEnd; k++) {
      for (const c of samples[k].counts) {
        train.push({ x: c.x, y: samples[k].actual === c.outlook ? 1 : 0 });
      }
    }
    w = fit(train, DIM);

    const rowsBar = [];
    for (let k = 0; k < trainEnd; k++) {
      rowsBar.push({ x: barFeatures(samples[k]), y: CLASSES.indexOf(samples[k].actual) });
    }
    /* The last 20% of the settled history is held out from the weights and used
       only to fit the temperature. Fitting both on the same rows would let the
       model calibrate itself against its own training error, which is exactly
       the number that is too good. */
    const cut = Math.floor(rowsBar.length * 0.8);
    W = fitSoftmax(rowsBar.slice(0, cut), BDIM, CLASSES.length);
    temp = fitTemperature(W, rowsBar.slice(cut));
  }
  if (!w) continue;
  for (let k = start; k < Math.min(start + block, samples.length); k++) {
    const s = samples[k];
    const ps = s.counts.map((c) => predict(w, c.x));
    const tot = ps.reduce((a, b2) => a + b2, 0) || 1;
    const scenario = { continuation: 0, correction: 0, reversal: 0 };
    s.counts.forEach((c, j) => { scenario[c.outlook] += ps[j] / tot; });
    let best = 0;
    for (let j = 1; j < ps.length; j++) if (ps[j] > ps[best]) best = j;
    out.push({ expected: s.counts[best].outlook, actual: s.actual, scenario, trained: trainEnd });

    /* The per-class head. Normalised only to make the three sum to one, which a
       Brier score expects; each was fitted free to be small. */
    const ps2 = predictSoftmax(W, barFeatures(s), temp);
    const sc2 = {};
    let pick = 0;
    CLASSES.forEach((cls, j) => { sc2[cls] = ps2[j]; if (ps2[j] > ps2[pick]) pick = j; });
    outClass.push({ expected: CLASSES[pick], actual: s.actual, scenario: sc2 });
  }
}

/* The FLAT scorer on exactly the same rows, so the comparison is like for like
   -- same bars, same labels, same horizon. */
const flat = out.map((r, k) => {
  const s = samples[k + block];
  const scenario = { continuation: 0, correction: 0, reversal: 0 };
  for (const c of s.counts) scenario[c.outlook] += c.share;
  let best = s.counts[0];
  for (const c of s.counts) if (c.share > best.share) best = c;
  return { expected: best.outlook, actual: s.actual, scenario };
}).filter(Boolean);

const acc = (rs) => rs.filter((r) => r.expected === r.actual).length / rs.length;
const majority = (rs) => {
  const n = {};
  for (const r of rs) n[r.actual] = (n[r.actual] || 0) + 1;
  return Math.max(...Object.values(n)) / rs.length;
};

const report = (name, rs) => {
  const c = calibration(rs);
  return {
    name, n: rs.length,
    accuracy: +acc(rs).toFixed(4),
    baseline: +majority(rs).toFixed(4),
    brier: +c.brier.toFixed(4),
    climatology: +c.brierBase.toFixed(4),
    skill: +c.skill.toFixed(4),
    buckets: c.buckets.map((b) => ({
      band: `${Math.round(b.lo * 100)}-${Math.round(Math.min(b.hi, 1) * 100)}%`,
      n: b.n, said: +b.claimed.toFixed(3), happened: +b.happened.toFixed(3),
      gap: +b.gap.toFixed(3),
    })),
  };
};

console.log(JSON.stringify({
  symbol: SYMBOL, tf: TF, horizon, stride, bars: bars.length,
  scored: out.length,
  flat: report('flat guideline weights', flat),
  fitted: report('fitted, per-count head', out),
  fittedClass: report('fitted softmax, temperature-scaled', outClass),
  temperature: +temp.toFixed(2),
  weights: w ? [...w].map((x) => +x.toFixed(4)) : null,
}, null, 1));
