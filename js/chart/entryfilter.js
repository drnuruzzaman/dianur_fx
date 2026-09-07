/**
 * entryfilter.js -- which breakouts to skip.
 *
 * WHY THE ENTRY AND NOT THE EXIT. The exit has now been measured four ways --
 * trailing, a fitted R multiple, a structural target, and half-position
 * versions of both -- and across twelve cells out of sample none of them beat
 * simply holding (logs/tp_struct_eval.txt). That is not a surprise in
 * retrospect: this rule's edge is a trend edge, and every exit variant is a way
 * of cutting the tail it is paid from. The entry is the side of the trade that
 * has never been filtered here, and it is the side where a rejected trade costs
 * nothing except the trades it would have won.
 *
 * THE TRAP, STATED FIRST. An entry filter is the single easiest way to overfit
 * a strategy, because there are unlimited things to condition on and the sample
 * shrinks with every one you add. Every sweep in this project has produced a
 * winner whose interval spanned zero. So:
 *
 *   THRESHOLDS ARE PRE-COMMITTED, at conventional or mechanically-implied
 *   values, and are NOT for tuning. They are written here, before the run, and
 *   tools/entry_filter_eval.py reports THE WHOLE GRID rather than its best row.
 *   If a later version widens the grid, the honest report is still the whole
 *   grid -- a filter chosen by looking at the answers is not a filter, it is a
 *   description of the sample.
 *
 *   EVERY FILTER STATES ITS MECHANISM. "It backtests well" is not a mechanism.
 *   A filter with no reason to work that happens to work on 700 trades is a
 *   coincidence with a name.
 *
 * WHAT EACH ONE CLAIMS, so a reader can disagree before seeing the numbers:
 *
 *   room     A breakout with a supply zone 0.4R above it has nowhere to go. The
 *            same structure that made a BAD take-profit -- cutting winners at
 *            the first obstacle -- should make a GOOD filter, because rejecting
 *            the trade outright cuts nobody's tail. This is the one genuinely
 *            new idea here and the reason the module exists.
 *
 *   thrust   A close that clears the channel by a hair is noise that happened
 *            to print on the far side of a line; one that clears it decisively
 *            is a move. The channel is a threshold with no notion of by how
 *            much it was crossed, which is a real gap in the rule as written.
 *
 *   adx      The standard ex-ante trend/chop discriminator, and the one filter
 *            with a documented prior here: splitting the validated cell into
 *            quarters showed the edge earning in trends and giving it back in
 *            chop. sim/strategies/adxfilter.py also states what will probably
 *            kill it -- ADX is doubly smoothed and therefore late, and a
 *            breakout IS the start of the trend it is being asked to confirm.
 *
 *   ema      Take the breakout only with the longer trend. The standard Turtle
 *            companion, present in most public Donchian EAs, and already
 *            supported by the rule itself as `emaLen` -- included as a BASELINE
 *            so the new ideas are judged against the obvious one rather than
 *            against nothing.
 *
 * CAUSALITY. Every filter reads bars[0..i] and nothing else, where i is the
 * signal bar. The EMA and ADX are not shifted, because a smoothing of closes up
 * to and including this close is knowable at this close; the channel must be
 * shifted, and js/chart/donchian.js is where that distinction lives.
 */

import { obstaclesAhead } from './levels.js';

/** Wilder smoothing, seeded with the mean of the first `length` values. */
export function wilder(values, length) {
  const n = values.length;
  const out = new Array(n).fill(NaN);
  if (n < length) return out;
  let prev = 0;
  for (let j = 0; j < length; j++) prev += values[j];
  prev /= length;
  out[length - 1] = prev;
  for (let i = length; i < n; i++) {
    prev = (prev * (length - 1) + values[i]) / length;
    out[i] = prev;
  }
  return out;
}

/**
 * Wilder's ADX, mirroring sim/indicators.py exactly.
 *
 * DIRECTIONAL MOVEMENT IS THE PART PEOPLE GET WRONG, and the Python says so for
 * the same reason: +DM and -DM are EXCLUSIVE -- only the larger of the two
 * moves counts on any bar, and neither counts on an inside bar. Taking both, or
 * taking a negative value, quietly turns a trend gauge into a volatility gauge
 * that would pass every smoke test.
 *
 * The second smoothing starts where DX first exists rather than at bar 0, which
 * is the other thing that silently shifts the series if you get it wrong.
 */
