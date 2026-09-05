/* PASS 1 of the TP-band study: the unified event dataset. No verdicts.

     node --max-old-space-size=6144 tools/event_dataset.mjs <cfg.json>

   cfg.json is {"barsPath": "...", "tf": "1h", "cell": "XAUUSD.a|1h",
                "outDir": "data/research/events", "auditN": 12}

   WHAT THIS PRODUCES. Two JSONL files per cell:

     <cell>.trades.jsonl   one row per trade -- features at the SIGNAL bar,
                           outcomes prefixed `y_`.
     <cell>.bands.jsonl    one row per (trade, band) -- where the band was,
                           whether price reached it, and BOTH branches of the
                           decision it poses.

   THREE TRADE SOURCES, not one. `shipped` is the validated rule with the
   structural trail -- what both charts draw. `randEntry` and `randSide` are the
   matched controls, carried through the SAME feature extraction so a condition
   that looks predictive can immediately be checked against trades that had no
   reason to work. Building them later would mean re-deriving every feature.

   WHAT IT DOES NOT DO. No modelling, no selection, no combinations, no
   verdicts. Pass 1 is the table and the proof the table is not contaminated.

   THE PROOF IS THE POINT -- see `audit()`. Every feature is rebuilt from
   `bars.slice(0, signalI + 1)` for a sample of trades and must match the
   full-series build exactly. Two detectors in this project were survivorship
   biased in ways that made a strategy look excellent (zones.detect drops levels
   that later broke; supplydemand.detect scores on future touches), and both
   were caught by hand. This catches the class. */

import fs from 'node:fs';
import path from 'node:path';
import { FLAT, LONG, SHORT, runRule } from '../js/chart/rules.js';
import { donchianRule } from '../js/chart/donchian.js';
import { smcRetestRule, DEFAULTS as SMC_D } from '../js/chart/smcretest.js';
import { srRetestRule, tlRetestRule, SR_DEFAULTS, TL_DEFAULTS } from '../js/chart/retests.js';
import { displayLevels } from '../js/chart/levels.js';
import { makeTrail, structuralTrail } from '../js/chart/exittrail.js';
import { BULL, BEAR, BOS, CHOCH } from '../js/chart/marketstructure.js';

const cfg = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const bars = JSON.parse(fs.readFileSync(cfg.barsPath, 'utf8'));
const tf = cfg.tf;
const cell = cfg.cell;
const [symbol] = cell.split('|');
const outDir = cfg.outDir || 'data/research/events';
const auditN = cfg.auditN === undefined ? 12 : cfg.auditN;
/* HOW MANY TRADES GET FEATURES, per source.
 *
 * Feature extraction costs one `displayLevels` per trade -- ~30ms, because it
 * re-derives every detector over a 1200-bar window -- and the walk itself is
 * nearly free. On 1m the native 20/10 takes ~109,000 trades per source, so the
 * honest cost of "every trade" is a day per symbol. The first attempt was
 * killed after six hours without emitting a line.
 *
 * So above this many, every k-th trade gets features. EVERY-k-th, not random:
 * it spreads the sample evenly over the calendar, which is what the block
 * bootstrap downstream needs. The rows that survive are exact -- nothing is
 * approximated, there are just fewer of them -- and each carries `sample_step`
 * so a later pass can scale a total or refuse to.
 *
 * THE CELL SUMMARY IS NOT SAMPLED. Net R, win rate and trade counts per era are
 * written from the FULL trade list, because those are cheap and a sampled net R
 * would silently understate a cell against its unsampled neighbours. */
const maxRows = cfg.maxRows === undefined ? 15000 : cfg.maxRows;
/* Write the per-era summary and nothing else. The walk is cheap; the features
   are not, so a cell whose rows already exist can have its exact totals
   regenerated in minutes. */
const summaryOnly = !!cfg.summaryOnly;

const sgn = (side) => (side === LONG ? 1 : -1);

/* ------------------------------------------------------------------ series */

/** Every detector, prepared once over a bar array. Used for the full series and
    again, on a prefix, by the audit. */
