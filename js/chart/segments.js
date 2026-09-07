/* segments.js — the market's history as a sequence of EPISODES.
 *
 * A port of sim/tl/segments.py, compared segment-for-segment in
 * tests/test_segment_parity.py.
 *
 * regime.js answers "what is the market doing at bar i". This answers the
 * question an annotated chart answers: "this was a downward channel, then a
 * range, then an upward channel" — a sequence with boundaries. Boundaries are
 * the part a per-bar label cannot give you.
 *
 * Minimum length and hysteresis are what stop it producing confetti: a
 * three-bar excursion is noise inside the episode either side of it, and a
 * regime must hold for `confirmBars` before a new episode opens.
 */

import * as regime from './regime.js';
import { atrSeries } from './tlengine.js';

export const DEFAULT_SEGMENT_PARAMS = {
  minBars: 12,
  confirmBars: 3,
  maxSegments: 12,
};

const LABEL = {
  [regime.TRENDING_UP]: 'Uptrend',
  [regime.TRENDING_DOWN]: 'Downtrend',
  [regime.SIDEWAYS]: 'Range',
  [regime.TRANSITION]: 'Transition',
};

export class Segment {
  constructor(o) { Object.assign(this, o); }
  get label() { return LABEL[this.kind] || this.kind; }
}

/** Raw runs with hysteresis: a new run opens only once `confirm` bars agree. */
function runs(kinds, confirm) {
  const n = kinds.length;
  if (!n) return [];
  const out = [];
  let cur = kinds[0], start = 0, j = 1;
  while (j < n) {
    if (kinds[j] === cur) { j++; continue; }
    let k = j;
    while (k < n && k - j < confirm && kinds[k] === kinds[j]) k++;
    if (k - j >= confirm || k >= n) {
      out.push([cur, start, j - 1]);
      cur = kinds[j];
      start = j;
      j++;
    } else {
      j = k;
    }
  }
  out.push([cur, start, n - 1]);
  return out;
}

/** Merge short runs into the LONGER neighbour — the choice that changes least. */
function absorb(rs, minBars) {
  if (!rs.length) return [];
  let list = rs.map((r) => [...r]);
  let changed = true;
  while (changed && list.length > 1) {
    changed = false;
    for (let idx = 0; idx < list.length; idx++) {
      const [, i0, i1] = list[idx];
      if (i1 - i0 + 1 >= minBars) continue;
      const prevLen = idx > 0 ? list[idx - 1][2] - list[idx - 1][1] : -1;
      const nextLen = idx + 1 < list.length ? list[idx + 1][2] - list[idx + 1][1] : -1;
      if (prevLen < 0 && nextLen < 0) continue;
      if (prevLen >= nextLen) list[idx - 1][2] = i1;
      else list[idx + 1][1] = i0;
      list.splice(idx, 1);
      changed = true;
      break;
    }
  }
  const fused = [list[0]];
  for (const r of list.slice(1)) {
    if (r[0] === fused[fused.length - 1][0]) fused[fused.length - 1][2] = r[2];
    else fused.push(r);
  }
  return fused;
}

/** Episodes over `bars`, oldest first. */
export function build(bars, params = {}) {
  const p = { ...DEFAULT_SEGMENT_PARAMS, ...params };
  const n = bars.length;
  if (!n) return [];
  const r = regime.compute(bars);
  const atr = r.atr || atrSeries(bars, 14);
  const kinds = r.regime.slice(0, n);
  const list = absorb(runs(kinds, p.confirmBars), p.minBars);

  const out = list.map(([kind, i0, i1], k) => {
    let hi = -Infinity, lo = Infinity;
    for (let j = i0; j <= i1; j++) {
      if (bars[j].h > hi) hi = bars[j].h;
      if (bars[j].l < lo) lo = bars[j].l;
    }
    const a0 = Number.isFinite(atr[i0]) ? atr[i0] : 0;
    return new Segment({
      kind, i0, i1, t0: bars[i0].t, t1: bars[i1].t,
      bars: i1 - i0 + 1, high: hi, low: lo,
      retAtr: a0 ? (bars[i1].c - bars[i0].c) / a0 : 0,
      closed: k < list.length - 1,
    });
  });
  return out.slice(-p.maxSegments);
}
