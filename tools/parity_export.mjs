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
import { TrendlineEngine, TF_MS } from '../js/chart/tlengine.js';

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
