/* Driver for tools/partial_tp_eval.py. Run by hand as:

     node --max-old-space-size=6144 tools/partial_tp_runner.mjs <cfg.json>

   cfg.json is {"barsPath": "...", "tf": "4h", "cell": "XAUUSD.a|4h"}.

   THE SPLIT IS RECONSTRUCTED, NOT RE-WALKED, and that is the whole design.

   A position taken in two halves that are closed independently is arithmetically
   two positions with the same entry and the same stop. So half B is EXACTLY the
   trade the shipped configuration already takes -- same entry, same trail, same
   exit -- and only half A needs working out: it leaves at the first bar whose
   range reaches TP1, or with the trade if that never happens.

   Doing it this way means the trade lifecycle is still js/chart/rules.js and
   nothing here re-implements fills, gaps or stops. `rules.js` says it plainly:
   three copies of that lifecycle is how the ATR divergence got in, and the
   take-profit machinery that used to live there was deliberately removed. This
   study does not put it back.

   WHAT THE RECONSTRUCTION DOES NOT MODEL: moving the stop to break-even after
   the first half is taken. That is a different rule and would need its own
   measurement -- the halves here are independent, which is the version the
   arithmetic above is exact for. */

import fs from 'node:fs';
import { runRule } from '../js/chart/rules.js';
import { donchianRule } from '../js/chart/donchian.js';
import { displayLevels } from '../js/chart/levels.js';
import { makeTrail } from '../js/chart/exittrail.js';
import { atrSeries } from '../js/chart/tlengine.js';

const cfg = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const bars = JSON.parse(fs.readFileSync(cfg.barsPath, 'utf8'));
const tf = cfg.tf;
const LONG = 1;

/* THE SHIPPED CONFIGURATION: the validated rule plus the structural trail, which
   is what both charts draw today. Every row below is a variation on ITS trades. */
const base = runRule(bars, donchianRule,
                     { tf, exitTrail: makeTrail('structure', { tf, cell: cfg.cell }) });
const atr = atrSeries(bars, 14);

/**
 * TP1 for a trade, computed the way the panel computes it: structural levels
 * ahead of the trade, chosen AT THE SIGNAL BAR from bars that existed then.
 * Never re-derived later -- a trade opened 700 bars ago was planned against the
 * structure visible then.
 */
function tp1Of(t) {
  const side = t.side === LONG ? 1 : -1;
  const at = Number.isFinite(t.signalI) ? t.signalI : t.entryI;
  const ls = displayLevels(bars, { side, from: t.entryPrice, upto: at, tf, max: 3 });
  return ls.length ? ls[0].price : NaN;
}

/**
 * Where half A leaves, given a target price.
 *
 * PESSIMISTIC ON TIES, like the walker: when a bar's range covers both the stop
 * and the target, the stop is assumed to fill first. Being generous there is how
 * a target study flatters itself -- the bar that reaches both is exactly the
 * volatile bar a real fill is least certain on.
 */
function halfExit(t, target) {
  if (!Number.isFinite(target)) return { price: t.exitPrice, i: t.exitI, hit: false };
  const side = t.side === LONG ? 1 : -1;
  for (let i = t.entryI + 1; i <= t.exitI; i++) {
    const b = bars[i];
    const stopped = side > 0 ? b.l <= t.stop : b.h >= t.stop;
    const reached = side > 0 ? b.h >= target : b.l <= target;
    if (stopped) return { price: t.exitPrice, i, hit: false };
    if (reached) return { price: target, i, hit: true };
  }
  return { price: t.exitPrice, i: t.exitI, hit: false };
}

const rOf = (t, px) => (px - t.entryPrice) * (t.side === LONG ? 1 : -1) / t.risk;

/* THE MATCHED CONTROL. Half out at the same DISTANCE as TP1 sat, in ATR, but at
   no particular level -- the row that says whether the structure matters or only
   the distance. Matched on the rule's own geometry (the median TP1 distance),
   before anything is scored. */
const dists = [];
for (const t of base.trades) {
  const tp = tp1Of(t);
  const a = atr[t.entryI];
  if (Number.isFinite(tp) && a > 0) dists.push(Math.abs(tp - t.entryPrice) / a);
}
dists.sort((a, b) => a - b);
const medDistAtr = dists.length ? dists[Math.floor(dists.length / 2)] : NaN;

const rows = { trailOnly: [], halfTp1: [], halfMatched: [], halfOneR: [] };
let hits = 0;
let withTp = 0;

for (const t of base.trades) {
  const full = rOf(t, t.exitPrice);
  rows.trailOnly.push({ ...t, r: full });

  const tp = tp1Of(t);
  const a = atr[t.entryI];
  const side = t.side === LONG ? 1 : -1;
  if (Number.isFinite(tp)) withTp++;

  const variants = [
    ['halfTp1', tp],
    ['halfMatched', (a > 0 && Number.isFinite(medDistAtr))
      ? t.entryPrice + side * medDistAtr * a : NaN],
    ['halfOneR', t.entryPrice + side * t.risk],
  ];
  for (const [name, target] of variants) {
    const ex = halfExit(t, target);
    if (name === 'halfTp1' && ex.hit) hits++;
    /* half at the target (or with the trade), half on the trail */
    const r = 0.5 * rOf(t, ex.price) + 0.5 * full;
    rows[name].push({ ...t, r, exitI: ex.hit ? ex.i : t.exitI });
  }
}

const out = {
  bars: bars.length, tf, cell: cfg.cell,
  medDistAtr, tradesWithTp1: withTp, tp1Hits: hits,
  order: ['trailOnly', 'halfTp1', 'halfMatched', 'halfOneR'],
  runs: {},
};
for (const [name, list] of Object.entries(rows)) {
  out.runs[name] = list.map((t) => ({
    side: t.side, r: t.r, reason: t.reason,
    entryI: t.entryI, entryTime: t.entryTime, bars: t.exitI - t.entryI,
  }));
  const net = list.reduce((s, t) => s + t.r, 0);
  process.stderr.write(`  ${cfg.cell} ${name}: ${list.length} trades, `
                       + `net ${net.toFixed(1)} R\n`);
}
process.stderr.write(`  ${cfg.cell}: TP1 known for ${withTp}/${base.trades.length} `
                     + `trades, reached on ${hits} (median TP1 `
                     + `${medDistAtr ? medDistAtr.toFixed(2) : '?'} ATR out)\n`);
console.log(JSON.stringify(out));
