/* trendlines.js — algorithmic trendline detection.
 *
 * Computed independently for each instrument × timeframe from that series'
 * own bars, then expressed in TIME (not bar index) so a line found on H4 can be
 * drawn on an M15 chart: the consumer maps timestamps back to its own index
 * space. That is the whole reason `slope` is per-millisecond here.
 *
 * The method, in order:
 *   1. Fractal pivots — a bar whose high (low) beats `strength` bars each side.
 *   2. Candidate lines — every pair of same-type pivots inside the window.
 *   3. Validation — a resistance line is only real while no bar CLOSES above it
 *      (wicks are allowed to pierce; tolerance is ATR-scaled, so a 20-point
 *      graze on gold is not treated like a break on EURUSD).
 *   4. Scoring — touches dominate, then span, then recency; a line still being
 *      respected near the last bar beats an old one nobody trades any more.
 *   5. Dedupe — near-identical lines collapse to the best-scoring one.
 *
 * Everything is pure: same bars in, same lines out, no clock reads.
 */

/** Wilder ATR per bar, used to size every tolerance in price terms. */
function atrSeries(bars, len = 14) {
  const out = new Array(bars.length).fill(null);
  let prev = null;
  for (let i = 0; i < bars.length; i++) {
    const tr = i === 0
      ? bars[i].h - bars[i].l
      : Math.max(bars[i].h - bars[i].l,
                 Math.abs(bars[i].h - bars[i - 1].c),
                 Math.abs(bars[i].l - bars[i - 1].c));
    if (i === len - 1) {
      let s = 0;
      for (let j = 0; j <= i; j++) {
        s += j === 0 ? bars[j].h - bars[j].l
          : Math.max(bars[j].h - bars[j].l,
                     Math.abs(bars[j].h - bars[j - 1].c),
                     Math.abs(bars[j].l - bars[j - 1].c));
      }
      prev = s / len;
    } else if (i >= len) {
      prev = (prev * (len - 1) + tr) / len;
    }
    out[i] = prev;
  }
  // back-fill the warm-up so early bars still have a usable tolerance
  const first = out.find((v) => v !== null) ?? (bars.length ? bars[0].h - bars[0].l : 1);
  return out.map((v) => v ?? first);
}

/**
 * Fractal pivots. Mirror of sim/tl/pivots.py find_pivots — the two are compared
 * over real bars by tests/test_parity.py, so any change here needs the same
 * change there.
 *
 * THE WICK SETS THE PRICE, ALWAYS. A swing high is the highest price TRADED,
 * because the wick is where the rejection happened and that is the level price
 * gets measured against later. `i` is where it IS.
 *
 * Two separate questions, on purpose:
 *
 *   candidate    the wick-fractal shape, `strength` bars each side. confirmedI
 *                = i + strength is the earliest bar the SHAPE is knowable.
 *
 *   closeConfirm=true asks whether price went on to actually CLOSE past the
 *                extreme -- establishing the turn, not just pausing at it. See
 *                confirmByClose. A candidate ends up CONFIRMED (confirmedI now
 *                the bar the closes actually established it, which can be well
 *                past i + strength), INVALIDATED (a later wick made a new
 *                extreme first), or PENDING (not enough bars yet). Invalidated
 *                and pending candidates are dropped.
 *
 * An earlier version filtered by requiring ALL of the next `strength` bars to
 * close past the extreme -- not the same rule: one weak close inside an
 * otherwise-decisive window killed the whole candidate. Measured against the
 * trendline/zone/BOS detectors that consume these pivots, that filter made
 * every one of them worse, because their signal comes from pivot DENSITY and
 * the all-K rule starved it by a third. This walk lets confirmation take as
 * long as the market actually takes, instead.
 */
