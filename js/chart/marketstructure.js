/* marketstructure.js — BOS and CHoCH.
 *
 * A port of sim/tl/market_structure.py, compared event-for-event in
 * tests/test_ms_parity.py.
 *
 *   BOS    Break of Structure. Price closes through the last swing in the SAME
 *          direction as the prevailing bias — the trend made another leg.
 *
 *   CHoCH  Change of Character. Price closes through the last swing AGAINST the
 *          bias. First evidence the trend may be over, and it flips the bias.
 *
 * The distinction is not in the break — it is the same event — but in the state
 * it arrives in. Closing above the last swing high is a BOS while the bias is
 * already bullish and a CHoCH while it is bearish. That is why this needs a
 * state machine rather than a per-bar rule, and why porting it means porting
 * the STATE, not just the comparison.
 *
 * Definitions follow "Market Structure CHoCH/BOS (Fractal)" by LuxAlgo
 * (TradingView, 2023, open source).
 *
 * CLOSES, NOT WICKS, exactly as tlengine breaks a trendline. And a level is
 * CONSUMED when it breaks: it cannot break twice, so no further bullish event
 * can fire until a new swing high confirms. Without that, one strong trend
 * prints a BOS on every bar that makes a new high.
 */

import { atrSeries } from './tlengine.js';
import { findPivots } from './trendlines.js';

export const BOS = 'bos';
export const CHOCH = 'choch';
export const BULL = 'bullish';
export const BEAR = 'bearish';
export const NEUTRAL = 'neutral';

export const DEFAULT_MS_PARAMS = {
  strength: 3,       // fractal size for the swings
  /* A break must clear the level by this much ATR. 0 is the textbook rule; a
     small value stops a close a tenth of a pip through a level counting as
     structure, which on a 15m chart happens constantly. */
  bufferAtr: 0,
};

/**
 * Returns { events, bias, swingHigh, swingLow, event, eventDir }.
 * Arrays are aligned to bars; `events` is in bar order.
 */
export function detect(bars, params = {}) {
  const p = { ...DEFAULT_MS_PARAMS, ...params };
  const n = bars.length;
  const atr = p.bufferAtr > 0 ? atrSeries(bars, 14) : null;

  const { highs, lows } = findPivots(bars, p.strength);
  /* findPivots carries no confirmedI — it is shared with the batch scorer and
     must stay byte-identical for the other parity tests — so the confirming bar
     is derived here: i + strength by definition. */
  const hiByConf = Array.from({ length: n + 1 }, () => []);
  const loByConf = Array.from({ length: n + 1 }, () => []);
  for (const q of highs) {
    const c = q.i + p.strength;
    if (c >= 0 && c < n) hiByConf[c].push(q);
  }
  for (const q of lows) {
    const c = q.i + p.strength;
    if (c >= 0 && c < n) loByConf[c].push(q);
  }

  let bias = NEUTRAL;
  let sh = null, sl = null;
  const events = [];
  const aBias = new Array(n).fill(NEUTRAL);
  const aSh = new Array(n).fill(NaN);
  const aSl = new Array(n).fill(NaN);
  const aEv = new Array(n).fill('');
  const aDir = new Array(n).fill('');

  for (let i = 0; i < n; i++) {
    for (const q of hiByConf[i]) sh = { price: q.price, i: q.i };
    for (const q of loByConf[i]) sl = { price: q.price, i: q.i };

    let buf = 0;
    if (p.bufferAtr > 0 && atr && Number.isFinite(atr[i])) buf = p.bufferAtr * atr[i];

    const c = bars[i].c;
    if (sh && c > sh.price + buf) {
      const kind = bias === BULL ? BOS : CHOCH;
      const before = bias;
      bias = BULL;
      events.push({ kind, direction: BULL, i, t: bars[i].t, level: sh.price,
                    levelI: sh.i, biasBefore: before, biasAfter: bias, close: c });
      aEv[i] = kind; aDir[i] = BULL;
      sh = null;                       // consumed
    } else if (sl && c < sl.price - buf) {
      const kind = bias === BEAR ? BOS : CHOCH;
      const before = bias;
      bias = BEAR;
      events.push({ kind, direction: BEAR, i, t: bars[i].t, level: sl.price,
                    levelI: sl.i, biasBefore: before, biasAfter: bias, close: c });
      aEv[i] = kind; aDir[i] = BEAR;
      sl = null;
    }

    aBias[i] = bias;
    aSh[i] = sh ? sh.price : NaN;
    aSl[i] = sl ? sl.price : NaN;
  }

  return { events, bias: aBias, swingHigh: aSh, swingLow: aSl,
           event: aEv, eventDir: aDir };
}

/** State at the last bar — what a panel needs. */
export function latest(bars, params = {}) {
  if (!bars || !bars.length) return null;
  const r = detect(bars, params);
  const i = bars.length - 1;
  const last = r.events.length ? r.events[r.events.length - 1] : null;
  return {
    bias: r.bias[i], swingHigh: r.swingHigh[i], swingLow: r.swingLow[i],
    lastEvent: last ? last.kind : null,
    lastEventDir: last ? last.direction : null,
    lastEventI: last ? last.i : null,
    barsSince: last ? i - last.i : null,
  };
}
