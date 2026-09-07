/* Phase 14 dataset: causal market state at the signal bar, TP labels after it.

     node --max-old-space-size=8192 tools/phase14_runner.mjs <cfg.json>

   cfg.json is {"barsPath", "higherPaths": {"15m": "...", "1h": "..."},
                "tf", "cell", "out"}.

   TWO HALVES, AND THE WALL BETWEEN THEM IS THE POINT.

   FEATURES are read at the SIGNAL bar -- the bar the rule decided on, one
   before the fill -- from js/chart/regime.js `dimensions` and
   js/chart/liquidity.js `compute`. Both are audited causal
   (tools/liquidity_audit.mjs rebuilds every field from a truncated prefix and
   compares). Nothing in this half may look forward.

   LABELS are read AFTER the entry and may look forward, because that is what a
   label is. `hit_kR` is the plain question: from the fill, did price reach
   k x risk in the trade's favour BEFORE touching the stop? Resolved bar by bar,
   and a bar that spans both is scored as the STOP -- the pessimistic reading
   this project uses everywhere, because the alternative flatters every result
   and cannot be checked from daily bars.

   `y_r` is what the SHIPPED rule actually returned on the same trade, carried
   so the conditional TP curve can be compared against the exit that ships
   rather than against zero. */

import fs from 'node:fs';
import { runRule } from '../js/chart/rules.js';
import { donchianRule } from '../js/chart/donchian.js';
import { makeTrail } from '../js/chart/exittrail.js';
import { dimensions } from '../js/chart/regime.js';
import { compute as liquidityOf } from '../js/chart/liquidity.js';

const cfg = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const bars = JSON.parse(fs.readFileSync(cfg.barsPath, 'utf8'));
const higher = {};
for (const [tf, p] of Object.entries(cfg.higherPaths || {})) {
  higher[tf] = JSON.parse(fs.readFileSync(p, 'utf8'));
}
const tf = cfg.tf;

const R_BANDS = [0.5, 0.75, 1, 1.5, 2, 3, 5];

process.stderr.write(`  ${cfg.cell}: ${bars.length} bars, higher [${Object.keys(higher)}]\n`);

const dims = dimensions(bars);
process.stderr.write('  dimensions done\n');
const liq = liquidityOf(bars, { higher });
process.stderr.write(`  liquidity done (${liq.levels.length} levels)\n`);

const run = runRule(bars, donchianRule, { tf, exitTrail: makeTrail('structure', { tf, cell: cfg.cell }) });
process.stderr.write(`  walk done (${run.trades.length} trades)\n`);

/** Did price reach `k` R before the stop? Ties inside a bar go to the stop. */
function hits(t, k) {
  const side = t.side;
  const target = t.entryPrice + side * k * t.risk;
  for (let i = t.entryI; i < bars.length; i++) {
    const b = bars[i];
    const stopped = side > 0 ? b.l <= t.stop : b.h >= t.stop;
    const made = side > 0 ? b.h >= target : b.l <= target;
    if (stopped) return 0;             // checked FIRST: the pessimistic tie
    if (made) return 1;
  }
  return 0;                            // ran out of data: not reached
}

const out = fs.openSync(cfg.out, 'w');
let n = 0;
for (const t of run.trades) {
  const si = Number.isFinite(t.signalI) ? t.signalI : t.entryI - 1;
  const f = liq.featuresAt(si);
  const row = {
    cell: cfg.cell, tf,
    signal_time: bars[si].t, entry_time: t.entryTime,
    side: t.side, entry: t.entryPrice, stop: t.stop, risk: t.risk,

    /* ---- causal state at the signal bar ---- */
    direction: dims.direction[si],
    phase: dims.phase[si],
    volatility: dims.volatility[si],
    give_back_atr: Number.isFinite(dims.giveBackAtr[si]) ? +dims.giveBackAtr[si].toFixed(3) : null,
    ema_gap_atr: Number.isFinite(dims.emaSepAtr[si]) ? +dims.emaSepAtr[si].toFixed(3) : null,
    range_position_40: Number.isFinite(dims.rangePos[si]) ? +dims.rangePos[si].toFixed(3) : null,
    atr_ratio_56: Number.isFinite(dims.energy[si]) ? +dims.energy[si].toFixed(3) : null,
    ...f,

    /* is the trade going the same way as the regime? */
    aligned: dims.direction[si] === 'bull' ? t.side > 0
      : dims.direction[si] === 'bear' ? t.side < 0 : null,

    /* ---- labels, which MAY look forward ---- */
    y_r: t.r,
    y_reason: t.reason,
  };
  for (const k of R_BANDS) row['hit_' + String(k).replace('.', '_') + 'R'] = hits(t, k);
  fs.writeSync(out, JSON.stringify(row) + '\n');
  if (++n % 500 === 0) process.stderr.write(`  ${n}/${run.trades.length}\n`);
}
fs.closeSync(out);
process.stderr.write(`  wrote ${n} rows -> ${cfg.out}\n`);
console.log(JSON.stringify({ cell: cfg.cell, tf, rows: n, levels: liq.levels.length }));