export function adxSeries(bars, length = 14) {
  const n = bars.length;
  const out = new Array(n).fill(NaN);
  if (n < 2) return out;

  const up = new Array(n).fill(0);
  const dn = new Array(n).fill(0);
  const tr = new Array(n).fill(0);
  tr[0] = bars[0].h - bars[0].l;
  for (let i = 1; i < n; i++) {
    const upMove = bars[i].h - bars[i - 1].h;
    const dnMove = bars[i - 1].l - bars[i].l;
    up[i] = (upMove > dnMove && upMove > 0) ? upMove : 0;
    dn[i] = (dnMove > upMove && dnMove > 0) ? dnMove : 0;
    const pc = bars[i - 1].c;
    tr[i] = Math.max(bars[i].h - bars[i].l,
      Math.abs(bars[i].h - pc), Math.abs(bars[i].l - pc));
  }

  const atrS = wilder(tr, length);
  const upS = wilder(up, length);
  const dnS = wilder(dn, length);

  const dx = new Array(n).fill(NaN);
  for (let i = 0; i < n; i++) {
    if (!Number.isFinite(atrS[i]) || atrS[i] === 0) continue;
    const plus = 100 * upS[i] / atrS[i];
    const minus = 100 * dnS[i] / atrS[i];
    const total = plus + minus;
    if (!(total > 0)) continue;
    dx[i] = 100 * Math.abs(plus - minus) / total;
  }

  let first = -1;
  for (let i = 0; i < n; i++) if (Number.isFinite(dx[i])) { first = i; break; }
  if (first < 0) return out;
  const tail = wilder(dx.slice(first), length);
  for (let i = 0; i < tail.length; i++) out[first + i] = tail[i];
  return out;
}

/** EMA matching js/chart/rules.js -- masked until `n` values exist. */
function emaSeries(values, n) {
  const a = 2 / (n + 1);
  const out = new Array(values.length).fill(NaN);
  if (!values.length) return out;
  let prev = values[0];
  for (let i = 0; i < values.length; i++) {
    prev = i === 0 ? values[0] : a * values[i] + (1 - a) * prev;
    if (i >= n - 1) out[i] = prev;
  }
  return out;
}

/**
 * THE GRID, AND THE ONE TIME IT WAS RE-SPECIFIED.
 *
 * The first version put `room` at 1 / 2 / 3 R, on the reasoning that a trade
 * wants a couple of risk-units of space. Run, it kept 6%, 3% and 2% of entries:
 * against a 2 ATR stop, almost every breakout has SOME structure within a
 * risk-unit -- the median headroom at a signal is 0.18 R and the 90th
 * percentile is 0.68 R, measured consistently across all eight cells by
 * tools/entry_filter_calibrate.mjs. Seventeen surviving trades is not a
 * measurement, so those rows said nothing about the idea in either direction.
 *
 * WHY RE-SPECIFYING IS NOT SWEEPING, and the distinction is the one that
 * matters in this file. The new thresholds come from the QUANTILES OF THE
 * PREDICTOR at the bars where the rule fires -- q25 / q50 / q75, so each gate
 * keeps roughly 75% / 50% / 25% of entries -- and that calibration never looks
 * at what a trade returned. It is the same procedure that produced ADX's
 * conventional 20 / 25 / 30, which sit near the 35th / 60th / 80th percentiles
 * of ADX at these signals. Choosing a threshold so a gate is comparably
 * selective is an experimental control; choosing one because its row won is
 * not, and this is the first.
 *
 * Said plainly because the sequence matters: the returns for the 1/2/3 R grid
 * HAD been seen when this was rewritten. They were uniformly bad, and the
 * replacement thresholds are more permissive rather than less -- they were not
 * picked because a looser gate had looked better. The full grid is reported
 * either way.
 *
 * MATCHED RETENTION IS WHAT MAKES THE ROWS COMPARABLE. With every gate keeping
 * about the same share of trades, a difference between them is about WHICH
 * trades each one kept, rather than about how many. `rand` is in the grid for
 * the same reason and is the most important row in it: a coin flip at 50%
 * retention is what "took fewer trades" looks like with no information in it at
 * all, and any gate that cannot beat it has not demonstrated that it knows
 * anything.
 */
export const GRID = {
  room: [0.1, 0.2, 0.4],        // q25 / q50 / q75 of headroom at a signal
  thrust: [0.2, 0.4, 0.8],      // q25 / q50 / q75 of how far the close cleared
  adx: [20, 25, 30],            // the textbook thresholds; ~q35 / q60 / q80 here
  ema: [200],                   // the standard Turtle companion, ~56% kept
  /* THREE CONTROLS, NOT ONE, matching the ~75% / ~50% / ~25% tiers the other
     gates were calibrated to. A single 50% coin flip can only be compared with
     the gates that happen to keep half; `adx30` keeps 20% and `room0.1` keeps
     68%, and both need a control at THEIR selectivity before "it beat the
     baseline" can be told apart from "it took fewer trades". */
  rand: [0.75, 0.5, 0.25],
};

/* Per-series caches. The eval walks the same bars once per configuration and
   the replay re-walks its history on every step, so an uncached ADX would be
   recomputed thousands of times over identical inputs. Keyed on the array
   identity and its length: a different slice of the same array is a different
   series, and a grown slice is too. */
const cache = new WeakMap();
function seriesFor(view, kind, len) {
  let byKey = cache.get(view);
  if (!byKey) { byKey = new Map(); cache.set(view, byKey); }
  const k = `${kind}|${len}|${view.length}`;
  if (byKey.has(k)) return byKey.get(k);
  const built = kind === 'adx'
    ? adxSeries(view, len)
    : emaSeries(view.map((b) => b.c), len);
  byKey.set(k, built);
  return built;
}

