/* elliott_report.mjs — run the Elliott fit across cells and persist the verdict.
 *
 * elliott_fit.mjs answers one cell and prints JSON. This runs the matrix and
 * writes runs/elliott_calibration.csv, because a result nobody can find later
 * gets re-litigated later. The trendline programme was only closed for good
 * once runs/struct/break_intrabar_check.csv existed on disk; this is the same
 * thing for the wave counter.
 *
 * The number that decides it is SKILL, not accuracy. Accuracy flatters any
 * classifier that learns to always say the commonest class -- and with three
 * unbalanced outlooks it will. Skill is Brier against climatology: 0 means no
 * better than quoting the base rate, and NEGATIVE means the forecast is worse
 * than saying nothing. A scorer can be accurate and have negative skill, which
 * is precisely the failure mode a chart annotation invites, because it looks
 * confident while being wrong in a biased direction.
 *
 * Cells are run per instrument and per timeframe, never pooled -- pooling
 * instruments manufactured a +0.784 correlation out of nothing earlier in this
 * project.
 *
 *   node tools/elliott_report.mjs
 */

import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';

const ROOT = path.resolve(
  path.dirname(new URL(import.meta.url).pathname).replace(/^\/([A-Za-z]:)/, '$1'), '..');

/* from-year is per instrument: gold has no real intraday history before 2016,
   and feeding the counter daily bars mislabelled as 1h would score a different
   detector than the one under test. */
const CELLS = [
  ['XAUUSD.a', '1h', 2016], ['XAUUSD.a', '4h', 2016],
  ['EURUSD.a', '1h', 1999], ['EURUSD.a', '4h', 1999],
  ['USDJPY.a', '1h', 1999], ['USDJPY.a', '4h', 1999],
];
const HORIZON = 24;

const rows = [];
for (const [symbol, tf, from] of CELLS) {
  process.stderr.write(`  ${symbol} ${tf} from ${from} ... `);
  let doc;
  try {
    const out = execFileSync(process.execPath,
      [path.join(ROOT, 'tools', 'elliott_fit.mjs'), symbol, tf, String(HORIZON), String(from)],
      { cwd: ROOT, encoding: 'utf8', maxBuffer: 64 * 1024 * 1024 });
    doc = JSON.parse(out);
  } catch (err) {
    process.stderr.write(`FAILED (${String(err.message).slice(0, 80)})\n`);
    continue;
  }
  for (const key of ['flat', 'fitted', 'fittedClass']) {
    const r = doc[key];
    if (!r) continue;
    rows.push({
      symbol, tf, from, bars: doc.bars, scored: doc.scored,
      scorer: r.name, n: r.n,
      accuracy: r.accuracy, majority_baseline: r.baseline,
      brier: r.brier, climatology: r.climatology, skill: r.skill,
      beats_climatology: r.skill > 0 ? 'yes' : 'NO',
    });
  }
  const f = doc.fitted || {};
  process.stderr.write(`skill flat ${doc.flat.skill} / fitted ${f.skill}\n`);
}

if (!rows.length) {
  console.error('no cells completed');
  process.exit(1);
}

const cols = Object.keys(rows[0]);
const csv = [cols.join(','), ...rows.map((r) => cols.map((c) => r[c]).join(','))].join('\n');
const out = path.join(ROOT, 'runs', 'elliott_calibration.csv');
fs.writeFileSync(out, csv + '\n');

console.log('\n=== ELLIOTT COUNTER: does it beat quoting the base rate? ===');
console.log('skill = 1 - brier/climatology.  0 = no better than the base rate.');
console.log('                                NEGATIVE = worse than saying nothing.\n');
const w = (s, n) => String(s).padEnd(n);
console.log(w('cell', 13) + w('scorer', 33) + w('n', 7) + w('acc', 8)
            + w('base', 8) + w('brier', 8) + w('clim', 8) + w('skill', 9) + 'beats?');
for (const r of rows) {
  console.log(w(`${r.symbol.replace('.a', '')} ${r.tf}`, 13) + w(r.scorer, 33)
    + w(r.n, 7) + w(r.accuracy, 8) + w(r.majority_baseline, 8)
    + w(r.brier, 8) + w(r.climatology, 8) + w(r.skill, 9) + r.beats_climatology);
}
const beat = rows.filter((r) => r.skill > 0);
console.log(`\ncells x scorers with positive skill: ${beat.length} of ${rows.length}`);
if (beat.length) for (const r of beat) console.log(`  ${r.symbol} ${r.tf} ${r.scorer}: skill ${r.skill}`);
console.log(`\nwrote ${out}`);
