/* EXPERIMENT 3: does a zone's `strength` predict whether it holds?
 *
 *     node --max-old-space-size=6144 tools/zone_strength_eval.mjs [maxBars]
 *
 * The score is 100 points across touches, tightness, span, proximity and
 * reaction, and it is printed on the chart as "strength 74". The README already
 * records that a high score holds no more often than a low one over ~21,800
 * approaches; this re-runs that on the current scorer -- `reaction` was added
 * afterwards and was meant to be the term that made the number mean something.
 *
 * BUCKETS, NOT A CORRELATION. A correlation over a bounded score with a
 * lumpy distribution hides where the signal is; if only the very top of the
 * range works, that is worth knowing and a single r would bury it.
 *
 * The random control is the same one experiment 1 used: a band of identical
 * width at an arbitrary price in the same range. It is the floor any bucket
 * has to clear before "strength" means anything at all.
 */

import fs from 'node:fs';
import zlib from 'node:zlib';
import path from 'node:path';
import { detect as detectZones } from '../js/chart/zones.js';
import { atrSeries } from '../js/chart/tlengine.js';

const MAX = Number(process.argv[2] || 80000);
const STEP = 40;
const HOLD_BARS = 20;
const ROOT = path.join(path.dirname(new URL(import.meta.url).pathname.slice(1)), '..');
const CELLS = [['XAUUSD.a', '15m'], ['XAUUSD.a', '1h'], ['XAUUSD.a', '4h'],
               ['EURUSD.a', '1h'], ['USDJPY.a', '1h']];
const BUCKETS = [[0, 40], [40, 55], [55, 70], [70, 85], [85, 101]];

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

/** Resolve the FIRST approach to one band after `from`: 1 hold, 0 break, null. */
function firstApproach(bars, from, low, high) {
  for (let i = from; i < Math.min(bars.length, from + STEP); i++) {
    if (!(bars[i].l <= high && bars[i].h >= low)) continue;
    const fromAbove = bars[i].o > high;
    for (let j = i + 1; j < Math.min(bars.length, i + 1 + HOLD_BARS); j++) {
      const far = fromAbove ? low : high;
      if (fromAbove ? bars[j].c < far : bars[j].c > far) return 0;
      if (fromAbove ? bars[j].c > high : bars[j].c < low) return 1;
    }
    return null;                       // unresolved inside the window
  }
  return null;
}

const tally = BUCKETS.map(() => ({ h: 0, n: 0 }));
let rndH = 0, rndN = 0;

for (const [sym, tf] of CELLS) {
  const dir = path.join(ROOT, 'data', 'bars', sym, tf);
  if (!fs.existsSync(dir)) continue;
  const bars = loadBars(dir, MAX);
  const atr = atrSeries(bars, 14);
  let seed = 24681357;
  const rand = () => (seed = (seed * 1103515245 + 12345) & 0x7fffffff) / 0x7fffffff;

  for (let i = 600; i + STEP < bars.length; i += STEP) {
    let zones = [];
    try { zones = detectZones(bars, i, tf, atr, {}); } catch { continue; }
    if (!zones.length) continue;
    let lo = Infinity, hi = -Infinity;
    for (let k = Math.max(0, i - 200); k <= i; k++) {
      if (bars[k].l < lo) lo = bars[k].l;
      if (bars[k].h > hi) hi = bars[k].h;
    }
    for (const z of zones) {
      const r = firstApproach(bars, i, z.low, z.high);
      if (r !== null) {
        const b = BUCKETS.findIndex(([a, c]) => z.strength >= a && z.strength < c);
        if (b >= 0) { tally[b].n++; tally[b].h += r; }
      }
      const w = z.high - z.low, mid = lo + rand() * (hi - lo);
      const rr = firstApproach(bars, i, mid - w / 2, mid + w / 2);
      if (rr !== null) { rndN++; rndH += rr; }
    }
  }
}

console.log('hold rate by zone strength, pooled over 5 cells');
console.log('');
console.log('strength      n     hold%');
console.log('-'.repeat(30));
for (let i = 0; i < BUCKETS.length; i++) {
  const [a, b] = BUCKETS[i], t = tally[i];
  if (!t.n) continue;
  console.log(`${(a + '-' + (b - 1)).padEnd(10)}${String(t.n).padStart(7)}`
    + `${(100 * t.h / t.n).toFixed(1).padStart(9)}`);
}
console.log('-'.repeat(30));
console.log(`${'random'.padEnd(10)}${String(rndN).padStart(7)}${(100 * rndH / rndN).toFixed(1).padStart(9)}`);
console.log('');
console.log('If the buckets do not rise with strength, the number is a description of');
console.log('how the zone is DRAWN, not a forecast -- and printing it as "strength"');
console.log('invites the reading the tooltip then has to spend a sentence undoing.');
