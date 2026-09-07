/* Driver for tools/pullback_eval.py.

     node --max-old-space-size=6144 tools/pullback_runner.mjs <cfg.json>

   cfg.json is {"barsPath": "...", "tf": "1h", "cell": "XAUUSD.a|1h"}.

   THE IDEA UNDER TEST: when price pulls back, close the trade and let the rule
   look for a new signal. The walker already re-enters on the next channel break,
   so an exit rule IS the whole proposal -- nothing extra is needed to "create a
   new signal", which is worth stating because it makes the change smaller and
   the comparison cleaner than it first sounds.

   THE PULLBACK IS THE PANEL'S OWN DEFINITION: the last three closes moving
   against the trade by more than `k` ATR. That threshold was chosen to make a
   sentence readable, not to trigger anything, so two values are run -- 0.3 as
   written and 0.6 for something less twitchy.

   AND A CONTROL, because "exit more often" is not the same claim as "exit when
   price pulls back". `randExit` closes at random, at the rate the pullback rule
   fired, with the same trail and the same re-entry. If the pullback rule cannot
   beat it, the detection added nothing and the result was the extra churn. */

import fs from 'node:fs';
import { FLAT, LONG, runRule } from '../js/chart/rules.js';
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

/** The shipped rule, plus an exit when price gives back `k` ATR over 3 bars. */
function pullbackRule(k, onFire) {
  return {
    ...donchianRule,
    decide(i, ctx) {
      const asked = donchianRule.decide(i, ctx);
      if (asked && asked.side === FLAT) return asked;      // its own exit wins
      if (ctx.pos && i >= 3) {
        const side = ctx.pos.side === LONG ? 1 : -1;
        const a = ctx.series.atr[i];
        const move = (ctx.close[i] - ctx.close[i - 3]) * side;
        if (a > 0 && move < -k * a) {
          if (onFire) onFire(i);
          return { side: FLAT, reason: 'pullback' };
        }
      }
      return asked;
    },
  };
}

/** The same shape, firing at random at a given per-bar rate while in a trade. */
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

/* Count the pullback exits so the control can be matched to them, before any
   result is looked at -- the same order every study here matches a control in. */
let fired = 0;
let barsInTrade = 0;
const counter = {
  ...donchianRule,
  decide(i, ctx) {
    if (ctx.pos) barsInTrade++;
    return pullbackRule(0.3).decide(i, ctx);
  },
};
const probe = runRule(bars, counter, { tf, exitTrail: trail() });
fired = probe.trades.filter((t) => t.reason === 'pullback').length;
const rate = barsInTrade > 0 ? fired / barsInTrade : 0;

const runs = {
  trailOnly: runRule(bars, donchianRule, { tf, exitTrail: trail() }),
  pullback03: probe,
  pullback06: runRule(bars, pullbackRule(0.6), { tf, exitTrail: trail() }),
  randExit: runRule(bars, randExitRule(rate, hashOf(cfg.cell)), { tf, exitTrail: trail() }),
};

const out = { bars: bars.length, tf, cell: cfg.cell, pullbackRate: rate,
              order: ['trailOnly', 'pullback03', 'pullback06', 'randExit'],
              runs: {} };
for (const [name, r] of Object.entries(runs)) {
  out.runs[name] = r.trades.map((t) => ({
    r: t.r, reason: t.reason, entryTime: t.entryTime, bars: t.exitI - t.entryI,
  }));
  const net = r.trades.reduce((s, t) => s + t.r, 0);
  process.stderr.write(`  ${cfg.cell} ${name}: ${r.trades.length} trades, `
                       + `net ${net.toFixed(1)} R\n`);
}
console.log(JSON.stringify(out));
