/* PHASE 1 — the canonical approach dataset. One row per zone approach.
 *
 *     node --max-old-space-size=8192 tools/approach_dataset.mjs [maxBars]
 *
 * WHY ONE TABLE. Phases 5-17 are all questions about the same event: price
 * arriving at a level. Answering each by re-deriving zones, liquidity, regime
 * and events from bars means five tools that agree only by luck, and it is how
 * the earlier one-off scripts ended up with the broker-clock bug in two of
 * them and not the third. This builds the row once; every later phase is a
 * query over the file.
 *
 * NO VERDICTS IN HERE. It emits features and outcomes and computes no rates,
 * no ratios between arms and no significance. That is deliberate: the outcome
 * definition has to be fixed before anything is measured (phase 7), and a tool
 * that both defines and scores invites tuning one against the other.
 *
 * THE OUTCOME, DECIDED BEFORE LOOKING (phase 7)
 * ---------------------------------------------
 *   rejection       price enters the band and leaves the way it came, travelling
 *                   >= MOVE_ATR from the entry edge, without ever CLOSING past
 *                   the far edge first.
 *   breakout        a close beyond the far edge, then >= MOVE_ATR further in
 *                   that direction.
 *   failed_breakout a close beyond the far edge, then price returns inside the
 *                   band and leaves the ORIGINAL way by >= MOVE_ATR.
 *   neutral         none of the above inside the horizon.
 *
 * MOVE_ATR = 0.5 is written here, once, before any result was read. Half an
 * ATR is roughly the smallest move that is not noise on these instruments and
 * it is the same threshold for every arm, so nothing about it can favour one.
 *
 * FOUR HORIZONS (phase 8), because a level that turns price for five bars and
 * one that ends a leg are different claims and a single horizon cannot tell
 * them apart. Every outcome is emitted at 5, 10, 20 and 40 bars.
 *
 * BOTH PIVOT DEFINITIONS (phases 2-3) in the same table, as `pivot_definition`,
 * with clusterAtr / minTouches / maxWidthAtr identical between them. The zone
 * engine is the same object either way -- `zones.js` takes a `pivotsFn` seam
 * whose default is its own call -- so the arms differ in the pivots and in
 * nothing else.
 *
 * CAUSALITY. Features are computed from bars[0..i] where i is the approach bar;
 * outcomes read bars after it and are prefixed `y_`. Nothing named without that
 * prefix may look forward.
 */

import fs from 'node:fs';
import zlib from 'node:zlib';
import path from 'node:path';
import { detect as detectZones } from '../js/chart/zones.js';
import { findPivots } from '../js/chart/trendlines.js';
import { compute as computeLiq, levelsAt, sessionOf } from '../js/chart/liquidity.js';
import { compute as computeRegime } from '../js/chart/regime.js';
import { atrSeries } from '../js/chart/tlengine.js';
import { zigzag, SWING_TIERS } from '../js/chart/structure.js';

const MAX = Number(process.argv[2] || 60000);
const STEP = 20;                         // recompute zones every N bars
const HORIZONS = [5, 10, 20, 40];
const MOVE_ATR = 0.5;                    // fixed before any result was read
const ROOT = path.join(path.dirname(new URL(import.meta.url).pathname.slice(1)), '..');
const OUT = path.join(ROOT, 'data', 'research', 'approaches.jsonl');
const CELLS = [['XAUUSD.a', '5m'], ['XAUUSD.a', '15m'], ['XAUUSD.a', '1h'],
               ['XAUUSD.a', '4h']];

/* THE HIGHER FRAME EACH CELL IS CHECKED AGAINST.
 *
 * The zone renderer refuses to PROJECT a 4h band onto a 15m chart, and that
 * argument is sound: a zone is horizontal, so a 4h band and a 15m band at one
 * price are the same band and drawing both double-counts a level. It justifies
 * not DRAWING it twice. It never justified refusing to KNOW.
 *
 * `htf_zone_present` asks the question the argument left open: when price
 * arrives at a 15m level, is that level also a level on 4h? Nothing in this
 * project has measured it, and it is the one S/R idea from the original plan
 * that was never tested.
 */
const HTF = { '5m': '1h', '15m': '4h', '1h': '4h', '4h': '1d' };
/* How often the higher frame's zones are recomputed, in HTF bars. 1 is exact
   and affordable here because the HTF series are small -- 1d is ~7k bars
   against 150k of 5m -- and a stride would make the feature stale by up to
   that many HTF bars, which on 1d is weeks. */
