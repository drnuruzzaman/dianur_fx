/* slopelines.js — one-pivot trendlines with a VOLATILITY-DERIVED slope.
 *
 * A port of sim/tl/slope_lines.py, compared value-for-value in
 * tests/test_slope_parity.py. That file implements the method published as
 * "Trendlines with Breaks" by LuxAlgo (TradingView, 2022, open source).
 *
 * THE DIFFERENCE FROM tlengine.js
 *
 * tlengine builds a line from TWO pivots: the anchors fix both position and
 * slope, and no line exists until a second pivot arrives. This uses ONE pivot
 * for position and takes the slope from volatility:
 *
 *     upper = (pivot high just visible) ? that price : upper - slope
 *     lower = (pivot low  just visible) ? that price : lower + slope
 *
 * The upper line decays DOWN toward price and the lower rises UP, until the
 * next pivot resets them — so a line always exists.
 *
 * WHY IT IS HERE. The two-pivot engine draws nothing on XAUUSD M15 (zero
 * confirmed lines), and eleven years across six cells produced eleven channels,
 * because confirmed lines are scarce. This method holds ~97% line coverage on
 * the same series. Measured against matched control candles its breaks carry
 * +3.38 / +3.22 / +2.70 pp across three eras (ATR method) — at least as much
 * information per break as the two-pivot engine's, from far fewer events.
 *
 * It is a READING aid, not a signal: the same breaks fail the economic gate
 * once real fills and friction are applied, exactly as every other candidate in
 * this project has.
 */

import { atrSeries } from './tlengine.js';

export const ATR = 'atr', STDEV = 'stdev', LINREG = 'linreg';

export const DEFAULT_SLOPE_PARAMS = {
  length: 14,        // pivot period AND volatility window
  mult: 1.0,         // steepness; 0 = flat levels
  method: ATR,
  /* The published script offers `backpaint`, which draws from the bar the pivot
     OCCURRED. A pivot is not knowable until `length` bars later, so that is
     look-ahead — the line moves at a bar nobody could have known it at. Default
     false: reset when the pivot became VISIBLE. */
  backpaint: false,
  /* A decaying line keeps decaying until the next pivot resets it, so after a
     break it can run a long way from price — up to 13.5 ATR measured on gold
     1h, with 14–24% of bars beyond 6. Those stretches are arithmetic, not
     structure. Above 0, blank the line where it is further than this from that
     bar's close. 0 = keep everything (published behaviour, parity default). */
  maxDistanceAtr: 0,
};

/** Fractal pivots, same comparison rule as trendlines.js findPivots. */
function pivotFlags(bars, length) {
  const n = bars.length;
  const ph = new Array(n).fill(false);
  const pl = new Array(n).fill(false);
  for (let i = length; i < n - length; i++) {
    let isH = true, isL = true;
    for (let k = 1; k <= length; k++) {
      if (!(bars[i].h > bars[i - k].h && bars[i].h >= bars[i + k].h)) isH = false;
      if (!(bars[i].l < bars[i - k].l && bars[i].l <= bars[i + k].l)) isL = false;
      if (!isH && !isL) break;
    }
    ph[i] = isH; pl[i] = isL;
  }
  return { ph, pl };
}