/* The structural answer is a pure function of bars[0..i] and side, and it costs
   ~40 ms, so it is memoised the same way the structural TARGET is. Keyed by
   cell as well: two symbols share an index space and nothing else. */
const roomCache = new Map();

/**
 * How much room a breakout has, in R, before the first obstacle.
 *
 * `Infinity` for clear air, which is the answer that matters most: a breakout
 * into space is the trade a trend rule is FOR, and any filter that treated
 * "nothing found" as "no room" would reject exactly those.
 */
export function headroomR(ctx, { tf = '1h', cell = '' } = {}) {
  const risk = Math.abs(ctx.signalPrice - ctx.stop);
  if (!(risk > 0)) return Infinity;
  const key = `${cell}|${ctx.side}|${ctx.i}`;
  if (roomCache.has(key)) return roomCache.get(key);
  const list = obstaclesAhead(ctx.view, {
    side: ctx.side, from: ctx.signalPrice, upto: ctx.i, tf,
  });
  const r = list.length
    ? Math.abs(list[0].price - ctx.signalPrice) / risk
    : Infinity;
  if (roomCache.size > 8000) roomCache.clear();
  roomCache.set(key, r);
  return r;
}

/**
 * Build one filter from the grid.
 *
 * Returns a function for `runRule`'s `entryFilter`, or null for the unfiltered
 * baseline -- null rather than `() => true` so a caller cannot accidentally
 * pay for a gate that permits everything.
 */
export function makeFilter(kind, threshold, { tf = '1h', cell = '' } = {}) {
  if (!kind || kind === 'none') return null;

  if (kind === 'room') {
    return (ctx) => headroomR(ctx, { tf, cell }) >= threshold;
  }

  if (kind === 'thrust') {
    /* HOW FAR THE CLOSE CLEARED THE CHANNEL, in ATR. The channel it cleared is
       the rule's own entry band, which `prepare` already published -- read from
       there rather than recomputed, so this cannot end up measuring a different
       channel from the one that fired. */
    return (ctx) => {
      const a = ctx.series.atr ? ctx.series.atr[ctx.i] : NaN;
      if (!(a > 0)) return true;
      const band = ctx.side > 0 ? ctx.series.hi : ctx.series.lo;
      if (!band || !Number.isFinite(band[ctx.i])) return true;
      const cleared = (ctx.signalPrice - band[ctx.i]) * ctx.side;
      return cleared >= threshold * a;
    };
  }

  if (kind === 'adx') {
    return (ctx) => {
      const s = seriesFor(ctx.view, 'adx', 14);
      const v = s[ctx.i];
      /* UNMEASURABLE IS NOT REJECTED. During warmup ADX is NaN, and treating
         that as "not trending" would silently drop every early trade and make
         the filter look like it improved a sample it had merely truncated. */
      return !Number.isFinite(v) ? true : v >= threshold;
    };
  }

  if (kind === 'rand') {
    /* THE NEGATIVE CONTROL. Deterministic, and deliberately not `Math.random`:
       a control that moves between runs cannot be compared with anything, and
       the whole job of this row is to be the same coin flip every time.

       Hashed from the signal bar index and side so it is uncorrelated with
       anything the market is doing -- and, being a pure function of them, it
       returns the same answer when the walker re-reaches a bar, which a
       stateful counter would not. */
    return (ctx) => {
      let h = (ctx.i * 2654435761 + (ctx.side > 0 ? 40503 : 12345)) >>> 0;
      h = (h ^ (h >>> 15)) >>> 0;
      h = Math.imul(h, 2246822519) >>> 0;
      /* `>>> 0` AFTER THE LAST XOR, not before it. `^` returns a SIGNED int32,
         so without this h is negative about half the time, `h % 1000` is
         negative with it, and `negative / 1000 < 0.5` is unconditionally true --
         a "50% coin flip" that kept 75% of entries. It was caught only because
         the control's retention is printed next to every other gate's; a
         negative control nobody checks is worse than none, because the whole
         point of it is to be the row you trust. */
      h = (h ^ (h >>> 13)) >>> 0;
      return (h % 1000) / 1000 < threshold;
    };
  }

  if (kind === 'ema') {
    return (ctx) => {
      const s = seriesFor(ctx.view, 'ema', threshold);
      const t = s[ctx.i];
      if (!Number.isFinite(t)) return true;
      return ctx.side > 0 ? ctx.signalPrice > t : ctx.signalPrice < t;
    };
  }

  throw new Error(`unknown entry filter: ${kind}`);
}

/** Every pre-committed configuration, as {name, kind, threshold}. */
export function gridConfigs() {
  const out = [{ name: 'none', kind: 'none', threshold: null }];
  for (const [kind, values] of Object.entries(GRID)) {
    for (const v of values) out.push({ name: `${kind}${v}`, kind, threshold: v });
  }
  return out;
}