const HTF_STEP = 1;
const TF_MS = { '1m': 60e3, '5m': 300e3, '15m': 900e3, '30m': 1800e3,
                '1h': 3600e3, '4h': 14400e3, '1d': 86400e3, '1w': 604800e3 };

/* UTC -> the broker's server clock. The stored bars are server time and the
   calendar is UTC; joining them raw reads the hour BEFORE each release. Same
   correction as tools/_brokerclock.py, restated here because this file must
   not depend on the Python side. */
function eventOffsetMs(t) {
  const d = new Date(t);
  const y = d.getUTCFullYear();
  const lastSun = (m) => { const x = new Date(Date.UTC(y, m + 1, 0));
    x.setUTCDate(x.getUTCDate() - ((x.getUTCDay() + 7) % 7)); x.setUTCHours(1); return x.getTime(); };
  return (t >= lastSun(2) && t < lastSun(9) ? 3 : 2) * 3600e3;
}

function loadBars(dir, max) {
  const rows = [];
  for (const f of fs.readdirSync(dir).filter((x) => x.endsWith('.csv.gz')).sort().reverse()) {
    const text = zlib.gunzipSync(fs.readFileSync(path.join(dir, f))).toString('utf8');
    const lines = text.split('\n');
    const head = lines[0].trim().split(',');
    const ix = Object.fromEntries(head.map((k, i) => [k, i]));
    const vk = ix.volume !== undefined ? ix.volume : ix.tick_volume;
    for (let i = 1; i < lines.length; i++) {
      const c = lines[i].trim().split(',');
      if (c.length < 5) continue;
      rows.push({ t: Number(c[ix.ts]) * 1000, o: +c[ix.open], h: +c[ix.high],
                  l: +c[ix.low], c: +c[ix.close], v: vk !== undefined ? +c[vk] : 0 });
    }
    if (rows.length >= max) break;
  }
  rows.sort((a, b) => a.t - b.t);
  return rows.length > max ? rows.slice(-max) : rows;
}

function zigzagPivots(atrLen) {
  return (bars) => {
    const highs = [], lows = [];
    for (const z of zigzag(bars, { atrLen, atrMult: SWING_TIERS[1] })) {
      (z.isHigh ? highs : lows).push({ i: z.i, t: bars[z.i].t, price: z.price });
    }
    return { highs, lows };
  };
}

/**
 * How many times has price already traversed this band, and how long ago?
 *
 * WHY THIS IS NOT AN AGE QUESTION. Every other lifetime field here counts bars
 * -- `zone_age_bars`, `zone_recency_bars` -- and the ageing tables say bars do
 * not matter: a 500-bar-old band resolves within 2.6pp of a fresh one. A BREAK
 * is a different event. It is the only thing that can happen to a level that
 * says the level stopped working, and until now the table had no column for it.
 * A band price has cut through four times and a band price has never crossed
 * are the same row in this dataset, which is a gap in the features rather than
 * a finding about them.
 *
 * DEFINITION: a confirmed traverse. Price must CLOSE beyond one edge and later
 * CLOSE beyond the opposite edge; bars inside the band leave the state alone,
 * so oscillating inside a wide zone cannot manufacture breaks. The first side
 * price is seen on is not a break -- it is where price happened to be.
 *
 * CAUSAL: reads bars[z.firstI .. i-1] only. The approach bar is an arrival and
 * has not resolved into anything yet.
 */
function breaks(bars, z, i) {
  let side = null, n = 0, last = null;
  for (let j = Math.max(0, z.firstI); j < i; j++) {
    const c = bars[j].c;
    const s = c > z.high ? 'above' : (c < z.low ? 'below' : null);
    if (s === null) continue;
    if (side !== null && s !== side) { n++; last = j; }
    side = s;
  }
  return { n, last };
}

/**
 * Resolve one approach over `hz` bars.
 *
 * `fromAbove` is how price arrived, so "the way it came" and "the far edge"
 * are both defined from it rather than from the zone's role, which can flip.
 */
