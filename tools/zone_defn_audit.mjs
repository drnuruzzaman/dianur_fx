/* EXPERIMENT 1: does the PIVOT DEFINITION change whether an S/R zone holds?
 *
 *     node --max-old-space-size=6144 tools/zone_defn_audit.mjs [maxBars]
 *
 * WHY THIS IS THE ONE LEFT. `zones.js` clusters pivots from `findPivots` at a
 * flat strength 3 on every instrument and every timeframe. That detector is
 * scale-free: swept over 46 cells it returns 13.5 marks per 150-bar screen on
 * gold 1m and cable 1w alike, because "is this the highest of 7 bars" is a
 * question about shape and shape statistics do not vary with the market. So
 * every zone in this project is clustered from turns that were never measured
 * against price. The same swap was measured for BOS/CHoCH and did not earn its
 * change; zones have never been tested at all.
 *
 * IT RUNS THE REAL DETECTOR. `detect()` takes a `pivotsFn` seam that defaults
 * to its own call, so the arms differ in the pivots and in nothing else --
 * same clustering, same width cap, same scoring, same code.
 *
 * WHAT IS MEASURED: THE HOLD RATE ON APPROACH.
 *
 *   approach   price enters the band having been outside it
 *   hold       within HOLD_BARS price leaves the way it came in, and never
 *              CLOSES beyond the far side
 *   break      a close beyond the far side inside the window
 *
 * That is the question a level is drawn to answer, and it does not depend on
 * any trade rule, so it is not another way of asking whether the shipped
 * strategy works.
 *
 * TWO ERAS, and a matched control: RANDOM BANDS of the same width placed at
 * the same bars. A zone has to beat a stripe drawn at an arbitrary price,
 * because price bounces off arbitrary prices all the time.
 */

import fs from 'node:fs';
import zlib from 'node:zlib';
import path from 'node:path';
import { detect, DEFAULT_ZONE_PARAMS } from '../js/chart/zones.js';
import { findPivots } from '../js/chart/trendlines.js';
import { atrSeries } from '../js/chart/tlengine.js';
import { zigzag, SWING_TIERS } from '../js/chart/structure.js';
import { BASE_STRENGTH, volRegime, strengthFor } from '../js/chart/sensitivity.js';

const MAX = Number(process.argv[2] || 160000);
const ERA = Date.UTC(2021, 0, 1);
const STEP = 40;          // recompute zones every N bars
const HOLD_BARS = 20;     // window in which a level has to do something
const ROOT = path.join(path.dirname(new URL(import.meta.url).pathname.slice(1)), '..');
const CELLS = [['XAUUSD.a', '15m'], ['XAUUSD.a', '1h'], ['XAUUSD.a', '4h'],
               ['EURUSD.a', '1h'], ['EURUSD.a', '4h'], ['USDJPY.a', '1h']];

function loadBars(dir, max) {
  const rows = [];
  for (const f of fs.readdirSync(dir).filter((x) => x.endsWith('.csv.gz')).sort().reverse()) {
    const text = zlib.gunzipSync(fs.readFileSync(path.join(dir, f))).toString('utf8');
    const lines = text.split('\n');
    const head = lines[0].trim().split(',');
    const ix = Object.fromEntries(head.map((k, i) => [k, i]));
    for (let i = 1; i < lines.length; i++) {
      const c = lines[i].trim().split(',');
      if (c.length < 5) continue;
      rows.push({ t: Number(c[ix.ts]) * 1000, o: +c[ix.open], h: +c[ix.high],
                  l: +c[ix.low], c: +c[ix.close] });
    }
    if (rows.length >= max) break;
  }
  rows.sort((a, b) => a.t - b.t);
  return rows.length > max ? rows.slice(-max) : rows;
}

/* A ZigZag dressed as findPivots: same shape out, different question asked.
   Rank 1 and above -- turns that ended a leg, which is the closest thing the
   ZigZag has to "a level price turned at". */
function zigzagPivots(atrLen) {
  return (bars) => {
    const turns = zigzag(bars, { atrLen, atrMult: SWING_TIERS[1] });
    const highs = [], lows = [];
    for (const z of turns) {
      const rec = { i: z.i, t: bars[z.i].t, price: z.price };
      (z.isHigh ? highs : lows).push(rec);
    }
    return { highs, lows };
  };
}

/* The adaptive fractal: the window sensitivity.js already calibrates and that
   zones.js ignores. Per-bar strength is not expressible here, so the arm uses
   the timeframe's base -- the part that carries almost all the difference,
   since the regime bump only fires on a quarter of bars. */
