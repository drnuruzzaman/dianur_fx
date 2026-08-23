/* tlengine.js — the trendline lifecycle engine, in the browser.
 *
 * A line-for-line port of sim/tl/engine.py + sim/tl/lines.py, so the chart and
 * the backtest run the SAME detector. Before this existed the page drew lines
 * from a batch scorer (trendlines.js) while the simulator used the incremental
 * lifecycle engine — two different algorithms, which meant the lines you could
 * see were not the lines the backtest traded. tests/test_tl_parity.py now
 * compares the two implementations line by line and fails on any drift.
 *
 * Because it is a port, this file follows the Python structure even where a more
 * idiomatic JS shape exists: same parameter defaults, same iteration order, same
 * scoring arithmetic, same archive reasons. Divergence between them is a bug in
 * one of the two, and the test cannot tell you which if they are written
 * differently for no reason.
 *
 * LOOK-AHEAD, FIRST CLASS: pivots carry `confirmedI = i + strength` and are only
 * admitted to the pool on that bar, and the walk is strictly forward — the state
 * at bar i only ever saw bars <= i. Snapshots freeze their scalars, because a
 * Trendline keeps mutating on later bars and reading `line.qualityScore`
 * afterwards would report a score from the future.
 */

import { findPivots } from './trendlines.js';

export const Status = {
  CANDIDATE: 'CANDIDATE', CONFIRMED: 'CONFIRMED', ACTIVE: 'ACTIVE',
  BROKEN: 'BROKEN', ARCHIVED: 'ARCHIVED',
};
export const Role = { SUPPORT: 'support', RESISTANCE: 'resistance' };
export const Direction = { UP: 'up', DOWN: 'down', HORIZONTAL: 'horizontal' };

/* A line moving less than this fraction of ATR per bar is flat enough to call
   horizontal: on gold 0.01/bar is noise, on USDJPY it is not. */
const HORIZONTAL_ATR_PER_BAR = 0.02;

export const TF_MS = {
  '1m': 60e3, '5m': 300e3, '15m': 900e3, '30m': 1800e3,
  '1h': 3600e3, '4h': 14400e3, '1d': 86400e3, '1w': 604800e3,
};

export const DEFAULT_PARAMS = {
  strength: 3,          // pivot fractal size
  window: 400,          // lookback for pairing pivots, in bars
  maxPivots: 26,        // most recent N per side (pairs grow as N^2)
  minSpan: 6,           // anchors at least this many bars apart
  tolAtr: 0.32,         // touch / break tolerance, in ATR
  minSwingAtr: 0,       // a pivot must stand out this far to count
  maxViolations: 0,     // closes beyond tolerance before BROKEN
  maxLive: 20,          // per role: a holding pool, not the offer list
  maxOffered: 4,        // per role, what consumers actually see
  archiveAfter: 40,     // bars after breaking before archiving
  maxDistanceAtr: 10,   // archive a line this far from price
  minQuality: 25,       // below this a line is not offered
};

const round2 = (v) => Math.round(v * 100) / 100;
const clamp = (v, lo, hi) => (v < lo ? lo : v > hi ? hi : v);

/* ---- ATR, mirroring sim/indicators.py (Wilder, SMA-seeded) ---- */
export function trueRange(bars) {
  const out = new Array(bars.length);
  out[0] = bars[0].h - bars[0].l;
  for (let i = 1; i < bars.length; i++) {
    const pc = bars[i - 1].c;
    out[i] = Math.max(bars[i].h - bars[i].l,
                      Math.abs(bars[i].h - pc), Math.abs(bars[i].l - pc));
  }
  return out;
}

export function atrSeries(bars, length = 14) {
  const tr = trueRange(bars);
  const out = new Array(tr.length).fill(NaN);
  if (tr.length < length) return out;
  let prev = 0;
  for (let j = 0; j < length; j++) prev += tr[j];
  prev /= length;
  out[length - 1] = prev;
  for (let i = length; i < tr.length; i++) {
    prev = (prev * (length - 1) + tr[i]) / length;
    out[i] = prev;
  }
  return out;
}

export function classifyDirection(slopePerMs, tfMs, atr) {
  if (!atr) return Direction.HORIZONTAL;
  const perBar = slopePerMs * tfMs;
  if (Math.abs(perBar) < HORIZONTAL_ATR_PER_BAR * atr) return Direction.HORIZONTAL;
  return perBar > 0 ? Direction.UP : Direction.DOWN;
}