function outcome(bars, i, low, high, atrAt, hz, fromAbove) {
  const far = fromAbove ? low : high;
  const near = fromAbove ? high : low;
  const move = MOVE_ATR * atrAt;
  let closedBeyond = false, cameBack = false;
  const end = Math.min(bars.length - 1, i + hz);
  for (let j = i + 1; j <= end; j++) {
    const b = bars[j];
    if (!closedBeyond) {
      const beyond = fromAbove ? b.c < far : b.c > far;
      if (beyond) { closedBeyond = true; continue; }
      // rejection: back out the way it came, far enough
      const away = fromAbove ? b.h - near : near - b.l;
      const backOut = fromAbove ? b.c > high : b.c < low;
      if (backOut && away >= move) return 'rejection';
    } else {
      const back = b.c >= low && b.c <= high;
      if (back) { cameBack = true; continue; }
      if (cameBack) {
        const orig = fromAbove ? b.c > high : b.c < low;
        const away = fromAbove ? b.h - near : near - b.l;
        if (orig && away >= move) return 'failed_breakout';
      }
      const on = fromAbove ? far - b.l : b.h - far;
      if (!cameBack && on >= move) return 'breakout';
    }
  }
  return 'neutral';
}

/**
 * THE WIDTH-FREE OUTCOME. A symmetric barrier race from the approach bar.
 *
 * WHY THE EDGE-BASED ONE HAD TO BE REPLACED AS PRIMARY. `outcome()` calls a
 * breakout only after a close beyond the FAR edge, which is one band-width
 * away, while a rejection only needs price to leave by the near edge, which is
 * underfoot. So a wider zone makes breakouts mechanically rarer -- measured,
 * rejection ran 48.1% in the narrowest width quintile and 65.0% in the widest,
 * a 17pp spread that is pure geometry. Every geometry feature in the table
 * inherited it, because touch_count and span correlate with width.
 *
 * This asks the question the location is supposed to answer instead: from the
 * bar price arrived, which way did it travel MOVE_ATR first? Both barriers sit
 * the same distance from the same point, so the band's size cannot tilt the
 * answer and zone geometry becomes a FEATURE rather than part of the label.
 *
 *   toward   price moved MOVE_ATR back the way it came, first
 *   through  price moved MOVE_ATR onward, first
 *   neither  the horizon ended with neither barrier touched
 *
 * Decided and written before any result from it was read.
 */
function barrier(bars, i, atrAt, hz, fromAbove) {
  const entry = bars[i].c;
  const move = MOVE_ATR * atrAt;
  const end = Math.min(bars.length - 1, i + hz);
  for (let j = i + 1; j <= end; j++) {
    const b = bars[j];
    // `toward` is up when price arrived from above; both are checked on the
    // same bar and a bar that touches both is called `neither`, because the
    // order inside a bar is not knowable from OHLC.
    const hitToward = fromAbove ? (b.h - entry) >= move : (entry - b.l) >= move;
    const hitThrough = fromAbove ? (entry - b.l) >= move : (b.h - entry) >= move;
    if (hitToward && hitThrough) return 'neither';
    if (hitToward) return 'toward';
    if (hitThrough) return 'through';
  }
  return 'neither';
}

function excursions(bars, i, hz, entry) {
  const end = Math.min(bars.length - 1, i + hz);
  let hi = -Infinity, lo = Infinity;
  for (let j = i + 1; j <= end; j++) { if (bars[j].h > hi) hi = bars[j].h; if (bars[j].l < lo) lo = bars[j].l; }
  return { up: hi - entry, down: entry - lo };
}

/**
 * Causal snapshots of the higher frame's zones.
 *
 * CAUSALITY IS THE WHOLE DIFFICULTY. An HTF bar stamped at time T is not USABLE
 * at time T -- it is still forming, and only closes one HTF interval later. So
 * each snapshot is keyed by the CLOSE time of the bar it was computed at, and a
 * lookup takes the last snapshot whose close is <= the approach's bar time. A
 * 15m approach at 09:05 sees the 4h zones as of the 08:00 bar's close, never
 * the 12:00 bar that contains it.
 *
 * Returns `at(t)` -- the zone array visible at time t, or [].
 */
