/**
 * levels.js -- the next things IN THE WAY of a trade.
 *
 * WHAT PRICE ACTUALLY STOPS AT is structure: a prior swing high, a level that
 * has been respected repeatedly, the base a decline left behind, a trendline
 * price has failed at three times, the unbroken swing whose break would be the
 * next BOS. This walks all of them and returns what stands in front of the
 * trade, nearest first.
 *
 * THEY ARE NOT TARGETS AND NOTHING TRADES TO THEM. This file used to also
 * choose one and hand it to the walker as a take-profit; that is gone with the
 * take-profit itself -- `logs/tp_struct_eval.txt` holds the run it went on the strength of: twelve
 * cells out of sample, and no target beat the trailing exit on net R. What survives is the DESCRIPTION: an open
 * trade at +7 R means nothing without knowing whether the next resistance is
 * two pips away or two hundred, and that is the question these answer.
 *
 * THE FIRST, NOT THE BEST. Returned nearest-first, because the obstacle price
 * meets first is the one that decides whether a trade gets paid, however much
 * more impressive the next one is. Strength decides whether a level is worth
 * respecting AT ALL -- below its detector's own floor it is not an obstacle --
 * and never lets a weaker nearer one be skipped.
 *
 * EVERYTHING IS CAUSAL. Every detector is handed `bars.slice(., signalI + 1)`
 * and nothing reads past it -- which matters more here than anywhere else in
 * the project, because structure detectors are exactly the tools that look
 * clairvoyant when they are accidentally shown the future. A supply zone fitted
 * with tomorrow's bars in view stops today's rally with uncanny precision, and
 * nothing about the resulting chart looks wrong.
 */

import { detect as detectSD, SUPPLY, DEMAND } from './supplydemand.js';
import { BOS, CHOCH, detect as detectMS } from './marketstructure.js';
import { detect as detectZones, RESISTANCE, SUPPORT } from './zones.js';
import { detectTrendlines } from './trendlines.js';
import { swingPoints } from './structure.js';

/** Wilder ATR, the same seeding as everywhere else in this project. */
function atrSeries(bars, length = 14) {
  const n = bars.length;
  const out = new Array(n).fill(NaN);
  if (n <= length) return out;
  let tr0 = 0;
  for (let i = 1; i <= length; i++) {
    const p = bars[i - 1].c;
    tr0 += Math.max(bars[i].h - bars[i].l,
      Math.abs(bars[i].h - p), Math.abs(bars[i].l - p));
  }
  let prev = tr0 / length;
  out[length] = prev;
  for (let i = length + 1; i < n; i++) {
    const p = bars[i - 1].c;
    const tr = Math.max(bars[i].h - bars[i].l,
      Math.abs(bars[i].h - p), Math.abs(bars[i].l - p));
    prev = (prev * (length - 1) + tr) / length;
    out[i] = prev;
  }
  return out;
}

export const DEFAULT_STRUCT_PARAMS = {
  /* PER-SOURCE FLOORS, each the module's own published default rather than a
     number chosen here. A level below its detector's own bar is not something
     the detector claims is a level. */
  minZoneStrength: 25,        // zones.js DEFAULT_ZONE_PARAMS.minStrength
  minSdStrength: 40,          // supplydemand.js: impulse alone can reach 40
  minTlTouches: 3,            // trendlines.js DEFAULTS.minTouches
  /* SWINGS HAVE NO SCORE, so they get an age limit instead: a high from 400
     bars ago that price has since traded through twice is not in the way.
     `swingPoints` already drops unconfirmed ones. */
  swingLookback: 250,
  /* WHERE A SLOPING LINE IS EVALUATED. A trendline has no single price -- it
     has one per bar -- so a fixed target off a sloping line has to name the bar
     it was read at. The fit horizon, because that is the window everything else
     in the plan is measured over, and because a target read at the entry bar
     would be systematically wrong for the trade that takes 30 bars to get
     there. Recorded on the result so it can be checked. */
  projectBars: 40,
  lookback: 600,
  /* HOW MANY BARS THE DETECTORS ARE ACTUALLY HANDED, ending at the signal bar.
     Not a lookback of its own -- every detector already has one, and this sits
     safely outside the largest of them (S/R 500, supply/demand 600, trendlines
     600, swings 250) plus room for ATR to warm up. It exists purely so a caller
     walking ten years of 1h bars does not slice a 60,000-element array at every
     entry and then run four detectors that were only ever going to read the
     last 600 of it.

     IT MUST NOT CHANGE THE ANSWER, and that is checked rather than asserted:
     tests/test_entryfilter.py compares the obstacles found with this window
     against the obstacles found from bar 0 on the same signal bars. If a
     detector's own lookback is ever raised above this, that test fails, which
     is the point of it. */
  windowBars: 1200,
};

/**
 * Everything standing between `from` and the trade's direction of travel.
 *
 * Returned nearest-first, each as a FIRST-CONTACT price: the edge of the zone
 * price meets, not its middle and not its far side. A trade does not get to the
 * middle of a supply zone before the supply is encountered.
 */
