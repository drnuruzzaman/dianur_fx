/* Before/after for the swing significance change, every instrument and frame.

     node tools/swing_sweep.mjs [maxBars]

   THE COLUMN THAT MATTERS IS `before rings/150b`. If a detector is measuring
   price, its rate has to differ between instruments and frames -- gold on 5m
   and cable on 1w are not turning at the same cadence. The strength-6 pass
   reports the same ~13 per screen everywhere, which is the signature of a
   test that only ever looked at SHAPE. `after` is the ZigZag: a leg must
   travel SIGNIFICANT_ATR before it can end.

   `same-kind` counts marks that repeat high-after-high or low-after-low. Any
   number above zero is a sequence that cannot be read as structure. */

import fs from 'node:fs';
import zlib from 'node:zlib';
import path from 'node:path';
import { swingPoints, nestedSwings } from '../js/chart/structure.js';

const MAX = Number(process.argv[2] || 400000);
const ROOT = path.join(path.dirname(new URL(import.meta.url).pathname.slice(1)), '..');
const BARS = path.join(ROOT, 'data', 'bars');
const ORDER = ['1m', '5m', '15m', '30m', '1h', '4h', '1d', '1w'];

function loadBars(d, max) {
  const rows = [];
  const files = fs.readdirSync(d).filter((f) => f.endsWith('.csv.gz')).sort();
  /* Newest files first when the series is long: the tail is the part any chart
     actually shows, and 2M bars of 1m data is memory for no extra evidence. */
  for (const f of files.slice().reverse()) {
    const text = zlib.gunzipSync(fs.readFileSync(path.join(d, f))).toString('utf8');
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

const med = (a) => (a.length ? a.slice().sort((x, y) => x - y)[a.length >> 1] : NaN);
const runs = (a) => {
  let n = 0;
  for (let i = 1; i < a.length; i++) if (a[i].isHigh === a[i - 1].isHigh) n++;
  return n;
};

console.log('                        rings per 150 bars     same-kind runs    median lag');
console.log('symbol    tf     bars   before    after       before    after   before after');
let worstRuns = 0, rows = 0;
for (const sym of fs.readdirSync(BARS).sort()) {
  const tfs = fs.readdirSync(path.join(BARS, sym))
    .sort((a, b) => ORDER.indexOf(a) - ORDER.indexOf(b));
  for (const tf of tfs) {
    const bars = loadBars(path.join(BARS, sym, tf), MAX);
    if (bars.length < 200) { console.log(`${sym.padEnd(10)}${tf.padEnd(6)} too short`); continue; }
    const base = swingPoints(bars, { strength: 3 });
    const maj6 = new Set(swingPoints(bars, { strength: 6 }).map((x) => x.i));
    const before = base.filter((s) => maj6.has(s.i));
    const after = nestedSwings(bars, { sens: 'normal' }).filter((s) => s.major);
    const rb = runs(before), ra = runs(after);
    worstRuns = Math.max(worstRuns, ra);
    rows++;
    console.log(
      sym.padEnd(10) + tf.padEnd(5) + String(bars.length).padStart(8) +
      (before.length / bars.length * 150).toFixed(1).padStart(8) +
      (after.length / bars.length * 150).toFixed(1).padStart(9) +
      String(rb).padStart(13) + String(ra).padStart(9) +
      (med(before.map((s) => s.confirmedI - s.i)) + 'b').padStart(9) +
      (med(after.map((s) => s.confirmedI - s.i)) + 'b').padStart(6));
  }
}
console.log(`\n${rows} cells; worst same-kind run count after: ${worstRuns}`);
process.exit(worstRuns ? 1 : 0);