function slopeSeries(bars, p) {
  const n = bars.length;
  const close = bars.map((b) => b.c);
  const out = new Array(n).fill(NaN);

  if (p.method === ATR) {
    const a = atrSeries(bars, p.length);
    for (let i = 0; i < n; i++) out[i] = a[i] / p.length;
  } else if (p.method === STDEV) {
    for (let i = p.length - 1; i < n; i++) {
      let m = 0;
      for (let j = i - p.length + 1; j <= i; j++) m += close[j];
      m /= p.length;
      let v = 0;
      for (let j = i - p.length + 1; j <= i; j++) v += (close[j] - m) ** 2;
      out[i] = Math.sqrt(v / p.length) / p.length;    // population sd, as numpy
    }
  } else if (p.method === LINREG) {
    for (let i = p.length - 1; i < n; i++) {
      const lo = i - p.length + 1;
      let mx = 0, my = 0;
      for (let j = lo; j <= i; j++) { mx += j; my += close[j]; }
      mx /= p.length; my /= p.length;
      /* Centred covariance — see the note in sim/tl/slope_lines.py. The
         published mean(xy) - mean(x)mean(y) form loses significant digits when
         bar index times price is large, and the two runtimes then disagree. */
      let cov = 0, vx = 0;
      for (let j = lo; j <= i; j++) {
        cov += (j - mx) * (close[j] - my);
        vx += (j - mx) ** 2;
      }
      cov /= p.length; vx /= p.length;
      if (vx > 0) out[i] = Math.abs(cov) / vx / 2;
    }
  } else {
    throw new Error('unknown slope method: ' + p.method);
  }
  for (let i = 0; i < n; i++) out[i] *= p.mult;
  return out;
}

/**
 * Returns { upper, lower, slopeUp, slopeDn, breakUp, breakDn } aligned to bars.
 * Both lines exist from the first pivot on, so unlike the two-pivot engine
 * there is no "no line available" state.
 */
export function compute(bars, params = {}) {
  const p = { ...DEFAULT_SLOPE_PARAMS, ...params };
  const n = bars.length;
  const { ph, pl } = pivotFlags(bars, p.length);
  const slope = slopeSeries(bars, p);

  const upper = new Array(n).fill(NaN);
  const lower = new Array(n).fill(NaN);
  const slopeUp = new Array(n).fill(NaN);
  const slopeDn = new Array(n).fill(NaN);
  const breakUp = new Array(n).fill(false);
  const breakDn = new Array(n).fill(false);

  let curU = NaN, curL = NaN, curSu = NaN, curSl = NaN;
  for (let i = 0; i < n; i++) {
    const j = i - p.length;
    const resetH = p.backpaint ? ph[i] : (j >= 0 && j < n ? ph[j] : false);
    const resetL = p.backpaint ? pl[i] : (j >= 0 && j < n ? pl[j] : false);
    const sv = slope[i];

    if (resetH) {
      curU = p.backpaint ? bars[i].h : bars[j].h;
      curSu = sv;
    } else if (Number.isFinite(curU) && Number.isFinite(curSu)) {
      curU -= curSu;
    }

    if (resetL) {
      curL = p.backpaint ? bars[i].l : bars[j].l;
      curSl = sv;
    } else if (Number.isFinite(curL) && Number.isFinite(curSl)) {
      curL += curSl;
    }

    upper[i] = curU; lower[i] = curL;
    slopeUp[i] = curSu; slopeDn[i] = curSl;

    if (i && Number.isFinite(upper[i]) && Number.isFinite(upper[i - 1])) {
      breakUp[i] = bars[i].c > upper[i] && bars[i - 1].c <= upper[i - 1];
    }
    if (i && Number.isFinite(lower[i]) && Number.isFinite(lower[i - 1])) {
      breakDn[i] = bars[i].c < lower[i] && bars[i - 1].c >= lower[i - 1];
    }
  }
  if (p.maxDistanceAtr > 0) {
    const a = atrSeries(bars, p.length);
    for (const arr of [upper, lower]) {
      for (let i = 0; i < n; i++) {
        if (Number.isFinite(arr[i]) && Number.isFinite(a[i])
            && Math.abs(arr[i] - bars[i].c) > p.maxDistanceAtr * a[i]) {
          arr[i] = NaN;
        }
      }
    }
  }
  return { upper, lower, slopeUp, slopeDn, breakUp, breakDn };
}

/** Just the last bar — what a panel or the chart legend needs. */
export function latest(bars, params = {}) {
  if (!bars || bars.length < 40) return null;
  const r = compute(bars, params);
  const i = bars.length - 1;
  return {
    upper: r.upper[i], lower: r.lower[i],
    slopeUp: r.slopeUp[i], slopeDn: r.slopeDn[i],
    breakUp: r.breakUp[i], breakDn: r.breakDn[i],
  };
}
