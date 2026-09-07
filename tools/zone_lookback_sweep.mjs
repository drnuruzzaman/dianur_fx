/* ZONE LOOKBACK SWEEP -- how many bars should a zone remember?
 *
 *     node --max-old-space-size=8192 tools/zone_lookback_sweep.mjs [maxBars]
 *
 * WHY THIS EXISTS. `DEFAULT_ZONE_PARAMS.lookback` is 500, it is the same 500
 * on 5m and on 4h, and nothing in this project ever measured it. Every other
 * zone parameter in that object carries a comment defending its value; this
 * one does not.
 *
 * WHAT IS NOT MEASURED HERE, DELIBERATELY: whether a lookback predicts
 * direction. It does not, and that is already settled -- 234,552 approaches,
 * every geometry feature within ~1pp of base, walk-forward AUC 0.499.
 * Selecting a window on a directional score would be fitting the window to
 * noise, and the noise is 43% wide. So this emits DESCRIPTIVE statistics only:
 * how many zones, how wide, how far, how long they live. The question is
 * "which window draws a chart a human can read", which is about clutter and
 * stability, not about edge.
 *
 * ONE SET OF PIVOTS FOR EVERY ARM. `zones.js` takes a `pivotsFn` seam; it is
 * fed the SAME precomputed findPivots output at every lookback, so the arms
 * differ in the window and in nothing else. This is also what makes the sweep
 * affordable -- findPivots over the full array once per cell instead of once
 * per snapshot per arm. Causality is unaffected: `detect` filters pivots by
 * `q.i + strength <= i` itself, so a precomputed array cannot leak forward.
 *
 * EVERY ARM SEES THE SAME BARS. Snapshots start at max(LOOKBACKS) so the
 * 2000-bar arm is not scored on a shorter history than the 100-bar arm.
 *
 * THE CAP IS LIFTED FOR COUNTING. maxZones=6 would hide exactly the clutter
 * this is looking for, so zones are detected uncapped and `capped%` reports
 * how often the shipped cap would have bound. That column IS the clutter
 * measure: an arm that binds on most bars is one where the chart shows you an
 * arbitrary six out of many.
 */

import fs from 'node:fs';
import zlib from 'node:zlib';
import path from 'node:path';
import { detect as detectZones, DEFAULT_ZONE_PARAMS } from '../js/chart/zones.js';
import { findPivots } from '../js/chart/trendlines.js';
import { atrSeries } from '../js/chart/tlengine.js';

const MAX = Number(process.argv[2] || 40000);
const STEP = 20;                       // bars between snapshots
const LOOKBACKS = [100, 200, 300, 500, 750, 1000, 1500, 2000];
const ROOT = path.join(path.dirname(new URL(import.meta.url).pathname.slice(1)), '..');
const CELLS = [['XAUUSD.a', '5m'], ['XAUUSD.a', '15m'], ['XAUUSD.a', '1h'],
               ['XAUUSD.a', '4h'], ['EURUSD.a', '15m'], ['EURUSD.a', '1h']];

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

const median = (a) => {
  if (!a.length) return NaN;
  const s = [...a].sort((x, y) => x - y);
  const m = s.length >> 1;
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
};

/* Is this the same band as one from the previous snapshot? IDENTITY IS A
   SHARED PIVOT, not a price. The first version of this matched mids within a
   quarter ATR, which reports a band that gains a fourth touch -- and so moves
   its mid -- as one zone dying and another being born, inventing churn out of
   the thing zones are supposed to do. A zone IS its cluster of pivots, so two
   snapshots hold the same zone when they were built from at least one pivot in
   common. `levels` is already rounded identically on both sides. */
function sameZone(a, b) {
  for (const x of a.levels) if (b.levels.includes(x)) return true;
  return false;
}

