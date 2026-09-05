/* liquidity.js — the levels price reaches for, and what happens when it gets
 * there.
 *
 * WHY THIS EXISTS. It is the one primitive in the system specification with
 * nothing behind it in this repo: structure, zones, supply/demand and
 * trendlines are all built and all measured, and "liquidity" was the only
 * named component that was still a word rather than a number. This file makes
 * it a number so it can be measured and, if it turns out to carry nothing, be
 * deleted with the same confidence the retest rules were.
 *
 * NOTHING HERE IS A SIGNAL. It emits levels and features. No rule imports it
 * yet and no chart draws from it, because eleven entry gates, three retest
 * rules and a regime gate have already failed their matched controls, and the
 * one thing that pattern justifies is refusing to draw a detector before it has
 * a verdict. `tools/liquidity_eval.py` is where that verdict comes from.
 *
 * CAUSALITY IS THE WHOLE CONTRACT, and it is the reason a sweep is harder to
 * define than it looks. Every level here is created from a COMPLETED period --
 * yesterday's high is knowable only once yesterday has ended, a session high
 * only once that session has closed, a swing only after `strength` bars have
 * confirmed it. `levelsAt(i)` returns what a reader had at bar i and nothing
 * else. The failure mode this avoids is the one js/chart/zones.js already had
 * once: a detector that quietly scored levels on how they were later respected,
 * which flatters every backtest that touches it.
 *
 * THE SESSION WINDOWS MATCH sim/tl/strategy.py `session_of` EXACTLY, including
 * its precedence -- london, then newyork, then tokyo, then sydney -- because
 * js/util.js SESSIONS lists four OVERLAPPING windows for display and does not
 * itself say which one a 13:00 bar belongs to. Resolving that overlap a second
 * way here is how two surfaces end up disagreeing about which bars are London.
 * tests/test_liquidity.py asserts the two agree on every hour.
 */

import { atrSeries } from './tlengine.js';

export const PDH = 'prev_day_high', PDL = 'prev_day_low';
export const PSH = 'prev_session_high', PSL = 'prev_session_low';
export const SWH = 'swing_high', SWL = 'swing_low';
export const EQH = 'equal_highs', EQL = 'equal_lows';

/** UTC hour -> session name, the same four windows the app displays. */
export function sessionOf(tMs) {
  const h = Math.floor(tMs / 3600000) % 24;
  if (h >= 7 && h < 16) return 'london';
  if (h >= 12 && h < 21) return 'newyork';
  if (h >= 0 && h < 9) return 'tokyo';
  return 'sydney';
}

const dayOf = (tMs) => Math.floor(tMs / 86400000);

/**
 * Every liquidity level, each stamped with the bar index it BECAME KNOWABLE at.
 *
 * `bornI` is the point of the whole structure: a level is not in play on the
 * bar that formed it, it is in play on the bar after the period that formed it
 * closed. Filtering on `bornI <= i` is what makes any feature built from this
 * legal to trade on at bar i.
 */
