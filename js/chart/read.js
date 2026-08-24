/* read.js — one tradeable verdict from regime + structure + live trendlines.
 *
 * regime.js says whether each frame is trending. structure.js says what the
 * swings are doing. tlengine.js says where the lines are. None of them says
 * "should I be long", and none of them should: they are descriptions. This
 * composes the three into the one sentence a trader wants, and — more
 * importantly — into the two NUMBERS that decide whether the sentence is worth
 * acting on:
 *
 *   INVALIDATION  the price at which this read is simply wrong. For a bull
 *                 read that is the most recent confirmed higher low, or the
 *                 nearest support beneath price, whichever is nearer — the one
 *                 that breaks first is the one that matters.
 *
 *   R:R TO FIRST  reward to the first opposing line over risk to invalidation.
 *                 Not to a target you picked: to the first thing in the way.
 *
 * THE GEOMETRY CAP is the point of the module. Three frames can agree perfectly
 * and the trade still be unplaceable, because price has already run to just
 * under resistance and the first zone is nearer than the stop. A conviction
 * score that ignores this is how a good read becomes a bad fill, so when R:R is
 * below 1 the score is capped and the panel SAYS it was capped. Agreement is
 * not an edge if the geometry has already spent it.
 *
 * STATUS: unlike regime.js and structure.js this composition layer has no
 * Python mirror, so it is not parity-tested. It reads only parity-tested
 * inputs, but the weights below are a stated opinion, not a measured one.
 * Read the score as a summary of what the panel shows — not as evidence.
 */

import * as regime from './regime.js';

export const BULL = 'BULL', BEAR = 'BEAR', WATCH = 'WATCH', NEUTRAL = 'NEUTRAL';

/* Directional conviction below this is not a lean, it is noise. */
const WATCH_MIN = 15;
/* Above this the read is stated as a side rather than as something to watch. */
const CALL_MIN = 45;

const STRUCT_TEXT = {
  up: 'HH + HL', down: 'LH + LL',
  broadening: 'HH + LL', contracting: 'LH + HL',
  undecided: 'mixed',
};

function sessionName(d = new Date()) {
  const h = d.getUTCHours();
  if (h >= 7 && h < 12) return 'London';
  if (h >= 12 && h < 16) return 'London/NY';
  if (h >= 16 && h < 21) return 'New York';
  if (h >= 23 || h < 7) return 'Tokyo';
  return 'Off-session';
}

/**
 * Invalidation and the first opposing zone.
 *
 * Both come from whatever is NEAREST price on the relevant side across every
 * frame read — a 4h line two pips under a 15m line invalidates the trade at the
 * same moment, so which timeframe found it is not the interesting part.
 */
export function geometry(reads, lines, close, side, atr) {
  const now = Date.now();
  const below = [], above = [];
  for (const l of (lines || [])) {
    let v;
    try { v = l.valueAt(now); } catch { continue; }
    if (!Number.isFinite(v)) continue;
    if (v < close) below.push(v); else if (v > close) above.push(v);
  }
  /* Structural swings are levels too: a higher low IS the invalidation. */
  for (const r of reads) {
    if (!r || !r.structure) continue;
    const { lastHigh, lastLow } = r.structure;
    if (Number.isFinite(lastLow) && lastLow < close) below.push(lastLow);
    if (Number.isFinite(lastHigh) && lastHigh > close) above.push(lastHigh);
  }
  below.sort((a, b) => b - a);      // nearest below first
  above.sort((a, b) => a - b);      // nearest above first

  if (side === 0) return { invalidation: NaN, firstZone: NaN, rr: NaN };

  const invalidation = side > 0 ? below[0] : above[0];
  const firstZone = side > 0 ? above[0] : below[0];
  if (!Number.isFinite(invalidation) || !Number.isFinite(firstZone)) {
    return { invalidation: Number.isFinite(invalidation) ? invalidation : NaN,
             firstZone: Number.isFinite(firstZone) ? firstZone : NaN, rr: NaN };
  }
  const risk = Math.abs(close - invalidation);
  const reward = Math.abs(firstZone - close);
  /* A stop a hair from price is not a tight stop, it is a stop inside the
     noise, and an R:R computed off it would be fiction. A tenth of ATR is the
     floor below which the number stops meaning anything. */
  if (!(risk > 0) || (atr > 0 && risk < atr * 0.1)) {
    return { invalidation, firstZone, rr: NaN };
  }
  return { invalidation, firstZone, rr: reward / risk };
}

/**
 * @param reads  [{tf, regime:{regime,direction}, structure:{bias,lastHigh,lastLow}}]
 * @param lines  live trendlines from tlengine.liveLines, any timeframe
 * @param close  last price
 * @param atr    ATR on the execution frame
 */
