/* htftrend.js — the higher-frame trend the Rayo ticket must agree with.
 *
 * THE TWIN OF `trend_ref` / `trend_at` in sim/strategies/rayo.py. A ticket is
 * posted only when the EMA20/50 trend on the last CLOSED bar of EVERY frame in
 * TREND_FRAMES points the ticket's way. Added to the live signal by request on
 * 2026-09-17 as 4h + daily, changed the same day to 1h + 4h (not backtested --
 * see TREND_FRAMES in rayo.py, which this must match).
 *
 * CLOSED BARS ONLY, keyed on close time (open + the frame). The bridge serves
 * the forming bar last; its close is in the future, so the "last bar closed at
 * or before the ticket's bar" test excludes it without special handling --
 * which is also what the Python does by dropping it.
 *
 * NOT LOADED IS NOT "AGREES". Studies and the panel are synchronous, so the
 * first paint after a page load runs before these bars arrive. `trendFor`
 * returns null until they do, `ticket()` treats null as "cannot check" and
 * posts nothing, and `onTrend` repaints when they land. A signal the filter
 * could not check is not a filtered signal.
 */

import { api } from '../api.js';

/** Must list the same frames as TREND_FRAMES in sim/strategies/rayo.py. */
export const TREND_FRAMES = ['1h', '4h'];

const FRAME_MS = { '15m': 15 * 60e3, '1h': 3600e3, '2h': 7200e3, '4h': 4 * 3600e3, '1d': 24 * 3600e3 };
/* Refetched at most every five minutes for 1h and slower -- their trend cannot
   change faster than such a bar closes -- but every minute for 15m, whose trend
   can flip four times an hour; a five-minute-old 15m trend would let a 1m
   ticket through against a trend that had already turned. */
const TTL_BY_FRAME = { '15m': 60e3 };
const ttlFor = (f) => TTL_BY_FRAME[f] || 5 * 60e3;

const cache = new Map();     // 'symbol|frame' -> { at, ref }
const pending = new Map();   // 'symbol|frame' -> promise
const listeners = new Set();

function emaSeries(vals, n) {
  const k = 2 / (n + 1);
  const out = new Array(vals.length);
  let prev = vals[0];
  for (let i = 0; i < vals.length; i++) {
    prev = i === 0 ? vals[0] : vals[i] * k + prev * (1 - k);
    out[i] = prev;
  }
  return out;
}

/** {close: number[], sign: (1|-1)[]} for one frame's bars. Mirrors trend_ref. */
export function trendRef(bars, tf, fast = 20, slow = 50) {
  if (!bars || !bars.length) return null;
  const c = bars.map((b) => b.c);
  const f = emaSeries(c, fast);
  const s = emaSeries(c, slow);
  return {
    close: bars.map((b) => b.t + FRAME_MS[tf]),
    sign: f.map((v, i) => (v > s[i] ? 1 : -1)),
  };
}

/** +1 / -1 from the last bar closed at or before `ms`, 0 if none. Mirrors trend_at. */
export function trendAt(ref, ms) {
  if (!ref) return 0;
  let lo = 0, hi = ref.close.length - 1, j = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (ref.close[mid] <= ms) { j = mid; lo = mid + 1; } else hi = mid - 1;
  }
  return j >= 0 ? ref.sign[j] : 0;
}

/** Which higher frames a ticket on `tf` must agree with (possibly none).
    Mirrors trend_frames() / TREND_FRAMES_BY_TF in sim/strategies/rayo.py:
    4h agrees with the daily, 1d and 1w follow only their own trend, everything
    faster uses TREND_FRAMES. */
const FRAMES_BY_TF = {
  '1m': ['15m'], '3m': ['15m'], '5m': ['15m'],    // 1m, 3m, 5m: 15m; 15m, 30m: TREND_FRAMES (1h + 4h)
  '1h': ['4h'], '2h': ['4h'], '4h': ['1d'],
  '1d': null, '1w': null,                          // disabled: no ticket at all
};
export function trendFrames(tf) {
  return tf in FRAMES_BY_TF ? FRAMES_BY_TF[tf] : TREND_FRAMES;
}

/** True when Rayo posts no ticket at all on this timeframe (1d, 1w). */
export function frameDisabled(tf) {
  return trendFrames(tf) === null;
}

/* ONE CACHE ENTRY PER (symbol, frame), so a 4h chart asking for the daily and
   a 15m chart asking for 1h + 4h share the 4h fetch instead of refetching. */
function loadFrame(symbol, tf) {
  const key = `${symbol}|${tf}`;
  if (pending.has(key)) return pending.get(key);
  const p = api.bars(symbol, tf, tf === '15m' ? 1500 : 400)   // 400 15m bars is only ~4 days of chart
    .then((payload) => {
      const ref = trendRef((payload && payload.bars) || [], tf);
      if (ref) {
        cache.set(key, { at: Date.now(), ref });
        for (const fn of listeners) { try { fn(symbol); } catch { /* keep going */ } }
      }
    })
    .catch(() => {})
    .finally(() => pending.delete(key));
  pending.set(key, p);
  return p;
}

/**
 * The trend refs a ticket on `symbol`/`tf` needs, as {frame: ref}; null while
 * any of them is loading; {} when the frame needs none (1d, 1w). Starts or
 * refreshes the fetches as a side effect, so callers only ever ask.
 */
export function trendFor(symbol, tf) {
  if (!symbol) return null;
  const frames = trendFrames(tf);
  /* A DISABLED FRAME returns `false`, which ticket() reads as "post nothing".
     Not null (that means still loading) and not {} (that means no filter). */
  if (frames === null) return false;
  const refs = {};
  let missing = false;
  for (const f of frames) {
    const hit = cache.get(`${symbol}|${f}`);
    if (!hit || Date.now() - hit.at > ttlFor(f)) loadFrame(symbol, f);
    if (hit) refs[f] = hit.ref; else missing = true;
  }
  return missing ? null : refs;
}

/** Called with the symbol whenever any of its trend bars (re)load. */
export function onTrend(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

/** Async form, for callers that can wait (the Signal Board's refresh loop). */
export async function ensureTrend(symbol, tf) {
  const now = trendFor(symbol, tf);
  if (now) return now;
  if (trendFrames(tf) === null) return false;
  await Promise.all(trendFrames(tf).map((f) =>
    pending.get(`${symbol}|${f}`) || loadFrame(symbol, f)));
  return trendFor(symbol, tf);
}
