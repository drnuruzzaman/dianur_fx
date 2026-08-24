/* structure.js — swing-sequence classification: HH, HL, LH, LL.
 *
 * A port of sim/tl/structure.py. Same comparison rule, same ATR equality band,
 * same causality: a swing is adopted at the bar it was CONFIRMED, never at the
 * bar it occurred. tests/test_structure_parity.py compares the two over real
 * bars, so the label on the panel is the label the backtest sees.
 *
 * regime.js says whether the market is trending. This says what the swings have
 * literally been doing, which can disagree — and the disagreement is the point.
 */

import { findPivots } from './trendlines.js';
import { atrSeries } from './tlengine.js';

export const HH = 'HH', HL = 'HL', LH = 'LH', LL = 'LL';

export const Bias = {
  UP: 'up',
  DOWN: 'down',
  BROADENING: 'broadening',
  CONTRACTING: 'contracting',
  UNDECIDED: 'undecided',
};

/* A swing within this many ATR of the one before it is neither higher nor
   lower in any meaningful sense — without it the label flickers on noise. */
export const EQUAL_ATR = 0.10;

/** Label each pivot against the PREVIOUS pivot of the same kind. */
export function labelSwings(pivots, pricesAreHighs, atr, equalAtr = EQUAL_ATR) {
  const out = [];
  let prev = null;
  for (const p of pivots) {
    let label = null;
    if (prev !== null) {
      const a = p.i < atr.length ? atr[p.i] : NaN;
      const band = (Number.isFinite(a) && a > 0) ? equalAtr * a : 0;
      const delta = p.price - prev.price;
      if (Math.abs(delta) <= band) {
        label = prev.label;               // a double top is not a new higher high
      } else if (pricesAreHighs) {
        label = delta > 0 ? HH : LH;
      } else {
        label = delta > 0 ? HL : LL;
      }
    }
    const rec = { i: p.i, confirmedI: p.confirmedI, price: p.price, label };
    out.push(rec);
    prev = rec;
  }
  return out;
}

/**
 * Per-bar market structure, causal.
 * `bars` is the chart's own {t,o,h,l,c} array.
 */
export function classify(bars, { strength = 3, atrLen = 14, equalAtr = EQUAL_ATR } = {}) {
  const n = bars.length;
  const a = atrSeries(bars, atrLen);
  // findPivots stamps confirmedI = i + strength on every wick candidate itself.
  const { highs, lows } = findPivots(bars, strength);
  const hiLab = labelSwings(highs, true, a, equalAtr);
  const loLab = labelSwings(lows, false, a, equalAtr);

  const highLabel = new Array(n).fill(null);
  const lowLabel = new Array(n).fill(null);
  const lastHigh = new Array(n).fill(NaN);
  const lastLow = new Array(n).fill(NaN);

  const fill = (labels, labArr, pxArr) => {
    let k = 0, curLab = null, curPx = NaN;
    for (let i = 0; i < n; i++) {
      while (k < labels.length && labels[k].confirmedI <= i) {
        curLab = labels[k].label;
        curPx = labels[k].price;
        k++;
      }
      labArr[i] = curLab;
      pxArr[i] = curPx;
    }
  };
  fill(hiLab, highLabel, lastHigh);
  fill(loLab, lowLabel, lastLow);

  const bias = new Array(n).fill(Bias.UNDECIDED);
  for (let i = 0; i < n; i++) {
    const h = highLabel[i], l = lowLabel[i];
    if (h === HH && l === HL) bias[i] = Bias.UP;
    else if (h === LH && l === LL) bias[i] = Bias.DOWN;
    else if (h === HH && l === LL) bias[i] = Bias.BROADENING;
    else if (h === LH && l === HL) bias[i] = Bias.CONTRACTING;
  }

  return { highLabel, lowLabel, bias, lastHigh, lastLow };
}

/**
 * The labelled swing points themselves, for drawing.
 *
 * classify() answers "what is the structure AT each bar" and returns per-bar
 * arrays. This returns the events instead: one record per pivot, which is what
 * a chart annotation needs.
 *
 * Pivots whose confirming bar has not printed yet are DROPPED. A fractal high
 * at bar n-1 is not knowable until strength bars later, and drawing it anyway
 * would put a label on the chart that the engine itself could not have acted
 * on -- look-ahead as a visual, which is the easiest kind to start believing.
 * With closeConfirm on, "confirming bar" is the close-confirmation walk's
 * result (see findPivots), not a flat i + strength.
 */
/**
 * Keep only swings that moved far enough from the LAST RETAINED swing.
 *
 * `Swing Threshold = ATR(n) x sensitivity`. A fractal window asks "is this the
 * highest of N bars" -- a question about SHAPE. This asks "did price actually
 * travel" -- a question about SIZE, which is what makes XAUUSD and EURUSD
 * comparable.
 *
 * CAUSAL BY CONSTRUCTION: compared against the last swing already KEPT, never
 * the next one. Dropping a swing because the FOLLOWING swing is close needs a
 * bar that has not printed.
 */
function atrFilter(swings, atr, sensitivity) {
  if (!(sensitivity > 0)) return swings;
  const out = [];
  let last = null;
  for (const s of swings) {
    const a = s.i < atr.length ? atr[s.i] : NaN;
    if (!(Number.isFinite(a) && a > 0)) continue;
    if (last === null || Math.abs(s.price - last.price) >= sensitivity * a) {
      out.push(s);
      last = s;
    }
  }
  return out;
}

export function swingPoints(bars, { strength = 3, atrLen = 14, equalAtr = EQUAL_ATR,
                                    closeConfirm = true, atrSensitivity = 0 } = {}) {
  if (!bars || bars.length < 20) return [];
  const a = atrSeries(bars, atrLen);
  const { highs, lows } = findPivots(bars, strength, closeConfirm);
  const last = bars.length - 1;
  const out = [];
  for (const [ps, isHigh] of [[highs, true], [lows, false]]) {
    for (const r of labelSwings(ps, isHigh, a, equalAtr)) {
      if (r.confirmedI > last) continue;
      out.push({ ...r, isHigh, t: bars[r.i].t });
    }
  }
  out.sort((x, y) => x.i - y.i);
  return atrSensitivity > 0 ? atrFilter(out, a, atrSensitivity) : out;
}

/** Just the final bar — what the Trend read panel needs. */
export function latest(bars, opts = {}) {
  if (!bars || bars.length < 20) return null;
  const r = classify(bars, opts);
  const i = bars.length - 1;
  return {
    highLabel: r.highLabel[i], lowLabel: r.lowLabel[i], bias: r.bias[i],
    lastHigh: r.lastHigh[i], lastLow: r.lastLow[i],
  };
}