/* ------------------------------------------------------------------ line ---- */
export class Trendline {
  constructor(o) {
    this.id = o.id;
    this.timeframe = o.timeframe;
    this.role = o.role;
    this.direction = o.direction;
    this.pivot1 = o.pivot1;              // {t, price, i}
    this.pivot2 = o.pivot2;
    this.slope = o.slope;                // price per millisecond
    this.intercept = o.intercept;
    this.createdAt = o.createdAt;
    this.status = Status.CANDIDATE;
    this.confirmedAt = null;
    this.lastTestAt = null;
    this.brokenAt = null;
    this.archivedAt = null;
    this.archiveReason = '';
    this.touches = 2;                    // the two anchors count
    this.tests = 0;
    this.violations = 0;
    this.qualityScore = 0;
    this.qualityAtBreak = null;
    this.ageBars = 0;
    this.spanBars = o.spanBars;
    this.atrAtCreation = o.atrAtCreation;
    this.lastPriceDistanceAtr = 0;
    this._lastTouchI = -10000;
  }

  valueAt(tMs) { return this.intercept + this.slope * (tMs - this.pivot1.t); }

  get type() {
    if (this.direction === Direction.HORIZONTAL) return `horizontal_${this.role}`;
    const rising = this.direction === Direction.UP;
    if (this.role === Role.SUPPORT) return rising ? 'rising_support' : 'falling_support';
    return rising ? 'rising_resistance' : 'falling_resistance';
  }

  get isLive() {
    return this.status === Status.CANDIDATE || this.status === Status.CONFIRMED
        || this.status === Status.ACTIVE;
  }

  /** Only a confirmed line is worth acting on; a candidate is a guess. */
  get isTradeable() {
    return this.status === Status.CONFIRMED || this.status === Status.ACTIVE;
  }

  registerTouch(tMs, barI, minGapBars) {
    if (barI - this._lastTouchI < minGapBars) return false;
    this._lastTouchI = barI;
    this.touches += 1;
    this.tests += 1;
    this.lastTestAt = tMs;
    if (this.status === Status.CANDIDATE && this.touches >= 3) {
      this.status = Status.CONFIRMED;
      this.confirmedAt = tMs;
    } else if (this.status === Status.CONFIRMED) {
      this.status = Status.ACTIVE;
    }
    return true;
  }

  registerViolation(tMs, maxViolations) {
    if (this.status === Status.BROKEN) return false;   // it does not break twice
    this.violations += 1;
    if (this.violations > maxViolations) {
      this.status = Status.BROKEN;
      this.brokenAt = tMs;
      this.qualityAtBreak = this.qualityScore;         // frozen at the break
      return true;
    }
    return false;
  }

  archive(tMs, reason = '') {
    this.status = Status.ARCHIVED;
    this.archivedAt = tMs;
    this.archiveReason = reason;
  }

  score(barsSeen, window, lastClose, atr) {
    const touchPts = Math.min(40, (this.touches - 2) * 13 + 14);
    const spanPts = Math.min(20, 20 * (this.spanBars / Math.max(window, 1)));
    let recencyPts = 0;
    if (this.lastTestAt !== null) {
      const gap = Math.max(0, barsSeen - this._lastTouchI);
      recencyPts = 20 * Math.max(0, 1 - gap / Math.max(window * 0.5, 1));
    }
    const dist = Math.abs(this.valueAt(this.pivot2.t) - lastClose);
    this.lastPriceDistanceAtr = atr ? dist / atr : 0;
    const proxPts = 20 * Math.max(0, 1 - this.lastPriceDistanceAtr / 6);
    const penalty = 12 * this.violations;
    const confirmBonus = (this.status === Status.CONFIRMED
                          || this.status === Status.ACTIVE) ? 6 : 0;
    this.qualityScore = round2(clamp(
      touchPts + spanPts + recencyPts + proxPts + confirmBonus - penalty, 0, 100));
    return this.qualityScore;
  }
}

/* ---------------------------------------------------------------- engine ---- */
export class TrendlineEngine {
  constructor(timeframe, tfMs, params = {}) {
    this.timeframe = timeframe;
    this.tfMs = tfMs;
    this.p = { ...DEFAULT_PARAMS, ...params };
    this._seq = 0;
  }

  _newId(role) {
    this._seq += 1;
    return `${this.timeframe}-${role === Role.SUPPORT ? 'S' : 'R'}-${this._seq}`;
  }

