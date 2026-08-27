/* emacross.js — fast/slow EMA cross with an ATR stop.
 *
 * A port of sim/strategies/ema_cross.py, held to it by
 * tests/test_emacross_parity.py. It is in the registry as a FAILED strategy,
 * deliberately: it cleared Stage 1 on USDJPY 1h at percentile 96.7 with avg R
 * +0.2453, then out of sample returned +0.0083 with PF 1.002. Being able to
 * step through the era where it looked good and watch the next era take it
 * apart is worth more than a dropdown containing only the winner.
 *
 * Like the Donchian rule it has NO take-profit: it exits when the cross goes
 * the other way, or on the stop.
 */

import { atrSeries } from './tlengine.js';
import { FLAT, LONG, SHORT, emaSeries } from './rules.js';

export const emaCrossRule = {
  key: 'ema_cross',
  label: 'EMA cross 21/50',
  defaults: { fast: 21, slow: 50, atrLen: 14, atrMult: 2.5 },
  summary: 'Enter when the 21 EMA crosses the 50; leave on the cross back, or '
    + 'on a stop 2.5 ATR from the signal close.',

  warmup: (p) => Math.max(p.slow, p.atrLen) + 2,

  prepare(bars, p) {
    const close = bars.map((b) => b.c);
    return {
      fast: emaSeries(close, p.fast),
      slow: emaSeries(close, p.slow),
      atr: atrSeries(bars, p.atrLen),
    };
  },

  decide(i, { series, close, pos, p }) {
    const f = series.fast[i], s = series.slow[i];
    const fp = series.fast[i - 1], sp = series.slow[i - 1];
    const a = series.atr[i];
    if (![f, s, fp, sp, a].every(Number.isFinite) || a <= 0) return null;

    const up = fp <= sp && f > s;
    const dn = fp >= sp && f < s;

    if (pos) {
      if ((pos.side === LONG && dn) || (pos.side === SHORT && up)) {
        return { side: FLAT, reason: 'signal' };
      }
      return null;
    }
    if (up) return { side: LONG, stop: close[i] - p.atrMult * a, tag: 'cross_up' };
    if (dn) return { side: SHORT, stop: close[i] + p.atrMult * a, tag: 'cross_dn' };
    return null;
  },

  /* There is no level to leave at -- the exit is a CROSS, not a price. Saying
     so is better than quoting the slow EMA and implying a close through it is
     the trigger, which it is not: the trigger is the fast line crossing it. */
  exitLevel: () => null,
};
