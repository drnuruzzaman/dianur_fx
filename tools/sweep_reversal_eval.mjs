/* IS A LIQUIDITY SWEEP A REVERSAL SIGNAL ON ITS OWN?
 *
 *     node --max-old-space-size=8192 tools/sweep_reversal_eval.mjs <tf> <from> <to> [H] [window]
 *     node --max-old-space-size=8192 tools/sweep_reversal_eval.mjs 15m 2016-01-01 2021-01-01
 *
 * THE CLAIM UNDER TEST is the one the Rayo Scalping Strategy's entry engine
 * rides on, and it is NOT the one tools/sweep_break_eval.mjs measured. That
 * file asked whether a sweep before a STRUCTURE BREAK improves the break's
 * continuation, and recorded that it does not clear. This asks the simpler,
 * prior thing the pattern is usually drawn as:
 *
 *     price dips under a low, takes the stops, closes back above  ->  LONG
 *     price pokes over a high, takes the stops, closes back below ->  SHORT
 *
 * with nothing else required. If this fails, an entry engine built on it has
 * nothing under it; if it holds, THAT is the ingredient worth building on, and
 * the rest of the Rayo score is decoration.
 *
 * SAME DEFINITION, SAME CONTROL, DELIBERATELY. The sweep is `liquidity.js
 * sweepAt` -- the strict round trip, pierce then CLOSE back -- and the control
 * is the matched-candle scheme from sweep_break_eval: every bar bucketed by
 * direction and by body/ATR and range/ATR quintile, events and a GUARD
 * neighbourhood held out of the pool, each event scored against its own
 * bucket. That control is load-bearing here more than anywhere: the bar a sweep
 * COMPLETES on is a reversal-shaped candle by construction -- long wick, close
 * back inside -- and reversal candles carry momentum whether or not a level
 * was under them. A raw hit rate would credit the level for the candle.
 *
 * ERA-SLICED BY TIMESTAMP, not last-N bars. Each era gets its own control pool
 * so the two are independent, and the result is comparable with everything
 * else measured on 2016-2020 / 2021-2026 in this project.
 *
 * GOLD ONLY, by request. XAUUSD is also the one instrument whose recorded spread
 * column is live, though this test charges no costs -- it asks about direction.
 *
 * WHAT WOULD MAKE IT REAL, written down before the run:
 *   - edge positive in BOTH eras, not one
 *   - stronger for scarcer families (DAY > SESSION > SWING), as the break test
 *     found -- a real level effect should follow scarcity
 *   - monotone in depth: a deeper sweep should be worth more than a graze
 *   - the event rate must be small. If a sweep completes on a large share of
 *     bars, it is not an event, and this file's history (97.5% under a loose
 *     definition) is the reason the rate is printed first.
 *
 * MEASURED, XAUUSD 5m and 15m, 2016-2020 and 2021-2026, H=20 and H=6.
 * DAY-level sell-side sweep -> LONG, edge in pp over matched candles:
 *
 *     cell             H=20              H=6
 *     15m 2016-2020    +1.13 (z  1.0)    +6.32 (z  5.4)
 *     15m 2021-2026    -4.83 (z -5.1)    -3.46 (z -3.7)
 *     5m  2016-2020    +6.83 (z  5.6)    +4.15 (z  3.4)
 *     5m  2021-2026    +1.45 (z  1.4)    -0.77 (z -0.8)
 *
 * An era, not an edge: positive in 2016-2020, null or reversed in 2021-2026,
 * same cell same horizon opposite sign. Event rate 29-33% of bars. Depth orders
 * it correctly in one cell of eight. Buy-side -> SHORT replicates in 7 of 8 at
 * +0.6 to +2.25 pp -- the wrong side, and inside the friction gap. Nothing here
 * should be traded. Full account in README.md, "The other reading".
 */

import fs from 'node:fs';
import zlib from 'node:zlib';
import path from 'node:path';
import { detect as detectLiq, sweepAt, PDH, PDL, PSH, PSL,
         SWH, SWL, EQH, EQL } from '../js/chart/liquidity.js';
import { atrSeries } from '../js/chart/tlengine.js';

const TF = process.argv[2] || '15m';
const FROM = Date.parse((process.argv[3] || '2016-01-01') + 'T00:00:00Z');
const TO = Date.parse((process.argv[4] || '2021-01-01') + 'T00:00:00Z');
const H = Number(process.argv[5] || 20);
const WINDOW = Number(process.argv[6] || 5);       // sweepAt's look-back for the pierce
const GUARD = 5;
const SYM = 'XAUUSD.a';
const ROOT = path.join(path.dirname(new URL(import.meta.url).pathname.slice(1)), '..');