  /** One snapshot per bar. Strictly forward, so state at i only saw bars <= i. */
  walk(bars) {
    const n = bars.length;
    const atr = atrSeries(bars, 14);
    let { highs, lows } = findPivots(bars, this.p.strength);
    // A fractal pivot can be a 0.1 ATR wiggle; requiring a minimum swing depth
    // is what separates a bar higher than its neighbours from a swing the market
    // actually turned at. Mirrors _significant() in sim/tl/engine.py.
    if (this.p.minSwingAtr > 0) {
      highs = significant(highs, bars, atr, this.p.strength, this.p.minSwingAtr, true);
      lows = significant(lows, bars, atr, this.p.strength, this.p.minSwingAtr, false);
    }
    const highsByConf = bucket(highs, n, this.p.strength);
    const lowsByConf = bucket(lows, n, this.p.strength);

    const live = { [Role.SUPPORT]: [], [Role.RESISTANCE]: [] };
    const pool = { [Role.SUPPORT]: [], [Role.RESISTANCE]: [] };
    const snapshots = [];

    for (let i = 0; i < n; i++) {
      const t = bars[i].t;
      const a = Number.isFinite(atr[i]) ? atr[i] : 0;
      const tol = a * this.p.tolAtr;
      const close = bars[i].c;

      // 1. newly visible pivots enter the pool
      for (const p of highsByConf[i]) pool[Role.RESISTANCE].push(p);
      for (const p of lowsByConf[i]) pool[Role.SUPPORT].push(p);

      const brokenNow = [];
      for (const role of [Role.SUPPORT, Role.RESISTANCE]) {
        // 2. keep the pool bounded and inside the lookback window
        pool[role] = pool[role].filter((p) => p.i >= i - this.p.window)
                               .slice(-this.p.maxPivots);

        // 3. form candidates from the newest pivot against older ones
        const fresh = role === Role.RESISTANCE ? highsByConf[i] : lowsByConf[i];
        for (const newp of fresh) {
          for (const oldp of pool[role]) {
            if (newp.i - oldp.i < this.p.minSpan) continue;
            const line = this._form(role, oldp, newp, bars, a, i);
            if (line === null) continue;
            if (this._duplicate(line, live[role], t, tol)) continue;
            live[role].push(line);
          }
        }

        // 4. update every live line against this bar
        for (const line of [...live[role]]) {
          line.ageBars = i - line.pivot1.i;
          const value = line.valueAt(t);
          if (!Number.isFinite(value) || value <= 0) {
            line.archive(t, 'degenerate');
            remove(live[role], line);
            continue;
          }

          let breached, grazed;
          if (role === Role.RESISTANCE) {
            breached = close > value + tol;
            grazed = Math.abs(bars[i].h - value) <= tol;
          } else {
            breached = close < value - tol;
            grazed = Math.abs(bars[i].l - value) <= tol;
          }

          if (breached) {
            // a break only counts if the market had ACKNOWLEDGED the line first
            const wasTradeable = line.isTradeable;
            if (line.registerViolation(t, this.p.maxViolations)) {
              if (wasTradeable) {
                brokenNow.push(line);
              } else {
                line.archive(t, 'candidate_failed');
                remove(live[role], line);
                continue;
              }
            }
          } else if (grazed) {
            line.registerTouch(t, i, this.p.strength + 1);
          }

          // 5. archive what no longer matters
          if (line.status === Status.BROKEN && line.brokenAt !== null) {
            if ((t - line.brokenAt) / this.tfMs >= this.p.archiveAfter) {
              line.archive(t, 'stale_break');
              remove(live[role], line);
              continue;
            }
          }
          if (line.status !== Status.BROKEN) {
            line.score(i, this.p.window, close, a);
          }
          if (line.lastPriceDistanceAtr > this.p.maxDistanceAtr) {
            line.archive(t, 'too_far');
            remove(live[role], line);
            continue;
          }
        }

        // 6. cap the population, best scores first
        live[role].sort((x, y) => (y.qualityScore - x.qualityScore)
                                 || (x.createdAt - y.createdAt));
        if (live[role].length > this.p.maxLive) {
          for (const extra of live[role].slice(this.p.maxLive)) {
            extra.archive(t, 'outranked');
          }
          live[role] = live[role].slice(0, this.p.maxLive);
        }
      }

      const offeredS = live[Role.SUPPORT].slice(0, this.p.maxOffered);
      const offeredR = live[Role.RESISTANCE].slice(0, this.p.maxOffered);
      const sup = this._best(offeredS, close, Role.SUPPORT, t);
      const res = this._best(offeredR, close, Role.RESISTANCE, t);
      snapshots.push({
        i, t, support: sup, resistance: res,
        live: [...live[Role.SUPPORT], ...live[Role.RESISTANCE]],
        brokenNow,
        liveCount: live[Role.SUPPORT].length + live[Role.RESISTANCE].length,
        // frozen scalars — the only values a consumer may use
        supportId: sup ? sup.id : null,
        supportPx: sup ? sup.valueAt(t) : NaN,
        supportQ: sup ? sup.qualityScore : NaN,
        resistanceId: res ? res.id : null,
        resistancePx: res ? res.valueAt(t) : NaN,
        resistanceQ: res ? res.qualityScore : NaN,
        breaks: brokenNow.map((b) => [b.role, b.valueAt(t),
          b.qualityAtBreak === null ? b.qualityScore : b.qualityAtBreak]),
      });
    }
    this.lastLive = live;
    return snapshots;
  }

