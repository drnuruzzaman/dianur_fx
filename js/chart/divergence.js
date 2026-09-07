/* divergence.js — RSI divergence detection for the chart.
 *
 * The mirror of sim/divergence.py, and kept a separate module from
 * indicators.js so the parity exporter can call exactly what the chart calls.
 * tests/test_parity.py compares the two implementations bar for bar: a
 * divergence drawn here that the backtester would not have traded is a bug, not
 * a nicer-looking chart.
 *
 *   Bullish: price prints a LOWER swing low  while RSI prints a HIGHER low
 *   Bearish: price prints a HIGHER swing high while RSI prints a LOWER high
 *
 * Each result carries both bar indexes that matter: `pivotI`, where the swing
 * actually is (so the line is DRAWN in the right place), and `confirmedI`,
 * `strength` bars later, where the fractal became visible (the earliest bar it
 * could have been ACTED on). The renderer shows both — the line at the swing,
 * a tick at the confirmation — because hiding that lag is how divergence gets
 * oversold as a technique.
 */

import { findPivots } from './trendlines.js';

export const DIV_DEFAULTS = {
  rsiLen: 14,
  strength: 3,
  minRsiGap: 2.0,
  minPivotGap: 5,
  maxPivotGap: 60,
  oversold: 40,
  overbought: 60,
  includeHidden: false,
  /* Regular divergence is filtered to RSI extremes because a reversal signal
     from mid-range RSI says little. Hidden divergence lives in the OPPOSITE
     place — a pullback inside a trend — so the extreme filter would reject
     nearly all of it. It gets a mid-range band instead. */
  hiddenBand: [30, 70],
};

/** Wilder RSI, identical to the `rsi` study in indicators.js. */
export function rsiSeries(bars, length = 14) {
  const out = new Array(bars.length).fill(null);
  if (bars.length < length + 1) return out;
  const gains = [];
  const losses = [];
  for (let i = 1; i < bars.length; i++) {
    gains.push(Math.max(0, bars[i].c - bars[i - 1].c));
    losses.push(Math.max(0, bars[i - 1].c - bars[i].c));
  }
  const wilder = (vals) => {
    const res = new Array(vals.length).fill(null);
    let prev = null;
    for (let i = 0; i < vals.length; i++) {
      if (i === length - 1) {
        let s = 0;
        for (let j = 0; j < length; j++) s += vals[j];
        prev = s / length;
      } else if (i >= length) {
        prev = (prev * (length - 1) + vals[i]) / length;
      }
      res[i] = i >= length - 1 ? prev : null;
    }
    return res;
  };
  const ag = wilder(gains);
  const al = wilder(losses);
  for (let i = 0; i < ag.length; i++) {
    if (ag[i] === null || al[i] === null) continue;
    out[i + 1] = al[i] === 0 ? 100 : 100 - 100 / (1 + ag[i] / al[i]);
  }
  return out;
}

/**
 * Every divergence in the series, oldest first (by confirmation bar).
 * `bars` is the full array; pivots inherently need `strength` bars of room.
 */
export function findDivergences(bars, options = {}) {
  const o = { ...DIV_DEFAULTS, ...options };
  const rsi = rsiSeries(bars, o.rsiLen);
  const { highs, lows } = findPivots(bars, o.strength);
  const out = [];

  /* HIDDEN divergence is the mirror comparison and means the opposite thing:
     regular says the trend is failing, hidden says a pullback inside it is
     ending. Off by default so this returns exactly what it always has — the
     parity fixtures and sim/strategies/rsi_divergence both depend on that. */
  const specs = [['bullish', lows], ['bearish', highs]];
  if (o.includeHidden) specs.push(['bullish_hidden', lows], ['bearish_hidden', highs]);

  for (const [kind, pivots] of specs) {
    for (let k = 1; k < pivots.length; k++) {
      const cur = pivots[k];
      const prev = pivots[k - 1];
      const gap = cur.i - prev.i;
      if (gap < o.minPivotGap || gap > o.maxPivotGap) continue;
      const rc = rsi[cur.i];
      const rp = rsi[prev.i];
      if (rc === null || rp === null) continue;

      const [loB, hiB] = o.hiddenBand;
      let ok;
      if (kind === 'bullish') {
        // price lower low, RSI higher low: the decline is losing force
        ok = cur.price < prev.price && rc > rp + o.minRsiGap && rc < o.oversold;
      } else if (kind === 'bearish') {
        ok = cur.price > prev.price && rc < rp - o.minRsiGap && rc > o.overbought;
      } else if (kind === 'bullish_hidden') {
        // price HIGHER low, RSI LOWER low: a pullback inside an uptrend
        ok = cur.price > prev.price && rc < rp - o.minRsiGap && rc >= loB && rc <= hiB;
      } else {
        // price LOWER high, RSI HIGHER high: a bounce inside a downtrend
        ok = cur.price < prev.price && rc > rp + o.minRsiGap && rc >= loB && rc <= hiB;
      }
      if (!ok) continue;

      out.push({
        kind,
        pivotI: cur.i,
        prevI: prev.i,
        confirmedI: cur.i + o.strength,
        t: cur.t,
        prevT: prev.t,
        price: cur.price,
        prevPrice: prev.price,
        rsi: rc,
        prevRsi: rp,
        rsiGap: Math.abs(rc - rp),
        barsApart: gap,
      });
    }
  }
  out.sort((a, b) => a.confirmedI - b.confirmedI || a.kind.localeCompare(b.kind));
  return out;
}