export function obstaclesAhead(bars, {
  side, from, upto, tf = '1h', params = {},
} = {}) {
  const p = { ...DEFAULT_STRUCT_PARAMS, ...params };
  const end = upto == null ? bars.length - 1 : Math.min(upto, bars.length - 1);
  /* THE CAUSAL CUT, made once and passed to every detector. Slicing here rather
     than trusting each module's own `upto` argument means a detector that grows
     a new code path cannot quietly start reading past the signal bar.

     The cut has a FLOOR as well as a ceiling -- see `windowBars`. The floor is
     only ever an optimisation; the ceiling is the causality contract. */
  const base = Math.max(0, end + 1 - (p.windowBars || Infinity));
  const view = bars.slice(base, end + 1);
  if (view.length < 60) return [];
  const atr = atrSeries(view, 14);
  const a = atr[view.length - 1];
  if (!(a > 0)) return [];

  const long = side > 0;
  const ahead = (price) => (long ? price > from : price < from);
  const out = [];

  /* 1. SWING EXTREMES. The cheapest and the most literal: price turned here
        before, in living memory, and has not been back through since. */
  try {
    const swings = swingPoints(view, { strength: 3 });
    const floor = view.length - 1 - p.swingLookback;
    for (const s of swings) {
      if (s.i < floor) continue;
      if (s.isHigh !== long) continue;      // longs meet highs, shorts meet lows
      if (!ahead(s.price)) continue;
      out.push({
        price: s.price,
        kind: 'swing',
        label: `${s.label || 'swing'} ${long ? 'high' : 'low'}`,
        score: 30,                          // no published score; below zones
        at: base + s.i,
      });
    }
  } catch { /* a detector that cannot read this series contributes nothing */ }

  /* 2. HORIZONTAL S/R. Levels price has respected repeatedly, which is the
        thing a human draws first and the thing a swing list cannot express:
        four touches at one price is a different object from four unrelated
        highs. */
  try {
    const zones = detectZones(view, view.length - 1, tf, atr, { lookback: p.lookback });
    for (const z of zones) {
      if ((z.strength ?? 0) < p.minZoneStrength) continue;
      /* The NEAR edge. A long meets the bottom of a resistance band. */
      const edge = long ? z.low : z.high;
      if (!ahead(edge)) continue;
      /* And it has to be the right KIND of level: a long is stopped by
         resistance, not by support it has already cleared. */
      const role = typeof z.roleAt === 'function' ? z.roleAt(from) : null;
      if (role && role !== (long ? RESISTANCE : SUPPORT)) continue;
      out.push({
        price: edge,
        kind: 'zone',
        label: `S/R zone (${z.touches ?? '?'} touches, strength ${z.strength})`,
        score: z.strength,
        at: z.lastI == null ? null : base + z.lastI,
      });
    }
  } catch { /* ditto */ }

  /* 3. SUPPLY AND DEMAND. The base a move LEFT, which is where the unfilled
        orders are. Distinct from an S/R level in that a fresh one has never
        been touched -- it is the one kind of obstacle with no track record and
        it is still the one that turns price hardest. */
  try {
    const sd = detectSD(view, tf, atr, { lookback: p.lookback }, view.length - 1);
    for (const z of sd) {
      if ((z.strength ?? 0) < p.minSdStrength) continue;
      if (z.kind !== (long ? SUPPLY : DEMAND)) continue;
      const edge = long ? z.low : z.high;
      if (!ahead(edge)) continue;
      out.push({
        price: edge,
        kind: 'sd',
        label: `${z.kind} zone${z.fresh ? ', fresh' : `, ${z.touches} touches`}`,
        score: z.strength,
        at: z.confirmedI == null ? null : base + z.confirmedI,
      });
    }
  } catch { /* ditto */ }

  /* 4. TRENDLINES, evaluated at the projection bar. The only source with no
        fixed price: see `projectBars`. */
  try {
    const lines = detectTrendlines(view, { window: p.lookback });
    const lastT = view[view.length - 1].t;
    const step = view.length > 1
      ? (view[view.length - 1].t - view[view.length - 2].t) : 0;
    const projT = lastT + step * p.projectBars;
    for (const l of lines) {
      if ((l.touches ?? 0) < p.minTlTouches) continue;
      if (l.kind !== (long ? 'resistance' : 'support')) continue;
      const price = l.valueAt(projT);
      if (!Number.isFinite(price) || !ahead(price)) continue;
      out.push({
        price,
        kind: 'trendline',
        label: `${l.kind} trendline (${l.touches} touches, read ${p.projectBars} bars out)`,
        score: 20 + l.touches * 5,
        at: null,
      });
    }
  } catch { /* ditto */ }

  /* 5. MARKET STRUCTURE. The level whose break would BE the next BOS, and the
        levels earlier breaks happened at.

        THIS IS A DIFFERENT OBJECT FROM A SWING, even though both are pivots.
        `swingPoints` lists every confirmed turn; market structure tracks the
        ONE high that is currently unbroken, which is the level the market is
        actually working against -- break it and the structure flips. A trade
        running into it is running into the decision point, and the events
        behind it are where previous decisions were made. */
  try {
    const ms = detectMS(view);
    const j = view.length - 1;
    const pending = long ? ms.swingHigh[j] : ms.swingLow[j];
    if (Number.isFinite(pending) && ahead(pending)) {
      out.push({
        price: pending,
        kind: 'structure',
        label: `unbroken swing ${long ? 'high' : 'low'} — a break here is a BOS`,
        score: 55,
        at: null,
      });
    }
    /* Past break levels, most recent first. A level that produced a BOS or a
       CHOCH was defended or surrendered there once already. */
    for (const e of ms.events.slice(-12)) {
      if (!Number.isFinite(e.level) || !ahead(e.level)) continue;
      out.push({
        price: e.level,
        kind: e.kind === CHOCH ? 'choch' : 'bos',
        label: `${e.kind === CHOCH ? 'CHoCH' : 'BOS'} level `
          + `(${e.direction === 'bullish' ? 'bullish' : 'bearish'} break)`,
        score: e.kind === CHOCH ? 50 : 45,
        at: e.i,
      });
    }
  } catch { /* ditto */ }

  out.sort((x, y) => (long ? x.price - y.price : y.price - x.price));
  return out;
}

