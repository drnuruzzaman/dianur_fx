/* signals.js — six independent reads of the same bar, and an honest scorecard.
 *
 * Each component scores -100..+100 on the EXECUTION frame only. They are
 * deliberately different in kind rather than six flavours of the same momentum
 * reading, because six agreeing indicators that all measure trend is one
 * opinion repeated, not six:
 *
 *   trend      EMA separation, in ATR units
 *   momentum   RSI distance from 50
 *   macd       histogram, normalised by its own recent scale
 *   meanrev    position in the Bollinger band — DELIBERATELY INVERTED, so it
 *              disagrees with trend at extremes. That disagreement is the
 *              point: a composite where every component points the same way in
 *              a stretched market is a composite that cannot warn you.
 *   breakout   position in the Donchian range
 *   flow       tick volume on up bars versus down bars
 *
 * THE COMPOSITE IS NOT A PREDICTION. It is a weighted average of six opinions,
 * and the panel therefore refuses to show it without its own track record
 * beside it: walk-forward accuracy, the majority baseline that accuracy has to
 * beat, how the last N signals actually resolved, and the average move that
 * followed. A number with no scorecard is a number that cannot be wrong out
 * loud, and this project has spent enough effort proving that its trendlines
 * carry no placebo-adjusted edge to know how easily that happens.
 *
 * `walkForward` is a genuine out-of-sample measurement, not a fit statistic:
 * every prediction at bar i is made by a model that has seen only bars < i.
 */

import { atrSeries } from './tlengine.js';

const clamp100 = (v) => (v < -100 ? -100 : v > 100 ? 100 : v);

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

function rsiSeries(close, length = 14) {
  const n = close.length;
  const out = new Array(n).fill(NaN);
  if (n <= length) return out;
  let gain = 0, loss = 0;
  for (let i = 1; i <= length; i++) {
    const d = close[i] - close[i - 1];
    if (d >= 0) gain += d; else loss -= d;
  }
  gain /= length; loss /= length;
  out[length] = loss === 0 ? 100 : 100 - 100 / (1 + gain / loss);
  for (let i = length + 1; i < n; i++) {
    const d = close[i] - close[i - 1];
    gain = (gain * (length - 1) + (d > 0 ? d : 0)) / length;
    loss = (loss * (length - 1) + (d < 0 ? -d : 0)) / length;
    out[i] = loss === 0 ? 100 : 100 - 100 / (1 + gain / loss);
  }
  return out;
}

function stdev(values, length, i) {
  if (i < length - 1) return NaN;
  let m = 0;
  for (let k = i - length + 1; k <= i; k++) m += values[k];
  m /= length;
  let s = 0;
  for (let k = i - length + 1; k <= i; k++) s += (values[k] - m) * (values[k] - m);
  return Math.sqrt(s / length);
}

export const COMPONENTS = ['trend', 'momentum', 'macd', 'meanrev', 'breakout', 'flow'];

export const LABEL = {
  trend: 'Trend', momentum: 'Momentum', macd: 'Macd',
  meanrev: 'Meanrev', breakout: 'Breakout', flow: 'Flow',
};

/* Weights are equal on purpose. An unequal set would be a claim about which
   component predicts better, and nothing here has measured that — the
   scorecard below is what would earn the right to weight them. */
const WEIGHT = { trend: 1, momentum: 1, macd: 1, meanrev: 1, breakout: 1, flow: 1 };

/** Component scores for every bar. Each is -100..+100, NaN until warm. */
export function components(bars) {
  const n = bars.length;
  const close = bars.map((b) => b.c);
  const atr = atrSeries(bars, 14);
  const e12 = ema(close, 12), e26 = ema(close, 26), e21 = ema(close, 21), e50 = ema(close, 50);
  const rsi = rsiSeries(close, 14);
  const macdLine = close.map((_, i) => e12[i] - e26[i]);
  const signal = ema(macdLine.map((v) => (Number.isFinite(v) ? v : 0)), 9);

  const out = {};
  for (const k of COMPONENTS) out[k] = new Array(n).fill(NaN);

  for (let i = 0; i < n; i++) {
    const a = atr[i];
    if (Number.isFinite(e21[i]) && Number.isFinite(e50[i]) && a > 0) {
      /* One ATR of EMA separation is a decisive trend; saturate there. */
      out.trend[i] = clamp100(((e21[i] - e50[i]) / a) * 100);
    }
    if (Number.isFinite(rsi[i])) out.momentum[i] = clamp100((rsi[i] - 50) * 2.5);

    if (Number.isFinite(macdLine[i]) && Number.isFinite(signal[i]) && a > 0) {
      out.macd[i] = clamp100(((macdLine[i] - signal[i]) / (a * 0.25)) * 100);
    }

    if (i >= 19) {
      const sd = stdev(close, 20, i);
      let m = 0;
      for (let k = i - 19; k <= i; k++) m += close[k];
      m /= 20;
      if (sd > 0) {
        /* INVERTED: stretched above the mean scores negative. This is the one
           component allowed to fight the other five. */
        out.meanrev[i] = clamp100(-((close[i] - m) / (2 * sd)) * 100);
      }
      let hi = -Infinity, lo = Infinity;
      for (let k = i - 19; k <= i; k++) {
        if (bars[k].h > hi) hi = bars[k].h;
        if (bars[k].l < lo) lo = bars[k].l;
      }
      if (hi > lo) out.breakout[i] = clamp100(((close[i] - lo) / (hi - lo) - 0.5) * 200);

      let up = 0, dn = 0;
      for (let k = i - 19; k <= i; k++) {
        const v = bars[k].v || bars[k].tick_volume || 1;
        if (bars[k].c >= bars[k].o) up += v; else dn += v;
      }
      if (up + dn > 0) out.flow[i] = clamp100(((up - dn) / (up + dn)) * 100);
    }
  }
  return out;
}

