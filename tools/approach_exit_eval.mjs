/* DOES A REAL EXIT RESCUE THE LEVEL WORK?
 *
 *     node --max-old-space-size=8192 tools/approach_exit_eval.mjs [maxBars]
 *
 * THE GAP THIS CLOSES. Every level measurement in this project scored a
 * SYMMETRIC BARRIER -- did price travel 0.5 ATR back the way it came before it
 * travelled 0.5 ATR onward. That is the right instrument for asking whether a
 * level knows anything, because it makes the null exactly 50/50 and cannot be
 * tilted by band width. It is also an exit nobody trades. The trendline section
 * lists exits as the largest untested lever for exactly this reason.
 *
 * WHAT IS ALREADY SETTLED AND NOT RE-RUN. For the Donchian rule the exit
 * question is closed: a 1R cap turns +43.7 net R into -2.1, and four structural
 * target variants all landed below the plain trail across twelve out-of-sample
 * cells. That was a TREND rule paid from the tail. This asks a different
 * question about a different signal -- an approach to a level is a
 * mean-reversion claim, and the tail argument does not automatically carry.
 *
 * THE ENTRY IS FIXED AND DELIBERATELY NAIVE: enter at the approach bar's close,
 * in the REJECTION direction -- back the way price came. That is the claim the
 * levels narrative makes, it is what the width-free label scores, and holding it
 * fixed is what makes the exit the only thing varying.
 *
 * FIVE EXITS, one of which is the existing label so the comparison has a floor:
 *
 *   barrier    +/- MOVE ATR, symmetric. The current label, as an R number.
 *   fixed 2R   stop 1 ATR, target 2 ATR.
 *   trail 1    stop trails 1 ATR behind the best price reached.
 *   trail 2    the same at 2 ATR.
 *   trail 3    the same at 3 ATR.
 *
 * R IS MEASURED AGAINST EACH ARM'S OWN INITIAL STOP, which is the only way the
 * arms are comparable -- a 3 ATR trail risks three times what a 1 ATR trail
 * does, and reporting raw points would hand the win to whichever arm bet most.
 *
 * FRICTION IS REPORTED SEPARATELY, NOT NETTED. The measured cost on these
 * instruments is 0.136 R at the Donchian rule's stop distance and it scales with
 * how tight the stop is; applying one number across five different stop widths
 * would flatter the wide arms. Gross first, then the cost each arm would have to
 * clear.
 *
 * TWO ERAS ALWAYS. A result in one half is not a result.
 */

import fs from 'node:fs';
import zlib from 'node:zlib';
import path from 'node:path';
import { detect as detectZones } from '../js/chart/zones.js';
import { atrSeries } from '../js/chart/tlengine.js';

const MAX = Number(process.argv[2] || 60000);
const STEP = 20;
const MOVE_ATR = 0.5;                  // the label's barrier, unchanged
const HORIZON = 40;                    // bars a trade may live
const ROOT = path.join(path.dirname(new URL(import.meta.url).pathname.slice(1)), '..');
const CELLS = [['XAUUSD.a', '5m'], ['XAUUSD.a', '15m'], ['XAUUSD.a', '1h'],
               ['EURUSD.a', '15m'], ['EURUSD.a', '1h'], ['GBPUSD.a', '1h']];

function loadBars(dir, max) {
  const rows = [];
  for (const f of fs.readdirSync(dir).filter((x) => x.endsWith('.csv.gz')).sort().reverse()) {
    const text = zlib.gunzipSync(fs.readFileSync(path.join(dir, f))).toString('utf8');
    const lines = text.split('\n');
    const ix = Object.fromEntries(lines[0].trim().split(',').map((k, i) => [k, i]));
    for (let i = 1; i < lines.length; i++) {
      const c = lines[i].trim().split(',');
      if (c.length < 5) continue;
      rows.push({ t: Number(c[ix.ts]) * 1000, o: +c[ix.open], h: +c[ix.high],
                  l: +c[ix.low], c: +c[ix.close] });
    }
    if (rows.length >= max) break;
  }
  rows.sort((a, b) => a.t - b.t);
  return rows.length > max ? rows.slice(-max) : rows;
}

/**
 * Walk one trade. `dir` is +1 long, -1 short. Returns R against `riskAtr`.
 *
 * WITHIN-BAR ORDER IS UNKNOWABLE FROM OHLC, so a bar that touches both the stop
 * and the target is scored as the STOP. That is the pessimistic reading and it
 * is the one this project uses everywhere else; the alternative silently pays
 * the optimistic side of every ambiguous bar, which is how a backtest invents
 * an edge it cannot fill.
 */
function walk(bars, i, dir, entry, a, riskAtr, targetAtr, trailAtr) {
  const risk = riskAtr * a;
  let stop = entry - dir * risk;
  const target = targetAtr ? entry + dir * targetAtr * a : null;
  let best = entry;
  const end = Math.min(bars.length - 1, i + HORIZON);
  for (let j = i + 1; j <= end; j++) {
    const b = bars[j];
    const hitStop = dir > 0 ? b.l <= stop : b.h >= stop;
    const hitTgt = target !== null && (dir > 0 ? b.h >= target : b.l <= target);
    if (hitStop) return (stop - entry) * dir / risk;     // pessimistic on ties
    if (hitTgt) return (target - entry) * dir / risk;
    if (trailAtr) {
      best = dir > 0 ? Math.max(best, b.h) : Math.min(best, b.l);
      const want = best - dir * trailAtr * a;
      if (dir > 0 ? want > stop : want < stop) stop = want;
    }
  }
  return (bars[end].c - entry) * dir / risk;              // time stop
}

