/**
 * trailmode.js -- the structural trailing exit, always on in the replay.
 *
 * WHAT IT DOES. The rule's exit is the extreme of the last N/2 bars, which is a
 * fixed window in TIME: on XAUUSD 5m it is 39.6 hours, so after a fast move the
 * exit sits wherever the high was a day and a half ago and cannot compress
 * faster than the clock. A short at +7.17 R open had its exit 139 points away,
 * worth +2.24 R if taken -- 31% of what was open. This trails the exit to the
 * nearest structure BEHIND price instead, and takes whichever of the two is
 * tighter.
 *
 * IT IS NOT A TAKE-PROFIT AND THE DIFFERENCE IS THE POINT. A target caps the
 * winner; twelve cells said not to take that bet and it was removed from the
 * whole app. A trail never limits how far a trade may run -- it only decides
 * when a move is over. `js/chart/rules.js` enforces the rest: the trail can only
 * sit INSIDE the rule's own exit, so turning this on can never loosen anything.
 *
 * WHAT THE MEASUREMENT SAYS, recorded beside the code rather than in a commit
 * message, and re-run after the trail was narrowed to S/R and swings so these
 * describe exactly what runs. tools/exit_trail_eval.py, eight cells, two eras,
 * gross, against the channel:
 *
 *     structure    2016-2020   +0.2 R [-102.6, +104.6]
 *                  2021-2026  +85.9 R [ -62.8, +234.0]
 *
 * The earlier era is now indistinguishable from the channel and the recent one
 * still leans hard the right way -- it turns -43.8 into +42.1. But the control
 * is what decides it, and the control is an ATR trail matched to the same
 * average distance from price -- ~1.85 ATR, knowing nothing about the chart:
 *
 *     structure - matched control   -49.1 R [-122.0, +30.2]
 *                                   +38.9 R [ -32.0, +104.6]
 *
 * Both span zero and the earlier era is NEGATIVE: a dumb trail at the same
 * distance did better there, by more than it did before the break-even floor
 * was added. Whatever the gain is, it is not structure knowing where an exit
 * belongs -- the same finding that killed eleven entry gates.
 *
 * NOT DEMONSTRATED is the honest verdict; not "does not work", because unlike a
 * target nothing here is structurally doomed. But note which way three
 * successive refinements have moved it: all five level kinds, then S/R and
 * swings only, then the break-even floor, and the control comparison went
 * +62.6/+14.8, then -32.4/+65.6, then -49.1/+38.9. Every change that made the
 * trail more sensible as a stop made it harder to distinguish from a dumb one.
 *
 * NOTHING ON SCREEN SAYS ANY OF THIS. There was a note on both the live panel
 * and the strategy replay; both were removed by request, along with the two-word
 * qualifier that replaced the first one. So the only record that the exit is not
 * the validated one is this comment and logs/exit_trail_eval.txt -- which is why
 * the numbers above are kept here in full rather than trimmed to a citation.
 *
 * A reader arriving at the Donchian panel sees a verdict chip earned on the
 * channel exit beside levels produced by a different exit, with nothing marking
 * the difference. Anyone re-opening this should know that before changing it.
 */

import { makeTrail } from './exittrail.js';

/**
 * NO SWITCH, ON EITHER SURFACE. The trail had a per-cell toggle for one
 * revision and it is gone by request: this IS the exit now, everywhere, and the
 * baseline is not one click away. The comparison against the plain channel exit
 * lives in tools/exit_trail_eval.py, which is where it belonged anyway -- a
 * toggle answers "what does this look like", never "is it better".
 */

/** The `exitTrail` option for `runRule`. Always on in the replay. */
export function trailOption(cell, tf) {
  return makeTrail('structure', { tf, cell });
}
