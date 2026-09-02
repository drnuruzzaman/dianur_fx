/**
 * retests.js -- two more INTRADAY candidates, on the same terms as the first.
 * Neither is validated. Neither is registered. Read the verdict at the bottom.
 *
 * WHY THEY EXIST. js/chart/smcretest.js tested the supply/demand version of
 * "wait for a pullback into the thing that caused the move" and could not beat
 * a coin flip trading at the same rate (logs/smc_eval.txt). Two obvious
 * variants remained, both named in the request that started this: a TRENDLINE
 * retest and an S/R LEVEL retest. They are here so the same harness can put the
 * same question to them, with the same control.
 *
 * ONE THING VARIES BETWEEN THE THREE RULES AND IT IS THE ENTRY. All three take
 * the same exit -- an opposite CHoCH or the stop, plus whatever trail the
 * caller supplies -- and all three are scored against a control that copies
 * their side, their opportunity bars, their stop width and their trade rate.
 * If the exit varied too, a win could come from either half and the comparison
 * would answer nothing.
 *
 * THE DETECTORS ARE REBUILT HERE, DELIBERATELY, and this is the part to
 * scrutinise. `js/chart/trendlines.js` and `js/chart/zones.js` are what the
 * chart draws with, and both are the wrong shape for a walk:
 *
 *   THEY LOOK FORWARD. `zones.detect` scores a zone using every touch in the
 *   series and `liveZones` filters on what survived to the last bar; the
 *   trendline engine validates a line against bars after the pivots that made
 *   it. Used in a backtest, both mean "only the levels that turned out to
 *   matter are visible", which is survivorship bias and would flatter anything
 *   built on it. The same trap `bases()` was added to avoid in supplydemand.js.
 *
 *   THEY COST TOO MUCH PER BAR. Re-running either at every bar of a 700k-bar
 *   series is hours of work for one configuration, and this study runs eight.
 *
 * So both are rebuilt as forward-only, O(n) constructions from CONFIRMED
 * pivots: a pivot printed at bar i is knowable at i + strength and not before,
 * which is the same confirmation the chart's own detectors use. They are
 * SIMPLER than what the chart draws and that is a real limitation -- a
 * negative result here is a result about these constructions, not about every
 * possible trendline. It is stated that way in the verdict.
 */

import { atrSeries } from './tlengine.js';
import { FLAT, LONG, SHORT } from './rules.js';
import { BULL, BEAR, CHOCH, detect as detectMs } from './marketstructure.js';
import { findPivots } from './trendlines.js';

export { FLAT, LONG, SHORT } from './rules.js';

const SHARED = {
  atrLen: 14,
  strength: 3,
  /* How close a bar has to come to count as a retest. */
  touchAtr: 0.25,
  /* And how far beyond the level the stop sits. */
  stopBufferAtr: 0.5,
  /* A stop wider than this is not a level. Skipped, not resized. */
  maxRiskAtr: 3.0,
};

/* ------------------------------------------------------------------ shared */

/** Confirmed pivots, indexed by the bar they become knowable on. */
function pivotsByConfirm(bars, strength) {
  const { highs, lows } = findPivots(bars, strength);
  const hi = new Map();
  const lo = new Map();
  for (const q of highs) {
    if (!hi.has(q.confirmedI)) hi.set(q.confirmedI, []);
    hi.get(q.confirmedI).push(q);
  }
  for (const q of lows) {
    if (!lo.has(q.confirmedI)) lo.set(q.confirmedI, []);
    lo.get(q.confirmedI).push(q);
  }
  return { hi, lo };
}

/** The CHoCH exit both rules share with js/chart/smcretest.js. */
function chochExit(i, { series, pos }) {
  const ev = series.msEvent[i];
  const dir = series.msDir[i];
  if (ev === CHOCH
      && ((pos.side === LONG && dir === BEAR) || (pos.side === SHORT && dir === BULL))) {
    return { side: FLAT, reason: 'choch' };
  }
  return null;
}

/* --------------------------------------------------------------- trendline */

export const TL_DEFAULTS = { ...SHARED, maxAgeBars: 400 };

/**
 * A line through the LAST TWO CONFIRMED PIVOTS on one side, extended forward.
 *
 * Two points is the minimum that defines a line and the maximum that can be
 * maintained in one pass. The chart's engine fits through three or more and
 * scores the fit; that is a better line and it cannot be built causally at this
 * cost. What is kept from it: a rising support line needs the second low ABOVE
 * the first (a falling one would be resistance), the line is discarded once a
 * close breaks it, and it expires after `maxAgeBars`.
 */