  _form(role, p1, p2, bars, atr, i) {
    const dt = bars[p2.i].t - bars[p1.i].t;
    if (dt <= 0) return null;
    const slope = (p2.price - p1.price) / dt;
    return new Trendline({
      id: this._newId(role), timeframe: this.timeframe, role,
      direction: classifyDirection(slope, this.tfMs, atr),
      pivot1: { t: bars[p1.i].t, price: p1.price, i: p1.i },
      pivot2: { t: bars[p2.i].t, price: p2.price, i: p2.i },
      slope, intercept: p1.price, createdAt: bars[i].t,
      spanBars: p2.i - p1.i, atrAtCreation: atr,
    });
  }

  /** Same line twice is churn: it costs updates and evicts real ones. */
  _duplicate(cand, existing, t, tol) {
    if (tol <= 0) return false;
    const now = cand.valueAt(t);
    const then = cand.valueAt(cand.pivot1.t);
    for (const l of existing) {
      if (Math.abs(l.valueAt(t) - now) < tol
          && Math.abs(l.valueAt(cand.pivot1.t) - then) < tol) return true;
    }
    return false;
  }

  /** Tradeable, decent quality, and on the correct side of price. */
  _best(lines, lastClose, role, t) {
    let best = null;
    let bestKey = null;
    for (const l of lines) {
      if (!l.isTradeable || l.qualityScore < this.p.minQuality) continue;
      const v = l.valueAt(t);
      if (role === Role.SUPPORT && v > lastClose) continue;
      if (role === Role.RESISTANCE && v < lastClose) continue;
      const key = [-l.qualityScore, Math.abs(v - lastClose)];
      if (bestKey === null || key[0] < bestKey[0]
          || (key[0] === bestKey[0] && key[1] < bestKey[1])) {
        best = l;
        bestKey = key;
      }
    }
    return best;
  }
}

function significant(pivots, bars, atr, strength, minSwingAtr, isHigh) {
  const out = [];
  const n = bars.length;
  for (const p of pivots) {
    const i = p.i;
    const a = atr[i];
    if (!Number.isFinite(a) || a <= 0) continue;
    const lo = Math.max(0, i - strength);
    const hi = Math.min(n, i + strength + 1);
    let extreme = isHigh ? Infinity : -Infinity;
    for (let k = lo; k < hi; k++) {
      extreme = isHigh ? Math.min(extreme, bars[k].l) : Math.max(extreme, bars[k].h);
    }
    const depth = isHigh ? bars[i].h - extreme : extreme - bars[i].l;
    if (depth >= minSwingAtr * a) out.push(p);
  }
  return out;
}

function bucket(pivots, n, strength) {
  const out = Array.from({ length: n + 1 }, () => []);
  for (const p of pivots) {
    const c = p.i + strength;             // the bar it became visible
    if (c >= 0 && c < n) out[c].push({ i: p.i, price: p.price, confirmedI: c });
  }
  return out;
}

function remove(arr, item) {
  const k = arr.indexOf(item);
  if (k >= 0) arr.splice(k, 1);
}

/**
 * What the chart needs: the lines a bar-`n-1` observer could act on, tagged with
 * lifecycle state. `limitBars` walks only the recent tail for responsiveness —
 * the backtest always walks everything.
 */
export function liveLines(bars, timeframe, { limitBars = 1500, params = {} } = {}) {
  if (!bars || bars.length < 40) return [];
  const slice = limitBars && bars.length > limitBars ? bars.slice(-limitBars) : bars;
  const eng = new TrendlineEngine(timeframe, TF_MS[timeframe] || 900e3, params);
  const snaps = eng.walk(slice);
  const last = snaps[snaps.length - 1];
  const seen = new Set();
  const out = [];
  for (const l of last.live) {
    if (!l.isTradeable || seen.has(l.id)) continue;
    seen.add(l.id);
    out.push({
      id: l.id, tf: timeframe, kind: l.role, status: l.status,
      direction: l.direction, type: l.type,
      p1: { t: l.pivot1.t, price: l.pivot1.price },
      p2: { t: l.pivot2.t, price: l.pivot2.price },
      slope: l.slope, touches: l.touches, tests: l.tests,
      violations: l.violations, score: l.qualityScore,
      age: l.ageBars, spanBars: l.spanBars,
      lastTestAt: l.lastTestAt, confirmedAt: l.confirmedAt,
      valueAt: (t) => l.valueAt(t),
    });
  }
  // best score first, so a chart drawing only a few draws the best few
  out.sort((a, b) => b.score - a.score);
  return out;
}
