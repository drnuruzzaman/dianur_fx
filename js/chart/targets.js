/* targets.js — TP1/TP2/TP3 as bands measured in R off the real stop.
 *
 * WHAT THEY ARE. Three ranges at fixed multiples of the risk you are actually
 * carrying, computed from the entry and the stop rather than from round numbers
 * or from a level someone liked the look of. Change the stop and every band
 * moves, which is the point: a target is only meaningful relative to what it
 * costs to be wrong.
 *
 * WHAT THEY ARE NOT. Part of the validated strategy. XAUUSD 4h Donchian passed
 * its gates with NO take-profit -- 138 of 207 out-of-sample exits were the
 * trailing 10-bar channel and none were a target -- and tools/tp_sweep.py
 * measured what capping costs: a 1R cap lifts the win rate from 35% to 49% and
 * turns +43.7 net R into -2.1. It LOSES MONEY. At 3R it keeps 82-85% of net R;
 * only past 4R is a cap harmless, and by then it fires on one trade in ten.
 *
 * So the bands start at 1.5R, deliberately clear of the range the measurement
 * condemned, and they are drawn as REFERENCE, not as an instruction. Reading
 * them as exits changes a strategy that was validated into one that was not.
 *
 * The centres track the structural targets worked out on gold at 4,596 -- TP1
 * 4,620-4,632, TP2 4,644-4,666, TP3 4,672-4,693 measured 1.6-2.4R, 3.2-4.7R and
 * 5.1-6.5R against a 15-point stop -- so 2R / 3.5R / 5R reproduces that ladder
 * from any entry, which is what makes it reusable rather than one chart\'s
 * answer.
 */

/* The ladder is CENTRES plus one half-width, not three hand-picked ranges.
 *
 * The first version wrote the ranges out longhand -- 1.5-2.5R, 3.0-4.5R,
 * 5.0-6.5R -- which made TP2 a band 1.5R wide. On gold 4h with a 30-point stop
 * that is a 45-point ribbon across the chart, wide enough to read as a ZONE
 * with meaning when it is nothing of the kind: the width was my arbitrary
 * choice and nothing measured it. A narrow band is the more honest shape,
 * because it admits these are reference levels rather than regions price is
 * expected to respect.
 *
 * Structural targets DO have width -- a supply zone is genuinely thick -- but
 * these are not structural. They are multiples of the risk you are carrying.
 */
export const TP_CENTRES = [
  { key: 'TP1', r: 2.0, note: 'first scale-out' },
  { key: 'TP2', r: 3.5, note: 'the main target' },
  { key: 'TP3', r: 5.0, note: 'the runner' },
];

/** Half the band's height, in R. One number, so widening is one edit. */
export const BAND_HALF_R = 0.25;

/**
 * How a level is named where NOTHING BESIDE IT SAYS WHAT IT IS -- the live
 * chart's line, which has no panel to correct a wrong reading. Mirrors
 * sim/targets.label, and tests/test_targets_parity.py fails when the two
 * drift.
 *
 * Never 'TP'. The word is an instruction, and these are not instructions: a
 * cap at the low end of this ladder was MEASURED to turn +43.7 net R into
 * -2.1. The replay keeps TP1/TP2/TP3 because its own panel states, in the same
 * frame, that the rule has no take-profit -- so the name is corrected on
 * screen. `naming: 'ref'` is for every surface that cannot do that.
 *
 * `%g` in the Python drops a trailing zero, and String() does the same: 2 ->
 * "2R ref", 3.5 -> "3.5R ref".
 */
export function refLabel(r) {
  return `${r}R ref`;
}

/**
 * Bands for one position, in price.
 *
 * `side` is +1 long / -1 short. Returns [] when there is nothing to measure
 * from -- no position, or a stop on the wrong side of the entry, which is a
 * broken input rather than a zero-risk trade.
 */
export function targetBands({ side, entry, stop, halfR = BAND_HALF_R,
                              naming = null } = {}) {
  if (!side || !Number.isFinite(entry) || !Number.isFinite(stop)) return [];
  /* MetaTrader reports an UNSET stop as 0.0, which is finite and sits on the
     right side of any long entry -- so a plain sign check accepts it and
     "risk" becomes the entire price. A live USDJPY position with sl 0.0 and
     entry 163.629 would have drawn bands 800 points up. No stop means no R to
     measure, so it means no bands. */
  if (!(entry > 0) || !(stop > 0)) return [];
  const risk = (entry - stop) * side;
  if (!(risk > 0)) return [];
  return TP_CENTRES.map((b) => {
    const lo = Math.max(0, b.r - halfR);
    const hi = b.r + halfR;
    const a = entry + side * lo * risk;
    const z = entry + side * hi * risk;
    return {
      key: b.key,
      /* null unless a caller asked to be named, so the replay's TP1/TP2/TP3
         cannot be changed by adding a surface that needs the other naming. */
      label: naming === 'ref' ? refLabel(b.r) : null,
      note: b.note,
      r: b.r,
      lo,
      hi,
      price: entry + side * b.r * risk,     // the centre, which is the level
      low: Math.min(a, z),
      high: Math.max(a, z),
      risk,
    };
  });
}

/** A one-line summary for a panel: "TP2 3.5R  4655.2". */
export function describeBand(band, digits = 2) {
  return `${band.key} ${band.r}R  ${band.price.toFixed(digits)}`;
}