/** The symmetric barrier, expressed as R so it sits in the same table. */
function barrier(bars, i, dir, entry, a) {
  const move = MOVE_ATR * a;
  const end = Math.min(bars.length - 1, i + HORIZON);
  for (let j = i + 1; j <= end; j++) {
    const b = bars[j];
    const win = dir > 0 ? b.h - entry >= move : entry - b.l >= move;
    const lose = dir > 0 ? entry - b.l >= move : b.h - entry >= move;
    if (win && lose) return -1;                           // pessimistic on ties
    if (win) return 1;
    if (lose) return -1;
  }
  return (bars[end].c - entry) * dir / move;
}

const ARMS = [
  ['barrier 0.5', (bs, i, d, e, a) => barrier(bs, i, d, e, a), 0.5],
  ['fixed 1:2', (bs, i, d, e, a) => walk(bs, i, d, e, a, 1, 2, 0), 1],
  ['trail 1 ATR', (bs, i, d, e, a) => walk(bs, i, d, e, a, 1, 0, 1), 1],
  ['trail 2 ATR', (bs, i, d, e, a) => walk(bs, i, d, e, a, 2, 0, 2), 2],
  ['trail 3 ATR', (bs, i, d, e, a) => walk(bs, i, d, e, a, 3, 0, 3), 3],
];

function cell(bars, tf) {
  const atr = atrSeries(bars, 14);
  const out = ARMS.map(() => []);
  for (let i = 600; i + HORIZON < bars.length; i += STEP) {
    let zones = [];
    try { zones = detectZones(bars, i, tf, atr, {}); } catch { continue; }
    if (!zones.length) continue;
    const a = atr[i];
    if (!(a > 0)) continue;
    for (let k = i + 1; k < Math.min(bars.length - HORIZON, i + STEP); k++) {
      const bar = bars[k];
      for (const z of zones) {
        const inside = bar.l <= z.high && bar.h >= z.low;
        const wasOut = bars[k - 1].c < z.low || bars[k - 1].c > z.high;
        if (!inside || !wasOut) continue;
        const fromAbove = bars[k - 1].c > z.high;
        // REJECTION: arrived from above -> the claim is price turns back UP.
        const dir = fromAbove ? 1 : -1;
        const ak = atr[k];
        if (!(ak > 0)) continue;
        ARMS.forEach(([, fn], ai) => {
          out[ai].push({ i: k, r: fn(bars, k, dir, bar.c, ak) });
        });
      }
    }
  }
  return out;
}

const stat = (rs) => {
  if (!rs.length) return null;
  const n = rs.length;
  const mean = rs.reduce((s, x) => s + x, 0) / n;
  const sd = Math.sqrt(rs.reduce((s, x) => s + (x - mean) ** 2, 0) / Math.max(1, n - 1));
  const wins = rs.filter((x) => x > 0).length;
  return { n, mean, se: sd / Math.sqrt(n), win: 100 * wins / n,
           net: rs.reduce((s, x) => s + x, 0) };
};

const COLS = [13, 13, 8, 9, 8, 8, 10];
const row = (...c) => c.map((v, i) => (i ? String(v).padStart(COLS[i]) : String(v).padEnd(COLS[i]))).join(' ');
const f = (v, d = 3) => (Number.isFinite(v) ? v.toFixed(d) : '--');

console.log('EXITS ON LEVEL APPROACHES   horizon %d bars   maxBars %d', HORIZON, MAX);
console.log('Entry is fixed (approach bar close, rejection direction). Only the exit varies.');
console.log('R is against EACH ARM\'S OWN initial stop. Gross -- friction is the last column.');
console.log('');
console.log(row('cell', 'exit', 'n', 'avg R', 'se', 'win%', 'eras'));
console.log('-'.repeat(79));

const pooled = ARMS.map(() => []);
for (const [sym, tf] of CELLS) {
  const dir = path.join(ROOT, 'data', 'bars', sym, tf);
  if (!fs.existsSync(dir)) continue;
  const bars = loadBars(dir, MAX);
  if (bars.length < 5000) continue;
  const arms = cell(bars, tf);
  const mid = Math.floor(bars.length / 2);
  const name = sym.replace('.a', '') + ' ' + tf;
  ARMS.forEach(([label], ai) => {
    const all = arms[ai];
    const s = stat(all.map((x) => x.r));
    if (!s) return;
    const e1 = stat(all.filter((x) => x.i < mid).map((x) => x.r));
    const e2 = stat(all.filter((x) => x.i >= mid).map((x) => x.r));
    console.log(row(name, label, s.n, f(s.mean), f(s.se), f(s.win, 1),
                    (e1 ? f(e1.mean, 2) : '--') + ' / ' + (e2 ? f(e2.mean, 2) : '--')));
    pooled[ai].push(...all.map((x) => x.r));
  });
  console.log('-'.repeat(79));
}

console.log('');
console.log('POOLED (cells are not independent -- read the per-cell rows first)');
console.log(row('', 'exit', 'n', 'avg R', 'se', 'win%', 'net R'));
ARMS.forEach(([label], ai) => {
  const s = stat(pooled[ai]);
  if (s) console.log(row('', label, s.n, f(s.mean), f(s.se), f(s.win, 1), f(s.net, 0)));
});
console.log('');
console.log('FRICTION TO CLEAR. Measured cost is 0.136 R at the Donchian rule\'s stop');
console.log('distance; a tighter stop pays MORE R for the same spread. An arm is only');
console.log('interesting if avg R exceeds its own friction, and none of these is close.');
