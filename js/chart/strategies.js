/* strategies.js — the registry the Strategy Replay reads.
 *
 * ADDING A STRATEGY is a data entry: a rule object (see js/chart/rules.js for
 * the contract) plus its VALIDATION RECORD. The record is not documentation, it
 * is what the panel puts on screen, and it is required rather than optional for
 * a specific reason: this project has measured eight strategy families and
 * exactly one survived. A dropdown that lists them all as equals would erase
 * the only finding that matters.
 *
 * `status` is one of:
 *   validated   passed the sample floor, the time-shift control and the
 *               profitability gate, IN SAMPLE AND OUT, on the cells listed
 *   failed      measured and did not survive -- kept because seeing HOW a rule
 *               fails is worth more than being told it does
 *   untested    ported but never put through the gates. Not "probably fine".
 *
 * `cells` names where the record applies. Pointing a strategy at a cell that is
 * not in its list is not forbidden -- the replay is for looking -- but the panel
 * says so, because the same rule on a different instrument is a different
 * hypothesis and this project has watched that distinction collapse before.
 */

import { donchianRule } from './donchian.js';
import { emaCrossRule } from './emacross.js';
import { turtleEaRule } from './turtle_ea.js';

export const STRATEGIES = [
  {
    ...donchianRule,
    status: 'validated',
    cells: ['XAUUSD.a 4h'],
    /* A FAMILY, NOT AN OPTIMUM. N=10, 20, 30 and 50 all pass every gate on
       XAUUSD 4h (runs/stage1_nsweep_gold.csv), so 20 is REPRESENTATIVE of a
       plateau rather than a tuned peak. The distinction matters: 'optimised
       at 20' invites the question of what else was tried, while 'the family
       N=10-50, sampled at 20' is what the sweep actually shows. */
    family: 'Donchian N=10-50 (plateau); 20 is representative, not optimal',
    record: {
      'OOS 2016-2020': { trades: 207, winPct: 36.2, avgR: 0.219, pf: 1.466, ddPct: -7.5 },
      'IS 2021-2026': { trades: 231, winPct: 35.1, avgR: 0.189, pf: 1.326, ddPct: -8.6 },
    },
    notes: 'N=20 is one of FOUR consecutive channel lengths that pass every gate (10, 20, 30, 50) -- a plateau, not a peak. Percentile 96.7 vs its time-shift control in both eras. 90 of 90 '
      + 'parameter neighbours profitable. 5 of 5 walk-forward windows positive. '
      + 'BUT walk-forward attributed the return to gold trending (corr +0.90 '
      + 'with absolute market move), so this is trend beta, not alpha. '
      + 'RE-SWEPT across 5m/15m/30m/1h/4h/1d on XAUUSD and USDJPY with the '
      + 'current cost model: 2 of 12 cells passed in sample, and only this one '
      + 'survived out of sample. USDJPY 1h passed in sample at percentile 96.7 '
      + '(+0.0788) and then came back -0.0580 with PF 0.896 on 2016-2020, its '
      + 'second negative OOS era. 4h is the frame, not a starting point.',
  },
  {
    ...emaCrossRule,
    status: 'failed',
    cells: ['USDJPY.a 1h', 'XAUUSD.a 4h'],
    record: {
      'IS 2021-2026 USDJPY 1h': { trades: 377, winPct: null, avgR: 0.2453, pf: 1.380, ddPct: null },
      'OOS pre-2021 USDJPY 1h': { trades: 1023, winPct: null, avgR: 0.0083, pf: 1.002, ddPct: null },
    },
    notes: 'Cleared Stage 1 in sample at percentile 96.7 and then died out of '
      + 'sample: avg R fell from +0.2453 to +0.0083 and PF from 1.380 to 1.002. '
      + 'Kept in the registry because watching a rule look good on one era and '
      + 'break on the next is the lesson, and a dropdown of only winners '
      + 'teaches nothing.',
  },
  {
    ...turtleEaRule,
    /* UNTESTED, and that word is doing real work. It is a faithful build of a
       published EA so it can be STEPPED THROUGH, not a rule this project has
       gated: no time-shift controls, no parameter-neighbour sweep, no
       walk-forward. The replay picker is the only place it appears -- the live
       rule panel imports donchian.js directly and never reads this registry. */
    status: 'untested',
    /* The cells its RECORD covers, which is not the same as cells it passed on
       -- `status` carries that, and it says untested. Both are measured here
       (see `record`), so the replay can quote real numbers beside the bar you
       are standing on instead of showing a strategy with nothing behind it. */
    cells: ['XAUUSD.a 4h', 'XAUUSD.a 1h'],
    record: {
      'IS 2021-2026 XAUUSD 4h': { trades: 84, winPct: null, avgR: 0.1618, pf: null, ddPct: -3.8 },
      'OOS 2016-2020 XAUUSD 4h': { trades: 69, winPct: null, avgR: 0.2170, pf: null, ddPct: null },
      'IS 2021-2026 XAUUSD 1h': { trades: 280, winPct: null, avgR: 0.1066, pf: null, ddPct: -9.7 },
      'OOS 2016-2020 XAUUSD 1h': { trades: 239, winPct: null, avgR: 0.0901, pf: null, ddPct: null },
    },
    notes: 'Reimplemented from github.com/ymodulus21/donchianturtle-ea to be '
      + 'measured, not recommended. Its filter stack cuts gold 4h from 41 to 15 '
      + 'trades a year and takes net R from +45.4 to +13.8 out of sample -- it '
      + 'rescues the timeframes the plain rule loses on and spoils the one it '
      + 'wins on. Sizing, the volatility scaler and the drawdown breaker are '
      + 'NOT implemented: R is size-invariant, so they cannot move these '
      + 'numbers.',
  },
];

export const byKey = (key) => STRATEGIES.find((s) => s.key === key) || STRATEGIES[0];

/** Does this strategy's validation record cover this cell? */
export function coversCell(strategy, symbol, tf) {
  return (strategy.cells || []).includes(`${symbol} ${tf}`);
}

export const STATUS_TEXT = {
  validated: 'validated — passed every gate in sample and out',
  failed: 'failed — measured and did not survive',
  untested: 'untested — never put through the gates',
};
