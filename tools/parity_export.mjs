/**
 * parity_export.mjs — run the REAL browser modules under node and dump their
 * output, so the Python port is compared against the actual live-chart code
 * rather than against a second reading of the spec.
 *
 *   node tools/parity_export.mjs
 *
 * Writes tests/fixtures/expected_<fixture>.json. Committed, so the parity test
 * runs without node; regenerate whenever the JS changes on purpose — a diff in
 * the regenerated file is exactly the signal you want to see.
 */

import { readFileSync, writeFileSync, readdirSync } from 'node:fs';
import { INDICATORS, heikinAshi } from '../js/chart/indicators.js';
import { detectTrendlines, findPivots, SENSITIVITY } from '../js/chart/trendlines.js';
import { findDivergences, rsiSeries } from '../js/chart/divergence.js';
import { TrendlineEngine, TF_MS, atrSeries } from '../js/chart/tlengine.js';
import { classify as classifyStructure } from '../js/chart/structure.js';
import { compute as computeRegime } from '../js/chart/regime.js';
import { detect as detectChannels } from '../js/chart/channels.js';
import { detect as detectZones } from '../js/chart/zones.js';
import { build as buildSegments } from '../js/chart/segments.js';
import { calibrate as calibrateSens } from '../js/chart/sensitivity.js';
import { compute as computeSlope } from '../js/chart/slopelines.js';
import { detect as detectSD } from '../js/chart/supplydemand.js';
import { detect as detectMS } from '../js/chart/marketstructure.js';
import { swingPoints } from '../js/chart/structure.js';

const FIXTURES = 'tests/fixtures';

function readBars(path) {
  const lines = readFileSync(path, 'utf8').trim().split(/\r?\n/);
  const head = lines[0].split(',');
  const col = (n) => head.indexOf(n);
  const [ti, oi, hi, li, ci, vi] = ['t', 'open', 'high', 'low', 'close', 'tick_volume'].map(col);
  return lines.slice(1).map((line) => {
    const p = line.split(',');
    return {
      t: Date.parse(p[ti].includes('T') ? p[ti] : p[ti].replace(' ', 'T') + 'Z'),
      o: +p[oi], h: +p[hi], l: +p[li], c: +p[ci], v: +p[vi], ticks: +p[vi],
    };
  });
}

/** Studies are compared plot by plot; only numeric series are serialised. */
function studyOutputs(bars) {
  const out = {};
  for (const [kind, def] of Object.entries(INDICATORS)) {
    const plots = def.calc(bars, def.inputs);
    out[kind] = plots.map((p) => ({
      type: p.type, label: p.label ?? null,
      data: p.data ?? null, upper: p.upper ?? null, lower: p.lower ?? null,
      value: p.value ?? null,
    }));
  }
  return out;
}

/** Trendlines, with the function-valued fields dropped. */
function lineOutputs(bars) {
  const out = {};
  for (const [key, s] of Object.entries(SENSITIVITY)) {
    out[key] = detectTrendlines(bars, { ...s, maxLines: 5 }).map((l) => ({
      kind: l.kind, p1: l.p1, p2: l.p2, slope: l.slope,
      touches: l.touches, score: l.score, lastTouchT: l.lastTouchT,
    }));
  }
  return out;
}

/* Every line the lifecycle engine ever created, keyed so the Python run can be
   matched against it: role + both anchor indexes identify a line independently
   of the id sequence, which legitimately differs between runs. */
