/* DO RINGED BREAKS BEAT EXTERNAL ONES? -- matched-candle continuation.
 *
 *     node --max-old-space-size=8192 tools/break_arm_eval.mjs [maxBars] [horizon]
 *
 * THE QUESTION. tools/external_ring_agree.mjs established that `external` and
 * `ringed` are NESTED, not parallel: ~80% of ringed breaks are external, only
 * ~18% of external breaks are ringed, kappa ~0.20 in all eight cells. So the
 * ring is roughly 4x the stricter test. Stricter is not the same as better, and
 * the chart currently weights `external` (x1.0 vs x0.7) while the ring carries
 * no weight in this layer at all. This asks whether that is the wrong way round.
 *
 * THREE DISJOINT ARMS, because the labels are nested and overlapping arms would
 * double-count the events that matter most:
 *
 *     RINGED    the broken swing is a ZigZag turn at ring tier
 *     EXT-ONLY  external -- the strength-6 pass broke it too -- but not ringed
 *     NEITHER   everything else
 *
 * THE OUTCOME IS CONTINUATION: did price close further in the break's direction
 * `H` bars later? Not an R multiple. A break is a claim about direction, not a
 * trade -- it has no entry, stop or size -- and scoring it as a trade would be
 * measuring a stop policy this file did not choose.
 *
 * WHY A MATCHED CANDLE CONTROL AND NOT A RAW RATE. Breaks fire on big
 * directional candles by construction, and big directional candles continue
 * more often than average whether or not anything broke. Scoring the raw rate
 * credits structure for momentum -- the earlier BOS/CHoCH work found exactly
 * that gap, raw +0.6 to +3.5 pp against matched +3.0 to +5.6.
 *
 * THE CONTROL, PRECISELY. Every bar is bucketed by (candle direction, signed
 * body/ATR quintile, range/ATR quintile). A bar within +/-5 of any event is
 * excluded from the pool so the control cannot contain the events themselves or
 * their immediate neighbourhood. Each event is then scored against the
 * continuation rate of ITS OWN bucket, and an arm's control is the mean of its
 * events' bucket rates -- so the control carries the same candle-shape mix as
 * the arm and differs only in whether a structure break happened.
 *
 * TWO ERAS, always, on this project's standing rule: a result that appears in
 * one half and not the other is not a result. The split is by bar index rather
 * than by date because the cells reach back different distances.
 *
 * WHAT WOULD CHANGE THE CHART. Only a ringed edge that beats ext-only in BOTH
 * eras across most cells. Anything less leaves the current weighting alone --
 * `external` is not being defended here, it is simply what already ships, and
 * replacing it on a one-era result would be the mistake this file is built to
 * avoid.
 */

import fs from 'node:fs';
import zlib from 'node:zlib';
import path from 'node:path';
import { detect as detectMS, BULL } from '../js/chart/marketstructure.js';
import { nestedSwings, RING_FROM } from '../js/chart/structure.js';
import { atrSeries } from '../js/chart/tlengine.js';
import { SENSITIVITY } from '../js/chart/trendlines.js';

const MAX = Number(process.argv[2] || 120000);
const H = Number(process.argv[3] || 20);
const STRENGTH = 3;
const MAJOR_STRENGTH = SENSITIVITY.major.strength;
const GUARD = 5;                       // bars around an event excluded from the pool
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
const quint = (v, edges) => {
  let k = 0;
  while (k < edges.length && v > edges[k]) k++;
  return k;
};

/** Did price close further in `dir` H bars on? null when the window runs out. */
function continued(bars, i, dir) {
  const j = i + H;
  if (j >= bars.length) return null;
  const d = bars[j].c - bars[i].c;
  return dir > 0 ? d > 0 : d < 0;
}

function cell(bars) {
  const atr = atrSeries(bars, 14);
  const r = detectMS(bars, { strength: STRENGTH });
  const major = detectMS(bars, { strength: MAJOR_STRENGTH });
  const majorLevels = new Set(major.events.map((e) => e.levelI));
  const ringed = new Set(nestedSwings(bars, { sens: 'normal' })
    .filter((s) => (s.rank || 0) >= RING_FROM.normal)
    .map((s) => K(s.i, s.isHigh)));

  /* Shape features for every usable bar, and the quintile edges taken from the
     WHOLE series so an arm cannot get its own scale. */
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
  const bucketOf = (i, dir) =>
    dir + '|' + quint(body[i], bE) + '|' + quint(range[i], rE);

  const near = new Set();
  for (const e of r.events) {
    for (let k = e.i - GUARD; k <= e.i + GUARD; k++) near.add(k);
  }

  /* The control pool: bucket -> [continuations], for BOTH directions, since a
     break can be bullish or bearish and each needs its own base rate. */
  const pool = new Map();
  for (let i = 14; i + H < bars.length; i++) {
    if (near.has(i) || !Number.isFinite(body[i])) continue;
    for (const dir of [1, -1]) {
      const c = continued(bars, i, dir);
      if (c === null) continue;
      const k = bucketOf(i, dir);
      let v = pool.get(k);
      if (!v) { v = [0, 0]; pool.set(k, v); }
      v[0] += c ? 1 : 0;
      v[1] += 1;
    }
  }

  const arms = { RINGED: [], 'EXT-ONLY': [], NEITHER: [] };
  for (const e of r.events) {
    const isHigh = e.direction === BULL;
    const dir = isHigh ? 1 : -1;
    const c = continued(bars, e.i, dir);
    if (c === null || !Number.isFinite(body[e.i])) continue;
    const b = pool.get(bucketOf(e.i, dir));
    if (!b || b[1] < 30) continue;             // too thin a control to trust
    const arm = ringed.has(K(e.levelI, isHigh)) ? 'RINGED'
      : (majorLevels.has(e.levelI) ? 'EXT-ONLY' : 'NEITHER');
    /* DISPLACEMENT, the obvious alternative explanation. The ring is a 3-ATR
       LEG test, so a ringed swing tends to sit at the end of a big move and its
       break tends to clear the level by more -- and clearing by 1.0 ATR is
       already the threshold behind DISPLACEMENT_V1. If the ring's edge is just
       displacement wearing a different name, the arms will differ in `disp` and
       the edge will vanish once the arms are cut to the same band. Same formula
       as js/main.js: |close - level| in ATR at the breaking bar. */
    const av = atr[e.i];
    const disp = (av > 0) ? Math.abs(bars[e.i].c - e.level) / av : NaN;
    arms[arm].push({ i: e.i, hit: c ? 1 : 0, ctrl: b[0] / b[1], disp });
  }
  return arms;
}

