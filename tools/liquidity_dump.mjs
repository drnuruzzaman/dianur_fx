/* Per-bar nearest live liquidity level, dumped for the Python exit variants.
 *
 *     node --max-old-space-size=8192 tools/liquidity_dump.mjs XAUUSD.a 4h out.csv
 *
 * WHY A DUMP RATHER THAN A PORT. `js/chart/liquidity.js` is the audited
 * detector -- tools/liquidity_audit.mjs rebuilds every field from a truncated
 * prefix with 0 mismatches, which is the only reason anything here can be
 * trusted not to leak. Re-implementing it in Python to feed sim/strategies
 * would create a second copy to keep honest, and this project has already
 * watched a duplicated ATR diverge by 0.86 price units. One detector, one dump.
 *
 * CAUSAL BY CONSTRUCTION, and the dump does not weaken it. `levelsAt(levels, i)`
 * returns only levels with bornI <= i that have not died by i, so the row
 * written for bar i contains only what a reader had at bar i. Nothing is
 * forward-filled and no level is written before it was born.
 *
 * NEAREST EACH SIDE ONLY. The same narrowing sweep_break_eval needed: there are
 * ~65 levels alive at once and the two price is actually interacting with are
 * the nearest above and the nearest below. A target chosen from all 65 is a
 * target chosen from noise.
 */

import fs from 'node:fs';
import zlib from 'node:zlib';
import path from 'node:path';
import { detect as detectLiq, levelsAt } from '../js/chart/liquidity.js';

const SYM = process.argv[2] || 'XAUUSD.a';
const TF = process.argv[3] || '4h';
const OUT = process.argv[4] || 'liq_' + SYM.replace('.', '') + '_' + TF + '.csv';
const ROOT = path.join(path.dirname(new URL(import.meta.url).pathname.slice(1)), '..');

function loadBars(dir) {
  const rows = [];
  for (const f of fs.readdirSync(dir).filter((x) => x.endsWith('.csv.gz')).sort()) {
    const text = zlib.gunzipSync(fs.readFileSync(path.join(dir, f))).toString('utf8');
    const lines = text.split('\n');
    const ix = Object.fromEntries(lines[0].trim().split(',').map((k, i) => [k, i]));
    for (let i = 1; i < lines.length; i++) {
      const c = lines[i].trim().split(',');
      if (c.length < 5) continue;
      rows.push({ t: Number(c[ix.ts]), o: +c[ix.open], h: +c[ix.high],
                  l: +c[ix.low], c: +c[ix.close] });
    }
  }
  rows.sort((a, b) => a.t - b.t);
  return rows;
}

const dir = path.join(ROOT, 'data', 'bars', SYM, TF);
const bars = loadBars(dir);
const forDetect = bars.map((b) => ({ ...b, t: b.t * 1000 }));
const levels = detectLiq(forDetect, {});
process.stderr.write(`${SYM} ${TF}: ${bars.length} bars, ${levels.length} levels\n`);

const out = ['ts,above_price,above_type,below_price,below_type,live'];
for (let i = 0; i < bars.length; i++) {
  const live = levelsAt(levels, i);
  const price = bars[i].c;
  let above = null, below = null;
  for (const l of live) {
    if (l.price >= price) { if (!above || l.price < above.price) above = l; }
    else if (!below || l.price > below.price) below = l;
  }
  out.push([bars[i].t,
            above ? above.price.toFixed(5) : '', above ? above.type : '',
            below ? below.price.toFixed(5) : '', below ? below.type : '',
            live.length].join(','));
}
fs.writeFileSync(path.join(ROOT, OUT), out.join('\n') + '\n');
process.stderr.write(`wrote ${OUT}\n`);
