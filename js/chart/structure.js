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

/* ------------------------------------------------------- significance ---- */

/* Reversal threshold in ATR: price must travel this far back from a leg's
   extreme before that extreme is accepted as a swing.

   MEASURED, not picked. Across XAUUSD and EURUSD on 5m/15m/1h/4h the reversal
   depth of strength-6 swings has a median near 4.6 ATR and a p10 near 2.0, and
   3.0 sits between them -- it cuts the tail of swings price never actually
   turned at without touching the legs that carry structure. It lands near 6-7
   rings on a 150-bar screen against 13 today. */
export const SIGNIFICANT_ATR = 3.0;

/**
 * The swings price actually turned at -- a ZigZag, not a fractal.
 *
 * WHY A SECOND DETECTOR RATHER THAN A FILTER ON THE FIRST. `strength` asks "is
 * this bar the highest of 2N+1?", which is a question about SHAPE, and shape
 * statistics are scale-free: measured over 3.6M bars, strength 6 returns one
 * swing per 10 bars at a median 3.3 ATR amplitude on XAUUSD 5m and on EURUSD
 * 4h alike. Two markets that are nothing like each other cannot both be turning
 * every ten bars; the detector was never measuring price, so no threshold laid
 * over its output can recover what it did not look at.
 *
 * The three things it gets wrong, and what this asks instead:
 *
 *   AMPLITUDE   fractals accept a turn of any size. Here a leg must travel
 *               `atrMult` ATR before it can end.
 *   ALTERNATION 20.8% of strength-6 swings repeat the same kind back-to-back,
 *               with runs up to 8 highs and no low between -- and 7.3% sit
 *               within three bars of the one before. A high and a low a few
 *               bars apart inside one impulse is not a swing sequence. Here
 *               alternation is STRUCTURAL: a leg has one extreme, and the next
 *               swing is necessarily the opposite kind.
 *   EXTREMITY   a fractal marks every local top in a leg. Here only the leg's
 *               highest high survives, because a later higher bar simply
 *               extends the running extreme rather than starting a new swing.
 *
 * CAUSALITY. The extreme is emitted at the bar the retracement COMPLETED, not
 * at the bar it occurred -- `confirmedI` is the flip bar. That is later than a
 * fractal's `i + strength` and the extra lag is the honest price of the
 * question: significance is not knowable until price has moved away. The walk
 * reads bars[0..j] only, so a prefix of the series produces a prefix of the
 * output; tests/test_swing_zigzag.mjs rebuilds it from truncated prefixes.
 *
 * The final, unconfirmed leg is DROPPED. Its extreme is still moving.
 */
export function zigzag(bars, { atrLen = 14, atrMult = SIGNIFICANT_ATR } = {}) {
  if (!bars || bars.length < 20) return [];
  const atr = atrSeries(bars, atrLen);
  const out = [];
  /* `dir` is the leg being tracked: +1 up (hunting a high), -1 down. It starts
     unset and is decided by whichever threshold price clears first, so the walk
     does not assume a direction it has not seen. */
  let dir = 0, extI = -1, extPx = NaN;

  for (let j = 0; j < bars.length; j++) {
    const a = atr[j];
    if (!(Number.isFinite(a) && a > 0)) continue;
    if (extI < 0) { extI = j; extPx = bars[j].c; continue; }
    const band = atrMult * a;
    const b = bars[j];

    if (dir >= 0 && b.h >= extPx) { extI = j; extPx = b.h; if (dir === 0) dir = 1; }
    if (dir <= 0 && b.l <= extPx) { extI = j; extPx = b.l; if (dir === 0) dir = -1; }

    if (dir > 0 && b.l <= extPx - band) {
      out.push({ i: extI, confirmedI: j, price: extPx, isHigh: true });
      dir = -1; extI = j; extPx = b.l;
    } else if (dir < 0 && b.h >= extPx + band) {
      out.push({ i: extI, confirmedI: j, price: extPx, isHigh: false });
      dir = 1; extI = j; extPx = b.h;
    }
  }
  return out;
}


