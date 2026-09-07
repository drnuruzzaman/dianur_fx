/**
 * exittrail.js -- where to move the exit as a trade works.
 *
 * THE PROBLEM THIS IS FOR, with the numbers that prompted it. The Donchian exit
 * is the extreme of the last N/2 bars, which on XAUUSD 5m is a 39.6-hour
 * window. A short opened at 4524.84 sat +7.17 R open with price at 4322.49 and
 * its exit still at 4461.69 -- 139 points away, because the high that set it
 * printed 29.8 hours earlier and had not yet rolled out. Taking that exit
 * realises +2.24 R: 31% of what was open. The channel cannot compress faster
 * than time passes.
 *
 * WHY THIS IS NOT THE TAKE-PROFIT AGAIN, which is the first thing to be sure
 * of. A target CAPS the winner -- it says "this is far enough" -- and that is a
 * bet against the tail a trend rule is paid from; twelve cells out of sample
 * said not to take it (logs/tp_struct_eval.txt). A trail says nothing about how
 * far a move may go. It only decides when one is over. It cannot limit the
 * upside, only shorten the give-back, so the argument that killed the target
 * does not reach it.
 *
 * WHAT WILL PROBABLY KILL IT, stated before the run rather than after. The
 * channel's slowness is not purely a defect: it is what lets a runner sit
 * through a multi-day retrace. This rule holds ~26 bars a trade on 4h, and a
 * trail pinned to the nearest structure behind price would exit many trades
 * during ordinary pullbacks -- before the move that paid. The prize is only
 * collected on trades that reach several R, which are rare; the cost is paid on
 * every trade.
 *
 * AND THE NUMBER ABOVE IS THE MOST FLATTERING ONE OBTAINABLE. It is a single
 * trade sampled at its maximum excursion, where ANY tightening looks brilliant.
 * tools/exit_trail_eval.py is the answer; this file does not get to claim one.
 *
 * ONLY S/R AND SWINGS HOLD IT. The other three kinds `levels.js` finds are
 * wrong for a STOP even though they are right for describing what is ahead: a
 * trendline has a different price every bar and would drift under a level that
 * is supposed to ratchet, a supply/demand base marks where a move started
 * rather than where a pullback ends, and a BOS/CHoCH level records that
 * structure changed there rather than that price will hold there again. See
 * `kinds`.
 *
 * THE CONTROL IS THE POINT. `atr` exists so that a structural trail can be
 * scored against a trail that sits equally close and knows nothing. If
 * structure cannot beat a dumb trail at the same distance, then the gain was
 * "exit sooner" and not "exit where it matters" -- which is exactly how eleven
 * entry gates died in tools/entry_filter_eval.py.
 */

import { obstaclesAhead } from './levels.js';

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

export const DEFAULT_TRAIL_PARAMS = {
  /* HOW FAR BEYOND THE LEVEL THE EXIT SITS, in ATR. Price overshoots a level
     before it turns -- a stop exactly on one is taken by the wick that confirms
     it. A quarter ATR is the same concession the levels themselves use. */
  bufferAtr: 0.25,
  /* AND HOW CLOSE IS TOO CLOSE -- MEASURED FROM BOTH ENDS.
   *
   * FROM THE CURRENT PRICE, because a trail inside this is placed in the noise
   * the stop was sized to survive and would be taken by the next bar's range
   * for reasons that have nothing to do with the trade being over.
   *
   * AND FROM THE ENTRY, which is the one that was missing. `entryPrice` is the
   * OPEN of the fill bar, and that bar's CLOSE can be most of an ATR away -- so
   * a level half an ATR behind the close can sit exactly on the fill. It did:
   * USDCAD 4h came back with a trail 0.3 pips from its entry on bar zero, and
   * GBPUSD 3.2 pips on bar three. Both cleared the price floor honestly and both
   * were break-even stops on a trade that had not done anything yet.
   *
   * BOTH, not one instead of the other. Measuring only from the entry would let
   * a trail 40 bars into a winner sit a hair under the close and hand the whole
   * move back to one noisy bar; measuring only from the close is what produced
   * the break-even stops. They guard different failures. */
  minAtr: 0.5,
  /* WHICH STRUCTURE MAY HOLD A STOP, and it is not all of it.
   *
   * `levels.js` finds five kinds and they are not equally suited to this. A
   * trendline is a projection -- it has a different price on every bar and
   * moves under the exit while the exit is supposed to be ratcheting, so a stop
   * placed on one is a stop that drifts for reasons unrelated to price. A
   * supply/demand base is a region price left in a hurry, which is where a move
   * STARTS rather than where a pullback stops. A BOS/CHoCH level is an event
   * marker: it says structure changed there, not that price will hold there
   * again.
   *
   * What is left is what a trader actually puts a stop behind: a level price
   * has turned at repeatedly, and the last swing it made. Both are prices price
   * has REACTED to, both are fixed once formed, and both are the thing a
   * pullback has to break for the move to be over. */
  kinds: ['zone', 'swing'],
};

/* The structure behind price is a pure function of (bars, side, bar index), so
   it is memoised on exactly that. A trail is asked once per bar per open trade,
   and the eval walks the same series several times -- without this the second
   configuration re-derives every zone the first one already found. */
