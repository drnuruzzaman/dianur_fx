/* Driver for tools/smc_eval.py — see that file for what is compared and why the
   controls are the rows that decide. Run by hand as:

     node --max-old-space-size=6144 tools/smc_runner.mjs <cfg.json>

   where cfg.json is {"barsPath": "...", "tf": "5m", "cell": "XAUUSD.a|5m"}. */

import fs from 'node:fs';
import { FLAT, LONG, SHORT, runRule } from '../js/chart/rules.js';
import { donchianRule } from '../js/chart/donchian.js';
import { smcRetestRule, DEFAULTS } from '../js/chart/smcretest.js';
import { makeTrail } from '../js/chart/exittrail.js';
import { BULL, BEAR } from '../js/chart/marketstructure.js';

const cfg = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const bars = JSON.parse(fs.readFileSync(cfg.barsPath, 'utf8'));
const tf = cfg.tf;

/* A DETERMINISTIC RNG, and the `>>> 0` is not decoration.
 *
 * tools/entry_filter_eval.py had a control that was supposed to keep 50% of
 * entries and kept 75%: `^` in JavaScript returns a SIGNED int32, so a negative
 * value modulo anything stayed negative and every comparison against a positive
 * threshold passed. The bug was invisible except in the retention column, which
 * is why retention is printed here too. */
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

const summarise = (trades) => {
  const rs = trades.map((t) => t.r);
  return { n: rs.length, netR: rs.reduce((a, b) => a + b, 0) };
};

/* ---------------------------------------------------------------- the rule */
const smc = runRule(bars, smcRetestRule, { tf });

/* WHAT THE CONTROLS HAVE TO MATCH, taken from the rule's own run before either
   of them is scored: how often it trades, and how wide its stop is in ATR.
   Choosing either after seeing returns would make the control an accomplice --
   the same order tools/exit_trail_eval.py matches its ATR trail in. */
const risks = smc.trades.map((t) => t.risk / (t.atrAtEntry || NaN)).filter(Number.isFinite);
const series = smcRetestRule.prepare(bars, { ...DEFAULTS });
const riskAtr = (() => {
  /* `runRule` does not report the ATR at entry, so it is recomputed here from
     the same series the rule used -- median, so one enormous zone cannot set
     the control's stop for every trade. */
  const v = smc.trades.map((t) => t.risk / series.atr[t.entryI]).filter(Number.isFinite)
    .sort((a, b) => a - b);
  return v.length ? v[Math.floor(v.length / 2)] : 1.0;
})();
void risks;

const usable = (() => {
  /* The control may only fire where the rule COULD have: bars with a decided
     bias and a live zone on that side. Letting it fire anywhere would make it a
     different experiment -- "does trading less help" rather than "does the zone
     pick better entries than chance". */
  let k = 0;
  for (let i = 0; i < bars.length; i++) {
    const b = series.msBias[i];
    if (b !== BULL && b !== BEAR) continue;
    const z = b === BULL ? series.demand[i] : series.supply[i];
    if (z && z.confirmedI < i) k++;
  }
  return k;
})();
const rate = usable > 0 ? smc.trades.length / usable : 0;

/** A control that keeps the rule's SIDE and its stop width, and nothing else. */
function randomEntryRule(seed, randomSide) {
  const rand = rng(seed);
  return {
    key: 'control',
    defaults: { ...DEFAULTS },
    summary: 'matched random entries',
    warmup: smcRetestRule.warmup,
    prepare: smcRetestRule.prepare,
    decide(i, ctx) {
      /* THE EXIT IS THE RULE'S OWN. Only the entry is randomised: if the
         control also exited differently, a win could come from either half and
         the comparison would answer nothing. */
      if (ctx.pos) return smcRetestRule.decide(i, ctx);
      const bias = ctx.series.msBias[i];
      let side = bias === BULL ? LONG : (bias === BEAR ? SHORT : FLAT);
      if (side === FLAT) return null;
      const z = side === LONG ? ctx.series.demand[i] : ctx.series.supply[i];
      if (!z || z.confirmedI >= i) return null;         // same opportunity set
      if (rand() >= rate) return null;                   // matched frequency
      if (randomSide) side = rand() < 0.5 ? LONG : SHORT;
      const a = ctx.series.atr[i];
      if (!(a > 0)) return null;
      const stop = side === LONG ? ctx.close[i] - riskAtr * a
                                 : ctx.close[i] + riskAtr * a;
      return { side, stop, tag: 'control' };
    },
  };
}

const cellSeed = hashOf(cfg.cell);
const trail = makeTrail('structure', { tf, cell: cfg.cell });

const runs = {
  /* the candidate */
  smc: smc,
  smcTrail: runRule(bars, smcRetestRule, { tf, exitTrail: trail }),
  /* THE CONTROLS -- the rows that decide */
  randEntry: runRule(bars, randomEntryRule(cellSeed, false), { tf }),
  randSide: runRule(bars, randomEntryRule(cellSeed ^ 0x9e3779b9, true), { tf }),
  /* and the shipped rule on the same bars, for scale */
  donchian: runRule(bars, donchianRule, { tf }),
};

const out = { bars: bars.length, tf, cell: cfg.cell,
              riskAtr, matchedRate: rate, opportunityBars: usable,
              order: ['smc', 'smcTrail', 'randEntry', 'randSide', 'donchian'],
              runs: {} };
for (const [name, r] of Object.entries(runs)) {
  out.runs[name] = r.trades.map((t) => ({
    side: t.side, r: t.r, reason: t.reason,
    entryI: t.entryI, entryTime: t.entryTime, bars: t.exitI - t.entryI,
  }));
  const s = summarise(r.trades);
  process.stderr.write(`  ${cfg.cell} ${name}: ${s.n} trades, net ${s.netR.toFixed(1)} R\n`);
}

console.log(JSON.stringify(out));
