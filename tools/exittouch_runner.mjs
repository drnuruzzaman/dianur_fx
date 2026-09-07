/* Driver for tools/exittouch_eval.py.

     node --max-old-space-size=6144 tools/exittouch_runner.mjs <cfg.json>

   cfg.json is {"barsPath": "...", "tf": "1h", "cell": "XAUUSD.a|1h"}.

   THE QUESTION. "As long as it hit the exit, why wait until the bar closes?"
   The stop already does not wait -- js/chart/rules.js checks it on the bar's
   range -- so what is on trial is only the CHANNEL exit and the trail, which
   fire on a close through the level and fill at the next open.

   WHAT CHANGES AND WHAT DOES NOT. `p.exitTouch` moves the same level from the
   close to the range: same number, same information, same trail, same
   re-entry. Nothing else differs, which is the only reason the difference can
   be attributed to the moment of acting.

   AND A CONTROL, because touching out is also LEAVING SOONER, and those are
   two claims. `randExit` closes at random at the rate the touch rule closed
   above and beyond the baseline, carrying the same trail. If touching cannot
   beat it, the LEVEL added nothing and the result was the shorter hold.

   NOT SEPARATED HERE: touching out also skips the next-open fill, since it
   fills at the level itself. So `touchExit` carries both halves of the wait --
   acting intrabar AND not giving away the gap to the next open -- and it is
   the more flattering of the two arrangements for the change under test. */

import fs from 'node:fs';
import { FLAT, runRule } from '../js/chart/rules.js';
import { donchianRule } from '../js/chart/donchian.js';
import { makeTrail } from '../js/chart/exittrail.js';

const cfg = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const bars = JSON.parse(fs.readFileSync(cfg.barsPath, 'utf8'));
const tf = cfg.tf;
const trail = () => makeTrail('structure', { tf, cell: cfg.cell });

function rng(seed) {
  let x = (seed >>> 0) || 1;
  return () => { x ^= x << 13; x >>>= 0; x ^= x >> 17; x ^= x << 5; x >>>= 0;
                 return x / 4294967296; };
}
function hashOf(s) {
  let h = 2166136261 >>> 0;
  for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619) >>> 0; }
  return h >>> 0;
}

/** The shipped rule, closing at random while in a trade at a given per-bar rate. */
function randExitRule(rate, seed) {
  const rand = rng(seed);
  return {
    ...donchianRule,
    decide(i, ctx) {
      const asked = donchianRule.decide(i, ctx);
      if (asked && asked.side === FLAT) return asked;
      if (ctx.pos && rand() < rate) return { side: FLAT, reason: 'rand_exit' };
      return asked;
    },
  };
}

/* Count bars held under the SHIPPED configuration, so the control's rate is a
   property of the baseline rather than of the variant it is standing in for. */
let barsInTrade = 0;
const counter = {
  ...donchianRule,
  decide(i, ctx) { if (ctx.pos) barsInTrade++; return donchianRule.decide(i, ctx); },
};
const base = runRule(bars, counter, { tf, exitTrail: trail() });
const touch = runRule(bars, donchianRule, { tf, exitTrail: trail(), exitTouch: true });

/* The touch rule closes MORE OFTEN; the control has to close that many extra
   times for the comparison to be about the level rather than the count. */
const extra = Math.max(0, touch.trades.length - base.trades.length);
const rate = barsInTrade > 0 ? extra / barsInTrade : 0;

const runs = {
  closeExit: base,
  touchExit: touch,
  randExit: runRule(bars, randExitRule(rate, hashOf(cfg.cell)),
                    { tf, exitTrail: trail() }),
};

const out = {
  bars: bars.length, tf, cell: cfg.cell,
  touchRate: rate,
  order: ['closeExit', 'touchExit', 'randExit'],
  runs: {},
};
for (const [name, r] of Object.entries(runs)) {
  out.runs[name] = r.trades.map((t) => ({
    r: t.r, reason: t.reason, entryTime: t.entryTime, bars: t.exitI - t.entryI,
  }));
  const net = r.trades.reduce((s, t) => s + t.r, 0);
  process.stderr.write(`  ${cfg.cell} ${name}: ${r.trades.length} trades, `
                       + `net ${net.toFixed(1)} R\n`);
}
console.log(JSON.stringify(out));
