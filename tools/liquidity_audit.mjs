/* Does js/chart/liquidity.js see the future?

     node tools/liquidity_audit.mjs data/bars/XAUUSD.a/15m [samples]

   THE ONLY TEST THAT CATCHES THIS CLASS OF BUG. A train/test split does not:
   a leaked feature is leaked in both halves and the split reports a clean
   score. What catches it is rebuilding the feature from a TRUNCATED PREFIX --
   bars[0..i] and nothing else -- and demanding the identical value.

   Two things are checked at each sampled bar:

     LEVELS   the set of levels live at i, by (type, price, bornI), must be the
              same whether computed from the full series or from the prefix.
     FEATURES every field of featuresAt(i), including the sweep fields, must be
              byte-identical.

   A single mismatch is a leak, not a rounding artefact: both sides run the same
   arithmetic on the same bars, so the only way they differ is one of them
   reading a bar the other could not. */

import fs from 'node:fs';
import zlib from 'node:zlib';
import path from 'node:path';
import { compute, detect, levelsAt, featuresAt } from '../js/chart/liquidity.js';
import { atrSeries } from '../js/chart/tlengine.js';

const dir = process.argv[2];
const SAMPLES = Number(process.argv[3] || 400);
/* Optional higher-timeframe directories, so the MTF re-indexing gets audited
   too -- that mapping is the likeliest place for this module to leak. */
const HIGHER = process.argv.slice(4);

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

const higher = {};
for (const h of HIGHER) higher[path.basename(h)] = loadBars(h);
for (const [k, v] of Object.entries(higher)) console.log(`  higher ${k}: ${v.length} bars`);

const full = compute(bars, { higher });
const fullAtr = atrSeries(bars, 14);

/* Sample across the whole series rather than the tail: a leak that only bites
   near the start (warmup) or only in the middle would be missed by either. */
const lo = 300, hi = bars.length - 1;
const idx = [];
for (let k = 0; k < SAMPLES; k++) {
  idx.push(lo + Math.floor((hi - lo) * ((k + 0.5) / SAMPLES)));
}

const key = (l) => `${l.type}@${l.price.toFixed(6)}#${l.bornI}`;
let levelBad = 0, featBad = 0, sweeps = 0, checked = 0;
const examples = [];

for (const i of idx) {
  const prefix = bars.slice(0, i + 1);
  const pAtr = atrSeries(prefix, 14);
  /* The higher frames are truncated to the same WALL-CLOCK instant, not to the
     same bar count -- that is what a live reader has. */
  const cutoff = bars[i].t;
  const pHigher = {};
  for (const [k, v] of Object.entries(higher)) {
    pHigher[k] = v.filter((b) => b.t <= cutoff);
  }
  const pLevels = compute(prefix, { higher: pHigher }).levels;

  const a = levelsAt(full.levels, i).map(key).sort().join('|');
  const b = levelsAt(pLevels, i).map(key).sort().join('|');
  if (a !== b) {
    levelBad++;
    if (examples.length < 3) {
      const A = new Set(levelsAt(full.levels, i).map(key));
      const B = new Set(levelsAt(pLevels, i).map(key));
      examples.push({ i, onlyFull: [...A].filter((x) => !B.has(x)).slice(0, 3),
                      onlyPrefix: [...B].filter((x) => !A.has(x)).slice(0, 3) });
    }
  }

  const fa = featuresAt(bars, full.levels, fullAtr, i);
  const fb = featuresAt(prefix, pLevels, pAtr, i);
  if (JSON.stringify(fa) !== JSON.stringify(fb)) {
    featBad++;
    if (examples.length < 6) examples.push({ i, full: fa, prefix: fb });
  }
  if (fa.sweep) sweeps++;
  checked++;
}

console.log(`checked ${checked} bars`);
console.log(`  level-set mismatches   ${levelBad}`);
console.log(`  feature mismatches     ${featBad}`);
console.log(`  bars with a completed sweep  ${sweeps} (${(100 * sweeps / checked).toFixed(1)}%)`);
if (examples.length) {
  console.log('\nfirst mismatches:');
  console.log(JSON.stringify(examples, null, 1).slice(0, 2000));
}
console.log(levelBad + featBad === 0
  ? '\nPASS - no field differs between the full series and a truncated prefix.'
  : '\nFAIL - something here reads a bar it should not.');
process.exit(levelBad + featBad === 0 ? 0 : 1);