/** Edge in percentage points, with a normal-approximation z on the difference. */
function score(rows) {
  if (!rows.length) return null;
  const n = rows.length;
  const hit = rows.reduce((s, x) => s + x.hit, 0) / n;
  const ctrl = rows.reduce((s, x) => s + x.ctrl, 0) / n;
  /* The control is a mean of bucket rates, so its own sampling error is small
     against the arm's; the z below treats it as known and is therefore a touch
     optimistic. It is reported as a rough guide, not as a p-value. */
  const se = Math.sqrt(Math.max(1e-9, ctrl * (1 - ctrl) / n));
  const ds = rows.map((x) => x.disp).filter(Number.isFinite).sort((a, b) => a - b);
  return { n, hit: 100 * hit, ctrl: 100 * ctrl, edge: 100 * (hit - ctrl),
           z: (hit - ctrl) / se,
           disp: ds.length ? ds[ds.length >> 1] : NaN };
}

const COLS = [13, 10, 6, 7, 7, 7, 7, 7];
const row = (...c) => c.map((v, i) => (i ? String(v).padStart(COLS[i]) : String(v).padEnd(COLS[i]))).join(' ');
const f = (v, d = 1) => (Number.isFinite(v) ? v.toFixed(d) : '--');

console.log('BREAK ARMS vs MATCHED CANDLES   horizon %d bars   maxBars %d', H, MAX);
console.log('edge = arm continuation rate minus its own matched-candle control.');
console.log('Arms are DISJOINT: RINGED first, then external-but-not-ringed, then the rest.');
console.log('');
console.log(row('cell', 'arm', 'n', 'hit%', 'ctrl%', 'edge', 'z', 'disp'));
console.log('-'.repeat(70));

const totals = {};
for (const [sym, tf] of CELLS) {
  const dir = path.join(ROOT, 'data', 'bars', sym, tf);
  if (!fs.existsSync(dir)) continue;
  const bars = loadBars(dir, MAX);
  if (bars.length < 5000) continue;
  const arms = cell(bars);
  const mid = Math.floor(bars.length / 2);
  const name = sym.replace('.a', '') + ' ' + tf;
  for (const arm of ['RINGED', 'EXT-ONLY', 'NEITHER']) {
    const all = arms[arm];
    const s = score(all);
    if (!s) continue;
    const e1 = score(all.filter((x) => x.i < mid));
    const e2 = score(all.filter((x) => x.i >= mid));
    console.log(row(name, arm, s.n, f(s.hit), f(s.ctrl), f(s.edge, 2), f(s.z, 2),
                    f(s.disp, 2))
      + '   eras ' + (e1 ? f(e1.edge, 2) : '--') + ' / ' + (e2 ? f(e2.edge, 2) : '--'));
    (totals[arm] = totals[arm] || []).push(...all);
    name === name;
  }
  console.log('-'.repeat(62));
}

console.log('');
console.log('POOLED across cells (cells are not independent -- read the per-cell rows first)');
console.log(row('', 'arm', 'n', 'hit%', 'ctrl%', 'edge', 'z', 'disp'));
for (const arm of ['RINGED', 'EXT-ONLY', 'NEITHER']) {
  const s = score(totals[arm] || []);
  if (s) console.log(row('', arm, s.n, f(s.hit), f(s.ctrl), f(s.edge, 2), f(s.z, 2),
                         f(s.disp, 2)));
}

/* THE DISPLACEMENT CONTROL. Cut every arm to the SAME displacement band and
   re-score. If the ring only wins because ringed breaks clear their level by
   more, the arms converge here; if it survives, the ring is carrying something
   `dispAtr` does not. */
console.log('');
console.log('SAME-DISPLACEMENT CUT -- every arm restricted to one dispAtr band');
console.log(row('', 'arm', 'n', 'hit%', 'ctrl%', 'edge', 'z', 'disp'));
for (const [lo, hi] of [[0, 0.25], [0.25, 0.5], [0.5, 1.0], [1.0, 99]]) {
  console.log('  disp ' + lo + ' - ' + hi);
  for (const arm of ['RINGED', 'EXT-ONLY', 'NEITHER']) {
    const sel = (totals[arm] || []).filter((x) => x.disp >= lo && x.disp < hi);
    const s = score(sel);
    if (s && s.n >= 60) {
      console.log(row('', '  ' + arm, s.n, f(s.hit), f(s.ctrl), f(s.edge, 2),
                      f(s.z, 2), f(s.disp, 2)));
    } else {
      console.log(row('', '  ' + arm, sel.length, 'thin'));
    }
  }
}