function htfZoneIndex(bars, tf, tfMs) {
  const atr = atrSeries(bars, 14);
  const times = [];      // close time of the bar each snapshot was taken at
  const snaps = [];
  for (let i = 60; i < bars.length; i += HTF_STEP) {
    let zs;
    try { zs = detectZones(bars, i, tf, atr, {}); } catch { zs = []; }
    times.push(bars[i].t + tfMs);
    snaps.push(zs);
  }
  return function at(t) {
    if (!times.length || t < times[0]) return [];
    let lo = 0, hi = times.length - 1, best = -1;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      if (times[mid] <= t) { best = mid; lo = mid + 1; } else hi = mid - 1;
    }
    return best >= 0 ? snaps[best] : [];
  };
}

/**
 * Is this level also a level on the higher frame?
 *
 * FOUR FIELDS, because "present" alone answers too little. Whether an HTF band
 * sits anywhere near, how far it is, whether it OVERLAPS this zone's own band,
 * and how strong the nearest one is -- confluence and proximity are different
 * claims and pooling them into a boolean would make the flat result that is
 * likely here impossible to interpret.
 *
 * DISTANCE IS IN THE BASE FRAME'S ATR, not the higher frame's. The question is
 * what this approach can see, and a 4h ATR would make every 5m distance look
 * negligible.
 */
function htfFields(at, htfTf, t, price, zone, atrNow) {
  if (!at || !(atrNow > 0)) {
    return { htf_tf: htfTf || null, htf_zone_present: null,
             htf_zone_dist_atr: null, htf_zone_overlap: null,
             htf_zone_strength: null, htf_zone_count: null };
  }
  const zs = at(t);
  let best = null, bestD = Infinity, overlap = 0;
  for (const h of zs) {
    const d = h.contains(price) ? 0
      : (price < h.low ? h.low - price : price - h.high) / atrNow;
    if (d < bestD) { bestD = d; best = h; }
    if (h.low <= zone.high && zone.low <= h.high) overlap = 1;
  }
  return {
    htf_tf: htfTf,
    /* PRESENT means the HTF band actually contains the approach price. The
       looser "within half an ATR" reading is recoverable from the distance
       column; a threshold baked into a boolean is not. */
    htf_zone_present: best && bestD === 0 ? 1 : 0,
    htf_zone_dist_atr: best ? +bestD.toFixed(4) : null,
    htf_zone_overlap: overlap,
    htf_zone_strength: best ? best.strength : null,
    htf_zone_count: zs.length,
  };
}

/* ------------------------------------------------------------------ run --- */
const events = JSON.parse(fs.readFileSync(path.join(ROOT, 'data', 'calendar', 'history.json'), 'utf8'))
  .map((e) => ({ ...e, st: e.t + eventOffsetMs(e.t) }))
  .sort((a, b) => a.st - b.st);
let priorVol = {};
const impactFile = path.join(ROOT, 'data', 'research', 'event_impact.jsonl');
if (fs.existsSync(impactFile)) {
  for (const line of fs.readFileSync(impactFile, 'utf8').trim().split('\n')) {
    const r = JSON.parse(line);
    priorVol[r.t] = r.prior_vol_ratio;
  }
}

const out = fs.createWriteStream(OUT);
let written = 0;