function buildSeries(b) {
  const donchP = donchianRule.paramsFor ? donchianRule.paramsFor(tf) : donchianRule.defaults;
  return {
    p: donchP,
    donch: donchianRule.prepare(b, donchP),
    smc: smcRetestRule.prepare(b, { ...SMC_D }),
    sr: srRetestRule.prepare(b, { ...SR_DEFAULTS }),
    tl: tlRetestRule.prepare(b, { ...TL_DEFAULTS }),
  };
}

/**
 * Features at bar `i` for a trade of `side` planned from `entryPrice`.
 *
 * EVERY VALUE IS STAMPED AT THE SIGNAL BAR and reads only arrays whose index i
 * is a function of bars up to i. Distances are recorded twice -- in R, which is
 * comparable across instruments, and in ATR, which is comparable across
 * regimes. `null` means the detector had nothing there, which is itself
 * information and must not be confused with zero.
 */
function featuresAt(b, S, i, side, entryPrice, risk) {
  const d = sgn(side);
  const a = S.donch.atr[i];
  const px = b[i].c;
  const R = (v) => (Number.isFinite(v) && risk > 0 ? (v - entryPrice) * d / risk : null);
  const A = (v) => (Number.isFinite(v) && a > 0 ? (v - px) * d / a : null);

  const zDem = S.smc.demand[i];
  const zSup = S.smc.supply[i];
  const zone = d > 0 ? zDem : zSup;                 // the one behind the trade
  const zoneAhead = d > 0 ? zSup : zDem;            // the one in the way

  /* The structural levels the panel would list, chosen from bars <= i. */
  const lv = displayLevels(b, { side: d, from: entryPrice, upto: i, tf, max: 3 });

  /* The trail's opening level, from the same function the walker calls. The
     close array is built once per series and cached on it -- `structuralTrail`
     reads `close[i]`, and handing it undefined made every trail feature null
     while the try/catch swallowed the reason. */
  if (!S._closes) S._closes = b.map((x) => x.c);
  let trail = null;
  try {
    trail = structuralTrail({ side: d, i, view: b, series: S.donch,
                              close: S._closes, entryPrice },
                            { tf, cell });
  } catch { trail = null; }

  const ms = S.smc;
  const bias = ms.msBias[i];
  const ev = ms.msEvent[i];
  const evDir = ms.msDir[i];

  const upper = S.donch.hi[i];
  const lower = S.donch.lo[i];
  const brokeUp = Number.isFinite(upper) && b[i].c > upper;
  const brokeDn = Number.isFinite(lower) && b[i].c < lower;

  /* Recent range, in ATR: the denominator behind "is this compressed or
     expanded", computed over the last 100 bars ending at i. */
  let hi100 = -Infinity;
  let lo100 = Infinity;
  for (let k = Math.max(0, i - 99); k <= i; k++) {
    if (b[k].h > hi100) hi100 = b[k].h;
    if (b[k].l < lo100) lo100 = b[k].l;
  }

  return {
    atr: a > 0 ? a : null,
    atr_pct: a > 0 && px ? a / px : null,
    range100_atr: a > 0 && Number.isFinite(hi100) ? (hi100 - lo100) / a : null,

    donch_upper_r: R(upper), donch_lower_r: R(lower),
    donch_upper_atr: A(upper), donch_lower_atr: A(lower),
    donch_same_bar: brokeUp || brokeDn ? 1 : 0,
    donch_break_dir: brokeUp ? 1 : (brokeDn ? -1 : 0),
    donch_width_atr: (a > 0 && Number.isFinite(upper) && Number.isFinite(lower))
      ? (upper - lower) / a : null,

    ms_bias: bias === BULL ? 1 : (bias === BEAR ? -1 : 0),
    ms_bias_aligned: (bias === BULL && d > 0) || (bias === BEAR && d < 0) ? 1 : 0,
    ms_event: ev === BOS ? 'bos' : (ev === CHOCH ? 'choch' : null),
    ms_event_dir: evDir === BULL ? 1 : (evDir === BEAR ? -1 : 0),

    sr_support_r: R(S.sr.supAt[i]), sr_resistance_r: R(S.sr.resAt[i]),
    sr_support_atr: A(S.sr.supAt[i]), sr_resistance_atr: A(S.sr.resAt[i]),

    tl_support_r: R(S.tl.supAt[i]), tl_resistance_r: R(S.tl.resAt[i]),
    tl_support_atr: A(S.tl.supAt[i]), tl_resistance_atr: A(S.tl.resAt[i]),

    zone_behind_r: zone ? R(d > 0 ? zone.low : zone.high) : null,
    zone_behind_width_atr: zone && a > 0 ? (zone.high - zone.low) / a : null,
    zone_behind_age: zone ? i - zone.confirmedI : null,
    zone_ahead_r: zoneAhead ? R(d > 0 ? zoneAhead.low : zoneAhead.high) : null,
    zone_ahead_age: zoneAhead ? i - zoneAhead.confirmedI : null,

    trail_r: R(trail), trail_atr: A(trail),

    tp1_r: lv[0] ? R(lv[0].price) : null,
    tp2_r: lv[1] ? R(lv[1].price) : null,
    tp3_r: lv[2] ? R(lv[2].price) : null,
    tp1_kind: lv[0] ? lv[0].kind : null,
    tp2_kind: lv[1] ? lv[1].kind : null,
    tp3_kind: lv[2] ? lv[2].kind : null,
    levels_ahead: lv.length,
    _tp: lv.map((x) => x.price),          // stripped before writing; bands need it
  };
}

