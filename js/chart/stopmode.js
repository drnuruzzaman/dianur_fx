/**
 * stopmode.js -- an optional stop width, measured rather than assumed.
 *
 * THIS FILE USED TO CARRY A TAKE-PROFIT, and most of it was that. Five modes --
 * structural and fitted, whole position and half -- a per-cell store, and the
 * warning a panel printed while one was on. All of it is gone with the feature:
 * no surface in this app trades a target and `js/chart/rules.js` can no longer
 * execute one. `logs/tp_struct_eval.txt` holds the run they went on the strength of: across
 * twelve cells out of sample, no target beat the trailing exit on net R, and
 * the full structural cap was the worst of the five.
 *
 * WHAT REMAINS is the stop, and it is a different kind of change. A target caps
 * where a trade ENDS; a stop width decides where it STARTS and which signals
 * are placeable at all -- a wider stop survives shakeouts the rule was scored on
 * being shaken out by, and a narrower one rejects entries whose gap made them
 * unplaceable at 2 ATR. That is why it is opt-in, off by default, and why the
 * panel says so loudly whenever it is on.
 *
 * PER INSTRUMENT AND PER TIMEFRAME, like everything else that was measured, and
 * `dnfx.` prefixed so the setting mirrors into `configs/workspace.json` with the
 * rest of the workspace rather than living only in this browser.
 *
 * EVERYTHING IS FITTED ON THE BARS IT IS GIVEN, AND NOTHING ELSE. No series is
 * stashed at module scope and no default argument reaches for the full history,
 * because the caller that matters is a replay standing at bar 4,000 of 12,000:
 * a width fitted on all 12,000 would be a number from the future drawn on a
 * chart whose whole purpose is not to have one, and it would look identical to
 * an honest one.
 */

import { fitStop } from './stopfit.js';

/** The width the rule was validated with, and the fallback for everything. */
export const STOP_ATR = 2.0;

/** The horizon the heat is measured over, in bars. */
export const STOP_HORIZON = 40;

/**
 * The quantile of favourable-path heat the fitted stop sits at.
 *
 * 0.75, so three favourable paths in four survive it. Not swept: `fitStop`
 * reports the survival its rounded width actually delivers, which is a claim
 * that can be checked, and a swept width would be a free parameter fitted to
 * one sample.
 */
export const STOP_Q = 0.75;

const STOP_ROOT = 'dnfx.ui.ruleStop';
const stopKeyFor = (cell) => `${STOP_ROOT}.${cell || 'default'}`;

export function stopEnabled(cell) {
  try { return localStorage.getItem(stopKeyFor(cell)) === '1'; }
  catch { return false; }
}

export function setStopEnabled(cell, on) {
  try { localStorage.setItem(stopKeyFor(cell), on ? '1' : '0'); }
  catch { /* private mode */ }
}

/* Memoised on the cell AND the bar count, because the caller is a replay that
   re-fits on a slice that grows by one bar per step. Small, and cleared rather
   than evicted: the cost of a miss is one re-fit, and an LRU here would be more
   machinery than the thing it protects. */
const stopCache = new Map();
const put = (cache, k, v) => {
  if (cache.size > 64) cache.clear();
  cache.set(k, v);
  return v;
};

/**
 * The stop width in ATR, per side.
 *
 * Returns the validated 2.0 with `source: 'fixed'` when the mode is off or the
 * series cannot support a fit -- never null, because the stop is not optional.
 * A trade has to have one, and the question is only whether it was measured.
 */
export function stopMultiples(bars, key) {
  const fixed = { 1: STOP_ATR, '-1': STOP_ATR, source: 'fixed', n: 0 };
  if (!stopEnabled(key)) return fixed;
  if (!bars || bars.length < 300) return fixed;
  const k = `${key}|${bars.length}`;
  if (stopCache.has(k)) return stopCache.get(k);
  const long = fitStop(bars, { side: 1, horizon: STOP_HORIZON, q: STOP_Q,
                               fallbackAtr: STOP_ATR });
  const short = fitStop(bars, { side: -1, horizon: STOP_HORIZON, q: STOP_Q,
                                fallbackAtr: STOP_ATR });
  /* BOTH SIDES OR NEITHER, the same rule the target follows. A measured stop on
     longs and an assumed one on shorts is a third configuration that nothing
     has been measured at. */
  const out = (long.source === 'measured' && short.source === 'measured')
    ? { 1: long.atr, '-1': short.atr, source: 'measured',
        n: Math.min(long.n, short.n),
        survival: { 1: long.survival, '-1': short.survival } }
    : fixed;
  return put(stopCache, k, out);
}

/**
 * The `atrMult` option for the rule, or undefined when the mode is off -- in
 * which case the rule keeps the 2.0 ATR stop it was validated with.
 *
 * A FUNCTION OF SIDE, because `donchian.js` asks per decision and the two sides
 * were measured separately. Returning a bare number would work and would quietly
 * average two things that are not the same.
 */
export function stopOption(bars, key) {
  const m = stopMultiples(bars, key);
  if (m.source !== 'measured') return undefined;
  const fn = (side) => (side > 0 ? m[1] : m['-1']);
  fn.multiples = m;
  return fn;
}

/**
 * What the panel must say while the fitted stop is on.
 *
 * IT USED TO DESCRIBE THE TAKE-PROFIT TOO, and that outlived the take-profit by
 * one commit: the modes were unwired from every surface but this still branched
 * on `tpMode`, so a cell with a stale stored mode printed "the rule is closing
 * the whole position just short of the first structure ahead" over a walk that
 * did nothing of the kind. A warning about behaviour that is not happening is
 * worse than no warning -- it teaches a reader to discount the ones that are
 * real. It now describes only what is actually wired.
 *
 * NULL WHEN NOTHING IS ON, which is the normal case: the rule as validated.
 */
export function ruleVerdictNote(bars, key) {
  const stops = stopMultiples(bars, key);
  if (stops.source !== 'measured') return null;
  return `Fitted-stop mode is ON for this symbol and timeframe: the stop is `
    + `${stops[1]} ATR (long) / ${stops['-1']} ATR (short) instead of the `
    + `${STOP_ATR} ATR the rule was validated with. THE VERDICT ABOVE DOES `
    + 'NOT APPLY — a different stop width changes which trades are placeable at '
    + 'all, not just where they end. The exit is still the trailing channel, '
    + 'and there is no take-profit.';
}
