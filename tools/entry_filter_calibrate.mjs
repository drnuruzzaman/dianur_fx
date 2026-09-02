/* What the gates' underlying quantities actually LOOK like at a breakout.
 *
 * WHY THIS EXISTS AND WHY IT RUNS BEFORE THE EVAL. The first pre-committed grid
 * put `room` at 1/2/3 R, on the reasoning that a trade wants a couple of
 * risk-units of space. Run, it kept 6%, 3% and 2% of entries: almost every
 * breakout has SOMETHING within a risk-unit, because a 2 ATR stop is wide
 * relative to the spacing of swing highs. Seventeen trades is not a measurement,
 * so those rows said nothing about the idea either way.
 *
 * The fix is to choose thresholds from the QUANTILES OF THE PREDICTOR, which is
 * how ADX's conventional 20/25/30 were arrived at in the first place -- they are
 * roughly the 30th/50th/65th percentiles of ADX on a liquid instrument. This
 * reads the distribution of each quantity at the bars where the rule actually
 * fires and prints its quartiles.
 *
 * THIS IS NOT A SWEEP, AND THE DISTINCTION IS THE WHOLE POINT. Nothing here
 * looks at what a trade RETURNED. It reads the predictor and nothing else, so
 * the thresholds it implies cannot have been chosen to make a result look good
 * -- they are chosen to make the gates comparably selective, which is what lets
 * "does this signal rank trades" be separated from "does taking fewer trades
 * help". Matched retention is the experimental control; the returns are still
 * measured afterwards, once, on the whole grid.
 *
 *     node tools/entry_filter_calibrate.mjs <cfg.json>
 */

import fs from 'node:fs';
import { runRule } from '../js/chart/rules.js';
import { donchianRule } from '../js/chart/donchian.js';
import { adxSeries, headroomR } from '../js/chart/entryfilter.js';

const cfg = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const bars = JSON.parse(fs.readFileSync(cfg.barsPath, 'utf8'));

const room = [];
const thrust = [];
const adxAt = [];
let adx = null;

/* Collected through the gate itself, so the sample is exactly the bars a gate
   would be asked about -- not "every bar", which would be a different and
   much more optimistic distribution. */
runRule(bars, donchianRule, {
  tf: cfg.tf,
  entryFilter: (ctx) => {
    room.push(headroomR(ctx, { tf: cfg.tf, cell: cfg.cell }));
    const a = ctx.series.atr ? ctx.series.atr[ctx.i] : NaN;
    const band = ctx.side > 0 ? ctx.series.hi : ctx.series.lo;
    if (a > 0 && band && Number.isFinite(band[ctx.i])) {
      thrust.push(((ctx.signalPrice - band[ctx.i]) * ctx.side) / a);
    }
    if (!adx) adx = adxSeries(bars, 14);
    if (Number.isFinite(adx[ctx.i])) adxAt.push(adx[ctx.i]);
    return true;                       // measuring, never gating
  },
});

const q = (arr, p) => {
  const s = arr.filter(Number.isFinite).slice().sort((x, y) => x - y);
  if (!s.length) return null;
  const pos = (s.length - 1) * p;
  const lo = Math.floor(pos), hi = Math.ceil(pos);
  return lo === hi ? s[lo] : s[lo] + (s[hi] - s[lo]) * (pos - lo);
};

const describe = (name, arr) => ({
  name,
  n: arr.length,
  infinite: arr.filter((x) => !Number.isFinite(x)).length,
  q10: q(arr, 0.10), q25: q(arr, 0.25), q50: q(arr, 0.50),
  q75: q(arr, 0.75), q90: q(arr, 0.90),
});

console.log(JSON.stringify({
  cell: cfg.cell,
  signals: room.length,
  /* The threshold that KEEPS p of the entries is the (1-p) quantile: a gate
     passes what is ABOVE it. */
  room: describe('room', room),
  thrust: describe('thrust', thrust),
  adx: describe('adx', adxAt),
}));
