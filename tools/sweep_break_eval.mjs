/* DOES A LIQUIDITY SWEEP BEFORE A BREAK ADD ANYTHING?
 *
 *     node --max-old-space-size=8192 tools/sweep_break_eval.mjs [maxBars] [horizon]
 *
 * THE CLAIM UNDER TEST, in the form it is usually taught:
 *
 *     sell-side sweep -> rejection -> displacement -> CHoCH
 *
 * and the assertion that this is "considerably more meaningful than a random
 * structure break". Three of its four ingredients are already in this project
 * and already measured -- the break requires a CLOSE beyond the level (never a
 * wick), `dispAtr` grades how hard it broke, and the swing that was broken is
 * graded by ring tier. The sweep is the one part never tested here, so it is
 * the only thing this file adds.
 *
 * THE SWEEP DEFINITION IS THE STRICT ONE, and it is doing the real work.
 * `liquidity.js sweepAt` requires a ROUND TRIP: price trades through a level
 * and then CLOSES back on the side it came from. A bar that pierces and closes
 * beyond is a BREAK, not a sweep. That file records why the distinction is not
 * pedantry -- under a loose definition "sweep" fired on 97.5% of bars, and a
 * condition that is nearly always true predicts nothing. Any positive result
 * here belongs to the strict definition and does not transfer to a looser one.
 *
 * SIDE MATTERS. A bullish break is set up by a SELL-side sweep -- price dips
 * under a low, takes the stops, and reclaims -- so this looks for a completed
 * sweep of a level on the side OPPOSITE the break's direction, inside the last
 * `WINDOW` bars. A sweep on the same side as the break is a different story and
 * is not counted as one.
 *
 * CAUSALITY. `sweepAt` resolves BACKWARD by construction: the pierce sits in
 * the past and the reclaim is the scored bar's own close, so nothing here knows
 * a sweep completed before it did. Levels carry `bornI`, and a level is only
 * offered to `sweepAt` from the bar it was born.
 *
 * THE CONTROL is the same matched-candle scheme as tools/break_arm_eval.mjs:
 * every bar bucketed by direction and by body/ATR and range/ATR quintile, with
 * events and their neighbourhoods held out of the pool, each event scored
 * against its own bucket. Breaks fire on big directional candles and so do
 * sweeps; a raw rate would credit structure for momentum twice over.
 */

import fs from 'node:fs';
import zlib from 'node:zlib';
import path from 'node:path';
import { detect as detectMS, BULL } from '../js/chart/marketstructure.js';
import { detect as detectLiq, sweepAt, PDH, PDL, PSH, PSL,
         SWH, SWL, EQH, EQL } from '../js/chart/liquidity.js';
import { nestedSwings, RING_FROM } from '../js/chart/structure.js';
import { atrSeries } from '../js/chart/tlengine.js';

const MAX = Number(process.argv[2] || 60000);
const H = Number(process.argv[3] || 20);
const STRENGTH = 3;
/* HOW LONG AGO THE SWEEP MAY HAVE BEEN. An argument, not a constant, because
   10 bars is 50 minutes on 5m and 10 hours on 1h -- and a prev-day level swept
   before a CHoCH plausibly needs longer on a fast frame than a swing level
   does. A family result that only appears at one window is a window artefact,
   so both are run. */
const WINDOW = Number(process.argv[4] || 10);
const DISP = 1.0;                      // DISPLACEMENT_V1.displacement_atr
const GUARD = 5;
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
                  l: +c[ix.low], c: +c[ix.close], v: 0 });
    }
    if (rows.length >= max) break;
  }
  rows.sort((a, b) => a.t - b.t);
  return rows.length > max ? rows.slice(-max) : rows;
}

const K = (i, isHigh) => i + '|' + isHigh;
const quint = (v, e) => { let k = 0; while (k < e.length && v > e[k]) k++; return k; };

function continued(bars, i, dir) {
  const j = i + H;
  if (j >= bars.length) return null;
  return dir > 0 ? bars[j].c > bars[i].c : bars[j].c < bars[i].c;
}

/**
 * Per-bar sweep flags, by the side of the level swept.
 *
 * AN INTERVAL WALK, not a filter per bar. `levelsAt` rescans all ~19,000 levels
 * for every bar; only ~65 are alive at once, and they carry `bornI`/`diesI`, so
 * a live set advanced with the cursor turns an O(bars x levels) scan into
 * O(bars x live).
 */
/* FOUR FAMILIES, because "any level" was the flaw in the first version of this
   test. A prior sweep of ANY of the ~65 live levels sits before 42-71% of
   breaks -- an OR across 65 strict tests is a loose test -- and the fix is not a
   stricter per-level rule but a NARROWER QUESTION. These are the four kinds
   liquidity.js emits, and they differ by an order of magnitude in how often
   they exist: on 15m over 62k bars there are 661 prev-day highs against 5,169
   swing highs. A day level is scarce by construction -- two per day -- so a
   sweep of one is a far more specific event than a sweep of something. */
