/* swingreadout.js — what the swing code is ACTUALLY doing, on the surfaces
 * where that matters.
 *
 * WHY THIS EXISTS. The chart draws swings from a ZigZag; BOS/CHoCH, S/R zones
 * and the levels reading all still find their swings with a fractal window,
 * and the trendline engine uses a third thing again -- a per-timeframe window
 * that widens with volatility. Those are four different answers to "where did
 * price turn", and until now the only way to know which one a mark came from
 * was to read the source.
 *
 * EVERY NUMBER HERE IS READ FROM THE MODULE THAT USES IT. Nothing is retyped:
 * if someone changes DEFAULT_MS_PARAMS.strength, this row changes with it. A
 * panel that hardcoded the same constants would agree with the code exactly
 * until the day it mattered.
 *
 * It reports; it does not reconcile. Making the four agree is a claim about
 * BOS/CHoCH and zones that has not been measured, and a readout that quietly
 * changed them would be the wrong way to make it.
 */

import { SWING_TIERS, RING_FROM } from '../chart/structure.js';
import { SENSITIVITY } from '../chart/trendlines.js';
import { DEFAULT_MS_PARAMS } from '../chart/marketstructure.js';
import { DEFAULT_ZONE_PARAMS } from '../chart/zones.js';
import { DEFAULT_STRUCT_PARAMS } from '../chart/levels.js';
import { BASE_STRENGTH } from '../chart/sensitivity.js';

const med = (a) => (a.length
  ? a.slice().sort((x, y) => x - y)[a.length >> 1] : NaN);

/**
 * Rows for a key/value panel: [key, value, title].
 *
 * `drawn` is the swing array the chart was actually handed -- not a fresh
 * computation, because the point is to show what IS on screen. Passing the
 * bars and recomputing here would report what this module thinks should be
 * drawn, which is exactly the kind of agreement that hides a bug.
 */