/* ------------------------------------------------------------------ bars -- */
function loadBarsEra(dir, t0, t1) {
  const y0 = new Date(t0).getUTCFullYear(), y1 = new Date(t1).getUTCFullYear();
  const rows = [];
  for (const f of fs.readdirSync(dir).filter((x) => x.endsWith('.csv.gz')).sort()) {
    const yr = Number(f.slice(0, 4));
    if (!(yr >= y0 && yr <= y1)) continue;
    const text = zlib.gunzipSync(fs.readFileSync(path.join(dir, f))).toString('utf8');
    const lines = text.split('\n');
    const ix = Object.fromEntries(lines[0].trim().split(',').map((k, i) => [k, i]));
    for (let i = 1; i < lines.length; i++) {
      const c = lines[i].trim().split(',');
      if (c.length < 5) continue;
      const t = Number(c[ix.ts]) * 1000;
      if (t < t0 || t >= t1) continue;
      rows.push({ t, o: +c[ix.open], h: +c[ix.high], l: +c[ix.low], c: +c[ix.close], v: 0 });
    }
  }
  rows.sort((a, b) => a.t - b.t);
  return rows;
}

/* ---------------------------------------------------------------- sweeps -- */
const FAMILY = {
  [PDH]: 'DAY', [PDL]: 'DAY',
  [PSH]: 'SESSION', [PSL]: 'SESSION',
  [SWH]: 'SWING', [SWL]: 'SWING',
  [EQH]: 'EQUAL', [EQL]: 'EQUAL',
};
const FAMILIES = ['DAY', 'SESSION', 'SWING', 'EQUAL'];

/* Verbatim from sweep_break_eval.mjs: per-bar DEEPEST completed sweep, by
   family and by the side of the level. `dn` = a level BELOW price was swept
   (sell-side liquidity taken, the bullish setup); `up` = a level above. */
function sweepFlags(bars, atr, levels) {
  const byBorn = new Map();
  for (const lv of levels) {
    if (!byBorn.has(lv.bornI)) byBorn.set(lv.bornI, []);
    byBorn.get(lv.bornI).push(lv);
  }
  const up = {}, dn = {};
  for (const fam of FAMILIES) {
    up[fam] = new Float32Array(bars.length);
    dn[fam] = new Float32Array(bars.length);
  }
  let live = [];
  for (let i = 0; i < bars.length; i++) {
    const born = byBorn.get(i);
    if (born) live.push(...born);
    if (live.length && (i & 31) === 0) live = live.filter((lv) => !(lv.diesI >= 0 && lv.diesI < i));
    for (const lv of live) {
      if (lv.diesI >= 0 && lv.diesI < i) continue;
      const s = sweepAt(bars, atr, lv, i, { window: WINDOW });
      if (!s) continue;
      const fam = FAMILY[lv.type];
      if (!fam) continue;
      const arr = (lv.side > 0 ? up : dn)[fam];
      if (s.depthAtr > arr[i]) arr[i] = s.depthAtr;
    }
  }
  return { up, dn };
}

/* --------------------------------------------------------------- scoring -- */
const quint = (v, e) => { let k = 0; while (k < e.length && v > e[k]) k++; return k; };
function continued(bars, i, dir) {
  const j = i + H;
  if (j >= bars.length) return null;
  return dir > 0 ? bars[j].c > bars[i].c : bars[j].c < bars[i].c;
}

function run(bars) {
  const atr = atrSeries(bars, 14);
  const liq = detectLiq(bars, {});
  const levels = liq.levels || liq;
  const { up, dn } = sweepFlags(bars, atr, levels);

  const body = [], range = [];
  for (let i = 0; i < bars.length; i++) {
    const a = atr[i];
    if (!(a > 0)) { body.push(NaN); range.push(NaN); continue; }
    body.push((bars[i].c - bars[i].o) / a);
    range.push((bars[i].h - bars[i].l) / a);
  }
  const edgesOf = (arr) => {
    const v = arr.filter(Number.isFinite).sort((x, y) => x - y);
    return [0.2, 0.4, 0.6, 0.8].map((q) => v[Math.floor(v.length * q)]);
  };
  const bE = edgesOf(body), rE = edgesOf(range);
  const bucketOf = (i, dir) => dir + '|' + quint(body[i], bE) + '|' + quint(range[i], rE);

  /* EVENTS: a sweep completing at bar i. A sell-side sweep (level below taken,
     `dn`) is the bullish setup -> dir +1; a buy-side sweep -> dir -1. Depth per
     family is carried so the cuts below can ask which family and how deep. */
  const events = [];
  for (let i = 14; i + H < bars.length; i++) {
    for (const [side, dir] of [[dn, 1], [up, -1]]) {
      const byFam = {};
      let depth = 0;
      for (const fam of FAMILIES) { byFam[fam] = side[fam][i]; if (side[fam][i] > depth) depth = side[fam][i]; }
      if (depth > 0) events.push({ i, dir, depth, byFam });
    }
  }

  /* CONTROL POOL: every bar not near an event, both directions, bucketed. */
  const near = new Set();
  for (const e of events) for (let k = e.i - GUARD; k <= e.i + GUARD; k++) near.add(k);
  const pool = new Map();
  for (let i = 14; i + H < bars.length; i++) {
    if (near.has(i) || !Number.isFinite(body[i])) continue;
    for (const dir of [1, -1]) {
      const c = continued(bars, i, dir);
      if (c === null) continue;
      const k = bucketOf(i, dir);
      let v = pool.get(k);
      if (!v) { v = [0, 0]; pool.set(k, v); }
      v[0] += c ? 1 : 0; v[1] += 1;
    }
  }

  const rows = [];
  for (const e of events) {
    const c = continued(bars, e.i, e.dir);
    if (c === null || !Number.isFinite(body[e.i])) continue;
    const b = pool.get(bucketOf(e.i, e.dir));
    if (!b || b[1] < 30) continue;
    rows.push({ ...e, hit: c ? 1 : 0, ctrl: b[0] / b[1] });
  }
  return { rows, nBars: bars.length, nLevels: levels.length, nEvents: events.length,
           poolBars: [...pool.values()].reduce((s, v) => s + v[1], 0) / 2 };
}