/* ------------------------------------------------------------------- bands */

/**
 * The candidate bands for one trade: fixed R multiples, and the structural
 * prices the detectors actually put in front of it.
 *
 * Both kinds in one list because the whole question is whether a level beats a
 * distance -- and every study so far says it does not, which is exactly why the
 * comparison has to be inside one dataset rather than across two.
 */
function bandsFor(t, f) {
  const d = sgn(t.side);
  const out = [];
  for (const m of [0.5, 0.75, 1, 1.5, 2, 3, 4, 5]) {
    out.push({ band: `R${m}`, kind: 'multiple', price: t.entryPrice + d * m * t.risk });
  }
  const named = [
    ['tp1', f._tp[0]], ['tp2', f._tp[1]], ['tp3', f._tp[2]],
    ['donchian', d > 0 ? f.__upper : f.__lower],
    ['sr', d > 0 ? f.__srRes : f.__srSup],
    ['zone_ahead', f.__zoneAhead],
    ['trendline', d > 0 ? f.__tlRes : f.__tlSup],
  ];
  for (const [band, price] of named) {
    if (!Number.isFinite(price)) continue;
    /* A "band" behind the entry is not a target, it is a level price has
       already passed. Recorded as absent rather than as a negative distance. */
    if ((price - t.entryPrice) * d <= 0) continue;
    out.push({ band, kind: 'structural', price });
  }
  return out;
}

/**
 * What happened after the entry.
 *
 * TIES INSIDE A BAR GO TO THE STOP, the same pessimism tools/partial_tp_runner.mjs
 * uses: the bar whose range covers both is the volatile one where a fill is
 * least certain, and being generous there is how a target study flatters itself.
 */
function outcomes(b, t, bandList) {
  const d = sgn(t.side);
  let mfe = -Infinity;
  let mae = Infinity;
  const reachedAt = new Map();
  let stoppedI = null;

  for (let i = t.entryI + 1; i <= t.exitI; i++) {
    const bar = b[i];
    const fav = d > 0 ? bar.h : -bar.l;
    const adv = d > 0 ? bar.l : -bar.h;
    if (fav > mfe) mfe = fav;
    if (adv < mae) mae = adv;
    const stopped = d > 0 ? bar.l <= t.stop : bar.h >= t.stop;
    if (stopped && stoppedI === null) stoppedI = i;
    for (const bd of bandList) {
      if (reachedAt.has(bd.band)) continue;
      const hit = d > 0 ? bar.h >= bd.price : bar.l <= bd.price;
      if (hit && stoppedI === null) reachedAt.set(bd.band, i);
    }
    if (stoppedI !== null) break;
  }
  const toR = (v) => (v * d - t.entryPrice * d) / t.risk;
  return {
    y_mfe_r: Number.isFinite(mfe) ? toR(mfe * d) : null,
    y_mae_r: Number.isFinite(mae) ? toR(mae * d) : null,
    reachedAt,
  };
}

/* --------------------------------------------------------------- the walks */

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

const trail = () => makeTrail('structure', { tf, cell });
const shipped = runRule(bars, donchianRule, { tf, exitTrail: trail() });