export const tlRetestRule = {
  key: 'tl_retest',
  label: 'Trendline retest (candidate)',
  defaults: TL_DEFAULTS,
  summary: 'Buy the first bar that touches a rising two-pivot support line and '
    + 'closes back above it; stop below the line. Short the mirror. Exit on an '
    + 'opposite CHoCH or the stop. NOT VALIDATED.',

  warmup: (p) => Math.max(p.atrLen, p.strength * 6, 80) + 2,

  /* NO STANDING EXIT LEVEL, and the walker asks for one whenever a position is
     open on the last bar. Every other rule here can answer with a price -- the
     Donchian channel has one on every bar -- but this rule's exit is an EVENT,
     a CHoCH against the trade, and there is no price at which that is true. A
     rule without this crashes only on the cells that happen to end mid-trade,
     which is how five of eight cells in the first run died after the other
     three had already printed a table. */
  exitLevel: () => null,

  prepare(bars, p) {
    const n = bars.length;
    const atr = atrSeries(bars, p.atrLen);
    const ms = detectMs(bars, { strength: p.strength });
    const { hi, lo } = pivotsByConfirm(bars, p.strength);

    /* The line's VALUE at each bar, per side, or NaN where there is none.
       Rebuilt whenever a new pivot confirms, killed by a close through it. */
    const supAt = new Float64Array(n).fill(NaN);
    const resAt = new Float64Array(n).fill(NaN);
    let sup = null;                 // { i0, p0, i1, p1 }
    let res = null;
    let lastLow = null;
    let lastHigh = null;

    for (let i = 0; i < n; i++) {
      for (const q of (lo.get(i) || [])) {
        if (lastLow && q.i > lastLow.i && q.price > lastLow.price) {
          sup = { i0: lastLow.i, p0: lastLow.price, i1: q.i, p1: q.price, bornI: i };
        }
        lastLow = q;
      }
      for (const q of (hi.get(i) || [])) {
        if (lastHigh && q.i > lastHigh.i && q.price < lastHigh.price) {
          res = { i0: lastHigh.i, p0: lastHigh.price, i1: q.i, p1: q.price, bornI: i };
        }
        lastHigh = q;
      }
      const val = (L) => (L ? L.p0 + (L.p1 - L.p0) * ((i - L.i0) / (L.i1 - L.i0)) : NaN);
      if (sup) {
        const v = val(sup);
        if (bars[i].c < v || (i - sup.bornI) > p.maxAgeBars) sup = null;
        else supAt[i] = v;
      }
      if (res) {
        const v = val(res);
        if (bars[i].c > v || (i - res.bornI) > p.maxAgeBars) res = null;
        else resAt[i] = v;
      }
    }
    return { atr, msBias: ms.bias, msEvent: ms.event, msDir: ms.eventDir,
             supAt, resAt };
  },

  /** Where a control is allowed to fire: a live line on the bias side. */
  opportunity(i, series) {
    const b = series.msBias[i];
    if (b === BULL) return Number.isFinite(series.supAt[i]) ? LONG : FLAT;
    if (b === BEAR) return Number.isFinite(series.resAt[i]) ? SHORT : FLAT;
    return FLAT;
  },

  decide(i, ctx) {
    const { series, close, high, low, pos, p } = ctx;
    const a = series.atr[i];
    if (!(a > 0)) return null;
    if (pos) return chochExit(i, ctx);

    const side = this.opportunity(i, series);
    if (side === FLAT) return null;
    const line = side === LONG ? series.supAt[i] : series.resAt[i];

    const touched = side === LONG ? low[i] <= line + p.touchAtr * a
                                  : high[i] >= line - p.touchAtr * a;
    const rejected = side === LONG ? close[i] > line : close[i] < line;
    if (!touched || !rejected) return null;

    const stop = side === LONG ? line - p.stopBufferAtr * a
                               : line + p.stopBufferAtr * a;
    const risk = Math.abs(close[i] - stop);
    if (!(risk > 0) || risk > p.maxRiskAtr * a) return null;
    return { side, stop, tag: 'tl_retest' };
  },
};

/* ------------------------------------------------------------------ levels */

export const SR_DEFAULTS = {
  ...SHARED,
  /* Two pivots within this many ATR are the same level. */
  mergeAtr: 0.5,
  /* A level nobody has come back to is not a level. Counted causally. */
  minTouches: 2,
  maxAgeBars: 800,
};

/**
 * HORIZONTAL LEVELS FROM CONFIRMED PIVOTS, clustered as they arrive.
 *
 * A pivot within `mergeAtr` of an existing level joins it and moves its price
 * to the running mean; otherwise it starts a new one. Touch counts accumulate
 * forward only. A level dies when a close passes through it by more than the
 * touch tolerance -- so `minTouches` means "price has turned here twice
 * ALREADY", never "twice in the whole series".
 *
 * The direction is the classic one: buy support, sell resistance. That is a
 * mean-reversion trade, unlike the other two candidates, and it is the version
 * the request asked for.
 */