function sweepCell(bars, tf, lookback, atr, pivotsFn) {
  const warm = Math.max(...LOOKBACKS);
  const counts = [], widths = [], dists = [], touches = [], strengths = [];
  const lives = [];                    // completed lifetimes, in bars
  let capped = 0, snaps = 0, born = 0;
  let live = [];                       // {z, since}

  for (let i = warm; i < bars.length; i += STEP) {
    const zs = detectZones(bars, i, tf, atr, {
      lookback, pivotsFn, maxZones: 999, minStrength: 0,
    });
    snaps++;
    counts.push(zs.length);
    // how often the SHIPPED filter would have had to throw a zone away
    const shippable = zs.filter((z) => z.strength >= DEFAULT_ZONE_PARAMS.minStrength);
    if (shippable.length > DEFAULT_ZONE_PARAMS.maxZones) capped++;

    const a = atr[i];
    const px = bars[i].c;
    for (const z of zs) {
      widths.push(z.widthAtr);
      dists.push(z.distanceAtr(px, a));
      touches.push(z.touches);
      strengths.push(z.strength);
    }

    // --- lifetime tracking ------------------------------------------------
    const next = [];
    const used = new Set();
    for (const prev of live) {
      let hit = -1;
      for (let k = 0; k < zs.length; k++) {
        if (used.has(k)) continue;
        if (sameZone(prev.z, zs[k])) { hit = k; break; }
      }
      if (hit >= 0) { used.add(hit); next.push({ z: zs[hit], since: prev.since }); }
      else lives.push((snaps - prev.since) * STEP);
    }
    for (let k = 0; k < zs.length; k++) {
      if (!used.has(k)) { next.push({ z: zs[k], since: snaps }); born++; }
    }
    live = next;
  }
  for (const p of live) lives.push((snaps - p.since) * STEP);

  const barsCovered = snaps * STEP;
  return {
    lookback,
    zones: median(counts),
    zmax: counts.length ? Math.max(...counts) : 0,
    capped: 100 * capped / snaps,
    per100: 100 * born / barsCovered,
    width: median(widths),
    dist: median(dists),
    touch: median(touches),
    strength: median(strengths),
    life: median(lives),
    lifeRatio: median(lives) / lookback,
  };
}

const fmt = (v, d = 2) => (Number.isFinite(v) ? v.toFixed(d) : '--');
/* node's console.log implements %s and %d but NOT a width, so `%-6s` prints
   literally. Rows are padded here instead. */
const COLS = [6, 6, 5, 8, 8, 7, 6, 6, 9, 7, 6];
const row = (...cells) => cells
  .map((c, i) => (i ? String(c).padStart(COLS[i]) : String(c).padEnd(COLS[i])))
  .join(' ');

console.log('ZONE LOOKBACK SWEEP  step=%d bars  warmup=%d  maxBars=%d',
            STEP, Math.max(...LOOKBACKS), MAX);
console.log('zones/max = visible bands BEFORE the maxZones cap.  capped%% = share of');
console.log('snapshots where the shipped cap of %d would have discarded one.',
            DEFAULT_ZONE_PARAMS.maxZones);
console.log('life = median bars a band stays on the chart; lf/lb = that as a fraction');
console.log('of the window (near 1.0 means bands die only by ageing out).');

for (const [sym, tf] of CELLS) {
  const dir = path.join(ROOT, 'data', 'bars', sym, tf);
  if (!fs.existsSync(dir)) { console.log('\n%s %s -- no bars', sym, tf); continue; }
  const bars = loadBars(dir, MAX);
  if (bars.length < Math.max(...LOOKBACKS) + 500) {
    console.log('\n%s %s -- only %d bars', sym, tf, bars.length);
    continue;
  }
  const atr = atrSeries(bars, 14);
  const pivots = findPivots(bars, DEFAULT_ZONE_PARAMS.strengthPivots);
  const pivotsFn = () => pivots;
  console.log('\n=== %s %s   %d bars   %d pivots ===',
              sym, tf, bars.length, pivots.highs.length + pivots.lows.length);
  console.log(row('lb', 'zones', 'max', 'capped%', 'new/100', 'width', 'dist',
                  'touch', 'strength', 'life', 'lf/lb'));
  console.log('-'.repeat(80));
  for (const lb of LOOKBACKS) {
    const r = sweepCell(bars, tf, lb, atr, pivotsFn);
    console.log(row(r.lookback, fmt(r.zones, 1), r.zmax, fmt(r.capped, 1),
                    fmt(r.per100, 2), fmt(r.width), fmt(r.dist, 1),
                    fmt(r.touch, 1), fmt(r.strength, 1), Math.round(r.life),
                    fmt(r.lifeRatio)));
  }
}
