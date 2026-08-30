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
 * implementation, and tests/test_strategy_parity.py checks this file against
 * sim/strategies/donchian.py signal for signal.
 *
 * CAUSALITY. Channels are computed from bars STRICTLY BEFORE the bar being
 * decided (the `shift(1)` in the Python), and `signalsAsOf(bars, {upto})` is a
 * pure function of bars[0..upto]. A signal fires on a CLOSE and fills at the
 * NEXT OPEN, which is why `pending` is reported separately from `position`:
 * the entry price does not exist yet.
 */

import { atrSeries } from './tlengine.js';
import { emaSeries, FLAT as FLAT_, LONG as LONG_, SHORT as SHORT_, rollingShifted, runRule }
  from './rules.js';

/*
 * `emaLen: 0` IS THE VALIDATED RULE. The filter adds no series and no
 * condition at zero, so the 207/231 trades and every gate they passed stand
 * untouched. Any positive length is a DIFFERENT STRATEGY that has passed
 * nothing, which is why it must be asked for rather than defaulted on.
 */
export const DEFAULTS = { entry: 20, exit: 10, atrLen: 14, atrMult: 2.0, emaLen: 0 };

export { FLAT, LONG, SHORT } from './rules.js';

/* ------------------------------------------------------------------ horizon
 *
 * A MIRROR OF sim/strategies/horizon.py, carried here because the panel draws
 * the channel before /signal answers. A copy is only safe while something
 * fails when the two disagree; tests/test_horizon_parity.py is that something,
 * and it runs THIS code rather than re-deriving it.
 *
 * THE EDGE IS A DURATION, NOT A BAR COUNT. N=20 was validated on gold 4h,
 * which is 80 hours of channel. Running "N=20" on 15m is not the same rule at
 * a finer resolution -- it is a FIVE-HOUR channel, a different strategy that
 * happens to share a number, and it was measured at -0.0756 R over 4,142
 * trades. The same 3.3-day duration on 15m is N=317 and measured +0.1762 R.
 * So the timeframe selects N and the horizon stays fixed.
 */

/** Bars per 24h. Nominal counts, identical to the ones the sweeps used. */
export const BARS_PER_DAY = { '1m': 1440, '5m': 288, '15m': 96, '30m': 48,
                              '1h': 24, '4h': 6, '1d': 1 };

/** The horizon that passed every gate in both eras, at 4h, 1h and 15m. */
export const HORIZON_DAYS = 3.3;

/**
 * Where a 3.3-day channel is a sensible number of bars.
 *
 * 1d is out because 20 bars is already 20 days -- a 3.3-day channel would be
 * N=3, which is noise. 1m is out because 3.3 days is 4,752 bars: arithmetically
 * fine, never measured. Outside this list the base 20/10 stands, and
 * `horizonDays: null` says so rather than presenting an extrapolation as the
 * validated rule.
 */
export const HORIZON_TFS = ['5m', '15m', '30m', '1h', '4h'];

/**
 * The registered strategy NAME this timeframe resolves to -- mirrors
 * sim.strategies.horizon.strategy_for_tf.
 *
 * It exists so a surface quoting a measured record can check that the record
 * belongs to the rule it is drawing. On 15m the panel now draws a 317-bar
 * channel; runs/ measured a 20-bar one. Those are different strategies that
 * share a file, and showing one's verdict beside the other's channel is the
 * same mistake in the opposite direction from the one the horizon map fixes.
 */
export function strategyForTf(tf, days = HORIZON_DAYS) {
  const p = paramsForTf(tf, days);
  return p.entry === DEFAULTS.entry ? 'donchian' : `donchian_n${p.entry}`;
}

/** Channel length covering `days` of `tf` bars. */
export function nFor(tf, days = HORIZON_DAYS) {
  return Math.max(5, Math.round(days * BARS_PER_DAY[tf]));
}

/**
 * Rule parameters for this timeframe.
 *
 * The exit stays at N/2, keeping the validated 20/10 SHAPE across the family:
 * letting it vary independently turns one degree of freedom into two and makes
 * any winner impossible to attribute.
 */
export function paramsForTf(tf, days = HORIZON_DAYS) {
  if (!HORIZON_TFS.includes(tf)) {
    return { ...DEFAULTS, horizonDays: null, tf };
  }
  const n = nFor(tf, days);
  return { ...DEFAULTS, entry: n, exit: Math.max(2, Math.floor(n / 2)),
           horizonDays: days, tf };
}

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

  warmup: (p) => Math.max(p.entry, p.exit, p.atrLen, p.emaLen || 0) + 2,

  /* What `runRule` asks when a caller names a timeframe. Declared on the rule
     rather than looked up by the walker so the walker stays rule-agnostic:
     every other rule simply has no paramsFor and keeps its defaults. */
  paramsFor: (tf) => paramsForTf(tf),

  prepare(bars, p) {
    const high = bars.map((b) => b.h);
    const low = bars.map((b) => b.l);
    const out = {
      hi: rollingShifted(high, p.entry, Math.max),
      lo: rollingShifted(low, p.entry, Math.min),
      exitHi: rollingShifted(high, p.exit, Math.max),
      exitLo: rollingShifted(low, p.exit, Math.min),
      atr: atrSeries(bars, p.atrLen),
      /* NOT shifted, unlike the channels. An EMA of closes up to and including
         now is knowable at this close; the channel must exclude the bar it is
         judging or `close > highestHigh` becomes unsatisfiable, since the high
         would already contain the close. Different quantities, different
         causality, and conflating them is a whole class of bug. */
    };
    /* Added only when on, mirroring the Python: the engine there rejects a
       non-bar-length series, and keeping the two shapes identical is what
       makes the parity test meaningful. */
    if (p.emaLen) out.ema = emaSeries(bars.map((b) => b.c), p.emaLen);
    return out;
  },

  decide(i, { series, close, pos, p }) {
    const a = series.atr[i];
    if (!Number.isFinite(a) || a <= 0) return null;
    if (pos) {
      const out = pos.side === LONG_ ? close[i] < series.exitLo[i]
                                     : close[i] > series.exitHi[i];
      return out ? { side: FLAT_, reason: 'signal' } : null;
    }
    /* The filter gates DIRECTION only: it never opens a trade the channel did
       not, and never closes one early. An exit that consulted the EMA would be
       a second exit rule competing with the channel that carries the edge. */
    let allowLong = true, allowShort = true;
    if (p.emaLen) {
      const t = series.ema[i];
      if (!Number.isFinite(t)) return null;
      allowLong = close[i] > t;
      allowShort = close[i] < t;
    }
    if (allowLong && Number.isFinite(series.hi[i]) && close[i] > series.hi[i]) {
      return { side: LONG_, stop: close[i] - p.atrMult * a, tag: 'breakout_up' };
    }
    if (allowShort && Number.isFinite(series.lo[i]) && close[i] < series.lo[i]) {
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
 * Back-compatible entry point. tests/test_strategy_parity.py drives this, and
 * it is the shape the panel used before the registry existed.
 */
export function signalsAsOf(bars, opts = {}) {
  return runRule(bars, donchianRule, opts);
}

export { instruction, tally } from './rules.js';
