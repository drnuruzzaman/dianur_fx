/* Does the ZigZag significance pass see the future?

     node tools/swing_audit.mjs data/bars/XAUUSD.a/15m [samples]

   Same argument as tools/liquidity_audit.mjs: a leaked feature is leaked in
   both halves of a train/test split and the split reports a clean score. What
   catches it is rebuilding from a TRUNCATED PREFIX -- bars[0..i] and nothing
   else -- and demanding the identical output.

   A ZigZag is the easy place to get this wrong. The obvious implementation
   walks the whole series, finds each leg's extreme, and emits it at the bar it
   OCCURRED -- which means the mark appears before the retracement that made it
   significant. This one emits at the flip bar, so a prefix must produce a
   prefix.

   Checked at each sampled bar i:
     PREFIX     zigzag(bars[0..i]) must equal the full run's turns confirmed
                at or before i, field for field.
     NO FUTURE  no returned turn may carry confirmedI > i. */

import fs from 'node:fs';
import zlib from 'node:zlib';
import path from 'node:path';
import { zigzag, nestedSwings } from '../js/chart/structure.js';

const dir = process.argv[2];
const SAMPLES = Number(process.argv[3] || 300);

function loadBars(d) {
  const rows = [];
  for (const f of fs.readdirSync(d).sort()) {
    if (!f.endsWith('.csv.gz')) continue;
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
  }
  rows.sort((a, b) => a.t - b.t);
  return rows;
}

const bars = loadBars(dir);
console.log(`bars: ${bars.length}  from ${dir}`);
const full = zigzag(bars);
console.log(`turns: ${full.length}`);

const key = (s) => `${s.i}|${s.confirmedI}|${s.price.toFixed(6)}|${s.isHigh}`;
const lo = 300, hi = bars.length - 1;
let bad = 0, future = 0, checked = 0;
const examples = [];

for (let k = 0; k < SAMPLES; k++) {
  const i = lo + Math.floor((hi - lo) * ((k + 0.5) / SAMPLES));
  const prefix = zigzag(bars.slice(0, i + 1));
  const expect = full.filter((s) => s.confirmedI <= i);
  checked++;
  if (prefix.some((s) => s.confirmedI > i)) future++;
  const a = prefix.map(key).join(','), b = expect.map(key).join(',');
  if (a !== b) {
    bad++;
    if (examples.length < 3) {
      examples.push(`  i=${i}  prefix ${prefix.length} turns, expected ${expect.length}`);
    }
  }
}

/* The merge is audited too: a ZigZag that is causal can still be ruined by a
   caller that labels it against the whole series. */
let mergeBad = 0;
for (let k = 0; k < 40; k++) {
  const i = lo + Math.floor((hi - lo) * ((k + 0.5) / 40));
  const sw = nestedSwings(bars.slice(0, i + 1), { sens: 'normal' });
  if (sw.some((s) => s.confirmedI > i)) mergeBad++;
}

console.log(`prefix mismatches: ${bad}/${checked}`);
console.log(`turns confirmed in the future: ${future}/${checked}`);
console.log(`nestedSwings future marks: ${mergeBad}/40`);
for (const e of examples) console.log(e);
process.exit(bad || future || mergeBad ? 1 : 0);