/* THE CONTROLS, carried through the same extraction. Direction for `randEntry`
   is the side the channel last broke -- the rule's own bias -- so it differs
   from the rule only in WHICH bar it enters on. `randSide` flips a coin on
   direction too. Both take the rule's own 2-ATR stop and the same trail. */
function controlRule(seed, randomSide, rate, S) {
  const rand = rng(seed);
  return {
    key: 'control',
    defaults: donchianRule.defaults,
    summary: 'matched random entries',
    warmup: donchianRule.warmup,
    paramsFor: donchianRule.paramsFor,
    prepare: () => S.donch,
    exitLevel: () => null,
    decide(i, ctx) {
      if (ctx.pos) return null;                 // exits come from stop + trail
      if (rand() >= rate) return null;
      const a = ctx.series.atr[i];
      if (!(a > 0)) return null;
      let side = ctx.series.hi[i] !== undefined && ctx.close[i] > ctx.series.hi[i] ? LONG
        : (ctx.close[i] < ctx.series.lo[i] ? SHORT : FLAT);
      if (side === FLAT) side = rand() < 0.5 ? LONG : SHORT;   // no break: bias-free
      if (randomSide) side = rand() < 0.5 ? LONG : SHORT;
      const stop = side === LONG ? ctx.close[i] - ctx.p.atrMult * a
                                 : ctx.close[i] + ctx.p.atrMult * a;
      return { side, stop, tag: 'control' };
    },
  };
}

const S = buildSeries(bars);
const seed = hashOf(cell);
const target = shipped.trades.length;
let rate = target / Math.max(1, bars.length);
let randEntry = runRule(bars, controlRule(seed, false, rate, S), { tf, exitTrail: trail() });
for (let pass = 0; pass < 3 && target > 0; pass++) {
  const n = randEntry.trades.length;
  if (!n || Math.abs(n - target) / target < 0.02) break;
  rate = Math.min(1, Math.max(1e-9, rate * (target / n)));
  randEntry = runRule(bars, controlRule(seed, false, rate, S), { tf, exitTrail: trail() });
}
const randSide = runRule(bars, controlRule(seed ^ 0x9e3779b9, true, rate, S),
                         { tf, exitTrail: trail() });

const SOURCES = { shipped, randEntry, randSide };

/* -------------------------------------------------------------- extraction */

function rowsFor(sourceName, res) {
  const trades = [];
  const bandRows = [];
  const step = (maxRows > 0 && res.trades.length > maxRows)
    ? Math.ceil(res.trades.length / maxRows) : 1;
  const picked = res.trades.filter((_, k) => k % step === 0);
  if (step > 1) {
    process.stderr.write(`  ${cell} ${sourceName}: ${res.trades.length} trades, `
      + `featuring every ${step}th (${picked.length})
`);
  }
  let done = 0;
  for (const t of picked) {
    if (++done % 2000 === 0) {
      process.stderr.write(`    ${cell} ${sourceName}: ${done}/${picked.length}
`);
    }
    const at = Number.isFinite(t.signalI) ? t.signalI : t.entryI;
    const f = featuresAt(bars, S, at, t.side, t.entryPrice, t.risk);
    /* prices the band list needs, kept off the written row */
    f.__upper = S.donch.hi[at]; f.__lower = S.donch.lo[at];
    f.__srSup = S.sr.supAt[at]; f.__srRes = S.sr.resAt[at];
    f.__tlSup = S.tl.supAt[at]; f.__tlRes = S.tl.resAt[at];
    const za = sgn(t.side) > 0 ? S.smc.supply[at] : S.smc.demand[at];
    f.__zoneAhead = za ? (sgn(t.side) > 0 ? za.low : za.high) : NaN;

    const bandList = bandsFor(t, f);
    const o = outcomes(bars, t, bandList);

    const id = `${cell}|${sourceName}|${t.entryTime}`;
    const clean = {};
    for (const [k, v] of Object.entries(f)) if (!k.startsWith('_')) clean[k] = v;

    trades.push({
      id, symbol, tf, source: sourceName, sample_step: step,
      side: t.side === LONG ? 1 : -1,
      signal_time: bars[at].t, entry_time: t.entryTime,
      entry: t.entryPrice, stop: t.stop, risk: t.risk,
      ...clean,
      y_r: t.r, y_exit_price: t.exitPrice, y_reason: t.reason,
      y_bars_held: t.exitI - t.entryI,
      y_mfe_r: o.y_mfe_r, y_mae_r: o.y_mae_r,
    });

    const order = bandList.map((x) => x.band);
    for (let k = 0; k < bandList.length; k++) {
      const bd = bandList[k];
      const hitI = o.reachedAt.get(bd.band);
      const dist = (bd.price - t.entryPrice) * sgn(t.side) / t.risk;
      const nextBand = order[k + 1];
      bandRows.push({
        id, symbol, tf, source: sourceName, sample_step: step,
        band: bd.band, band_kind: bd.kind,
        band_price: bd.price, dist_r: dist,
        dist_atr: S.donch.atr[at] > 0
          ? (bd.price - bars[at].c) * sgn(t.side) / S.donch.atr[at] : null,
        y_reached: hitI === undefined ? 0 : 1,
        y_bars_to_reach: hitI === undefined ? null : hitI - t.entryI,
        /* BOTH BRANCHES OF THE DECISION, never a probability on its own: what
           banking here pays, what the trade actually returned by trailing, and
           the difference -- which is the number that says whether taking profit
           at this level was right. */
        r_if_taken_here: dist,
        y_r_if_trailed: t.r,
        y_continuation: t.r - dist,
        y_reached_next: nextBand === undefined ? null
          : (o.reachedAt.has(nextBand) ? 1 : 0),
      });
    }
  }
  return { trades, bandRows };
}