export function detect(bars, { swingStrength = 3, equalTolAtr = 0.1,
                               maxAgeBars = 2000, tf = null } = {}) {
  const n = bars.length;
  const out = [];
  if (!n) return out;
  const atr = atrSeries(bars, 14);

  /* ---- previous day, and previous session, from COMPLETED periods only ---- */
  const seal = (key, kindHi, kindLo) => {
    let curKey = null, hi = -Infinity, lo = Infinity, startI = 0;
    for (let i = 0; i < n; i++) {
      const k = key(bars[i]);
      if (curKey === null) { curKey = k; startI = i; }
      if (k !== curKey) {
        /* the period ENDED at i-1, so its extremes are knowable at i */
        if (Number.isFinite(hi)) {
          out.push({ type: kindHi, price: hi, bornI: i, fromI: startI, side: 1 });
          out.push({ type: kindLo, price: lo, bornI: i, fromI: startI, side: -1 });
        }
        curKey = k; hi = -Infinity; lo = Infinity; startI = i;
      }
      if (bars[i].h > hi) hi = bars[i].h;
      if (bars[i].l < lo) lo = bars[i].l;
    }
  };
  seal((b) => dayOf(b.t), PDH, PDL);
  seal((b) => dayOf(b.t) + ':' + sessionOf(b.t), PSH, PSL);

  /* ---- swings, confirmed `strength` bars after the fact ---- */
  const s = swingStrength;
  for (let i = s; i < n - s; i++) {
    let isHi = true, isLo = true;
    for (let k = i - s; k <= i + s; k++) {
      if (k === i) continue;
      if (bars[k].h >= bars[i].h) isHi = false;
      if (bars[k].l <= bars[i].l) isLo = false;
    }
    /* bornI is i + s: the bar the confirmation completed on, NOT the pivot. A
       swing that "existed" at its own bar is the classic look-ahead in every
       pivot detector, and it is worth s bars of lateness to avoid. */
    if (isHi) out.push({ type: SWH, price: bars[i].h, bornI: i + s, fromI: i, side: 1 });
    if (isLo) out.push({ type: SWL, price: bars[i].l, bornI: i + s, fromI: i, side: -1 });
  }

  /* ---- equal highs and lows: two confirmed swings within a tolerance ----
     These are the levels a sweep narrative is usually built on, so they are
     kept separate from ordinary swings rather than folded in. */
  const swings = out.filter((l) => l.type === SWH || l.type === SWL);
  for (let a = 0; a < swings.length; a++) {
    for (let b = a + 1; b < swings.length; b++) {
      const x = swings[a], y = swings[b];
      if (x.type !== y.type) continue;
      if (y.fromI - x.fromI > 200) break;
      const tol = (atr[y.bornI] || atr[x.bornI] || 0) * equalTolAtr;
      if (tol > 0 && Math.abs(x.price - y.price) <= tol) {
        out.push({ type: x.type === SWH ? EQH : EQL,
                   price: (x.price + y.price) / 2,
                   bornI: Math.max(x.bornI, y.bornI), fromI: x.fromI,
                   side: x.side, pair: [x.fromI, y.fromI] });
      }
    }
  }

  for (const l of out) l.tf = tf;
  out.sort((p, q) => p.bornI - q.bornI);

  /* ---- A LEVEL DIES WHEN IT IS CONSUMED, and this is not optional ----
     Without it the live set on 4h gold reached a median of 3,227 levels and a
     "sweep" fired on 97.5% of bars: with enough levels on the board, every bar
     closes back inside one of them. That is the unfalsifiable definition this
     file's own header warns about, caught by measuring the event rate rather
     than by reading the code.

     Consumed means a CLOSE decisively beyond it -- the level stopped holding,
     so it is no longer liquidity resting there. `consumedI` is found in a
     forward pass, but it is only ever READ as `consumedI > i`, which asks
     "had it been consumed by bar i?" -- a question bar i can answer. */
  for (const l of out) {
    l.diesI = Math.min(l.bornI + maxAgeBars, n);
    const tol = 0;
    for (let k = l.bornI; k < Math.min(n, l.bornI + maxAgeBars); k++) {
      const beyond = l.side > 0 ? bars[k].c > l.price + tol : bars[k].c < l.price - tol;
      if (beyond) { l.diesI = k + 1; break; }
    }
  }

  /* ---- MERGE NEAR-DUPLICATES ---- Sessions and days stamp the same shelf
     repeatedly; twelve copies of one price is one level a reader sees once. */
  const merged = [];
  for (const l of out) {
    const tol = (atr[l.bornI] || 0) * equalTolAtr;
    const near = merged.find((m) => m.type === l.type && tol > 0
      && Math.abs(m.price - l.price) <= tol && m.diesI > l.bornI);
    if (near) { near.diesI = Math.max(near.diesI, l.diesI); near.merged = (near.merged || 1) + 1; }
    else merged.push(l);
  }
  return merged;
}

/** The levels a reader had at bar `i`.
 *
 * Early-exits on `bornI`, which `detect` and `compute` both leave sorted. A
 * plain filter over the whole array was fine at 84k levels on one timeframe and
 * is not at 600k across four: this is called once per trade over a decade, and
 * the difference is minutes. */
