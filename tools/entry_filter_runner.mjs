/* Driver for tools/entry_filter_eval.py — see that file for what is compared
   and how the comparison is kept honest. Run by hand as:

     node tools/entry_filter_runner.mjs <cfg.json>

   where cfg.json is {"barsPath": "...", "tf": "4h", "cell": "XAUUSD.a|4h"}. */

import fs from 'node:fs';
import { runRule } from '../js/chart/rules.js';
import { donchianRule } from '../js/chart/donchian.js';
import { gridConfigs, makeFilter } from '../js/chart/entryfilter.js';

const cfg = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const bars = JSON.parse(fs.readFileSync(cfg.barsPath, 'utf8'));

const out = { bars: bars.length, runs: {}, rejected: {} };

for (const c of gridConfigs()) {
  const filter = makeFilter(c.kind, c.threshold, { tf: cfg.tf, cell: cfg.cell });
  /* COUNTED, NOT INFERRED. "kept 62%" derived from two trade counts is wrong in
     a way that is invisible: skipping an entry frees the rule to take a later
     one it was in a position for, so the filtered run can hold trades the
     baseline never had. The only honest count of what the gate did is the gate
     counting itself. */
  let seen = 0;
  let passed = 0;
  const counted = filter && ((ctx) => {
    seen += 1;
    const ok = filter(ctx);
    if (ok) passed += 1;
    return ok;
  });

  const r = runRule(bars, donchianRule, {
    tf: cfg.tf, ...(counted ? { entryFilter: counted } : {}),
  });
  out.runs[c.name] = r.trades.map((t) => ({
    side: t.side, r: t.r, reason: t.reason, entryI: t.entryI,
    entryTime: t.entryTime, bars: t.exitI - t.entryI,
  }));
  out.rejected[c.name] = { seen, passed,
                           keptPct: seen ? (100 * passed) / seen : null };
}

console.log(JSON.stringify(out));