export const srRetestRule = {
  key: 'sr_retest',
  label: 'S/R level retest (candidate)',
  defaults: SR_DEFAULTS,
  summary: 'Buy the first bar that trades into a support level touched twice '
    + 'before and closes back above it; stop below the level. Sell resistance '
    + 'the same way. Exit on an opposite CHoCH or the stop. NOT VALIDATED.',

  warmup: (p) => Math.max(p.atrLen, p.strength * 6, 120) + 2,

  /* NO STANDING EXIT LEVEL, and the walker asks for one whenever a position is
     open on the last bar. Every other rule here can answer with a price -- the
     Donchian channel has one on every bar -- but this rule's exit is an EVENT,
     a CHoCH against the trade, and there is no price at which that is true. A
     rule without this crashes only on the cells that happen to end mid-trade,
     which is how five of eight cells in the first run died after the other
     three had already printed a table. */
  exitLevel: () => null,

  prepare(bars, p) {
    const n = bars.length;
    const atr = atrSeries(bars, p.atrLen);
    const ms = detectMs(bars, { strength: p.strength });
    const { hi, lo } = pivotsByConfirm(bars, p.strength);

    /* The nearest qualifying level below (support) and above (resistance) at
       each bar, or NaN. One pass, levels mutated forward only. */
    const supAt = new Float64Array(n).fill(NaN);
    const resAt = new Float64Array(n).fill(NaN);
    const levels = [];              // { price, touches, side, bornI, dead }

    for (let i = 0; i < n; i++) {
      const a = atr[i];
      if (!(a > 0)) continue;

      const addPivot = (price, side) => {
        let best = null;
        let bestD = Infinity;
        for (const L of levels) {
          if (L.dead || L.side !== side) continue;
          const d = Math.abs(L.price - price);
          if (d <= p.mergeAtr * a && d < bestD) { best = L; bestD = d; }
        }
        if (best) {
          best.price = (best.price * best.touches + price) / (best.touches + 1);
          best.touches += 1;
          best.lastI = i;
        } else {
          levels.push({ price, touches: 1, side, bornI: i, lastI: i, dead: false });
        }
      };
      for (const q of (lo.get(i) || [])) addPivot(q.price, 'support');
      for (const q of (hi.get(i) || [])) addPivot(q.price, 'resistance');

      const tol = p.touchAtr * a;
      let bestSup = NaN;
      let bestRes = NaN;
      for (const L of levels) {
        if (L.dead) continue;
        if ((i - L.lastI) > p.maxAgeBars) { L.dead = true; continue; }
        /* A close through the level kills it -- support that gave way is not
           support, and the level list must not accumulate every price the
           market has ever visited. */
        if (L.side === 'support' && bars[i].c < L.price - tol) { L.dead = true; continue; }
        if (L.side === 'resistance' && bars[i].c > L.price + tol) { L.dead = true; continue; }
        if (L.touches < p.minTouches) continue;
        if (L.side === 'support' && L.price <= bars[i].c) {
          if (!Number.isFinite(bestSup) || L.price > bestSup) bestSup = L.price;
        }
        if (L.side === 'resistance' && L.price >= bars[i].c) {
          if (!Number.isFinite(bestRes) || L.price < bestRes) bestRes = L.price;
        }
      }
      supAt[i] = bestSup;
      resAt[i] = bestRes;
    }
    return { atr, msBias: ms.bias, msEvent: ms.event, msDir: ms.eventDir,
             supAt, resAt };
  },

  /** BOTH sides are available here: this rule fades levels, it does not
      follow structure, so the bias does not gate it. */
  opportunity(i, series) {
    if (Number.isFinite(series.supAt[i])) return LONG;
    if (Number.isFinite(series.resAt[i])) return SHORT;
    return FLAT;
  },

  decide(i, ctx) {
    const { series, close, high, low, pos, p } = ctx;
    const a = series.atr[i];
    if (!(a > 0)) return null;
    if (pos) return chochExit(i, ctx);

    const tol = p.touchAtr * a;
    const sup = series.supAt[i];
    const res = series.resAt[i];

    /* Support first when both are in range on the same bar -- an arbitrary tie
       break, and it fires on well under 1% of bars. */
    if (Number.isFinite(sup) && low[i] <= sup + tol && close[i] > sup) {
      const stop = sup - p.stopBufferAtr * a;
      const risk = close[i] - stop;
      if (risk > 0 && risk <= p.maxRiskAtr * a) return { side: LONG, stop, tag: 'sr' };
    }
    if (Number.isFinite(res) && high[i] >= res - tol && close[i] < res) {
      const stop = res + p.stopBufferAtr * a;
      const risk = stop - close[i];
      if (risk > 0 && risk <= p.maxRiskAtr * a) return { side: SHORT, stop, tag: 'sr' };
    }
    return null;
  },
};