function tlEngineOutput(bars, tf) {
  const eng = new TrendlineEngine(tf, TF_MS[tf], {});
  const snaps = eng.walk(bars);
  const seen = new Map();
  for (const s of snaps) {
    for (const l of s.live) if (!seen.has(l.id)) seen.set(l.id, l);
  }
  const lines = [...seen.values()].map((l) => ({
    k: `${l.role}|${l.pivot1.i}|${l.pivot2.i}`,
    role: l.role, dir: l.direction, status: l.status,
    touches: l.touches, tests: l.tests, violations: l.violations,
    q: l.qualityScore, qBreak: l.qualityAtBreak, span: l.spanBars,
    created: l.createdAt, confirmed: l.confirmedAt, broken: l.brokenAt,
    archived: l.archivedAt, reason: l.archiveReason,
  }));
  lines.sort((a, b) => (a.k < b.k ? -1 : a.k > b.k ? 1 : 0));
  const last = snaps[snaps.length - 1];
  return {
    lines,
    final: {
      supportPx: last.supportPx, supportQ: last.supportQ,
      resistancePx: last.resistancePx, resistanceQ: last.resistanceQ,
      liveCount: last.liveCount,
    },
    breakBars: snaps.filter((s) => s.breaks.length)
                    .map((s) => [s.i, s.breaks.length]),
  };
}