/** The weighted composite, -100..+100. */
export function composite(comp, i) {
  let sum = 0, w = 0;
  for (const k of COMPONENTS) {
    const v = comp[k][i];
    if (!Number.isFinite(v)) continue;
    sum += v * WEIGHT[k];
    w += WEIGHT[k];
  }
  return w ? sum / w : NaN;
}

/**
 * Walk-forward scorecard — the part that keeps the composite honest.
 *
 * At each bar a logistic model trained ONLY on the previous `window` bars
 * predicts whether the next bar closes up. Nothing at bar i has seen bar i, so
 * `accuracy` is out-of-sample by construction rather than by assertion.
 *
 * `baseline` is the majority class over the same evaluated bars, and it is
 * reported next to accuracy for one reason: on FX the next bar is close to a
 * coin flip, so 56% sounds impressive until you notice the market printed 55%
 * up bars over the same span. Accuracy without its baseline is not a result.
 */
export function walkForward(bars, comp, { window = 250, lr = 0.06, epochs = 24 } = {}) {
  const n = bars.length;
  const rows = [];
  for (let i = 0; i < n - 1; i++) {
    const x = COMPONENTS.map((k) => (Number.isFinite(comp[k][i]) ? comp[k][i] / 100 : 0));
    if (COMPONENTS.every((k) => !Number.isFinite(comp[k][i]))) continue;
    rows.push({ i, x, y: bars[i + 1].c > bars[i].c ? 1 : 0 });
  }
  if (rows.length < window + 30) {
    return { accuracy: NaN, baseline: NaN, n: 0, pUp: NaN,
             hit: NaN, hitN: 0, avgBps: NaN };
  }

  let correct = 0, total = 0, ups = 0;
  let pUp = NaN;
  const D = COMPONENTS.length;

  /* Refit every `stride` bars rather than every bar: a one-bar-older model is
     a rounding error next to the cost of refitting on every one of thousands of
     bars in a UI thread, and the walk-forward guarantee is unaffected because
     the model still never sees its own evaluation bar. */
  const stride = 10;
  let wts = new Array(D).fill(0), b = 0;

  for (let r = window; r < rows.length; r++) {
    if ((r - window) % stride === 0) {
      wts = new Array(D).fill(0); b = 0;
      for (let e = 0; e < epochs; e++) {
        for (let t = r - window; t < r; t++) {
          const { x, y } = rows[t];
          let z = b;
          for (let d = 0; d < D; d++) z += wts[d] * x[d];
          const p = 1 / (1 + Math.exp(-z));
          const g = y - p;
          for (let d = 0; d < D; d++) wts[d] += lr * g * x[d];
          b += lr * g;
        }
      }
    }
    const { x, y } = rows[r];
    let z = b;
    for (let d = 0; d < D; d++) z += wts[d] * x[d];
    const p = 1 / (1 + Math.exp(-z));
    if ((p >= 0.5 ? 1 : 0) === y) correct++;
    total++;
    ups += y;
    pUp = p;
  }

  const accuracy = total ? (correct / total) * 100 : NaN;
  const upRate = total ? (ups / total) * 100 : NaN;
  const baseline = Math.max(upRate, 100 - upRate);

  /* How the recent strong signals actually resolved, five bars forward. This is
     the number that most often contradicts a good-looking accuracy: a model can
     be right about direction on bars that barely move. */
  const FWD = 5, THRESH = 40;
  let hits = 0, hitN = 0, bpsSum = 0;
  for (let i = window; i < n - FWD; i++) {
    const c = composite(comp, i);
    if (!Number.isFinite(c) || Math.abs(c) < THRESH) continue;
    const dir = c > 0 ? 1 : -1;
    const move = (bars[i + FWD].c - bars[i].c) / bars[i].c * 10000;   // bps
    bpsSum += move * dir;
    if (move * dir > 0) hits++;
    hitN++;
  }
  return {
    accuracy, baseline, n: total, pUp: pUp * 100,
    hit: hitN ? (hits / hitN) * 100 : NaN, hitN,
    avgBps: hitN ? bpsSum / hitN : NaN,
  };
}

/** Everything the Signal engine panel needs, for the last bar. */
export function latest(bars, opts = {}) {
  if (!bars || bars.length < 120) return null;
  const comp = components(bars);
  const i = bars.length - 1;
  const scores = {};
  for (const k of COMPONENTS) scores[k] = comp[k][i];
  const score = composite(comp, i);
  const card = walkForward(bars, comp, opts);
  let badge = 'NEUTRAL';
  if (score >= 35) badge = 'BULLISH';
  else if (score <= -35) badge = 'BEARISH';
  return { scores, score, badge, card };
}
