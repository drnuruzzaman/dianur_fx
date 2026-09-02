/* Driver for tools/exit_trail_eval.py — see that file for what is compared and
   how the comparison is kept honest. Run by hand as:

     node tools/exit_trail_runner.mjs <cfg.json>

   where cfg.json is {"barsPath": "...", "tf": "4h", "cell": "XAUUSD.a|4h"}. */

import fs from 'node:fs';
import { runRule } from '../js/chart/rules.js';
import { donchianRule } from '../js/chart/donchian.js';
import { makeTrail, trailDistance } from '../js/chart/exittrail.js';

const cfg = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const bars = JSON.parse(fs.readFileSync(cfg.barsPath, 'utf8'));
const base = { tf: cfg.tf };
const opt = { tf: cfg.tf, cell: cfg.cell };

/* THE CONTROL IS MATCHED BEFORE ANYTHING IS SCORED.
 *
 * The structural trail is first run WITHOUT being allowed to act, purely to
 * record how far from price it sits. The ATR control is then given the `k` that
 * reproduces that average distance. Only then is either allowed to close a
 * trade.
 *
 * Doing it in this order is the whole design: matched on the predictor's own
 * geometry, never on what either configuration returned. Choosing `k` after
 * seeing the results would make the control an accomplice. */
const structFn = makeTrail('structure', opt);
const dist = trailDistance(bars, donchianRule, runRule, base, structFn);
const k = Number.isFinite(dist.meanAtr) ? Math.round(dist.meanAtr * 100) / 100 : 2.0;

const runs = {
  /* the rule exactly as validated -- channel exit, no trail */
  baseline: {},
  structure: { exitTrail: structFn },
  /* the null hypothesis: equally close, knows nothing */
  atrMatched: { exitTrail: makeTrail('atr', { k }) },
  /* two conventional widths for shape, so `atrMatched` is not the only ATR
     point and a reader can see which way distance moves the result */
  atr2: { exitTrail: makeTrail('atr', { k: 2 }) },
  atr4: { exitTrail: makeTrail('atr', { k: 4 }) },
};

const out = { bars: bars.length, matchedK: k,
              structDistAtr: dist.meanAtr, structSamples: dist.n, runs: {} };

for (const [name, extra] of Object.entries(runs)) {
  const r = runRule(bars, donchianRule, { ...base, ...extra });
  out.runs[name] = r.trades.map((t) => ({
    side: t.side, r: t.r, reason: t.reason,
    entryI: t.entryI, entryTime: t.entryTime, bars: t.exitI - t.entryI,
  }));
}

console.log(JSON.stringify(out));