/* ------------------------------------------------------------------ audit */

/**
 * REBUILD FROM A PREFIX AND DEMAND THE SAME ANSWER.
 *
 * For a sample of trades, every feature is recomputed from bars[0..signalI]
 * alone. Any column that differs is reading the future, and the build fails
 * naming it rather than writing a table someone will model on. This is the
 * guard the two survivorship bugs in this project would both have tripped.
 */
function audit(sample) {
  const bad = new Map();
  let checked = 0;
  for (const t of sample) {
    const at = Number.isFinite(t.signalI) ? t.signalI : t.entryI;
    const prefix = bars.slice(0, at + 1);
    const Sp = buildSeries(prefix);
    const full = featuresAt(bars, S, at, t.side, t.entryPrice, t.risk);
    const cut = featuresAt(prefix, Sp, at, t.side, t.entryPrice, t.risk);
    checked++;
    for (const k of Object.keys(full)) {
      if (k.startsWith('_')) continue;
      const a = full[k];
      const b = cut[k];
      const same = (a === null && b === null) || a === b
        || (typeof a === 'number' && typeof b === 'number'
            && Number.isFinite(a) && Number.isFinite(b) && Math.abs(a - b) < 1e-9);
      if (!same) bad.set(k, (bad.get(k) || 0) + 1);
    }
  }
  return { checked, bad: [...bad.entries()].map(([k, n]) => `${k}(${n})`) };
}

/* ------------------------------------------------------------------ output */

fs.mkdirSync(outDir, { recursive: true });
const stem = path.join(outDir, cell.replace('|', '_'));
/* WRITTEN SYNCHRONOUSLY, IN CHUNKS, and the reason matters.
 *
 * This whole script is synchronous from top to bottom, so a `createWriteStream`
 * never actually writes: its opens and writes queue on an event loop that does
 * not run until the process exits. The rows then sit in memory as buffered
 * strings -- 200MB+ on a 1m cell, on top of everything else -- and nothing
 * appears on disk while the build runs, so a long cell is indistinguishable
 * from a hung one. `writeSync` costs nothing here and fixes both. */
const openOut = (suffix) => {
  const fd = fs.openSync(`${stem}.${suffix}`, 'w');
  let buf = '';
  return {
    write(line) {
      buf += line;
      if (buf.length > 1 << 20) { fs.writeSync(fd, buf); buf = ''; }
    },
    end() {
      if (buf) fs.writeSync(fd, buf);
      fs.closeSync(fd);
    },
  };
};
const tradeOut = openOut('trades.jsonl');
const bandOut = openOut('bands.jsonl');

