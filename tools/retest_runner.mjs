/* Driver for tools/retest_eval.py. Run by hand as:

     node --max-old-space-size=6144 tools/retest_runner.mjs <cfg.json>

   cfg.json is {"barsPath": "...", "tf": "5m", "cell": "XAUUSD.a|5m",
                "rule": "tl" | "sr" | "smc"}.

   ONE RUNNER FOR EVERY CANDIDATE, so the control cannot drift between them: the
   matched coin flip is built here, once, from whatever rule it is handed. */

import fs from 'node:fs';
import { FLAT, LONG, SHORT, runRule } from '../js/chart/rules.js';
import { donchianRule } from '../js/chart/donchian.js';
import { smcRetestRule } from '../js/chart/smcretest.js';
import { srRetestRule, tlRetestRule } from '../js/chart/retests.js';
import { makeTrail } from '../js/chart/exittrail.js';
import { BULL, BEAR } from '../js/chart/marketstructure.js';

const cfg = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const bars = JSON.parse(fs.readFileSync(cfg.barsPath, 'utf8'));
const tf = cfg.tf;

const RULES = { smc: smcRetestRule, tl: tlRetestRule, sr: srRetestRule };
const rule = RULES[cfg.rule];
if (!rule) throw new Error(`unknown rule: ${cfg.rule}`);
const P = { ...rule.defaults };

/* The `>>> 0` after every xor is not decoration: `^` returns a SIGNED int32 in
   JavaScript, and a control that forgot it kept 75% of entries while reporting
   50% in tools/entry_filter_eval.py. Retention is printed for the same reason. */
function rng(seed) {
  let x = (seed >>> 0) || 1;
  return () => {
    x ^= x << 13; x >>>= 0;
    x ^= x >> 17;
    x ^= x << 5; x >>>= 0;
    return x / 4294967296;
  };
}
function hashOf(s) {
  let h = 2166136261 >>> 0;
  for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619) >>> 0; }
  return h >>> 0;
}

/* PREPARED ONCE. Every run below walks the same bars with the same parameters,
   so re-deriving pivots, structure and zones per run is the difference between
   a study that finishes and one that does not -- and the calibration loop below
   needs several runs. */
const series = rule.prepare(bars, P);
const cached = { ...rule, prepare: () => series };
const real = runRule(bars, cached, { tf });

/* WHERE THE CONTROL IS ALLOWED TO FIRE. Every rule declares its own opportunity
   set -- the bars where it COULD have traded and on which side. Letting the
   control fire anywhere would answer "does trading less help" instead of "does
   this trigger pick better than chance". */
function opportunityAt(i) {
  if (typeof rule.opportunity === 'function') return rule.opportunity(i, series);
  const b = series.msBias[i];
  if (b !== BULL && b !== BEAR) return FLAT;
  const z = b === BULL ? series.demand[i] : series.supply[i];
  return (z && z.confirmedI < i) ? (b === BULL ? LONG : SHORT) : FLAT;
}

let usable = 0;
for (let i = 0; i < bars.length; i++) if (opportunityAt(i) !== FLAT) usable++;
const rate = usable > 0 ? real.trades.length / usable : 0;

/* And how wide the stop is, in ATR: the MEDIAN of the rule's own trades, so one
   enormous level cannot set the control's stop for every entry. Read off the
   rule's geometry before anything is scored -- never off its returns. */
const riskAtr = (() => {
  const v = real.trades.map((t) => t.risk / series.atr[t.entryI])
    .filter(Number.isFinite).sort((a, b) => a - b);
  return v.length ? v[Math.floor(v.length / 2)] : 1.0;
})();

function controlRule(seed, randomSide, rate) {
  const rand = rng(seed);
  return {
    key: 'control',
    defaults: P,
    summary: 'matched random entries',
    warmup: rule.warmup,
    prepare: () => series,
    /* The control needs this for the same reason the rules do -- a walk that
       ends mid-trade asks for it -- and forgetting it here rather than there is
       what made the SECOND run fail on the same cells as the first. */
    exitLevel: () => null,
    decide(i, ctx) {
      if (ctx.pos) return rule.decide(i, ctx);        // the rule's own exit
      let side = opportunityAt(i);
      if (side === FLAT) return null;
      if (rand() >= rate) return null;
      if (randomSide) side = rand() < 0.5 ? LONG : SHORT;
      const a = ctx.series.atr[i];
      if (!(a > 0)) return null;
      const stop = side === LONG ? ctx.close[i] - riskAtr * a
                                 : ctx.close[i] + riskAtr * a;
      return { side, stop, tag: 'control' };
    },
  };
}