export function findPivots(bars, strength = 3, closeConfirm = false) {
  const highs = [];
  const lows = [];
  for (let i = strength; i < bars.length - strength; i++) {
    let isHigh = true;
    let isLow = true;
    for (let k = 1; k <= strength; k++) {
      // strict on the left, tolerant on the right, so a flat top reports once
      if (!(bars[i].h > bars[i - k].h && bars[i].h >= bars[i + k].h)) isHigh = false;
      if (!(bars[i].l < bars[i - k].l && bars[i].l <= bars[i + k].l)) isLow = false;
      if (!isHigh && !isLow) break;
    }
    if (isHigh) highs.push({ i, t: bars[i].t, price: bars[i].h, confirmedI: i + strength });
    if (isLow) lows.push({ i, t: bars[i].t, price: bars[i].l, confirmedI: i + strength });
  }
  if (!closeConfirm) return { highs, lows };
  return { highs: confirmByClose(highs, bars, strength, true),
           lows: confirmByClose(lows, bars, strength, false) };
}

/**
 * CANDIDATE -> CONFIRMED or dropped (invalidated / pending). Walks forward on
 * CLOSES. See find_pivots._confirm_by_close in sim/tl/pivots.py for the same
 * walk in Python -- keep the two in lockstep.
 *
 * Invalidation triggers on a later WICK, not a later close: once a bar has
 * actually traded through the level, this candidate is no longer the extreme
 * no matter what anything closed at.
 */
function confirmByClose(pivots, bars, confirmBars, isHigh) {
  const n = bars.length;
  const out = [];
  for (const p of pivots) {
    const { i, price } = p;
    const floor = i + confirmBars;
    let run = 0;
    let confirmedAt = null;
    for (let j = i + 1; j < n; j++) {
      const w = isHigh ? bars[j].h : bars[j].l;
      if (isHigh ? w > price : w < price) break;          // superseded
      const turned = isHigh ? bars[j].c < bars[i].c : bars[j].c > bars[i].c;
      run = turned ? run + 1 : 0;
      if (run >= confirmBars) { confirmedAt = j; break; }
    }
    if (confirmedAt !== null) out.push({ ...p, confirmedI: Math.max(confirmedAt, floor) });
  }
  return out;
}

const DEFAULTS = {
  strength: 3,          // pivot fractal size
  window: 600,          // only look this far back for pivots
  maxPivots: 34,        // most recent N pivots per side (pairs grow as N²)
  minSpan: 6,           // anchors must be at least this many bars apart
  minTouches: 3,        // anchors count, so 3 means "one confirmation"
  tolAtr: 0.32,         // touch/break tolerance in ATR
  maxViolations: 0,     // closes beyond the line before it is dead
  maxLines: 4,          // per side, after dedupe
  maxDistanceAtr: 5,    // drop lines this far (in ATR) from the last close
};

/**
 * Detect trendlines for one series.
 * @returns {Array<{kind, p1, p2, slope, touches, score, lastTouchT, valueAt}>}
 *   `slope` is price per millisecond; `valueAt(t)` evaluates the line.
 */