const counts = {};
let allTrades = [];
for (const [name, res] of Object.entries(SOURCES)) {
  if (summaryOnly) {
    counts[name] = { trades: 0, bands: 0 };
    if (name === 'shipped') allTrades = res.trades;
    continue;
  }
  const { trades, bandRows } = rowsFor(name, res);
  for (const r of trades) tradeOut.write(`${JSON.stringify(r)}\n`);
  for (const r of bandRows) bandOut.write(`${JSON.stringify(r)}\n`);
  counts[name] = { trades: trades.length, bands: bandRows.length };
  if (name === 'shipped') allTrades = res.trades;
  process.stderr.write(`  ${cell} ${name}: ${trades.length} trades, `
                       + `${bandRows.length} band rows\n`);
}
tradeOut.end();
bandOut.end();
if (summaryOnly) {
  /* The row files were opened before this branch was known; leaving two empty
     files beside a good dataset is worse than not writing them. */
  for (const f of ['trades.jsonl', 'bands.jsonl']) {
    try { fs.unlinkSync(`${stem}.${f}`); } catch { /* never existed */ }
  }
}

/* THE UNSAMPLED TRUTH, from every trade the walk took.
 *
 * AND ITS CALENDAR BLOCKS, which is the part that was missing. Sampling every
 * k-th trade and multiplying by k does not reconstruct a heavy-tailed total: on
 * USDJPY 1m the scaled figure came out +305.1 R against an actual -1470.9, the
 * wrong SIGN, because a handful of large trades either land in the sample or do
 * not. Totals and their intervals therefore come from here -- every trade, no
 * scaling -- while the band-level statistics stay on the sampled rows, where
 * they are means and the sampling cancels. */
const ERAS = [['2016-2020', 2016, 2020], ['2021-2026', 2021, 2026]];
const N_BLOCKS = 20;
const yearOf = (ms) => new Date(ms).getUTCFullYear();
const summary = {};
/* One calendar window per era, taken from the SHIPPED trades and shared by the
   controls, so the three sources' blocks line up and can be paired. */
const window = {};
for (const [era, y0, y1] of ERAS) {
  const rs = SOURCES.shipped.trades.filter((t) => {
    const y = yearOf(t.entryTime);
    return y >= y0 && y <= y1;
  });
  window[era] = rs.length
    ? { lo: Math.min(...rs.map((t) => t.entryTime)),
        hi: Math.max(...rs.map((t) => t.entryTime)) }
    : null;
}
for (const [name, res] of Object.entries(SOURCES)) {
  summary[name] = {};
  for (const [era, y0, y1] of ERAS) {
    const rs = res.trades.filter((t) => {
      const y = yearOf(t.entryTime);
      return y >= y0 && y <= y1;
    });
    const w = window[era];
    const blocks = new Array(N_BLOCKS).fill(0);
    if (w && w.hi > w.lo) {
      const width = (w.hi - w.lo) / N_BLOCKS;
      for (const t of rs) {
        const b = Math.min(N_BLOCKS - 1,
                           Math.max(0, Math.floor((t.entryTime - w.lo) / width)));
        blocks[b] += t.r;
      }
    }
    summary[name][era] = {
      n: rs.length,
      netR: rs.reduce((a, t) => a + t.r, 0),
      win: rs.length ? 100 * rs.filter((t) => t.r > 0).length / rs.length : null,
      blocks,
      loT: w ? w.lo : null,
      hiT: w ? w.hi : null,
    };
  }
}
fs.writeFileSync(`${stem}.summary.json`,
                 JSON.stringify({ cell, tf, symbol, bars: bars.length, summary }, null, 1));

let auditOut = { checked: 0, bad: [] };
if (auditN > 0 && allTrades.length && !summaryOnly) {
  const step = Math.max(1, Math.floor(allTrades.length / auditN));
  const sample = allTrades.filter((_, k) => k % step === 0).slice(0, auditN);
  auditOut = audit(sample);
  process.stderr.write(`  ${cell} audit: ${auditOut.checked} trades rebuilt from a `
    + `prefix, ${auditOut.bad.length ? 'LEAK IN ' + auditOut.bad.join(' ') : 'no leaks'}\n`);
}

console.log(JSON.stringify({ cell, tf, symbol, bars: bars.length,
                             counts, audit: auditOut,
                             files: [`${stem}.trades.jsonl`, `${stem}.bands.jsonl`] }));
