/* EXPERIMENT 2: are S/R zones and liquidity levels the same thing twice?
 *
 *     node --max-old-space-size=6144 tools/zone_liquidity_overlap.mjs [maxBars]
 *
 * TWO DETECTORS, ONE QUESTION. `zones.js` clusters repeated pivots into a band
 * -- "price has turned here before". `liquidity.js` marks previous-day and
 * previous-session extremes, swing points and equal highs/lows -- "resting
 * orders are here". Both are drawn, and the same comparison was run for zones
 * against supply/demand (low overlap, which is why both survive) but never for
 * zones against liquidity. If the overlap is high the chart is saying one
 * thing twice in two vocabularies.
 *
 * THE CONTROL IS THE WHOLE POINT. Zones sit near recent extremes and so do
 * liquidity levels, so SOME overlap is guaranteed by both being drawn on the
 * same price range. The control shuffles each liquidity level to a random
 * price inside the same recent range, keeping the count and the zone bands
 * fixed. Overlap above that is agreement; overlap at it is arithmetic.
 */

import fs from 'node:fs';
import zlib from 'node:zlib';
import path from 'node:path';
import { detect as detectZones } from '../js/chart/zones.js';
import { compute as computeLiq, levelsAt } from '../js/chart/liquidity.js';
import { atrSeries } from '../js/chart/tlengine.js';

const MAX = Number(process.argv[2] || 60000);
const STEP = 60;
const ROOT = path.join(path.dirname(new URL(import.meta.url).pathname.slice(1)), '..');
const CELLS = [['XAUUSD.a', '15m'], ['XAUUSD.a', '1h'], ['XAUUSD.a', '4h'],
               ['EURUSD.a', '1h'], ['USDJPY.a', '1h']];

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

console.log(`liquidity levels landing inside an S/R band, against a matched random control`);
console.log('');
console.log('cell            samples  zones  liq lvls   inside   real%   random%   lift');
console.log('-'.repeat(78));

for (const [sym, tf] of CELLS) {
  const dir = path.join(ROOT, 'data', 'bars', sym, tf);
  if (!fs.existsSync(dir)) continue;
  const bars = loadBars(dir, MAX);
  const atr = atrSeries(bars, 14);
  /* compute() returns { levels, atr, featuresAt } -- the array is `.levels`,
     and passing the wrapper to levelsAt() silently yields nothing. */
  let liq;
  try { liq = computeLiq(bars, {}).levels; }
  catch (e) { console.log(sym, tf, 'liquidity failed:', e.message); continue; }

  let seed = 987654321;
  const rand = () => (seed = (seed * 1103515245 + 12345) & 0x7fffffff) / 0x7fffffff;

  let samples = 0, nz = 0, nl = 0, inside = 0, fakeInside = 0;
  for (let i = 800; i < bars.length; i += STEP) {
    let zones = [];
    try { zones = detectZones(bars, i, tf, atr, {}); } catch { continue; }
    if (!zones.length) continue;
    const levels = levelsAt(liq, i) || [];
    if (!levels.length) continue;
    samples++; nz += zones.length; nl += levels.length;

    let lo = Infinity, hi = -Infinity;
    for (let k = Math.max(0, i - 200); k <= i; k++) {
      if (bars[k].l < lo) lo = bars[k].l;
      if (bars[k].h > hi) hi = bars[k].h;
    }
    for (const lv of levels) {
      const p = lv.price;
      if (zones.some((z) => p >= z.low && p <= z.high)) inside++;
      const fake = lo + rand() * (hi - lo);
      if (zones.some((z) => fake >= z.low && fake <= z.high)) fakeInside++;
    }
  }
  if (!nl) { console.log(`${sym} ${tf}: no samples`); continue; }
  const real = 100 * inside / nl, fake = 100 * fakeInside / nl;
  console.log(`${(sym.replace('.a', '') + ' ' + tf).padEnd(15)}${String(samples).padStart(7)}`
    + `${String(nz).padStart(7)}${String(nl).padStart(10)}${String(inside).padStart(9)}`
    + `${real.toFixed(1).padStart(8)}${fake.toFixed(1).padStart(10)}`
    + `${(real - fake >= 0 ? '  +' : '  ') + (real - fake).toFixed(1)}pp`);
}
console.log('');
console.log('A lift near zero means the two detectors agree no more than chance --');
console.log('they are answering different questions and both earn their place. A');
console.log('large lift means the chart is drawing one level under two names.');