function score(rows) {
  if (!rows.length) return null;
  const n = rows.length;
  const hit = rows.reduce((s, x) => s + x.hit, 0) / n;
  const ctrl = rows.reduce((s, x) => s + x.ctrl, 0) / n;
  const se = Math.sqrt(Math.max(1e-9, ctrl * (1 - ctrl) / n));
  return { n, hit: 100 * hit, ctrl: 100 * ctrl, edge: 100 * (hit - ctrl), z: (hit - ctrl) / se };
}

/* ----------------------------------------------------------------- main --- */
const dir = path.join(ROOT, 'data', 'bars', SYM, TF);
const t0 = Date.now();
const bars = loadBarsEra(dir, FROM, TO);
const R = run(bars);
const secs = ((Date.now() - t0) / 1000).toFixed(1);

const COLS = [36, 7, 7, 7, 8, 7];
const row = (...c) => c.map((v, i) => (i ? String(v).padStart(COLS[i]) : String(v).padEnd(COLS[i]))).join(' ');
const f = (v, d = 2) => (Number.isFinite(v) ? v.toFixed(d) : '--');
const era = `${new Date(FROM).toISOString().slice(0, 10)} .. ${new Date(TO).toISOString().slice(0, 10)}`;

console.log(`SWEEP AS REVERSAL   ${SYM} ${TF}   ${era}   H=${H} bars   window=${WINDOW}   (${secs}s)`);
console.log(`bars ${R.nBars}   levels ${R.nLevels}   sweep events ${R.nEvents}   ` +
            `EVENT RATE ${(100 * R.nEvents / R.nBars).toFixed(2)}% of bars   control pool ${Math.round(R.poolBars)} bars`);
console.log('edge = reversal-direction hit rate minus the matched-candle control, in pp.');
console.log('');
console.log(row('cut', 'n', 'hit%', 'ctrl%', 'edge', 'z'));
console.log('-'.repeat(76));

const all = R.rows;
const CUTS = [
  ['ALL SWEEPS (reverse into the sweep)', () => true],
  ['  sell-side swept -> LONG', (x) => x.dir > 0],
  ['  buy-side swept  -> SHORT', (x) => x.dir < 0],
  ['BY LEVEL FAMILY', null],
  ['  DAY (PDH/PDL)', (x) => x.byFam.DAY > 0],
  ['  SESSION (PSH/PSL)', (x) => x.byFam.SESSION > 0],
  ['  EQUAL (EQH/EQL)', (x) => x.byFam.EQUAL > 0],
  ['  SWING', (x) => x.byFam.SWING > 0],
  ['  DAY only (no other family)', (x) => x.byFam.DAY > 0 && !x.byFam.SESSION && !x.byFam.SWING && !x.byFam.EQUAL],
  ['BY SWEEP DEPTH (deepest at the bar)', null],
  ['  depth 0 - 0.25 ATR', (x) => x.depth > 0 && x.depth < 0.25],
  ['  depth 0.25 - 0.5', (x) => x.depth >= 0.25 && x.depth < 0.5],
  ['  depth 0.5 - 1.0', (x) => x.depth >= 0.5 && x.depth < 1.0],
  ['  depth 1.0+', (x) => x.depth >= 1.0],
  ['DAY FAMILY, BY SIDE', null],
  ['  DAY sell-side -> LONG', (x) => x.byFam.DAY > 0 && x.dir > 0],
  ['  DAY buy-side  -> SHORT', (x) => x.byFam.DAY > 0 && x.dir < 0],
];
for (const [name, fn] of CUTS) {
  if (!fn) { console.log(name); continue; }
  const s = score(all.filter(fn));
  if (!s || s.n < 40) { console.log(row(name, s ? s.n : 0, 'thin')); continue; }
  console.log(row(name, s.n, f(s.hit, 1), f(s.ctrl, 1), f(s.edge), f(s.z)));
}