/* The INNER threshold. Same units, smaller move: a turn price made inside a
   major leg rather than one that ended it.

   MEASURED. At 1.0 ATR a 15m chart carries 85 turns per 150-bar screen, which
   is texture, not structure; at 2.0 it carries 22 against the majors' 8, so a
   major leg holds about two inner turns -- which is what a leg on the chart
   actually looks like. The old fractal dots sat at 13.5, so this is a similar
   density made of moves price actually made. */
export const INNER_ATR = 2.0;

/**
 * THE THREE TIERS every swing is ranked into, in ATR of travel.
 *
 * One ladder, folded successively, so tier 2 is a subset of tier 1 and tier 1
 * a subset of tier 0. A mark's rank is a property of the market, not of the
 * menu: the same turn is rank 1 at every setting.
 *
 *   0   2.0 ATR   a turn inside a leg
 *   1   3.0 ATR   a turn that ended a leg
 *   2   6.0 ATR   a turn that shaped the range
 */
export const SWING_TIERS = [INNER_ATR, SIGNIFICANT_ATR, SIGNIFICANT_ATR * 2];

/**
 * The rank at which a setting starts drawing a RING. Nothing else changes.
 *
 * This is the whole of what the sensitivity menu does to swings. Every setting
 * marks the same turns at the same ranks; the menu only moves the line between
 * "solid dot" and "circle". So switching to `major` does not hide a swing --
 * what was circled at `normal` becomes a solid dot, and the circle moves up to
 * the turns that shaped the range.
 *
 * `fine` and `normal` are deliberately equal here rather than collapsed into
 * one entry, because the two settings still differ everywhere else they are
 * read: trendline pivots, BOS/CHoCH and S/R zones all take `strength` from the
 * same menu. Folding the rows together would hide that only the SWING view
 * treats them alike.
 */
export const RING_FROM = { fine: 1, normal: 1, major: 2 };

/**
 * The lowest rank a setting DRAWS at all.
 *
 * `major` starts at rank 1: asking for major structure and being handed the
 * turns inside every leg is the noise the setting exists to escape. The ranks
 * themselves do not move -- a rank-1 turn is rank 1 at every setting -- so the
 * marks `major` shows are exactly `normal`'s ringed ones, now solid, with the
 * circle moved up to rank 2. Nothing is reclassified; the smallest tier is
 * simply not drawn.
 */
export const SHOW_FROM = { fine: 0, normal: 0, major: 1 };

/**
 * Fold a ZigZag into a coarser one, OVER ITS OWN OUTPUT rather than over bars.
 *
 * WHY NOT JUST RUN `zigzag` TWICE AT TWO THRESHOLDS. Because the two do not
 * nest, and a hierarchy that does not nest is a lie. Measured on XAUUSD,
 * EURUSD and USDJPY across 15m/1h/4h, only 95-97% of 3.0 ATR turns are also
 * 1.0 ATR turns -- so one ring in twenty would sit on a bar carrying no inner
 * swing at all, and "this major turn is made of these inner turns" would be
 * false wherever a reader bothered to check.
 *
 * Folding the finer sequence guarantees containment by construction: a major
 * turn IS an inner turn, always, because it is chosen from among them.
 *
 * CAUSALITY. A major turn is confirmed at the later of its own confirmation
 * and that of the inner turn whose retracement proved it -- the bar where both
 * facts are known. The final leg is dropped, its extreme still moving.
 */
export function fold(inner, atr, atrMult) {
  const out = [];
  let ext = null;
  for (const p of inner) {
    if (!ext) { ext = p; continue; }
    const a = p.i < atr.length ? atr[p.i] : NaN;
    if (!(Number.isFinite(a) && a > 0)) continue;

    if (p.isHigh === ext.isHigh) {
      /* Same kind, so no leg ended between them: the later one simply extends
         the running extreme if it is the more extreme of the two. */
      if (p.isHigh ? p.price >= ext.price : p.price <= ext.price) ext = p;
      continue;
    }
    if (Math.abs(p.price - ext.price) >= atrMult * a) {
      out.push({ ...ext, confirmedI: Math.max(ext.confirmedI, p.confirmedI) });
      ext = p;
    }
  }
  return out;
}