export function swingEngineRows(drawn, { sens = 'normal', tf = '',
                                         drawnFrom = 'zigzag',
                                         used = null, zigzag = null,
                                         atrNow = NaN, priceNow = NaN,
                                         digits = 2 } = {}) {
  const rows = [];
  const marks = drawn || [];

  /* WHICH DETECTOR PUT THE MARKS ON SCREEN, said first, because the rest of
     the panel is only meaningful once that is known. The strategy replay draws
     the fractal its own detectors use; the live chart and the Elliott replay
     draw the ranked ZigZag. Reporting the ZigZag's tiers on a chart showing
     fractal pivots would be the exact failure this panel exists to prevent. */
  if (drawnFrom === 'backend') {
    rows.push(['swing marks', `fractal ±${DEFAULT_MS_PARAMS.strength}`,
               'the same pivots bos/choch, zones and levels are built on']);
    rows.push(['rings', `also survive ±${SENSITIVITY.major.strength}`,
               'what marketstructure keys an EXTERNAL break on']);
    rows.push(['drawn', `${marks.filter((s) => !s.major).length} · `
               + `${marks.filter((s) => s.major).length}`,
               'marks: internal · external']);
    /* THE LINE IS A SECOND ANSWER, and an unlabelled second answer on a chart
       is worse than none: a reader would take the line for something the
       engine uses. It is named here, with its count, and marked as reading
       nothing. */
    if (zigzag) {
      rows.push(['zigzag line', zigzag.length
        ? `${zigzag.length} turns ≥${SWING_TIERS[1].toFixed(1)} ATR` : 'none',
        'the other definition, drawn for comparison — no engine reads it']);
      /* WHAT THE THRESHOLD IS IN MONEY, at this bar.
         "3 ATR" is the honest statement of the rule and a useless one at the
         screen: a classic ZigZag is set in percent, and nobody can convert ATR
         to price in their head. Both are shown, and they move bar to bar --
         which is the whole difference between this and a fixed-percent
         ZigZag, made visible rather than asserted. */
      if (atrNow > 0 && priceNow > 0) {
        const dist = SWING_TIERS[1] * atrNow;
        rows.push(['reversal', `${dist.toFixed(digits)}  (${(100 * dist / priceNow).toFixed(2)}%)`,
                   'price must retrace this far from a leg extreme to end the leg — at THIS bar']);
      }
      /* NO DEPTH PARAMETER, and it is worth saying so. MT4's ZigZag takes
         Depth (12) and Backstep (3); this takes neither, so two turns can sit
         one bar apart, or share a bar when one candle is both a leg's low and
         the next leg's high. Measured: the minimum gap is 0 bars and the 10th
         percentile is 1-4, against a median of 13-16. */
      rows.push(['min leg', 'no bar minimum',
                 'unlike MT4 Depth/Backstep: size is the only test, never bar count']);
    }
  } else {
    const ringFrom = RING_FROM[sens] ?? RING_FROM.normal;
    rows.push(['swing marks', 'zigzag (display only)',
               'no engine here reads these -- see the rows below']);
    rows.push(['tiers', SWING_TIERS.map((t) => t.toFixed(1)).join(' / ') + ' ATR',
               'travel a leg must make to reach each rank']);
    rows.push(['ring from', `rank ${ringFrom}  (${sens})`,
               'the only thing the sensitivity menu changes about swings']);
    const by = [0, 0, 0];
    for (const s of marks) by[Math.min(2, s.rank ?? 0)]++;
    rows.push(['drawn', by.join(' · '),
               'marks by rank: inside a leg · ended a leg · shaped the range']);
  }

  const lag = marks.map((s) => s.confirmedI - s.i).filter(Number.isFinite);
  if (lag.length) {
    rows.push(['confirm lag', `${med(lag)} bars`,
               'median bars from the turn to the bar that proved it']);
  }

  /* THE OTHER FOUR ANSWERS. Shown together because the disagreement is the
     thing worth seeing, and it is invisible on the chart itself: a BOS tag and
     a swing ring can point at different bars and nothing says why. */
  /* THE VALUES THE SURFACE ACTUALLY PASSED, when it tells us.
     
     These read the module defaults, and a caller that overrides them made this
     panel false rather than merely incomplete: the strategy replay used to
     hand BOS/CHoCH and the zones the sensitivity menu's number while this row
     went on reporting the default. `used` closes that -- a surface reports
     what it called with, and one that passes nothing is saying it runs the
     defaults. */
  const u = used || {};
  const say = (v, dflt) => `fractal ±${v === undefined || v === null ? dflt : v}`
    + (v !== undefined && v !== null && v !== dflt ? ' (overridden)' : '');
  rows.push(['bos/choch', say(u.ms, DEFAULT_MS_PARAMS.strength),
             'js/chart/marketstructure.js']);
  rows.push(['s/r zones', say(u.zones, DEFAULT_ZONE_PARAMS.strengthPivots),
             'js/chart/zones.js']);
  rows.push(['levels', say(u.levels, DEFAULT_STRUCT_PARAMS.swingStrength),
             'js/chart/levels.js']);
  /* TRENDLINES: WHICHEVER OF TWO PATHS THE SURFACE ACTUALLY TOOK.
   *
   * This row reported `BASE_STRENGTH[tf]` with the note "widens with
   * volatility", and on the strategy replay that named a module the chart
   * never calls. `liveLines` uses the adaptive sensitivity ONLY when handed a
   * `sensitivity` object; with none, TrendlineEngine.strength() falls through
   * to `params.strength` -- the menu preset. The replay passes none, so its
   * trendlines were running on the menu's window (2 at `fine`) while this row
   * announced 5 from a module that was never involved.
   *
   * So the surface says which path it took. `tl.adaptive` means a calibration
   * was supplied and the window really is per-timeframe and volatility-aware;
   * otherwise the menu number is reported.
   *
   * The ATR clause appears only when there IS one. `minSwingAtr` defaults to 0
   * -- the engine's prominence test is OFF on this path, and only a supplied
   * calibration turns it on via `minProminenceAtr`. Printing a floor of 0 as
   * though it were a filter would be the same kind of false comfort this panel
   * was built to remove. */
  const tl = u.tl;
  if (tl && tl.adaptive) {
    const base = BASE_STRENGTH[tf];
    rows.push(['trendlines', base === undefined ? 'adaptive'
      : `adaptive ±${base}, +1/+2 when fast`,
               'js/chart/sensitivity.js — per timeframe, widens with volatility']);
  } else if (tl) {
    rows.push(['trendlines', `fractal ±${tl.strength}`
      + (tl.minSwingAtr > 0 ? ` · ≥${tl.minSwingAtr} ATR` : ''),
               'js/chart/tlengine.js — the sensitivity menu, not the adaptive module']);
  } else {
    rows.push(['trendlines', 'not drawn here', 'no trendlines on this surface']);
  }
  return rows;
}