export function levelsAt(levels, i) {
  const out = [];
  for (let k = 0; k < levels.length; k++) {
    const l = levels[k];
    if (l.bornI > i) break;
    if (i < l.diesI) out.push(l);
  }
  return out;
}

/**
 * A SWEEP IS A ROUND TRIP, NOT A TOUCH, and that distinction is the only
 * reason this function is more than a comparison.
 *
 * Price must trade THROUGH the level and then close back on the side it came
 * from. A bar that pierces and closes beyond it is a BREAK, not a sweep, and
 * calling both the same thing is how "liquidity sweep" becomes unfalsifiable --
 * every level is eventually either swept or broken, so a definition covering
 * both predicts nothing.
 *
 * RESOLVED BACKWARD, WHICH IS THE ONLY CAUSAL WAY TO ASK IT. The first version
 * of this function took a pierce at bar i and scanned FORWARD for the reclaim,
 * which reports at bar i a fact that is not known until bar i+k. That is the
 * same leak js/chart/zones.js shipped once and it is invisible in a train/test
 * split. So the question asked here is the one a live reader can answer:
 * "did a sweep COMPLETE on this bar?" -- the pierce sits in the recent past
 * and the reclaim is this bar's own close.
 *
 * Returns null when no sweep completes at `i`. `depthAtr` is how far past the
 * level price reached; `reclaimAtr` how far back inside it closed. Both are
 * emitted rather than thresholded, so "is a deep sweep better than a shallow
 * one" stays open for measurement instead of being answered here by a constant
 * nobody validated.
 */
export function sweepAt(bars, atr, level, i, { window = 5 } = {}) {
  const a = atr[i];
  if (!(a > 0) || i <= 0 || i >= bars.length) return null;
  if (level.bornI > i) return null;                 // not knowable yet
  const up = level.side > 0;

  /* this bar must close back INSIDE -- that is the completion */
  const back = up ? bars[i].c < level.price : bars[i].c > level.price;
  if (!back) return null;

  /* and some bar in the recent past must have pierced it, with nothing in
     between closing beyond -- a close beyond ends the episode as a break */
  let depth = 0, atI = -1;
  for (let j = i - 1; j >= Math.max(0, i - window) && j >= level.bornI; j--) {
    const beyond = up ? bars[j].c > level.price : bars[j].c < level.price;
    const pierced = up ? bars[j].h > level.price : bars[j].l < level.price;
    if (pierced) {
      const d = (up ? bars[j].h - level.price : level.price - bars[j].l) / a;
      if (d > depth) { depth = d; atI = j; }
    }
    if (beyond && atI < 0) return null;   // it broke and stayed out
  }
  if (atI < 0) return null;

  return {
    type: level.type, price: level.price, atI, reclaimedI: i,
    depthAtr: +depth.toFixed(4),
    reclaimAtr: +(Math.abs(bars[i].c - level.price) / a).toFixed(4),
    bars: i - atI,
  };
}

/**
 * The causal liquidity feature row for bar `i`.
 *
 * Distances are signed by SIDE, not by direction of travel: `aboveR` is how far
 * the nearest level overhead sits, in ATR, and `belowR` the same underneath.
 * A caller that wants "distance to the level in front of me" combines that with
 * its own trade direction, because this file does not know which way anyone is
 * facing and should not guess.
 */