/**
 * The drawn swing set: two nested levels, both measured on price.
 *
 * Proved in the strategy replay first, then adopted by the live chart, so
 * both surfaces run this one function -- the alternative was keeping the old
 * fractal-dot version alive for the live chart, which would have meant two
 * detectors disagreeing about what a swing is and one of them known wrong.
 *
 * INNER dots are turns of `INNER_ATR`; MAJOR rings are the subset of those
 * that also ended a `majorAtr` leg. Every ring is a dot promoted, never a
 * separate finding -- see `fold`.
 *
 * WHY NEITHER LEVEL IS A FRACTAL ANY MORE. `strength` asks "is this bar the
 * highest of 2N+1?", a question about SHAPE, and shape statistics are
 * scale-free: swept over 46 instrument x timeframe cells, strength 3 returns
 * 13.5 dots per 150-bar screen on every one of them, gold on 1m and cable on
 * 1w alike. It also let dots repeat -- two highs three bars apart at the same
 * price, with no low between -- because it never compares prices at all.
 *
 * SENSITIVITY SCALES BOTH THRESHOLDS TOGETHER, keeping the menu's meaning: at
 * `fine` (strength 2) the pair is 1.3/2.0 ATR and the chart shows small turns;
 * at `major` (strength 6) it is 4.0/6.0 and only the moves that shaped the
 * range survive. The ratio between the levels is fixed, so a ring means the
 * same thing relative to its dots at every setting.
 */
export function nestedSwings(bars, { sens = 'normal', atrLen = 14,
                                    equalAtr = EQUAL_ATR } = {}) {
  if (!bars || bars.length < 20) return [];
  const ringFrom = RING_FROM[sens] ?? RING_FROM.normal;
  const atr = atrSeries(bars, atrLen);

  /* Each tier is folded out of the one below it, never detected afresh from
     bars: two ZigZags run independently at two thresholds agree only 95-97% of
     the time, and a rank that is not a subset is not a rank. */
  const tiers = [zigzag(bars, { atrLen, atrMult: SWING_TIERS[0] })];
  if (!tiers[0].length) return [];
  for (let k = 1; k < SWING_TIERS.length; k++) {
    tiers.push(fold(tiers[k - 1], atr, SWING_TIERS[k]));
  }

  const key = (z) => z.i + '|' + z.isHigh;
  const rank = new Map();
  tiers.forEach((list, k) => { for (const z of list) rank.set(key(z), k); });

  /* `seq` is the turn's position in the tier-0 sequence -- ONE numbering
     shared by every tier. Numbering each tier separately looked equivalent and
     was not: where two marks land on the same bar (one huge candle that is
     both a leg's low and the next leg's high), the sort fell back on unrelated
     counters and put them in the wrong order, breaking the alternation the
     ZigZag guarantees on 100-166 marks per cell. */
  const order = new Map();
  tiers[0].forEach((z, k) => order.set(key(z), k));

  /* LABELLED WITHIN THE TIER THAT RINGS. A ring's HH compares it to the
     previous ring, not to a lesser turn between them -- which is the point of
     having tiers, and what the fractal could never say because its marks were
     not a sequence of anything. Everything below the ring tier is labelled as
     one sequence, so the dots read as a sequence too. */
  const showFrom = SHOW_FROM[sens] ?? SHOW_FROM.normal;
  const ringed = tiers[ringFrom] || [];
  const ringedKeys = new Set(ringed.map(key));
  /* Drawn but not ringed: everything at or above the floor, minus the ringed
     tier. At `normal` the floor is 0 and this is every turn; at `major` the
     floor is 1, so the turns inside a leg are dropped rather than shrunk. */
  const rest = (tiers[showFrom] || tiers[0]).filter((z) => !ringedKeys.has(key(z)));

  const label = (list, isMajor) => {
    const out = [];
    for (const isHigh of [true, false]) {
      const ps = list.filter((z) => z.isHigh === isHigh);
      labelSwings(ps, isHigh, atr, equalAtr).forEach((r, k) => {
        out.push({ ...r, isHigh, t: bars[r.i].t, major: isMajor,
                   rank: rank.get(key(ps[k])) ?? 0,
                   seq: order.get(key(ps[k])) ?? 0 });
      });
    }
    return out;
  };

  return label(ringed, true).concat(label(rest, false))
    .sort((x, y) => (x.i - y.i) || (x.seq - y.seq));
}