export function computeRead(reads, lines, close, atr) {
  const got = reads.filter((r) => r && r.regime);
  const sess = sessionName();
  if (got.length < 2) {
    return { badge: NEUTRAL, arrow: null, score: 0, theme: 'Reading',
             session: sess, invalidation: NaN, rr: NaN, firstZone: NaN,
             structText: '—', capped: null, agree: 0, n: reads.length,
             loading: true };
  }

  /* ---- 1. regime agreement, worth up to 45 ------------------------------
     Weighted by position in the ladder: the higher frame decides whether a
     lower frame is a trend or a retracement inside somebody else's trend, so
     it carries more. */
  let dirScore = 0, wTotal = 0;
  got.forEach((r, k) => {
    const w = 1 + k;                        // 1, 2, 3 up the ladder
    wTotal += w;
    if (r.regime.regime === regime.TRENDING_UP) dirScore += w;
    else if (r.regime.regime === regime.TRENDING_DOWN) dirScore -= w;
    else if (r.regime.regime === regime.TRANSITION) {
      /* A transition still leans, but only a third as hard as a trend. */
      if (r.regime.direction === regime.DIR_UP) dirScore += w / 3;
      else if (r.regime.direction === regime.DIR_DOWN) dirScore -= w / 3;
    }
  });
  const dirNorm = wTotal ? dirScore / wTotal : 0;          // -1..1
  const side = dirNorm > 0 ? 1 : dirNorm < 0 ? -1 : 0;
  const regimePts = Math.abs(dirNorm) * 45;

  /* ---- 2. structure agreement, worth up to 25 -------------------------- */
  const top = [...got].reverse().find((r) => r.structure);
  const bias = top && top.structure ? top.structure.bias : 'undecided';
  let structPts;
  if ((bias === 'up' && side > 0) || (bias === 'down' && side < 0)) structPts = 25;
  else if (bias === 'broadening' || bias === 'contracting') structPts = 8;
  else if (bias === 'undecided') structPts = 4;
  /* Structure contradicting the regime is the informative case: it costs. */
  else structPts = -10;

  /* ---- 3. geometry ----------------------------------------------------- */
  const geo = geometry(reads, lines, close, side, atr);

  /* Geometry MODIFIES a directional read; it does not create one.
     
     This was wrong in the first cut: R:R contributed up to 30 points on its own,
     so USDCAD printed "BEAR 75" on a 5.62:1 setup while its M15 frame was
     trending UP — 30 of those points came from geometry and 25 from structure,
     with only 15 from an almost non-existent directional signal. A stop far away
     and a target further away is not evidence about direction; it is evidence
     about room. So the bonus is smaller, and it is only paid once the direction
     has earned something to multiply. */
  const base = regimePts + structPts;
  const rrPts = (Number.isFinite(geo.rr) && base >= 30)
    ? Math.min(15, Math.max(0, geo.rr - 1) * 7.5)
    : 0;

  let score = Math.round(Math.max(0, Math.min(100, base + rrPts)));

  /* The cap. Agreement cannot buy conviction the geometry has already spent. */
  let capped = null;
  if (Number.isFinite(geo.rr) && geo.rr < 1 && score > 35) {
    score = 35;
    capped = `capped by ${geo.rr.toFixed(2)}:1 geometry`;
  }

  /* A frame trending AGAINST the call demotes it to WATCH however good the
     rest looks. "BEAR" while the frame you would fill on is trending up is not
     a strong opinion, it is two opinions averaged into one misleading word. */
  const opposed = got.some((r) => (side > 0 && r.regime.regime === regime.TRENDING_DOWN)
                               || (side < 0 && r.regime.regime === regime.TRENDING_UP));

  let badge = NEUTRAL, arrow = null;
  if (score >= CALL_MIN && side !== 0 && !opposed) badge = side > 0 ? BULL : BEAR;
  else if (score >= WATCH_MIN && side !== 0) { badge = WATCH; arrow = side > 0 ? '↑' : '↓'; }

  /* The theme names WHY, which is the part a score cannot carry: the same 51
     means something different when structure agrees than when it fights. */
  const structAgrees = (bias === 'up' && side > 0) || (bias === 'down' && side < 0);
  let theme;
  if (side === 0) theme = 'No directional read';
  else if (structAgrees) theme = 'Trend continuation';
  else if (bias === 'up' || bias === 'down') theme = 'Reversal risk';
  else if (bias === 'contracting') theme = 'Compression';
  else if (bias === 'broadening') theme = 'Expansion — wide stops';
  else theme = 'Unresolved';

  const rangey = got.filter((r) => r.regime.regime === regime.SIDEWAYS).length;
  if (rangey >= 2 && score < CALL_MIN) theme = 'Range — fade edges';
  if (opposed && side !== 0) theme = 'Unconfirmed trend';

  return {
    badge, arrow, score, theme, session: sess,
    invalidation: geo.invalidation, rr: geo.rr, firstZone: geo.firstZone,
    structText: STRUCT_TEXT[bias] || 'mixed', capped,
    agree: got.filter((r) => (side > 0 && r.regime.regime === regime.TRENDING_UP)
                          || (side < 0 && r.regime.regime === regime.TRENDING_DOWN)).length,
    n: got.length, loading: false,
  };
}
