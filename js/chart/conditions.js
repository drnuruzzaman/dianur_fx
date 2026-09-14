/* conditions.js — the three axes, as percentiles against the cell's own past.
 *
 * WHAT THIS IS FOR. Context for the right rail: is this instrument, on this
 * frame, pushing or drifting, calm or violent, busy or empty. It is DECORATION.
 * Nine signal-time scores have been tested against outcome in this project
 * under a pre-registered bar and none passed, so nothing here is a gate, a
 * filter, or an input to a ticket.
 *
 * WHY THREE AND NOT SEVEN. The obvious build -- a CNN-style average of
 * momentum, position in range, drawdown from the high, EMA separation, up-bar
 * share and so on -- was measured first on XAU 1h. Those price transforms
 * correlate 0.84 to 0.95 with one another; the first principal component alone
 * carries 59% of the variance. Six of them averaged is one number wearing six
 * hats. Only VOLATILITY and PARTICIPATION are independent of that cluster
 * (correlations of -0.02 and +0.03 against momentum), so the honest reading is
 * three axes, and DIRECTION is the single representative of the price cluster.
 *
 * SPREAD WAS THE FOURTH AXIS AND THE DATA KILLED IT. Widening spread is the
 * closest thing this project has to a VIX, and the bar archive carries a
 * per-bar `spread` column -- but EURUSD's is 61.5% zeros with p90 = p99 = 50,
 * and gold's median is 5 points against the 19 the registry actually charges.
 * It is some other quantity, not the traded spread. Not used here, and not to
 * be used for costing either.
 *
 * PERCENTILE, NOT Z-SCORE. Each axis is ranked against its own recent history
 * on the SAME instrument and frame, which is the one part of CNN's method that
 * ports cleanly: it makes gold and the yen readable on one scale without
 * either's units or volatility leaking in. A z-score would need the
 * distribution to be roughly normal, and none of these are.
 *
 * DIRECTION IS LIVE, VOLATILITY AND PARTICIPATION ARE NOT, and that split is
 * forced by what a forming bar can honestly say.
 *
 * Direction is `(close - EMA50) / ATR`, and the live close is a real close --
 * it is the price right now. The EMA and the ATR are taken to the last CLOSED
 * bar, which is the same one-bar shift every other reading in this project
 * uses, so the level a tick is measured against never moves under it.
 *
 * Volatility and participation cannot do that. A bar five minutes into an hour
 * has five minutes of range and five minutes of ticks; ranking those against
 * whole bars reads as a volatility collapse and a liquidity drought, every
 * hour, for fifty-five minutes. They step on the close and stand still in
 * between -- which is the truth, not a limitation to apologise for.
 *
 * THE WINDOW IS THE LAST `lookback` BARS OF WHAT THE CHART HAS LOADED, which
 * means the reading is relative to a few hundred bars, not to nine years. That
 * is a real limitation and the reason the panel prints the sample beside the
 * dial rather than hiding it: "72nd percentile of 500 bars" is a different
 * claim from "72nd percentile of a decade", and on 1m five hundred bars is
 * eight hours.
 */

/** Minimum bars before any reading is offered at all. */
export const MIN_BARS = 200;

function emaSeries(v, n) {
  const k = 2 / (n + 1);
  const out = new Array(v.length);
  let e = v[0];
  for (let i = 0; i < v.length; i++) { e = i ? v[i] * k + e * (1 - k) : v[0]; out[i] = e; }
  return out;
}

/** Wilder ATR, the same alpha the rule uses (1/n, not 2/(n+1)). */
function atrSeries(bars, n = 14) {
  const out = new Array(bars.length);
  let a = null;
  for (let i = 0; i < bars.length; i++) {
    const b = bars[i];
    const pc = i ? bars[i - 1].c : b.c;
    const tr = Math.max(b.h - b.l, Math.abs(b.h - pc), Math.abs(b.l - pc));
    a = a === null ? tr : a + (tr - a) / n;
    out[i] = a;
  }
  return out;
}

/**
 * Where the last value sits in the distribution of the ones before it.
 *
 * RETURNS THE SAMPLE, NOT JUST THE RANK. The panel printed "of 1999 bars" --
 * the size of the loaded window -- while the rank was against the last 500,
 * overstating the comparison fourfold. A percentile without its sample is half
 * a number, so the count comes back with it and the caller has nothing to
 * guess at.
 */
function percentile(series, lookback) {
  const end = series.length - 1;
  const start = Math.max(0, end - lookback);
  const cur = series[end];
  // Shape-consistent with the success path; callers read `.pct`.
  if (!Number.isFinite(cur)) return { pct: null, seen: 0 };
  let seen = 0;
  let below = 0;
  for (let i = start; i < end; i++) {
    const v = series[i];
    if (!Number.isFinite(v)) continue;
    seen += 1;
    if (v <= cur) below += 1;
  }
  return seen < 30 ? { pct: null, seen } : { pct: (100 * below) / seen, seen };
}

/**
 * `{ direction, volatility, participation, n }` as percentiles, or nulls.
 *
 * DIRECTION IS SIGNED AND THEN RANKED, so 50 means "as pushed as it usually
 * is", not "flat". That is the same convention CNN uses and it catches people
 * out: a market that is always trending reads mid-scale, because the scale is
 * its own history rather than an absolute.
 */
export function conditions(bars, { slow = 50, atrN = 14, lookback = 500 } = {}) {
  const out = { direction: null, volatility: null, participation: null,
                n: 0, window: 0, live: null };
  if (!bars || bars.length < MIN_BARS) return out;

  /* THE HISTORY IS CLOSED BARS ONLY. Everything ranked below is computed from
     these; the forming bar contributes the live PRICE and nothing else. */
  const b = bars.slice(0, -1);
  const forming = bars[bars.length - 1];
  out.n = b.length;

  const closes = b.map((x) => x.c);
  const ema = emaSeries(closes, slow);
  const atr = atrSeries(b, atrN);

  const dir = b.map((_, i) => (atr[i] > 0 ? (closes[i] - ema[i]) / atr[i] : NaN));
  const vol = atr.slice();
  const part = b.map((x) => (Number.isFinite(x.v) ? x.v : NaN));

  /* THE LIVE READING IS APPENDED, NOT SUBSTITUTED. Price now, against the
     trend and the range of the last closed bar, ranked among the closed
     history -- so the needle moves on every tick while the yardstick it is
     measured against only moves on a close. */
  const lastEma = ema[ema.length - 1];
  const lastAtr = atr[atr.length - 1];
  if (forming && Number.isFinite(forming.c) && lastAtr > 0) {
    out.live = (forming.c - lastEma) / lastAtr;
    dir.push(out.live);
  }
  const d = percentile(dir, lookback);
  out.direction = d.pct;
  /* THE RANKING WINDOW, which is what the reader needs, not the loaded window.
     All three axes share it unless one series carries gaps. */
  out.window = d.seen;
  out.volatility = percentile(vol, lookback).pct;
  /* Tick volume is present on bridge bars (`v`) and absent from some archives;
     a missing series is null, never zero -- "no data" and "nobody traded" are
     different facts and only one of them is alarming. */
  out.participation = part.some(Number.isFinite)
    ? percentile(part, lookback).pct : null;
  return out;
}
