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
import { forRole, pivotProminence } from './sensitivity.js';

export const Status = {
  CANDIDATE: 'CANDIDATE', CONFIRMED: 'CONFIRMED', ACTIVE: 'ACTIVE',
  BROKEN: 'BROKEN', ARCHIVED: 'ARCHIVED',
  RECLAIMED: 'RECLAIMED',
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
  minTouches: 3,        // distinct touches that confirm a candidate (anchors count)
  breakConfirmBars: 1,  // CONSECUTIVE closes beyond tolerance before a break counts
  /* Consecutive closes back on the working side that revive a BROKEN line.
     0 = the original one-way lifecycle. Deliberately harder than breaking: a
     line should not resurrect because price brushed past it. Only possible
     within `archiveAfter` bars of the break. See sim/tl/lines.py. */
  /* ON by default — see sim/tl/engine.py for the pooled measurement. The
     reclaimed slice runs +4.76 / +3.10 / +4.75 pp across three eras once
     gated by reclaimMinQuality. */
  reclaimConfirmBars: 3,
  /* Quality floor a RECLAIMED line must clear to be OFFERED. Measured, not
     chosen: 42,273 reclaimed approaches over three eras, bucketed on the
     reclaimed line's LIVE score — >=0 gives +1.12 pp, >=70 gives +3.41 pp
     (z 7.59), positive in all three eras (+3.02/+3.61/+3.85), while the half
     below is negative in two.

     Applied after rescoring, never at the transition: the score frozen at the
     break is almost always above 70 (the line was tradeable before it broke),
     so gating there filtered 9% of reclaims and changed nothing. */
  reclaimMinQuality: 70,
  maxLive: 20,          // per role: a holding pool, not the offer list
  maxOffered: 4,        // per role, what consumers actually see
  archiveAfter: 40,     // bars after breaking before archiving
  maxDistanceAtr: 10,   // archive a line this far from price
  minQuality: 90,       // below this a line is not offered — see sim/tl/engine.py
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
    this._run = 0;
    this._back = 0;
    this.reclaims = 0;
    this.reclaimedAt = null;
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
        || this.status === Status.ACTIVE || this.status === Status.RECLAIMED;
  }

  /** Only a confirmed line is worth acting on; a candidate is a guess. */
  get isTradeable() {
    return this.status === Status.CONFIRMED || this.status === Status.ACTIVE
           || this.status === Status.RECLAIMED;
  }

  registerTouch(tMs, barI, minGapBars, minTouches = 3) {
    if (barI - this._lastTouchI < minGapBars) return false;
    this._lastTouchI = barI;
    this.touches += 1;
    this.tests += 1;
    this.lastTestAt = tMs;
    if (this.status === Status.CANDIDATE && this.touches >= minTouches) {
      this.status = Status.CONFIRMED;
      this.confirmedAt = tMs;
    } else if (this.status === Status.CONFIRMED) {
      this.status = Status.ACTIVE;
    }
    return true;
  }

  /** A close back on the working side: resets the consecutive run. */
  registerInside() { this._run = 0; }

  registerViolation(tMs, maxViolations, confirmBars = 1) {
    if (this.status === Status.BROKEN) return false;   // it does not break twice
    this._back = 0;
    this._run = (this._run || 0) + 1;
    if (this._run < Math.max(1, confirmBars)) return false;
    this.violations += 1;
    if (this.violations > maxViolations) {
      this.status = Status.BROKEN;
      this.brokenAt = tMs;
      this.qualityAtBreak = this.qualityScore;         // frozen at the break
      return true;
    }
    return false;
  }

  /**
   * Price closed back on the working side and STAYED there.
   *
   * BROKEN was terminal, and that buried lines the market was still using: on
   * gold H1 a rising support with five touches sat one point under price,
   * marked BROKEN because price had left it 29 bars earlier — and seven of the
   * ten bars since had closed back above it.
   *
   * The violation is NOT forgiven: `violations` stays, so the quality penalty
   * and `qualityAtBreak` both survive. A reclaimed line is a line with a scar.
   */
  registerReclaim(tMs, confirmBars) {
    if (this.status !== Status.BROKEN) { this._back = 0; return false; }
    this._back = (this._back || 0) + 1;
    if (this._back < Math.max(1, confirmBars)) return false;
    this._back = 0;
    this._run = 0;
    this.status = Status.RECLAIMED;
    this.reclaimedAt = tMs;
    this.reclaims = (this.reclaims || 0) + 1;
    return true;
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
  /* `sensitivity` is an optional object from js/chart/sensitivity.js. When
     given, its per-SIDE values override the flat ones in `params`: pivot
     window, prominence bar, touch/break tolerance and the offer bar.
     Left out, everything comes from `params` exactly as before.

     Unlike the Python engine this takes only a STATIC sensitivity, never a
     callable. The chart calibrates once at the bar it is drawing, so a rolling
     calibration has nothing to vary over — and the rolling variant measured
     WORSE than the static one in all three eras, so there is nothing to port. */
  constructor(timeframe, tfMs, params = {}, sensitivity = null) {
    this.timeframe = timeframe;
    this.tfMs = tfMs;
    this.p = { ...DEFAULT_PARAMS, ...params };
    this.sens = sensitivity;
    this._seq = 0;
  }

  _newId(role) {
    this._seq += 1;
    return `${this.timeframe}-${role === Role.SUPPORT ? 'S' : 'R'}-${this._seq}`;
  }

  /** One snapshot per bar. Strictly forward, so state at i only saw bars <= i. */
  tolAtrFor(role) {
    return this.sens ? forRole(this.sens, role).tolAtr : this.p.tolAtr;
  }

  minQualityFor(role) {
    return this.sens ? forRole(this.sens, role).minQuality : this.p.minQuality;
  }

  strength() {
    return this.sens ? this.sens.support.strength : this.p.strength;
  }

  /** Tradeable, and above the measured floor if it is a reclaimed line. */
  offerable(l) {
    if (!l.isTradeable) return false;
    if (l.status === Status.RECLAIMED
        && l.qualityScore < this.p.reclaimMinQuality) return false;
    return true;
  }

  walk(bars) {
    const n = bars.length;
    const atr = atrSeries(bars, 14);
    const strength = this.strength();
    let { highs, lows } = findPivots(bars, strength);
    // A fractal pivot can be a 0.1 ATR wiggle; requiring a minimum swing depth
    // is what separates a bar higher than its neighbours from a swing the market
    // actually turned at. Mirrors _significant() in sim/tl/engine.py.
    /* Prominence bars are per side when a sensitivity is supplied: a swing HIGH
       feeds resistance, a swing LOW feeds support. They are only meaningful
       alongside the wider window — measured over +/-strength, a 7-bar depth
       barely discriminates, which is why the two must move together. */
    const hiBar = this.sens ? this.sens.resistance.minProminenceAtr : this.p.minSwingAtr;
    const loBar = this.sens ? this.sens.support.minProminenceAtr : this.p.minSwingAtr;
    if (hiBar > 0) highs = significant(highs, bars, atr, strength, hiBar, true);
    if (loBar > 0) lows = significant(lows, bars, atr, strength, loBar, false);
    const highsByConf = bucket(highs, n, strength);
    const lowsByConf = bucket(lows, n, strength);

    const live = { [Role.SUPPORT]: [], [Role.RESISTANCE]: [] };
    const pool = { [Role.SUPPORT]: [], [Role.RESISTANCE]: [] };
    const snapshots = [];

    for (let i = 0; i < n; i++) {
      const t = bars[i].t;
      const a = Number.isFinite(atr[i]) ? atr[i] : 0;
      const tol = a * this.p.tolAtr;      // default; per-role below
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

          const rtol = a * this.tolAtrFor(role);
          let breached, grazed;
          if (role === Role.RESISTANCE) {
            breached = close > value + rtol;
            grazed = Math.abs(bars[i].h - value) <= rtol;
          } else {
            breached = close < value - rtol;
            grazed = Math.abs(bars[i].l - value) <= rtol;
          }

          if (breached) {
            // a break only counts if the market had ACKNOWLEDGED the line first
            const wasTradeable = line.isTradeable;
            if (line.registerViolation(t, this.p.maxViolations,
                                       this.p.breakConfirmBars)) {
              if (wasTradeable) {
                brokenNow.push(line);
              } else {
                line.archive(t, 'candidate_failed');
                remove(live[role], line);
                continue;
              }
            }
          } else {
            /* Close back on the working side: the run resets, which is what
               makes breakConfirmBars CONSECUTIVE rather than cumulative. The
               original `else if (grazed)` semantics are preserved — a bar that
               breached does not also count as a touch. */
            line.registerInside();
            if (grazed) {
              line.registerTouch(t, i, strength + 1, this.p.minTouches);
            }
          }

          /* 4b. a broken line that price has closed back through, and stayed.
             Placed AFTER the touch block so both engines evaluate it at the
             same point in the bar — the first port had it before, and the two
             produced different status sequences. */
          if (this.p.reclaimConfirmBars > 0 && line.status === Status.BROKEN) {
            const inside = role === Role.RESISTANCE
              ? close < value - rtol : close > value + rtol;
            if (inside) line.registerReclaim(t, this.p.reclaimConfirmBars);
            else line._back = 0;
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
      if (!this.offerable(l)) continue;
      if (l.qualityScore < this.minQualityFor(role)) continue;
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
/**
 * Lines for DRAWING, which is deliberately a lower bar than lines for TRADING.
 *
 * `minQuality` (90) is a measured threshold: below ~80 a line holds several
 * points LESS often than a random parallel level, in two disjoint out-of-sample
 * eras. That is the right bar for what a strategy acts on. It is the wrong bar
 * for what a chart shows -- at 90 a EURUSD 4h chart draws ZERO lines, and a
 * structure tool that draws nothing is not being rigorous, it is being useless.
 *
 * So the two are separated. `minDraw` decides what appears; `minQuality` decides
 * what is flagged `offered`, and the renderer draws the rest dimmer. You can see
 * the structure and still tell at a glance which lines the engine would stand
 * behind.
 */
/**
 * Break EVENTS over the visible window: which bar a confirmed line failed on,
 * and what it was worth at that moment.
 *
 * `qualityAtBreak` is frozen by the engine precisely so this can be honest --
 * reading `qualityScore` after the walk would report the score the line
 * eventually decayed to, not the score it had when the break happened.
 *
 * Only CONFIRMED lines count. A candidate breaking is a bad guess expiring.
 *
 * NOT DRAWN ON THE CHART. Markers for these were added and then removed: even
 * deduplicated to one per bar and capped at 30, they were too busy to read
 * across a timeframe -- 1200 bars of EURUSD 4h produce 562 raw break events, so
 * any cap that leaves the chart legible is also a cap that hides most of them.
 * The function stays because break events are worth QUERYING (sim/tl/ uses the
 * same events for the breakout diagnostics); they are just not worth painting.
 */
export function breakEvents(bars, timeframe, { limitBars = 1500, params = {},
                                               minQuality = null,
                                               max = 40 } = {}) {
  if (!bars || bars.length < 40) return [];
  const slice = limitBars && bars.length > limitBars ? bars.slice(-limitBars) : bars;
  const offset = bars.length - slice.length;
  /* Two calibrations, not one. `sensitivity` drives DETECTION and is the
     permissive one -- a looser prominence bar means more swings survive, so
     more lines exist to look at. `offerSensitivity` is the measured one and
     only decides which of them are flagged `offered`. Running the engine at
     the strict setting instead would delete the lines rather than dim them,
     which is the mistake min_quality=90 made on its own. */
  const eng = new TrendlineEngine(timeframe, TF_MS[timeframe] || 900e3, params,
                                  sensitivity);
  const atr = atrSeries(slice, 14);
  const snaps = eng.walk(slice);
  const bar = minQuality === null ? eng.p.minQuality : minQuality;
  const out = [];
  for (const s of snaps) {
    if (!s.breaks || !s.breaks.length) continue;
    for (const [role, price, quality] of s.breaks) {
      if (!(quality >= bar)) continue;
      out.push({
        i: s.i + offset, t: s.t, tf: timeframe, role, price, quality,
        /* A support failing is a DOWN break and a resistance failing is an UP
           break: the direction is the opposite of the rail's own role, which is
           the bit that reads backwards if it is not named. */
        dir: role === Role.SUPPORT ? 'down' : 'up',
      });
    }
  }
  /* Several lines commonly fail on the SAME bar -- a cluster of near-parallel
     supports all give way to one impulse candle. That is one event to a reader,
     so only the strongest per bar is kept; without this a single sharp move
     stamps six overlapping triangles on one candle.

     Then a cap: 1200 bars of EURUSD 4h produce 562 raw breaks, roughly one
     every two bars, which is a texture rather than a set of events. The most
     RECENT are kept, because an old break is history the chart already shows in
     its price. */
  const byBar = new Map();
  for (const e of out) {
    const prev = byBar.get(e.i);
    if (!prev || e.quality > prev.quality) byBar.set(e.i, e);
  }
  const kept = [...byBar.values()].sort((a, b) => a.i - b.i);
  return max && kept.length > max ? kept.slice(-max) : kept;
}

export function liveLines(bars, timeframe,
                          { limitBars = 1500, params = {}, minDraw = null,
                            sensitivity = null, offerSensitivity = null } = {}) {
  if (!bars || bars.length < 40) return [];
  const slice = limitBars && bars.length > limitBars ? bars.slice(-limitBars) : bars;
  /* Two calibrations, not one. `sensitivity` drives DETECTION and is the
     permissive one -- a looser prominence bar means more swings survive, so
     more lines exist to look at. `offerSensitivity` is the measured one and
     only decides which of them are flagged `offered`. Running the engine at
     the strict setting instead would delete the lines rather than dim them,
     which is the mistake min_quality=90 made on its own. */
  const eng = new TrendlineEngine(timeframe, TF_MS[timeframe] || 900e3, params,
                                  sensitivity);
  const atr = atrSeries(slice, 14);
  const snaps = eng.walk(slice);
  const last = snaps[snaps.length - 1];
  const seen = new Set();
  const out = [];
  for (const l of last.live) {
    /* Quality gates what is OFFERED, not what is tradeable: `isTradeable` is a
       lifecycle fact (confirmed / active) and Snapshot.tradeable deliberately
       keeps reporting every one of them so diagnostics can still MEASURE the
       low-quality population. Drawing them was the mistake -- a sub-80 line is
       measurably worse than a random parallel level. */
    const bar = minDraw === null ? eng.p.minQuality : minDraw;
    if (!eng.offerable(l) || l.qualityScore < bar || seen.has(l.id)) continue;
    /* The offer bar comes from the strict calibration when one is supplied,
       so `offered` keeps meaning "the engine would hand this to a strategy"
       even though the population around it is deliberately wider. */
    let isOffered;
    if (offerSensitivity) {
      /* A line is `offered` only if the STRICT calibration would have built it
         at all: both anchors must clear its prominence bar, measured at its
         window, as well as the quality bar. Checking quality alone would flag
         lines the measured detector never sees, which is the opposite of what
         the flag is for. */
      const side = forRole(offerSensitivity, l.role);
      const isHigh = l.role === Role.RESISTANCE;
      const anchorsOk = [l.pivot1.i, l.pivot2.i].every((pi) =>
        pivotProminence(slice, pi, side.strength, isHigh, atr) >= side.minProminenceAtr);
      isOffered = anchorsOk && l.qualityScore >= side.minQuality;
    } else {
      isOffered = l.qualityScore >= eng.p.minQuality;
    }
    seen.add(l.id);
    out.push({
      id: l.id, tf: timeframe, kind: l.role, status: l.status,
      direction: l.direction, type: l.type,
      p1: { t: l.pivot1.t, price: l.pivot1.price },
      p2: { t: l.pivot2.t, price: l.pivot2.price },
      slope: l.slope, touches: l.touches, tests: l.tests,
      violations: l.violations, score: l.qualityScore,
      /* Above the measured threshold the engine would offer this line to a
         strategy; below it, the line is shown as structure only. */
      offered: isOffered,
      age: l.ageBars, spanBars: l.spanBars,
      lastTestAt: l.lastTestAt, confirmedAt: l.confirmedAt,
      valueAt: (t) => l.valueAt(t),
    });
  }
  // best score first, so a chart drawing only a few draws the best few
  out.sort((a, b) => b.score - a.score);
  return out;
}