export function detectTrendlines(bars, options = {}) {
  const o = { ...DEFAULTS, ...options };
  // never anchor on the forming bar: its high/low is still moving
  const closed = bars.length > 1 ? bars.slice(0, -1) : bars;
  if (closed.length < o.strength * 2 + 12) return [];

  const atr = atrSeries(closed, 14);
  const n = closed.length;
  const last = closed[n - 1];
  const lastAtr = atr[n - 1] || (last.c * 0.001);
  const from = Math.max(0, n - o.window);
  const { highs, lows } = findPivots(closed, o.strength);

  const out = [];
  for (const [kind, pivots] of [['resistance', highs], ['support', lows]]) {
    const pool = pivots.filter((p) => p.i >= from).slice(-o.maxPivots);
    const found = [];

    for (let a = 0; a < pool.length - 1; a++) {
      for (let b = a + 1; b < pool.length; b++) {
        const p1 = pool[a];
        const p2 = pool[b];
        if (p2.i - p1.i < o.minSpan) continue;

        // fit in index space (bars are evenly spaced there, unlike in time)
        const slopeBar = (p2.price - p1.price) / (p2.i - p1.i);
        const at = (i) => p1.price + slopeBar * (i - p1.i);

        let touches = 0;
        let violations = 0;
        let lastTouch = p1.i;
        let dead = false;
        // A retest is an EVENT, not a bar: ten consecutive bars grazing the line
        // is one touch, so counts stay comparable across timeframes.
        const touchGap = o.strength + 1;
        let prevTouch = -Infinity;

        for (let i = p1.i; i < n; i++) {
          const line = at(i);
          const tol = (atr[i] || lastAtr) * o.tolAtr;
          const bar = closed[i];
          const grazes = kind === 'resistance'
            ? Math.abs(bar.h - line) <= tol
            : Math.abs(bar.l - line) <= tol;
          const breaks = kind === 'resistance' ? bar.c > line + tol : bar.c < line - tol;
          if (breaks) { violations++; if (violations > o.maxViolations) { dead = i; break; } }
          if (grazes) {
            if (i - prevTouch >= touchGap) touches++;
            prevTouch = i;
            lastTouch = i;
          }
        }
        if (dead !== false) continue;
        if (touches < o.minTouches) continue;

        // a line the market has walked away from is noise
        const endValue = at(n - 1);
        if (endValue <= 0) continue;
        if (Math.abs(endValue - last.c) > o.maxDistanceAtr * lastAtr) continue;

        const span = (p2.i - p1.i) / o.window;
        const recency = 1 - (n - 1 - lastTouch) / o.window;
        // Proximity matters as much as history: a line 5 ATR away is true but
        // untradeable today, so distance is penalised rather than only capped.
        const distAtr = Math.abs(endValue - last.c) / lastAtr;
        const score = touches * 3 + span * 2 + Math.max(0, recency) * 2.5 - distAtr * 1.3;

        found.push({
          kind,
          p1: { t: p1.t, price: p1.price, i: p1.i },
          p2: { t: p2.t, price: p2.price, i: p2.i },
          slopeBar,
          touches,
          score,
          lastTouchT: closed[lastTouch].t,
          endValue,
        });
      }
    }

    // best first, then drop anything that is effectively the same line
    found.sort((x, y) => y.score - x.score);
    const kept = [];
    for (const cand of found) {
      const near = kept.some((k) => {
        const tol = lastAtr * o.tolAtr * 2;
        const midI = Math.round((n - 1 + Math.max(cand.p1.i, k.p1.i)) / 2);
        const candMid = cand.p1.price + cand.slopeBar * (midI - cand.p1.i);
        const kMid = k.p1.price + k.slopeBar * (midI - k.p1.i);
        return Math.abs(cand.endValue - k.endValue) < tol && Math.abs(candMid - kMid) < tol;
      });
      if (!near) kept.push(cand);
      if (kept.length >= o.maxLines) break;
    }
    out.push(...kept);
  }

  // Convert to a time-based slope so the line can be drawn on ANY timeframe of
  // the same instrument. Index space is local to this series; time is not.
  return out.map((l) => {
    const dt = l.p2.t - l.p1.t;
    const slope = dt ? (l.p2.price - l.p1.price) / dt : 0;
    const line = {
      kind: l.kind,
      p1: { t: l.p1.t, price: l.p1.price },
      p2: { t: l.p2.t, price: l.p2.price },
      slope,
      touches: l.touches,
      score: Number(l.score.toFixed(2)),
      lastTouchT: l.lastTouchT,
      valueAt: (t) => l.p1.price + slope * (t - l.p1.t),
    };
    return line;
  }).sort((a, b) => b.score - a.score);
}

/** Pivot strength presets exposed in the UI. */
export const SENSITIVITY = {
  fine: { label: 'Fine (minor swings)', strength: 2, minTouches: 3 },
  normal: { label: 'Normal', strength: 3, minTouches: 3 },
  major: { label: 'Major structure', strength: 6, minTouches: 3 },
};
