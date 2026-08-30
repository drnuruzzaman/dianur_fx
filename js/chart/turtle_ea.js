/* turtle_ea.js — the JS twin of sim/strategies/turtle_ea.py.
 *
 * FOR THE REPLAY, NOT THE LIVE CHART. It is registered in strategies.js, which
 * only js/ui/strategyreplay.js reads; the live rule panel imports donchian.js
 * directly and never consults the registry, so nothing here can reach the
 * chart you trade from.
 *
 * WHY IT EXISTS AT ALL. The replay is only worth stepping through if it walks
 * the SAME rule the measurement walked. tests/test_strategy_parity.py holds
 * this file against the Python trade for trade; without that, the panel would
 * be showing a strategy the numbers never described.
 *
 * NO PRIVATE INDICATORS. sma/ema/wilder come from indicators.js, ATR from
 * tlengine.js, RSI from divergence.js -- each the one implementation
 * tests/test_parity.py already holds to sim/indicators.py. The seeding matters
 * more than it looks: indicators.js `ema` seeds with the MEAN of the first n,
 * while rules.js `emaSeries` seeds with values[0]. Both are correct for their
 * own callers and they are not interchangeable. sim/indicators.ema mirrors the
 * first, so MACD here must use the first, and a strategy that reached for the
 * nearer import would diverge from the engine in a way no chart would ever
 * look wrong.
 */

import { ema, sma, wilder } from './indicators.js';
import { atrSeries } from './tlengine.js';
import { rsiSeries } from './divergence.js';
import { FLAT as FLAT_, LONG as LONG_, rollingShifted } from './rules.js';

export const DEFAULTS = {
  entry1: 20, entry2: 55, exit: 10, atrLen: 14, atrMult: 2.0,
  regimeLen: 200, adxLen: 14, minAdx: 25, rsiLen: 14, smaLen: 50,
  beAtR: 1.0, trailAtR: 2.0, trailAtr: 1.5, atrSpike: 3.0,
};

const fin = (v) => v !== null && v !== undefined && Number.isFinite(v);

/** Mean of everything seen SO FAR. Never of the future. */
function expandingMean(values) {
  const out = new Array(values.length).fill(NaN);
  let total = 0, n = 0;
  for (let i = 0; i < values.length; i++) {
    if (fin(values[i])) { total += values[i]; n += 1; }
    if (n) out[i] = total / n;
  }
  return out;
}

/**
 * MACD, reproducing sim/indicators.macd including its quirk: the signal line
 * is an EMA of the macd series with undefined values replaced by ZERO before
 * smoothing, then re-masked wherever macd itself is undefined. Smoothing over
 * the gap rather than around it is what the Python does, so it is what this
 * does -- matching a quirk beats being tidy and disagreeing.
 */
function macdSeries(close, fast, slow, signal) {
  const f = ema(close, fast);
  const s = ema(close, slow);
  const line = close.map((_, i) => (fin(f[i]) && fin(s[i]) ? f[i] - s[i] : NaN));
  const defined = line.map((v) => (fin(v) ? v : 0));
  const sig = ema(defined, signal);
  return {
    line,
    signal: line.map((v, i) => (fin(v) && fin(sig[i]) ? sig[i] : NaN)),
  };
}

/**
 * Wilder's ADX, mirroring sim/indicators.adx.
 *
 * +DM and -DM are EXCLUSIVE: only the larger move counts on a bar, and neither
 * counts on an inside bar. Taking both turns a trend gauge into a volatility
 * gauge, which is the usual way this gets written wrong.
 */
function adxSeries(bars, length) {
  const n = bars.length;
  if (n < 2) return new Array(n).fill(NaN);

  const up = new Array(n).fill(0);
  const dn = new Array(n).fill(0);
  for (let i = 1; i < n; i++) {
    const upMove = bars[i].h - bars[i - 1].h;
    const dnMove = bars[i - 1].l - bars[i].l;
    up[i] = (upMove > dnMove && upMove > 0) ? upMove : 0;
    dn[i] = (dnMove > upMove && dnMove > 0) ? dnMove : 0;
  }

  const tr = new Array(n);
  tr[0] = bars[0].h - bars[0].l;
  for (let i = 1; i < n; i++) {
    const pc = bars[i - 1].c;
    tr[i] = Math.max(bars[i].h - bars[i].l,
                     Math.abs(bars[i].h - pc), Math.abs(bars[i].l - pc));
  }

  const atrS = wilder(tr, length);
  const upS = wilder(up, length);
  const dnS = wilder(dn, length);

  const dx = new Array(n).fill(NaN);
  for (let i = 0; i < n; i++) {
    if (!fin(atrS[i]) || !fin(upS[i]) || !fin(dnS[i]) || atrS[i] === 0) continue;
    const plus = 100 * upS[i] / atrS[i];
    const minus = 100 * dnS[i] / atrS[i];
    const total = plus + minus;
    if (total === 0) continue;
    dx[i] = 100 * Math.abs(plus - minus) / total;
  }

  /* the second smoothing starts where DX first exists, not at bar 0 */
  const first = dx.findIndex(fin);
  const out = new Array(n).fill(NaN);
  if (first < 0) return out;
  const tail = wilder(dx.slice(first), length);
  for (let i = 0; i < tail.length; i++) out[first + i] = fin(tail[i]) ? tail[i] : NaN;
  return out;
}

