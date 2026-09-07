/* Driver for tools/scalp_eval.py.

     node --max-old-space-size=6144 tools/scalp_runner.mjs <cfg.json>

   cfg.json is {"barsPath": "...", "tf": "5m", "cell": "XAUUSD.a|5m"}.

   WHAT THIS EMITS AND WHY. One row per trade with `risk` on it, because the
   whole question here is NET of spread and the cost of a trade in R is
   `spread / risk` -- a per-trade number, not a per-cell constant. A cell-level
   average would hide exactly the trades the cost falls hardest on, which are
   the tight-stop ones a scalper takes most of.

   `hour` is the UTC hour of the ENTRY BAR, which is the bar after the signal.
   That is the hour a live trader is actually in the market for, and it is
   knowable at entry -- unlike anything about how the trade turns out.

   THE CONTROLS ARE THE POINT, as everywhere else in this project. `randEntry`
   keeps the rule's bias, stop width, exit and trade count and moves only the
   bar; `randSide` also flips a coin on direction. An hour filter that cannot
   beat these has not been shown to do anything. */

import fs from 'node:fs';
import { LONG, SHORT, runRule } from '../js/chart/rules.js';
import { donchianRule } from '../js/chart/donchian.js';
import { makeTrail } from '../js/chart/exittrail.js';
import { compute as regimeOf } from '../js/chart/regime.js';

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

/* THE REGIME AT THE SIGNAL BAR, from the SAME module the panel draws, rather
   than a second implementation or a timestamp join in the analysis script.
   js/chart/regime.js is causal by construction -- the EMAs, the range window
   and the ATR mean at bar i all end at i -- so the label on the decision bar is
   one a live reader had. Labelling the ENTRY bar instead would be a half-bar of
   hindsight, which is exactly the kind of leak this project keeps finding. */
const reg = regimeOf(bars);

const shipped = runRule(bars, donchianRule, { tf, exitTrail: trail() });

/* THE CONTROLS, calibrated to the rule's own trade COUNT rather than to a rate
   guessed in advance: a control that trades a different number of times is not
   a control, it is a different strategy. */
function randomRule(rate, seed, flipSide) {
  const rand = rng(seed);
  return {
    ...donchianRule,
    decide(i, ctx) {
      const asked = donchianRule.decide(i, ctx);
      if (ctx.pos) return asked;                    // exits stay the rule's own
      if (asked && asked.side !== 0) {
        // the rule wants in; the control enters on its own schedule instead
        return rand() < rate ? { ...asked, side: pick(asked.side, rand, flipSide) } : null;
      }
      if (rand() < rate) {
        const a = ctx.series.atr[i];
        if (!(a > 0)) return null;
        const side = flipSide ? (rand() < 0.5 ? LONG : SHORT)
                              : (ctx.close[i] >= ctx.close[i - 1] ? LONG : SHORT);
        const stop = side === LONG ? ctx.close[i] - 2 * a : ctx.close[i] + 2 * a;
        return { side, stop, tag: 'ctrl' };
      }
      return null;
    },
  };
}
function pick(side, rand, flipSide) {
  return flipSide ? (rand() < 0.5 ? LONG : SHORT) : side;
}

/* Calibrate each control's per-bar rate so its trade count lands on the rule's,
   by bisection. Done BEFORE any result is looked at. */
function calibrate(seed, flipSide) {
  const target = shipped.trades.length;
  let lo = 0, hi = 0.02, rate = 0.004, best = null;
  for (let k = 0; k < 14; k++) {
    rate = (lo + hi) / 2;
    const r = runRule(bars, randomRule(rate, seed, flipSide), { tf, exitTrail: trail() });
    const n = r.trades.length;
    if (best === null || Math.abs(n - target) < Math.abs(best.n - target)) best = { r, n, rate };
    if (n < target) lo = rate; else hi = rate;
    if (Math.abs(n - target) <= target * 0.02) break;
  }
  return best;
}

const seed = hashOf(cfg.cell);
const ctrlEntry = calibrate(seed, false);
const ctrlSide = calibrate(seed ^ 0x9e3779b9, true);

const runs = { shipped, randEntry: ctrlEntry.r, randSide: ctrlSide.r };

const out = { bars: bars.length, tf, cell: cfg.cell,
              order: ['shipped', 'randEntry', 'randSide'], runs: {} };
for (const [name, r] of Object.entries(runs)) {
  out.runs[name] = r.trades.map((t) => {
    const si = Number.isFinite(t.signalI) ? t.signalI : t.entryI - 1;
    return {
      r: t.r,
      risk: t.risk,                     // price units; cost in R is spread / risk
      entryTime: t.entryTime,
      hour: new Date(t.entryTime).getUTCHours(),
      regime: reg.regime[si] || null,   // as of the DECISION bar, not the fill
      dir: reg.direction[si] || null,
      sepAtr: Number.isFinite(reg.emaSepAtr[si]) ? +reg.emaSepAtr[si].toFixed(3) : null,
      rangePos: Number.isFinite(reg.rangePos[si]) ? +reg.rangePos[si].toFixed(3) : null,
      energy: Number.isFinite(reg.energy[si]) ? +reg.energy[si].toFixed(3) : null,
      side: t.side,
      reason: t.reason,
      bars: t.exitI - t.entryI,
    };
  });
  const net = r.trades.reduce((s, t) => s + t.r, 0);
  process.stderr.write(`  ${cfg.cell} ${name}: ${r.trades.length} trades, `
                       + `net ${net.toFixed(1)} R\n`);
}
console.log(JSON.stringify(out));