export function featuresAt(bars, levels, atr, i, opts = {}) {
  const a = atr[i];
  const live = levelsAt(levels, i);
  const price = bars[i].c;
  let above = null, below = null;
  for (const l of live) {
    if (l.price >= price) { if (!above || l.price < above.price) above = l; }
    else if (!below || l.price > below.price) below = l;
  }
  const tests = (l) => {
    if (!l) return 0;
    let c = 0;
    for (let k = l.bornI; k <= i; k++) {
      if (l.side > 0 ? bars[k].h >= l.price : bars[k].l <= l.price) c++;
    }
    return c;
  };
  /* A sweep COMPLETING on this bar, asked ONLY of the nearest live level on
     each side -- the two price is actually interacting with. Asking it of every
     live level is what produced a 97.5% event rate; a sweep of a shelf twenty
     ATR away is not an event anyone traded. */
  let sweep = null;
  for (const l of [above, below]) {
    if (!l) continue;
    const s = sweepAt(bars, atr, l, i, opts);
    if (s && (!sweep || s.depthAtr > sweep.depthAtr)) sweep = s;
  }
  return {
    liq_above_atr: above && a > 0 ? +((above.price - price) / a).toFixed(4) : null,
    liq_below_atr: below && a > 0 ? +((price - below.price) / a).toFixed(4) : null,
    liq_above_type: above ? above.type : null,
    liq_below_type: below ? below.type : null,
    liq_above_tf: above ? (above.tf || null) : null,
    liq_below_tf: below ? (below.tf || null) : null,
    liq_above_age: above ? i - above.bornI : null,
    liq_below_age: below ? i - below.bornI : null,
    liq_above_tests: tests(above),
    liq_below_tests: tests(below),
    liq_live_count: live.length,
    sweep: sweep ? sweep.type : null,
    sweep_depth_atr: sweep ? sweep.depthAtr : null,
    sweep_reclaim_atr: sweep ? sweep.reclaimAtr : null,
    sweep_bars: sweep ? sweep.bars : null,
    session: sessionOf(bars[i].t),
  };
}

/**
 * Levels from a HIGHER timeframe, re-indexed onto this one.
 *
 * WHY IT IS NOT ENOUGH TO DETECT ON THE BASE SERIES. A 15m swing high is a
 * level a 5m trader reacts to, and it is not the same thing as a 5m swing high:
 * it took 15-minute bars to form, so it sits where a faster series would never
 * have put a pivot. Detecting only on the base series silently drops every
 * level the higher frames put on the chart, which is most of the ones anyone
 * actually watches.
 *
 * THE RE-INDEXING IS WHERE THE LEAK WOULD BE. An HTF level detected at HTF bar
 * `j` is knowable from the moment that bar OPENS -- `detect` already sealed it
 * out of periods that had closed by then -- so it maps to the first base bar at
 * or after `htfBars[j].t`. Mapping it to the bar that FORMED it instead would
 * hand the base series a level minutes or hours before anyone had it, which is
 * the single most common way multi-timeframe features leak.
 */
export function fromHigherTf(baseBars, htfBars, opts = {}) {
  const levels = detect(htfBars, opts);
  const out = [];
  let b = 0;
  const sorted = levels.slice().sort((x, y) => htfBars[x.bornI].t - htfBars[y.bornI].t);
  for (const l of sorted) {
    const born = htfBars[Math.min(l.bornI, htfBars.length - 1)].t;
    while (b < baseBars.length && baseBars[b].t < born) b++;
    if (b >= baseBars.length) break;
    const dies = l.diesI < htfBars.length ? htfBars[l.diesI].t : Infinity;
    let d = b;
    while (d < baseBars.length && baseBars[d].t < dies) d++;
    out.push({ ...l, bornI: b, diesI: d, htfBornI: l.bornI });
  }
  return out.sort((x, y) => x.bornI - y.bornI);
}

/**
 * Everything a caller needs in one pass.
 *
 * `higher` is an optional map of { tfLabel: barsArray } for the frames above
 * this one; their levels are merged into the same board, each keeping the `tf`
 * it came from so a feature can ask "how far to the nearest 1h level" rather
 * than only "the nearest level".
 */
export function compute(bars, opts = {}) {
  const atr = atrSeries(bars, 14);
  let levels = detect(bars, opts);
  for (const [tf, htf] of Object.entries(opts.higher || {})) {
    if (Array.isArray(htf) && htf.length) {
      levels = levels.concat(fromHigherTf(bars, htf, { ...opts, tf }));
    }
  }
  levels.sort((p, q) => p.bornI - q.bornI);
  return { levels, atr, featuresAt: (i) => featuresAt(bars, levels, atr, i, opts) };
}