/* MATCHED ON COUNT, NOT JUST ON PROBABILITY.
 *
 * Firing at the rule's own hit rate does NOT reproduce its trade count: both
 * are blocked while a position is open, and the two hold for different lengths,
 * so a rule that clusters its entries consumes fewer opportunities than a coin
 * flip that spreads them out. The first version of this control came in 33%
 * short on the S/R candidate, which would have handed the rule a third more
 * chances than its control and made any comparison meaningless.
 *
 * So the rate is calibrated: fire, count, scale, repeat. Three passes settle it
 * to within a couple of percent. Nothing here looks at returns -- only at how
 * many trades came out. */
const seed = hashOf(`${cfg.cell}|${cfg.rule}`);
const target = real.trades.length;
let useRate = rate;
let randEntry = runRule(bars, controlRule(seed, false, useRate), { tf });
for (let pass = 0; pass < 3 && target > 0; pass++) {
  const n = randEntry.trades.length;
  if (!n || Math.abs(n - target) / target < 0.02) break;
  useRate = Math.min(1, Math.max(1e-6, useRate * (target / n)));
  randEntry = runRule(bars, controlRule(seed, false, useRate), { tf });
}
process.stderr.write(`  ${cfg.cell} ${cfg.rule}: rate ${(100 * rate).toFixed(3)}%`
  + ` -> ${(100 * useRate).toFixed(3)}% (rule ${target}, control `
  + `${randEntry.trades.length})
`);

/* A TRAILED ROW NEEDS A TRAILED CONTROL, and the first version of this study
 * did not have one.
 *
 * `ruleTrail` adds an EXIT to the rule. Scoring it against `randEntry`, which
 * has no trail, compares entry+trail against random-entry-no-trail and charges
 * the whole difference to the entry -- which is how the S/R candidate came back
 * "BEATS THE COIN FLIP IN BOTH ERAS" on the hold-out while its untrailed twin,
 * the row that actually tests the entry, was not demonstrated. `randEntryTrail`
 * is the same coin flip carrying the same trail, so the trailed comparison
 * varies the entry and nothing else. */
const trail = () => makeTrail('structure', { tf, cell: cfg.cell });
const runs = {
  rule: real,
  ruleTrail: runRule(bars, cached, { tf, exitTrail: trail() }),
  randEntry,
  randEntryTrail: runRule(bars, controlRule(seed, false, useRate),
                          { tf, exitTrail: trail() }),
  randSide: runRule(bars, controlRule(seed ^ 0x9e3779b9, true, useRate), { tf }),
  donchian: runRule(bars, donchianRule, { tf }),
};

const out = { bars: bars.length, tf, cell: cfg.cell, rule: cfg.rule,
              riskAtr, matchedRate: useRate, rawRate: rate, opportunityBars: usable,
              ruleTrades: target, controlTrades: randEntry.trades.length,
              order: ['rule', 'ruleTrail', 'randEntry', 'randEntryTrail',
                      'randSide', 'donchian'],
              /* Which control each row is measured against. The eval reads
                 this rather than assuming one baseline for the whole table. */
              baselineFor: { rule: 'randEntry', ruleTrail: 'randEntryTrail',
                             randEntry: 'randEntry', randEntryTrail: 'randEntry',
                             randSide: 'randEntry', donchian: 'randEntry' },
              runs: {} };
for (const [name, r] of Object.entries(runs)) {
  out.runs[name] = r.trades.map((t) => ({
    side: t.side, r: t.r, reason: t.reason,
    entryI: t.entryI, entryTime: t.entryTime, bars: t.exitI - t.entryI,
  }));
  const net = r.trades.reduce((s, t) => s + t.r, 0);
  process.stderr.write(`  ${cfg.cell} ${cfg.rule}/${name}: ${r.trades.length} trades, `
                       + `net ${net.toFixed(1)} R\n`);
}
console.log(JSON.stringify(out));