function adaptivePivots(tf) {
  const s = BASE_STRENGTH[tf] === undefined ? 3 : BASE_STRENGTH[tf];
  return (bars) => findPivots(bars, s);
}

/** hold / break counts for a set of bands over the bars that follow. */
function score(bars, from, bands) {
  let holds = 0, breaks = 0;
  for (const b of bands) {
    const { low, high } = b;
    if (!(high > low)) continue;
    let inside = false, entryFromAbove = false;
    for (let i = from; i < Math.min(bars.length, from + STEP); i++) {
      const bar = bars[i];
      const wasInside = inside;
      inside = bar.l <= high && bar.h >= low;
      if (inside && !wasInside) {
        entryFromAbove = bar.o > high;
        // resolve this approach over the next HOLD_BARS
        let done = false;
        for (let j = i + 1; j < Math.min(bars.length, i + 1 + HOLD_BARS); j++) {
          const far = entryFromAbove ? low : high;
          const through = entryFromAbove ? bars[j].c < far : bars[j].c > far;
          if (through) { breaks++; done = true; break; }
          const backOut = entryFromAbove ? bars[j].c > high : bars[j].c < low;
          if (backOut) { holds++; done = true; break; }
        }
        if (!done) breaks += 0;      // unresolved: counted as neither
        i += HOLD_BARS;              // one approach per visit
        inside = false;
      }
    }
  }
  return { holds, breaks };
}

const pct = (h, b) => (h + b ? (100 * h / (h + b)) : NaN);

console.log(`hold rate on approach; window ${HOLD_BARS} bars; eras split 2021-01-01`);
console.log('');
console.log('cell            arm         era        zones  appr   hold%   random%');
console.log('-'.repeat(72));

for (const [sym, tf] of CELLS) {
  const dir = path.join(ROOT, 'data', 'bars', sym, tf);
  if (!fs.existsSync(dir)) continue;
  const bars = loadBars(dir, MAX);
  const atr = atrSeries(bars, 14);

  const arms = {
    fixed: undefined,                       // exactly what ships
    adaptive: adaptivePivots(tf),
    zigzag: zigzagPivots(14),
  };

  for (const [name, pivotsFn] of Object.entries(arms)) {
    const acc = { 0: { h: 0, b: 0, n: 0, z: 0 }, 1: { h: 0, b: 0, n: 0, z: 0 } };
    const rnd = { 0: { h: 0, b: 0 }, 1: { h: 0, b: 0 } };
    let seed = 12345;
    const rand = () => (seed = (seed * 1103515245 + 12345) & 0x7fffffff) / 0x7fffffff;

    for (let i = 600; i + STEP < bars.length; i += STEP) {
      const era = bars[i].t >= ERA ? 1 : 0;
      let zones = [];
      try {
        zones = detect(bars, i, tf, atr, pivotsFn ? { pivotsFn } : {});
      } catch { continue; }
      if (!zones.length) continue;
      acc[era].z += zones.length;
      const bands = zones.map((z) => ({ low: z.low, high: z.high }));
      const r = score(bars, i, bands);
      acc[era].h += r.holds; acc[era].b += r.breaks;

      /* MATCHED CONTROL: the same number of bands, the same widths, placed at
         random prices inside the recent range. Price turns at arbitrary levels
         often enough that a hold rate means nothing without this. */
      let lo = Infinity, hi = -Infinity;
      for (let k = Math.max(0, i - 200); k <= i; k++) {
        if (bars[k].l < lo) lo = bars[k].l;
        if (bars[k].h > hi) hi = bars[k].h;
      }
      const fake = bands.map((b) => {
        const w = b.high - b.low;
        const mid = lo + rand() * (hi - lo);
        return { low: mid - w / 2, high: mid + w / 2 };
      });
      const rr = score(bars, i, fake);
      rnd[era].h += rr.holds; rnd[era].b += rr.breaks;
    }

    for (const era of [0, 1]) {
      const a = acc[era], rd = rnd[era];
      if (a.h + a.b < 30) continue;
      console.log(`${(sym.replace('.a', '') + ' ' + tf).padEnd(15)}${name.padEnd(11)}`
        + `${(era ? 'post-21' : 'pre-21').padEnd(9)}${String(a.z).padStart(6)}`
        + `${String(a.h + a.b).padStart(6)}  ${pct(a.h, a.b).toFixed(1).padStart(6)}`
        + `  ${pct(rd.h, rd.b).toFixed(1).padStart(7)}`);
    }
  }
  console.log('');
}
console.log('hold% must beat random% in BOTH eras for the definition to matter.');