for (const [sym, tf] of CELLS) {
  const dir = path.join(ROOT, 'data', 'bars', sym, tf);
  if (!fs.existsSync(dir)) continue;
  const bars = loadBars(dir, MAX);
  if (bars.length < 2000) continue;
  const atr = atrSeries(bars, 14);
  const reg = computeRegime(bars);
  const liq = computeLiq(bars, {}).levels;

  /* THE HIGHER FRAME, loaded and indexed once per cell. Absent is a legitimate
     answer, and a missing folder must not become a zero: `htfAt` stays null and
     every htf_* field is emitted as null. A null says "not asked"; a zero would
     say "asked, and no". */
  const htfTf = HTF[tf];
  const htfDir = htfTf ? path.join(ROOT, 'data', 'bars', sym, htfTf) : null;
  let htfAt = null;
  if (htfDir && fs.existsSync(htfDir)) {
    const hb = loadBars(htfDir, MAX);
    if (hb.length >= 200) {
      htfAt = htfZoneIndex(hb, htfTf, TF_MS[htfTf]);
      process.stderr.write(`  htf ${htfTf}: ${hb.length} bars\n`);
    }
  }
  process.stderr.write(`${sym} ${tf}: ${bars.length} bars\n`);

  for (const [defName, pivotsFn] of [['fixed', undefined], ['zigzag', zigzagPivots(14)]]) {
    for (let i = 600; i + 40 < bars.length; i += STEP) {
      let zones = [];
      try { zones = detectZones(bars, i, tf, atr, pivotsFn ? { pivotsFn } : {}); } catch { continue; }
      if (!zones.length) continue;
      const a = atr[i];
      if (!(a > 0)) continue;
      const price = bars[i].c;
      const liqNow = levelsAt(liq, i);

      for (let k = i + 1; k < Math.min(bars.length, i + STEP); k++) {
        const bar = bars[k];
        for (const z of zones) {
          const inside = bar.l <= z.high && bar.h >= z.low;
          const wasOut = bars[k - 1].c < z.low || bars[k - 1].c > z.high;
          if (!inside || !wasOut) continue;
          const fromAbove = bars[k - 1].c > z.high;

          const nearest = liqNow.reduce((best, l) => {
            const d = Math.abs(l.price - z.mid) / a;
            return (best === null || d < best.d) ? { d, l } : best;
          }, null);

          const bk = breaks(bars, z, k);
          const nextEv = events.find((e) => e.st >= bars[k].t);
          const lastEv = [...events].reverse().find((e) => e.st <= bars[k].t);
          const row = {
            // identity
            t: bar.t, symbol: sym, timeframe: tf, pivot_definition: defName,
            // zone geometry (phase 4)
            zone_low: z.low, zone_high: z.high, zone_mid: z.mid,
            zone_width_atr: z.widthAtr, touch_count: z.touches,
            touch_density: z.touches / Math.max(1, (z.lastI - z.firstI)),
            zone_age_bars: k - z.firstI, zone_span_bars: z.lastI - z.firstI,
            zone_recency_bars: k - z.lastI,
            reaction_atr: z.reactionAtr ?? null,
            break_count: bk.n,
            bars_since_break: bk.last === null ? null : k - bk.last,
            strength: z.strength,                 // phase 9 tests it, nothing uses it
            role: z.roleAt(price),
            ...htfFields(htfAt, htfTf, bar.t, bars[k - 1].c, z, a),
            distance_from_price_atr: z.distanceAtr(bars[k - 1].c, a),
            from_above: fromAbove,
            atr: a,
            // liquidity (phase 11)
            liq_count: liqNow.length,
            liq_nearest_atr: nearest ? nearest.d : null,
            liq_nearest_type: nearest ? nearest.l.type : null,
            liq_present: nearest ? (nearest.d <= 0.5 ? 1 : 0) : 0,
            // regime + session (phase 16)
            regime: reg.regime ? reg.regime[k] : null,
            regime_dir: reg.direction ? reg.direction[k] : null,
            ema_sep_atr: reg.emaSepAtr ? +reg.emaSepAtr[k].toFixed(3) : null,
            range_pos: reg.rangePos ? +reg.rangePos[k].toFixed(3) : null,
            session: sessionOf(bar.t),
            // events (phase 13)
            mins_since_event: lastEv ? Math.round((bar.t - lastEv.st) / 60000) : null,
            mins_to_event: nextEv ? Math.round((nextEv.st - bar.t) / 60000) : null,
            event_type: lastEv ? lastEv.kind : null,
            event_impact: lastEv ? (lastEv.impact || null) : null,
            prior_vol_ratio: lastEv ? (priorVol[lastEv.t] ?? null) : null,
          };
          for (const hz of HORIZONS) {
            // PRIMARY: width-free. `y_outcome_*` is kept beside it because the
            // earlier numbers were read off it and have to stay explicable,
            // but it is geometry-dependent and must not be used for feature
            // work -- see the note on barrier() above.
            row['y_first_' + hz] = barrier(bars, k, a, hz, fromAbove);
            row['y_outcome_' + hz] = outcome(bars, k, z.low, z.high, a, hz, fromAbove);
            const ex = excursions(bars, k, hz, bar.c);
            row['y_mfe_atr_' + hz] = +( (fromAbove ? ex.up : ex.down) / a ).toFixed(4);
            row['y_mae_atr_' + hz] = +( (fromAbove ? ex.down : ex.up) / a ).toFixed(4);
          }
          out.write(JSON.stringify(row) + '\n');
          written++;
        }
      }
    }
  }
}
out.end();
process.stderr.write(`\nwrote ${written} approaches -> ${OUT}\n`);