/**
 * The nearest few obstacles ahead, thinned for DISPLAY.
 *
 * `obstaclesAhead` returns everything, and everything is the wrong thing to
 * draw: four detectors over 600 bars routinely find a dozen levels within two
 * risk-units, several of them the same price wearing different names -- a swing
 * high, the S/R zone clustered on it, and the BOS level it defines are one line
 * on a chart and three rows in a list.
 *
 * So near-duplicates collapse, and the STRONGEST candidate wins outright --
 * its price AND its name -- rather than the first one found: "supply zone,
 * fresh" tells a reader more than "HH high" about the same price, and quoting
 * one detector's price under another's label is its own small lie.
 *
 * HALF AN ATR IS THE SAME PRICE, and this number is a JUDGEMENT rather than a
 * measurement -- worth saying, because most of the constants in this project
 * are the other kind.
 *
 * It was a third of an ATR, which left 4331.61 and 4332.55 -- 0.39 ATR apart --
 * stacked as `TP` and `TP1`: two rows and two tags for one decision. The
 * obvious fix was a whole ATR, on the reasoning that price crosses anything
 * closer in a single bar. That was wrong in the other direction and worse: the
 * four levels on that same chart sat 0.39, 0.82 and 0.62 ATR apart, so one ATR
 * collapsed ALL of them into a single row. A rule that merges every level a
 * reader can see is not a tidier list, it is a missing one.
 *
 * Half sits in the gap those two cases leave -- it merges 0.39 and keeps 0.62 --
 * and it is calibrated on exactly that one observation. No sweep, no
 * measurement, and nothing here claims otherwise.
 *
 * THREE SURVIVORS, NOT FOUR. `max` was 4 and is 3 by request. It is a display
 * cap and nothing else: `obstaclesAhead` still finds everything ahead, the trade
 * is unaffected either way -- the rule has no take-profit and exits on the stop
 * or the trail -- and the levels these are cut from were never targets. A fourth
 * row was the furthest one, the least likely to be reached and the first to be
 * stale, printed at the point where a list of "what is in the way" starts
 * reading as a forecast. The dedup above runs before the cap, so dropping it
 * removes a level rather than promoting a near-duplicate into view.
 */
export function displayLevels(bars, {
  side, from, upto, tf = '1h', max = 3, minGapAtr = 0.5, params = {},
} = {}) {
  const p = { ...DEFAULT_STRUCT_PARAMS, ...params };
  const list = obstaclesAhead(bars, { side, from, upto, tf, params: p });
  if (!list.length) return [];
  const end = upto == null ? bars.length - 1 : Math.min(upto, bars.length - 1);
  const view = bars.slice(Math.max(0, end + 1 - (p.windowBars || Infinity)), end + 1);
  const atr = atrSeries(view, 14);
  const a = atr[view.length - 1];
  const gap = (a > 0 ? a : 0) * minGapAtr;

  const kept = [];
  for (const o of list) {
    const near = kept.find((k) => Math.abs(k.price - o.price) <= gap);
    if (near) {
      /* THE STRONGER ONE REPLACES THE WEAKER ENTIRELY. Taking its name but
         keeping the other's price would report a level no detector found: an
         S/R zone's strength attached to a swing high's price. Within one ATR
         the two prices are the same decision, so the accurate one to show is
         the better-evidenced one. */
      if ((o.score || 0) > (near.score || 0)) {
        const also = near.also;
        Object.assign(near, o, { also });
      }
      near.also = (near.also || 0) + 1;
      continue;
    }
    kept.push({ ...o });
    if (kept.length >= max) break;
  }
  return kept;
}