export const turtleEaRule = {
  key: 'turtle_ea',
  label: 'Turtle EA v3 (published)',
  defaults: DEFAULTS,
  summary: 'Long only. Enter at the next open when a close clears the 20- or '
    + '55-bar high, while price is above the 200 SMA, ADX >= 25 and at least '
    + 'two of (RSI > 50, MACD above signal, close > SMA 50) agree. Stop 2 ATR; '
    + 'break-even at +1R; past +2R the stop trails 1.5 ATR under the high. '
    + 'Leave on a close below the 10-bar low.',

  warmup: (p) => Math.max(p.entry2, p.regimeLen, 2 * p.adxLen, p.smaLen,
                          26 + 9, p.atrLen) + 2,

  prepare(bars, p) {
    const close = bars.map((b) => b.c);
    const high = bars.map((b) => b.h);
    const low = bars.map((b) => b.l);
    const atr = atrSeries(bars, p.atrLen);
    const m = macdSeries(close, 12, 26, 9);
    return {
      hi1: rollingShifted(high, p.entry1, Math.max),
      hi2: rollingShifted(high, p.entry2, Math.max),
      exitLo: rollingShifted(low, p.exit, Math.min),
      atr,
      atrAvg: expandingMean(atr),
      regime: sma(close, p.regimeLen),
      adx: adxSeries(bars, p.adxLen),
      rsi: rsiSeries(bars, p.rsiLen),
      macd: m.line,
      macdSig: m.signal,
      sma: sma(close, p.smaLen),
    };
  },

  decide(i, { series, close, high, pos, p }) {
    const a = series.atr[i];
    if (!fin(a) || a <= 0) return null;
    const c = close[i];

    if (pos) {
      const lo = series.exitLo[i];
      /* 'signal' is the walker's word for a RULE-DRIVEN exit, and it is what
         the Python engine reports for the same event -- donchian.js uses it
         for its channel exit too. A prettier tag here just made the two
         disagree about the same trade. */
      if (fin(lo) && c < lo) return { side: FLAT_, reason: 'signal' };

      /* R is measured from `pos.risk` -- the entry-to-stop distance FIXED AT
         FILL by the walker. The live stop shrinks as it ratchets, and
         atrMult * atr moves every bar; either would drag the +1R line around
         under the trade instead of holding it where the risk was taken. */
      const gainR = pos.risk ? (c - pos.entryPrice) / pos.risk : 0;

      let want = null;
      if (gainR >= p.trailAtR) {
        let peak = -Infinity;
        for (let k = pos.entryI; k <= i; k++) if (high[k] > peak) peak = high[k];
        if (Number.isFinite(peak)) want = peak - p.trailAtr * a;
      } else if (gainR >= p.beAtR) {
        want = pos.entryPrice;
      }
      /* ratchet only: a stop that can fall is not a stop */
      if (want !== null && fin(want) && want > pos.stop) pos.stop = want;
      return null;
    }

    const hi1 = series.hi1[i], hi2 = series.hi2[i];
    if (!((fin(hi1) && c > hi1) || (fin(hi2) && c > hi2))) return null;

    const avg = series.atrAvg[i];
    if (fin(avg) && avg > 0 && a > p.atrSpike * avg) return null;   // ATR spike

    const regime = series.regime[i];
    if (!fin(regime) || c <= regime) return null;                   // under the MA

    const adxNow = series.adx[i];
    if (!fin(adxNow) || adxNow < p.minAdx) return null;             // not trending

    let votes = 0;
    if (fin(series.rsi[i]) && series.rsi[i] > 50) votes += 1;
    if (fin(series.macd[i]) && fin(series.macdSig[i])
        && series.macd[i] > series.macdSig[i]) votes += 1;
    if (fin(series.sma[i]) && c > series.sma[i]) votes += 1;
    if (votes < 2) return null;                                     // no consensus

    return { side: LONG_, stop: c - p.atrMult * a, tag: 'turtle_break' };
  },

  /* The channel the position leaves on, recomputed every bar. Long only, so
     there is no short branch to report. */
  exitLevel: (i, { series }) => series.exitLo[i],
};