const cache = new Map();

/**
 * The nearest structure BEHIND the trade, as an exit price.
 *
 * BEHIND, NEVER AHEAD. For a long that means support below the current close --
 * levels price has already cleared. Structure ahead is where the trade is
 * going; putting an exit there would be a target, which is the thing this is
 * not.
 *
 * Returns null when nothing is found or when the nearest level is inside the
 * noise floor, and null means "no opinion this bar": the walker keeps whatever
 * the trail already ratcheted to, and the rule's own exit is untouched.
 */
export function structuralTrail(ctx, { tf = '1h', cell = '', params = {} } = {}) {
  const p = { ...DEFAULT_TRAIL_PARAMS, ...params };
  /* KEYED ON THE BAR'S TIME, NOT ITS INDEX.
   *
   * An index is only stable while the array is. The strategy replay always
   * slices from bar 0 so `i` means the same bar every walk -- but the live panel
   * is handed a rolling window from the bridge, and when that window slides,
   * index 900 becomes a different bar. A cache keyed on it would then serve one
   * bar's structure for another's, silently, and the level would be wrong in a
   * way nothing on screen could show. A timestamp cannot slide. */
  const at = ctx.view[ctx.i];
  if (!at) return null;
  /* THE ENTRY IS PART OF THE KEY NOW. The answer depends on it -- the break-even
     floor is measured from it -- so a cache that ignored it would serve one
     trade's level to another opened at a different price on the same bar. That
     happens routinely in tools/exit_trail_eval.py, where each configuration
     takes its own entries over the same bars. */
  const key = `${cell}|${ctx.side}|${at.t}|${ctx.entryPrice}|${p.kinds.join(',')}`;
  if (cache.has(key)) return cache.get(key);

  const close = ctx.close[ctx.i];
  const a = ctx.series.atr ? ctx.series.atr[ctx.i] : NaN;
  let out = null;
  if (a > 0) {
    /* `side: -1` for a long: `obstaclesAhead` returns what is in the way of a
       trade travelling DOWN, which is exactly the support under a long. */
    const behind = obstaclesAhead(ctx.view, {
      side: -ctx.side, from: close, upto: ctx.i, tf,
    }).filter((o) => p.kinds.includes(o.kind));
    if (behind.length) {
      const lvl = behind[0].price;
      const px = lvl - ctx.side * p.bufferAtr * a;
      const clearOfPrice = Math.abs(close - px) >= p.minAtr * a;
      /* `entryPrice` is absent when a caller is only drawing the line and has
         not said which trade it belongs to; then there is no break-even to
         protect and the price floor is the whole test. */
      const clearOfEntry = !Number.isFinite(ctx.entryPrice)
        || Math.abs(ctx.entryPrice - px) >= p.minAtr * a;
      if (clearOfPrice && clearOfEntry) out = px;
    }
  }
  if (cache.size > 200000) cache.clear();
  cache.set(key, out);
  return out;
}

/**
 * A trail at a fixed multiple of ATR from the close. THE CONTROL.
 *
 * It knows nothing about the chart: it sits `k` ATR behind price and ratchets,
 * which is the standard chandelier exit and also the null hypothesis. Scored at
 * the `k` that makes its average distance match the structural trail's, so the
 * two differ only in WHERE they sit and not in HOW CLOSE -- without that
 * matching, "structure won" and "tighter won" are the same measurement.
 */
export function atrTrail(ctx, { k = 2.0 } = {}) {
  const a = ctx.series.atr ? ctx.series.atr[ctx.i] : NaN;
  if (!(a > 0)) return null;
  return ctx.close[ctx.i] - ctx.side * k * a;
}

/** Build an `exitTrail` for `runRule`, or null for the unmodified rule. */
export function makeTrail(kind, opt = {}) {
  if (!kind || kind === 'none') return null;
  if (kind === 'structure') {
    return (ctx) => structuralTrail(ctx, opt);
  }
  if (kind === 'atr') {
    return (ctx) => atrTrail(ctx, opt);
  }
  throw new Error(`unknown exit trail: ${kind}`);
}

/**
 * How far a trail actually sat from price, in ATR, averaged over every bar it
 * was asked about.
 *
 * This is what the control is matched ON. Measured by running the trail without
 * letting it act -- the walker is handed a function that records and returns
 * null -- so the distances come from the same bars the real run would see.
 */
export function trailDistance(bars, rule, runRule, opts, trailFn) {
  const seen = [];
  runRule(bars, rule, {
    ...opts,
    exitTrail: (ctx) => {
      const px = trailFn(ctx);
      const a = ctx.series.atr ? ctx.series.atr[ctx.i] : NaN;
      if (Number.isFinite(px) && a > 0) {
        seen.push(Math.abs(ctx.close[ctx.i] - px) / a);
      }
      return null;                    // measuring, never acting
    },
  });
  if (!seen.length) return { n: 0, meanAtr: NaN };
  return { n: seen.length, meanAtr: seen.reduce((s, x) => s + x, 0) / seen.length };
}