const FAMILY = {
  [PDH]: 'DAY', [PDL]: 'DAY',
  [PSH]: 'SESSION', [PSL]: 'SESSION',
  [SWH]: 'SWING', [SWL]: 'SWING',
  [EQH]: 'EQUAL', [EQL]: 'EQUAL',
};
const FAMILIES = ['DAY', 'SESSION', 'SWING', 'EQUAL'];

function sweepFlags(bars, atr, levels) {
  const byBorn = new Map();
  for (const lv of levels) {
    if (!byBorn.has(lv.bornI)) byBorn.set(lv.bornI, []);
    byBorn.get(lv.bornI).push(lv);
  }
  /* DEPTH, NOT A FLAG. A boolean "was anything swept" is an OR across the ~65
     levels alive at once, and with that many chances it fires before most
     breaks -- measured at 42-71% by cell, which is close enough to always to
     carry little information whatever the per-level definition says. Keeping
     the DEEPEST sweep lets the question "is a deep sweep worth more than a
     graze" be answered instead of assumed, which is what liquidity.js emits
     `depthAtr` unthresholded for. */
  const up = {}, dn = {};                     // family -> deepest sweep depth, ATR
  for (const fam of FAMILIES) {
    up[fam] = new Float32Array(bars.length);  // a BUY-side level (above) was swept
    dn[fam] = new Float32Array(bars.length);  // a SELL-side level (below) was swept
  }
  let live = [];
  for (let i = 0; i < bars.length; i++) {
    const born = byBorn.get(i);
    if (born) live.push(...born);
    if (live.length && (i & 31) === 0) {
      live = live.filter((lv) => !(lv.diesI >= 0 && lv.diesI < i));
    }
    for (const lv of live) {
      if (lv.diesI >= 0 && lv.diesI < i) continue;
      const s = sweepAt(bars, atr, lv, i);
      if (!s) continue;
      const fam = FAMILY[lv.type];
      if (!fam) continue;
      const arr = (lv.side > 0 ? up : dn)[fam];
      if (s.depthAtr > arr[i]) arr[i] = s.depthAtr;
    }
  }
  return { up, dn };
}

function cell(bars) {
  const atr = atrSeries(bars, 14);
  const r = detectMS(bars, { strength: STRENGTH });
  const ringed = new Set(nestedSwings(bars, { sens: 'normal' })
    .filter((s) => (s.rank || 0) >= RING_FROM.normal).map((s) => K(s.i, s.isHigh)));
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

  const near = new Set();
  for (const e of r.events) for (let k = e.i - GUARD; k <= e.i + GUARD; k++) near.add(k);

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
  for (const e of r.events) {
    const isHigh = e.direction === BULL;
    const dir = isHigh ? 1 : -1;
    const c = continued(bars, e.i, dir);
    if (c === null || !Number.isFinite(body[e.i])) continue;
    const b = pool.get(bucketOf(e.i, dir));
    if (!b || b[1] < 30) continue;
    /* OPPOSITE SIDE: a bullish break wants a sell-side sweep under it. */
    const side = dir > 0 ? dn : up;
    const byFam = {};
    let depth = 0;
    for (const fam of FAMILIES) {
      let d = 0;
      const arr = side[fam];
      for (let j = Math.max(0, e.i - WINDOW); j <= e.i; j++) if (arr[j] > d) d = arr[j];
      byFam[fam] = d;
      if (d > depth) depth = d;
    }
    const swept = depth > 0;
    const a = atr[e.i];
    const disp = (a > 0) ? Math.abs(bars[e.i].c - e.level) / a : NaN;
    rows.push({
      i: e.i, hit: c ? 1 : 0, ctrl: b[0] / b[1],
      swept, sweepDepth: depth, byFam, disp, choch: e.kind === 'choch',
      ringed: ringed.has(K(e.levelI, isHigh)),
    });
  }
  return rows;
}

function score(rows) {
  if (!rows.length) return null;
  const n = rows.length;
  const hit = rows.reduce((s, x) => s + x.hit, 0) / n;
  const ctrl = rows.reduce((s, x) => s + x.ctrl, 0) / n;
  const se = Math.sqrt(Math.max(1e-9, ctrl * (1 - ctrl) / n));
  return { n, hit: 100 * hit, ctrl: 100 * ctrl, edge: 100 * (hit - ctrl),
           z: (hit - ctrl) / se };
}

const COLS = [34, 7, 7, 7, 8, 7];
const row = (...c) => c.map((v, i) => (i ? String(v).padStart(COLS[i]) : String(v).padEnd(COLS[i]))).join(' ');
const f = (v, d = 2) => (Number.isFinite(v) ? v.toFixed(d) : '--');

