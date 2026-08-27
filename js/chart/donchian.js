/* donchian.js — the one validated strategy, as a causal signal source.
 *
 * WHAT THIS IS. XAUUSD 4h Donchian(20/10, stop 2.0 ATR) is the only strategy in
 * this project that passed every gate: percentile 96.7 against its time-shift
 * control in both eras, 90 of 90 parameter neighbours profitable, 5 of 5
 * walk-forward windows positive, and it survived the spread floor and the
 * out-of-sample split. Everything else measured -- trendline bounce, trendline
 * breakout, retest, mean reversion, stretch-follow, RSI divergence, Elliott
 * wave counts -- came back at or below its own placebo.
 *
 * So this file is deliberately small. It is not a framework for strategies; it
 * is the one rule that earned the right to be traded, written out so the chart
 * shows exactly what the backtest measured.
 *
 * IT HAS NO TAKE-PROFIT, AND THAT IS THE POINT. 138 of its 207 out-of-sample
 * exits were the trailing 10-bar channel and none were a target. At a 36% win
 * rate and PF 1.47 the arithmetic only closes because a few winners run for
 * weeks. tools/tp_sweep.py measured what a cap costs: a 1R take-profit lifts
 * the win rate to 49% and turns +43.7 net R into -2.1 -- it loses money. Any
 * "take profit" this panel shows is therefore a MOVING channel level, not a
 * price you set once, and the distinction is the strategy.
 *
 * WHY IT MATCHES PYTHON. ATR comes from `atrSeries` in tlengine.js, which
 * tests/test_parity.py holds to sim/indicators.atr at 1e-9. The strategies used
 * to carry a private ATR that seeded the Wilder recursion differently and
 * diverged by up to 0.86 price units during warmup -- a signal service built on
 * that would have quoted stops the backtest never traded. That is now one
 * implementation, and tests/test_donchian_parity.py checks this file against
 * sim/strategies/donchian.py signal for signal.
 *
 * CAUSALITY. Channels are computed from bars STRICTLY BEFORE the bar being
 * decided (the `shift(1)` in the Python), and `signalsAsOf(bars, {upto})` is a
 * pure function of bars[0..upto]. A signal fires on a CLOSE and fills at the
 * NEXT OPEN, which is why `pending` is reported separately from `position`:
 * the entry price does not exist yet.
 */

import { atrSeries } from './tlengine.js';
import { FLAT as FLAT_, LONG as LONG_, SHORT as SHORT_, rollingShifted, runRule }
  from './rules.js';

export const DEFAULTS = { entry: 20, exit: 10, atrLen: 14, atrMult: 2.0 };

export { FLAT, LONG, SHORT } from './rules.js';

/**
 * The rule, in the shape js/chart/rules.js walks and js/chart/strategies.js
 * registers. The trade lifecycle -- fill at the next open, gaps at the open,
 * stop on the range -- deliberately lives in the walker: it is what the
 * simulator does, not what this strategy decides, and three copies of it is how
 * the ATR divergence got in.
 */
export const donchianRule = {
  key: 'donchian',
  label: 'Donchian 20/10',
  defaults: DEFAULTS,
  summary: 'Enter at the next open when a close clears the 20-bar extreme; '
    + 'leave when a close crosses the 10-bar channel the other way, or on a '
    + 'stop 2.0 ATR from the signal close. No take-profit.',

  warmup: (p) => Math.max(p.entry, p.exit, p.atrLen) + 2,

  prepare(bars, p) {
    const high = bars.map((b) => b.h);
    const low = bars.map((b) => b.l);
    return {
      hi: rollingShifted(high, p.entry, Math.max),
      lo: rollingShifted(low, p.entry, Math.min),
      exitHi: rollingShifted(high, p.exit, Math.max),
      exitLo: rollingShifted(low, p.exit, Math.min),
      atr: atrSeries(bars, p.atrLen),
    };
  },

  decide(i, { series, close, pos, p }) {
    const a = series.atr[i];
    if (!Number.isFinite(a) || a <= 0) return null;
    if (pos) {
      const out = pos.side === LONG_ ? close[i] < series.exitLo[i]
                                     : close[i] > series.exitHi[i];
      return out ? { side: FLAT_, reason: 'signal' } : null;
    }
    if (Number.isFinite(series.hi[i]) && close[i] > series.hi[i]) {
      return { side: LONG_, stop: close[i] - p.atrMult * a, tag: 'breakout_up' };
    }
    if (Number.isFinite(series.lo[i]) && close[i] < series.lo[i]) {
      return { side: SHORT_, stop: close[i] + p.atrMult * a, tag: 'breakout_dn' };
    }
    return null;
  },

  /* The live exit: where the position leaves on a CLOSE through it, recomputed
     every bar. That is why it is not a take-profit. */
  exitLevel: (i, { series, pos }) =>
    (pos.side === LONG_ ? series.exitLo[i] : series.exitHi[i]),
};

/**
 * Back-compatible entry point. tests/test_donchian_parity.py drives this, and
 * it is the shape the panel used before the registry existed.
 */
export function signalsAsOf(bars, opts = {}) {
  return runRule(bars, donchianRule, opts);
}

export { instruction, tally } from './rules.js';