for (const name of readdirSync(FIXTURES).filter((f) => f.startsWith('bars_') && f.endsWith('.csv'))) {
  const bars = readBars(`${FIXTURES}/${name}`);
  const stem = name.replace(/^bars_/, '').replace(/\.csv$/, '');
  const doc = {
    source: name,
    bars: bars.length,
    first_t: bars[0].t,
    last_t: bars[bars.length - 1].t,
    pivots: (() => {
      const o = {};
      for (const strength of [2, 3, 6]) {
        const p = findPivots(bars.slice(0, -1), strength);
        o[strength] = {
          highs: p.highs.map((x) => [x.i, x.t, x.price]),
          lows: p.lows.map((x) => [x.i, x.t, x.price]),
        };
      }
      return o;
    })(),
    studies: studyOutputs(bars),
    heikin: heikinAshi(bars).map((b) => [b.o, b.h, b.l, b.c]),
    trendlines: lineOutputs(bars),
    // divergence: the chart must only draw what the backtester would trade, so
    // the detected set is exported and compared against sim/divergence.py
    // the lifecycle engine, compared against sim/tl/engine.py
    tl_engine: tlEngineOutput(bars, stem.endsWith('_1h') ? '1h' : '15m'),
    // market structure + regime: the Trend read panel must say what the
    // Python engine would say, so both are exported bar by bar.
    structure: (() => {
      const r = classifyStructure(bars.slice(0, -1));
      return { high_label: r.highLabel, low_label: r.lowLabel, bias: r.bias,
               last_high: r.lastHigh, last_low: r.lastLow };
    })(),
    // channels: derived from the live line population at the last bar
    channels: (() => {
      const tf = stem.endsWith('_1h') ? '1h' : '15m';
      const b = bars.slice(0, -1);
      const eng = new TrendlineEngine(tf, TF_MS[tf], {});
      const snaps = eng.walk(b);
      const last = snaps[snaps.length - 1];
      const live = last.live.filter((l) => l.isTradeable);
      const atr = atrSeries(b, 14);
      return detectChannels(live, b, atr, b.length - 1, tf).map((c) => ({
        kind: c.kind, type: c.type, direction: c.direction,
        lower_id: c.lower.id, upper_id: c.upper.id,
        slope: c.slope, t_start: c.tStart, t_end: c.tEnd,
        width_atr: c.widthAtr, containment: c.containment,
        touches_lower: c.touchesLower, touches_upper: c.touchesUpper,
        bars: c.bars, quality_score: c.qualityScore,
        projected_side: c.projectedSide,
        lower_now: c.lowerAt(b[b.length - 1].t),
        upper_now: c.upperAt(b[b.length - 1].t),
        median_now: c.medianAt(b[b.length - 1].t),
      }));
    })(),
    zones: (() => {
      const tf = stem.endsWith('_1h') ? '1h' : '15m';
      const b = bars.slice(0, -1);
      return detectZones(b, b.length - 1, tf, atrSeries(b, 14)).map((z) => ({
        low: z.low, high: z.high, mid: z.mid, touches: z.touches,
        from_highs: z.fromHighs, from_lows: z.fromLows,
        first_t: z.firstT, last_t: z.lastT, first_i: z.firstI, last_i: z.lastI,
        width_atr: z.widthAtr, strength: z.strength, levels: z.levels,
        reaction_atr: z.reactionAtr,
        role_now: z.roleAt(b[b.length - 1].c),
      }));
    })(),
    segments: (() => {
      const b = bars.slice(0, -1);
      return buildSegments(b).map((s) => ({
        kind: s.kind, label: s.label, i0: s.i0, i1: s.i1,
        t0: s.t0, t1: s.t1, bars: s.bars,
        high: s.high, low: s.low, ret_atr: s.retAtr, closed: s.closed,
      }));
    })(),
    sensitivity: (() => {
      const tf = stem.endsWith('_1h') ? '1h' : '15m';
      const b = bars.slice(0, -1);
      const cut = Math.max(300, Math.floor(0.2 * b.length));
      const s = calibrateSens(b, tf, stem, {}, cut);
      const full = calibrateSens(b, tf, stem);
      const eng = new TrendlineEngine(tf, TF_MS[tf], {}, full);
      const snaps = eng.walk(b);
      const last = snaps[snaps.length - 1];
      return {
        cut,
        causal: {
          vol_regime: s.volRegime, atr_pct: s.atrPct,
          strength: s.support.strength,
          prom_sup: s.support.minProminenceAtr,
          prom_res: s.resistance.minProminenceAtr,
          tol_sup: s.support.tolAtr, tol_res: s.resistance.tolAtr,
          q_sup: s.support.minQuality, q_res: s.resistance.minQuality,
          n_pivots: s.nPivots,
        },
        full: {
          vol_regime: full.volRegime, strength: full.support.strength,
          prom_sup: full.support.minProminenceAtr,
          prom_res: full.resistance.minProminenceAtr,
        },
        // the calibrated engine's own output, so a drift in EITHER the
        // calibration or how the engine consumes it fails a test
        walk: {
          live_count: last.liveCount,
          support_px: last.supportPx, resistance_px: last.resistancePx,
          support_q: last.supportQ, resistance_q: last.resistanceQ,
          break_bars: snaps.filter((x) => x.breaks.length)
                           .map((x) => [x.i, x.breaks.length]),
        },
      };
    })(),
    slope_lines: (() => {
      const b = bars.slice(0, -1);
      const out = {};
      for (const m of ['atr', 'stdev', 'linreg']) {
        const r = computeSlope(b, { method: m });
        out[m] = {
          upper: r.upper, lower: r.lower,
          slope_up: r.slopeUp, slope_dn: r.slopeDn,
          break_up: r.breakUp.map((x, i) => (x ? i : -1)).filter((x) => x >= 0),
          break_dn: r.breakDn.map((x, i) => (x ? i : -1)).filter((x) => x >= 0),
        };
      }
      // the look-ahead variant, so the parity test can prove the two differ
      const bp = computeSlope(b, { method: 'atr', backpaint: true });
      out.atr_backpaint = { upper: bp.upper, lower: bp.lower };
      return out;
    })(),
    sd_zones: (() => {
      const tf = stem.endsWith('_1h') ? '1h' : '15m';
      const b = bars.slice(0, -1);
      return detectSD(b, tf, atrSeries(b, 14)).map((z) => ({
        kind: z.kind, low: z.low, high: z.high, mid: z.mid,
        base_i0: z.baseI0, base_i1: z.baseI1, impulse_i1: z.impulseI1,
        confirmed_i: z.confirmedI, t_base: z.tBase, t_confirmed: z.tConfirmed,
        impulse_atr: z.impulseAtr, base_bars: z.baseBars,
        width_atr: z.widthAtr, touches: z.touches, fresh: z.fresh,
        strength: z.strength,
      }));
    })(),
    // the reclaim lifecycle, exercised with the feature ON so the parity test
    // compares a path the defaults never touch
    reclaim: (() => {
      const tf = stem.endsWith('_1h') ? '1h' : '15m';
      // FULL bars, matching tlEngineOutput and run_python in the tl_parity
      // tests. Slicing the last bar off here cost a one-bar offset that showed
      // up as every quality score differing by ~0.1 while every status matched.
      const b = bars;
      const eng = new TrendlineEngine(tf, TF_MS[tf],
        { reclaimConfirmBars: 3, breakConfirmBars: 2, minTouches: 3 });
      const snaps = eng.walk(b);
      const last = snaps[snaps.length - 1];
      const counts = {};
      for (const l of last.live) counts[l.status] = (counts[l.status] || 0) + 1;
      return {
        live_count: last.liveCount,
        tradeable: last.live.filter((l) => l.isTradeable).length,
        status_counts: counts,
        /* Keyed by the line's anchors, not by position: the two engines build
           snap.live by iterating roles in their own order, and that ordering
           is not part of the contract. */
        byline: last.live.map((l) => ({
          k: `${l.role}|${l.pivot1.i}|${l.pivot2.i}`,
          status: l.status, reclaims: l.reclaims || 0,
          violations: l.violations, quality: l.qualityScore,
        })).sort((a, b) => (a.k < b.k ? -1 : a.k > b.k ? 1 : 0)),
      };
    })(),
    market_structure: (() => {
      const b = bars.slice(0, -1);
      const r = detectMS(b);
      return {
        events: r.events.map((e) => ({
          kind: e.kind, direction: e.direction, i: e.i, t: e.t,
          level: e.level, level_i: e.levelI,
          bias_before: e.biasBefore, bias_after: e.biasAfter, close: e.close,
        })),
        bias: r.bias, swing_high: r.swingHigh, swing_low: r.swingLow,
        event: r.event, event_dir: r.eventDir,
      };
    })(),
    // ATR-threshold swings: `Swing Threshold = ATR(n) x sensitivity`. Exported
    // at several sensitivities because a size filter agrees trivially at 0,
    // where it does nothing, and can diverge the moment it removes anything.
    swings_atr: (() => {
      const b = bars.slice(0, -1);
      const out = {};
      for (const s of [0, 0.5, 1, 1.5, 2.5]) {
        out[String(s)] = swingPoints(b, { strength: 3, closeConfirm: false,
                                          atrSensitivity: s }).map((x) => x.i);
      }
      return out;
    })(),
    regime: (() => {
      const r = computeRegime(bars.slice(0, -1));
      return { regime: r.regime, direction: r.direction,
               range_pos: r.rangePos, energy: r.energy, ema_sep_atr: r.emaSepAtr };
    })(),
    // hidden divergence, opt-in: the default `divergences` list above must stay
    // byte-identical, so this is exported as its own field
    divergences_hidden: findDivergences(bars, { includeHidden: true }).map((d) => ({
      kind: d.kind, pivotI: d.pivotI, prevI: d.prevI, confirmedI: d.confirmedI,
      price: d.price, prevPrice: d.prevPrice, rsi: d.rsi, prevRsi: d.prevRsi,
      rsiGap: d.rsiGap, barsApart: d.barsApart,
    })),
    divergences: findDivergences(bars).map((d) => ({
      kind: d.kind, pivotI: d.pivotI, prevI: d.prevI, confirmedI: d.confirmedI,
      price: d.price, prevPrice: d.prevPrice, rsi: d.rsi, prevRsi: d.prevRsi,
      rsiGap: d.rsiGap, barsApart: d.barsApart,
    })),
  };
  const out = `${FIXTURES}/expected_${stem}.json`;
  writeFileSync(out, JSON.stringify(doc));
  console.log(`${out}  bars=${bars.length}  ` +
              `pivots(3)=${doc.pivots[3].highs.length}H/${doc.pivots[3].lows.length}L  ` +
              `lines(normal)=${doc.trendlines.normal.length}  ` +
              `divergences=${doc.divergences.length}  ` +
              `tl_lines=${doc.tl_engine.lines.length}`);
}
