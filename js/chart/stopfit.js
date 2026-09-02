/**
 * stopfit.js -- how wide the stop has to be, per instrument and per timeframe.
 *
 * IT USED TO FIT A TARGET TOO. `excursionProfile`, `reachStats` and `fitTp`
 * placed a take-profit at the median favourable excursion of the loaded series,
 * and they are gone with the take-profit itself: no surface in this app trades
 * a target and the walker can no longer execute one.
 * `logs/tp_struct_eval.txt` holds the run they went on the strength of: across
 * twelve cells out of sample, no target beat the trailing exit on net R.
 *
 * THE CIRCULARITY, AND HOW IT IS BROKEN. R is *defined* by the stop, so a stop
 * measured in R measures itself. Everything here is therefore in ATR, and the
 * favourable/adverse classification never mentions a stop at all -- which is
 * what made it safe to fit the stop first and then, when there was a target,
 * fit that in the R this stop creates.
 *
 * WHAT IS MEASURED. For every bar: walk the horizon tracking the running
 * adverse and favourable excursions in ATR. A bar's future is called FAVOURABLE
 * when its best favourable excursion exceeds its worst adverse one -- a
 * definition that needs no stop, no target and no rule. For those bars, the
 * quantity that matters is the HEAT: the worst adverse excursion suffered
 * BEFORE the favourable peak, because that is what a stop would have had to sit
 * through to collect the move.
 *
 * The stop is placed at the `q`-th percentile of that heat. It is a quantile
 * and not a sweep: `survival` reports the share of favourable paths the chosen
 * width actually survives, so the number makes a claim that can be checked
 * rather than a promise that cannot. Sweeping for the best expectancy would fit
 * a free parameter to one sample, and every such winner in this project has had
 * an interval spanning zero.
 */

/** Wilder ATR, the same one the position tool sizes its stop with. */
function atrSeries(bars, length = 14) {
  const n = bars.length;
  const out = new Array(n).fill(NaN);
  if (n <= length) return out;
  let tr0 = 0;
  for (let i = 1; i <= length; i++) {
    const p = bars[i - 1].c;
    tr0 += Math.max(bars[i].h - bars[i].l,
      Math.abs(bars[i].h - p), Math.abs(bars[i].l - p));
  }
  let prev = tr0 / length;
  out[length] = prev;
  for (let i = length + 1; i < n; i++) {
    const p = bars[i - 1].c;
    const tr = Math.max(bars[i].h - bars[i].l,
      Math.abs(bars[i].h - p), Math.abs(bars[i].l - p));
    prev = (prev * (length - 1) + tr) / length;
    out[i] = prev;
  }
  return out;
}

function quantile(sorted, q) {
  if (!sorted.length) return NaN;
  const pos = (sorted.length - 1) * q;
  const lo = Math.floor(pos);
  const hi = Math.ceil(pos);
  return lo === hi ? sorted[lo] : sorted[lo] + (sorted[hi] - sorted[lo]) * (pos - lo);
}

/** Below this the series cannot say anything and the fallback is used. */
export const MIN_SAMPLES = 200;

/**
 * The heat a favourable path takes, in ATR, for every bar in the series.
 *
 * One walk, used by both the profile and the survival check, so the two can
 * never be computed from slightly different loops -- which is exactly how a
 * "checked" number stops matching the thing it claims to check.
 */
function heatSeries(bars, { side = 1, horizon = 40, atrLen = 14 } = {}) {
  const n = bars.length;
  const atr = atrSeries(bars, atrLen);
  const heat = [];
  let seen = 0;
  for (let i = atrLen + 1; i + horizon < n; i++) {
    const a = atr[i];
    if (!(a > 0)) continue;
    seen += 1;
    const entry = bars[i].c;
    let mae = 0;            // worst adverse so far, in ATR
    let mfe = 0;            // best favourable so far, in ATR
    let heatAtPeak = 0;     // the adverse suffered BEFORE that best
    for (let j = i + 1; j <= i + horizon; j++) {
      const b = bars[j];
      const adv = (side > 0 ? entry - b.l : b.h - entry) / a;
      if (adv > mae) mae = adv;
      const fav = (side > 0 ? b.h - entry : entry - b.l) / a;
      if (fav > mfe) { mfe = fav; heatAtPeak = mae; }
    }
    /* FAVOURABLE WITHOUT REFERENCE TO A STOP: the best move in your favour beat
       the worst move against you. No stop, no target, no rule -- which is what
       makes it safe to choose the stop from it. */
    if (mfe > mae) heat.push(heatAtPeak);
  }
  return { heat, seen };
}

export function stopProfile(bars, { side = 1, horizon = 40, atrLen = 14,
  qs = [0.5, 0.75, 0.9] } = {}) {
  const { heat, seen } = heatSeries(bars, { side, horizon, atrLen });
  const sorted = heat.slice().sort((x, y) => x - y);
  const q = {};
  for (const p of qs) q[p] = quantile(sorted, p);
  return { n: sorted.length, seen, side, horizon, q,
           favourableRate: seen ? sorted.length / seen : NaN };
}

/**
 * The chosen stop width in ATR, with the survival rate it actually delivers.
 *
 * `survival` is counted against the ROUNDED width rather than inferred from the
 * quantile. Rounding to a hundredth of an ATR moves it, and a claim that has not
 * been re-checked after the number it describes was changed is not a claim.
 */
export function fitStop(bars, { side = 1, horizon = 40, q = 0.75,
  atrLen = 14, fallbackAtr = 2.0 } = {}) {
  const { heat, seen } = heatSeries(bars, { side, horizon, atrLen });
  const sorted = heat.slice().sort((x, y) => x - y);
  const profile = { n: sorted.length, seen, side, horizon,
                    q: { [q]: quantile(sorted, q) },
                    favourableRate: seen ? sorted.length / seen : NaN };
  if (sorted.length < MIN_SAMPLES || !(profile.q[q] > 0)) {
    return { atr: fallbackAtr, source: 'fallback', n: sorted.length, q,
             profile, survival: NaN };
  }
  const width = Math.round(profile.q[q] * 100) / 100;
  const ok = sorted.reduce((acc, h) => acc + (h <= width ? 1 : 0), 0);
  return { atr: width, source: 'measured', n: sorted.length, q, profile,
           survival: sorted.length ? ok / sorted.length : NaN };
}