const all = [];
const perCell = [];
for (const [sym, tf] of CELLS) {
  const dir = path.join(ROOT, 'data', 'bars', sym, tf);
  if (!fs.existsSync(dir)) continue;
  const bars = loadBars(dir, MAX);
  if (bars.length < 5000) continue;
  const rows = cell(bars);
  perCell.push([sym.replace('.a', '') + ' ' + tf, rows, Math.floor(bars.length / 2)]);
  all.push(...rows);
  process.stderr.write(`${sym} ${tf}: ${rows.length} breaks, ` +
    `${rows.filter((x) => x.swept).length} with a prior opposite-side sweep\n`);
}

console.log('SWEEP BEFORE BREAK   horizon %d bars   window %d   maxBars %d', H, WINDOW, MAX);
console.log('edge = continuation rate minus the arm\'s own matched-candle control.');
console.log('');

const CUTS = [
  ['ALL BREAKS', () => true],
  ['  no prior sweep', (x) => !x.swept],
  ['  prior opposite-side sweep', (x) => x.swept],
  ['CHoCH ONLY', (x) => x.choch],
  ['  CHoCH, no sweep', (x) => x.choch && !x.swept],
  ['  CHoCH + sweep', (x) => x.choch && x.swept],
  ['THE TAUGHT SEQUENCE', null],
  ['  sweep + disp>=1 + CHoCH', (x) => x.swept && x.disp >= DISP && x.choch],
  ['  same, but no sweep', (x) => !x.swept && x.disp >= DISP && x.choch],
  ['  sweep + disp>=1 (any kind)', (x) => x.swept && x.disp >= DISP],
  ['BY SWEEP DEPTH (all breaks)', null],
  ['  no sweep', (x) => !x.swept],
  ['  depth 0 - 0.25 ATR', (x) => x.sweepDepth > 0 && x.sweepDepth < 0.25],
  ['  depth 0.25 - 0.5', (x) => x.sweepDepth >= 0.25 && x.sweepDepth < 0.5],
  ['  depth 0.5 - 1.0', (x) => x.sweepDepth >= 0.5 && x.sweepDepth < 1.0],
  ['  depth 1.0+', (x) => x.sweepDepth >= 1.0],
  ['BY LEVEL FAMILY (all breaks)', null],
  ['  DAY swept (PDH/PDL)', (x) => x.byFam.DAY > 0],
  ['  DAY not swept', (x) => x.byFam.DAY === 0],
  ['  SESSION swept (PSH/PSL)', (x) => x.byFam.SESSION > 0],
  ['  SESSION not swept', (x) => x.byFam.SESSION === 0],
  ['  SWING swept', (x) => x.byFam.SWING > 0],
  ['  EQUAL swept (EQH/EQL)', (x) => x.byFam.EQUAL > 0],
  ['BY FAMILY, CHoCH ONLY', null],
  ['  CHoCH + DAY sweep', (x) => x.choch && x.byFam.DAY > 0],
  ['  CHoCH, no DAY sweep', (x) => x.choch && x.byFam.DAY === 0],
  ['  CHoCH + SESSION sweep', (x) => x.choch && x.byFam.SESSION > 0],
  ['  CHoCH, no SESSION sweep', (x) => x.choch && x.byFam.SESSION === 0],
  ['  CHoCH + DAY + disp>=1', (x) => x.choch && x.byFam.DAY > 0 && x.disp >= DISP],
  ['RING CROSSED WITH SWEEP', null],
  ['  ringed + sweep', (x) => x.ringed && x.swept],
  ['  ringed, no sweep', (x) => x.ringed && !x.swept],
  ['  not ringed + sweep', (x) => !x.ringed && x.swept],
];

console.log(row('cut', 'n', 'hit%', 'ctrl%', 'edge', 'z'));
console.log('-'.repeat(74));
for (const [name, fn] of CUTS) {
  if (!fn) { console.log(name); continue; }
  const s = score(all.filter(fn));
  if (!s || s.n < 40) { console.log(row(name, s ? s.n : 0, 'thin')); continue; }
  console.log(row(name, s.n, f(s.hit, 1), f(s.ctrl, 1), f(s.edge), f(s.z)));
}

console.log('');
console.log('PER CELL AND PER ERA -- DAY sweep (PDH/PDL) vs none, on CHoCH');
console.log(row('cell', 'n+sw', 'edge+', 'n-sw', 'edge-', 'eras+'));
console.log('-'.repeat(74));
for (const [name, rows, mid] of perCell) {
  const sw = rows.filter((x) => x.choch && x.byFam.DAY > 0);
  const no = rows.filter((x) => x.choch && x.byFam.DAY === 0);
  const a = score(sw), b = score(no);
  const e1 = score(sw.filter((x) => x.i < mid)), e2 = score(sw.filter((x) => x.i >= mid));
  console.log(row(name, a ? a.n : 0, a ? f(a.edge) : '--', b ? b.n : 0,
                  b ? f(b.edge) : '--',
                  (e1 ? f(e1.edge, 1) : '--') + ' / ' + (e2 ? f(e2.edge, 1) : '--')));
}
