/* indicators.js — the study library.
 *
 * Every indicator is a pure function of the bar array. `calc` returns plot
 * descriptors the renderer draws blindly, so adding a study needs no renderer
 * change: give it plots, a pane target, and a legend formatter.
 *
 *   pane: 'main'    drawn over price
 *   pane: 'volume'  the short histogram sub-pane
 *   pane: 'own'     its own stacked pane with an independent scale
 */

import { findDivergences, rsiSeries } from './divergence.js';
import { rollingShifted } from './rules.js';

const C = {
  pink: '#E31C79', navy: '#4959ff', green: '#93C90F', orange: '#FF9E1B',
  grey: '#B1B3B3', cyan: '#31c7d6', violet: '#a86bff', white: '#e8eef6',
};

/* An EMA over a series that STARTS with nulls, seeded from the first real
   value rather than through them.

   `ema` above seeds with an SMA of the first `len` entries, which is right for
   price and wrong for anything derived: RSI's first `length` values are null,
   and averaging nulls yields NaN that then decays through the entire series.
   MACD sidesteps it by substituting 0, which is safe for a series centred on
   zero and would be visibly wrong here -- an RSI EMA seeded at 0 climbs toward
   50 over its first bars and draws a hook that never happened. */
function emaAfterNulls(vals, len) {
  const out = new Array(vals.length).fill(null);
  const start = vals.findIndex((v) => v !== null && v !== undefined && !Number.isNaN(v));
  if (start < 0) return out;
  const e = ema(vals.slice(start), len);
  for (let i = 0; i < e.length; i++) out[start + i] = e[i];
  return out;
}

const src = (bars, key) => bars.map((b) => (key === 'hl2' ? (b.h + b.l) / 2 : b[key ?? 'c']));

function sma(vals, len) {
  const out = new Array(vals.length).fill(null);
  let sum = 0;
  for (let i = 0; i < vals.length; i++) {
    sum += vals[i];
    if (i >= len) sum -= vals[i - len];
    if (i >= len - 1) out[i] = sum / len;
  }
  return out;
}

function ema(vals, len) {
  const out = new Array(vals.length).fill(null);
  const k = 2 / (len + 1);
  let prev = null;
  for (let i = 0; i < vals.length; i++) {
    if (i === len - 1) {
      let s = 0;
      for (let j = 0; j < len; j++) s += vals[j];
      prev = s / len;
    } else if (i >= len) {
      prev = vals[i] * k + prev * (1 - k);
    }
    out[i] = i >= len - 1 ? prev : null;
  }
  return out;
}

/** Wilder smoothing — used by RSI and ATR so values match MetaTrader. */
function wilder(vals, len) {
  const out = new Array(vals.length).fill(null);
  let prev = null;
  for (let i = 0; i < vals.length; i++) {
    if (i === len - 1) {
      let s = 0;
      for (let j = 0; j < len; j++) s += vals[j];
      prev = s / len;
    } else if (i >= len) {
      prev = (prev * (len - 1) + vals[i]) / len;
    }
    out[i] = i >= len - 1 ? prev : null;
  }
  return out;
}

function stdev(vals, len, basis) {
  const out = new Array(vals.length).fill(null);
  for (let i = len - 1; i < vals.length; i++) {
    const mean = basis[i];
    if (mean === null) continue;
    let s = 0;
    for (let j = i - len + 1; j <= i; j++) s += (vals[j] - mean) ** 2;
    out[i] = Math.sqrt(s / len);
  }
  return out;
}

function trueRange(bars) {
  return bars.map((b, i) => (i === 0
    ? b.h - b.l
    : Math.max(b.h - b.l, Math.abs(b.h - bars[i - 1].c), Math.abs(b.l - bars[i - 1].c))));
}

/** Heikin Ashi transform, used by the chart-type switch rather than as a study. */
export function heikinAshi(bars) {
  const out = [];
  let po = null, pc = null;
  for (const b of bars) {
    const c = (b.o + b.h + b.l + b.c) / 4;
    const o = po === null ? (b.o + b.c) / 2 : (po + pc) / 2;
    out.push({ ...b, o, c, h: Math.max(b.h, o, c), l: Math.min(b.l, o, c) });
    po = o; pc = c;
  }
  return out;
}

export const INDICATORS = {
  ema: {
    label: 'EMA', pane: 'main', inputs: { length: 21 },
    calc: (bars, o) => [{ type: 'line', label: `EMA ${o.length}`, color: C.orange, data: ema(src(bars), o.length) }],
  },
  sma: {
    label: 'SMA', pane: 'main', inputs: { length: 50 },
    calc: (bars, o) => [{ type: 'line', label: `SMA ${o.length}`, color: C.cyan, data: sma(src(bars), o.length) }],
  },
  bb: {
    label: 'Bollinger Bands', pane: 'main', inputs: { length: 20, mult: 2 },
    calc: (bars, o) => {
      const v = src(bars);
      const basis = sma(v, o.length);
      const sd = stdev(v, o.length, basis);
      const up = basis.map((b, i) => (b === null || sd[i] === null ? null : b + o.mult * sd[i]));
      const dn = basis.map((b, i) => (b === null || sd[i] === null ? null : b - o.mult * sd[i]));
      return [
        { type: 'band', label: `BB ${o.length}`, color: 'rgba(73,89,255,.13)', upper: up, lower: dn },
        { type: 'line', label: 'upper', color: C.navy, data: up, width: 1 },
        { type: 'line', label: 'basis', color: C.grey, data: basis, width: 1, dash: [4, 3] },
        { type: 'line', label: 'lower', color: C.navy, data: dn, width: 1 },
      ];
    },
  },
  donchian: {
    label: 'Donchian channel', pane: 'main', inputs: { entry: 20, exit: 10 },
    calc: (bars, o) => {
      // rollingShifted is the SAME function js/chart/donchian.js uses to make
      // the trading decision, so the drawn channel cannot disagree with the
      // rule that fires on it. The shift matters: the channel excludes the bar
      // being decided, which is why the line looks one bar behind price.
      const hi = bars.map((b) => b.h);
      const lo = bars.map((b) => b.l);
      const up = rollingShifted(hi, o.entry, Math.max);
      const dn = rollingShifted(lo, o.entry, Math.min);
      // The 10-bar channel is the EXIT, and only one side of it is live at a
      // time: exitLo closes a long, exitHi closes a short. Both are drawn
      // because which one is live depends on a position the chart does not
      // know about -- the panel names the live one.
      const xu = rollingShifted(hi, o.exit, Math.max);
      const xd = rollingShifted(lo, o.exit, Math.min);
      const fin = (a) => a.map((v) => (Number.isFinite(v) ? v : null));
      return [
        { type: 'line', label: `upper ${o.entry}`, color: C.green, data: fin(up), width: 1.4 },
        { type: 'line', label: `lower ${o.entry}`, color: C.pink, data: fin(dn), width: 1.4 },
        { type: 'line', label: `exit ${o.exit} hi`, color: C.grey, data: fin(xu), width: 1, dash: [3, 3] },
        { type: 'line', label: `exit ${o.exit} lo`, color: C.grey, data: fin(xd), width: 1, dash: [3, 3] },
      ];
    },
  },
  vwap: {
    label: 'VWAP (session)', pane: 'main', inputs: {},
    calc: (bars) => {
      const out = new Array(bars.length).fill(null);
      let pv = 0, vv = 0, day = null;
      bars.forEach((b, i) => {
        const d = new Date(b.t).getUTCDate();
        if (d !== day) { day = d; pv = 0; vv = 0; }
        const tp = (b.h + b.l + b.c) / 3;
        const vol = b.v || b.ticks || 1;
        pv += tp * vol; vv += vol;
        out[i] = vv ? pv / vv : null;
      });
      return [{ type: 'line', label: 'VWAP', color: C.violet, data: out, width: 1.6 }];
    },
  },
  volume: {
    label: 'Volume', pane: 'volume', inputs: {},
    calc: (bars) => [{
      type: 'histogram', label: 'Vol', data: bars.map((b) => b.v || b.ticks || 0),
      colors: bars.map((b) => (b.c >= b.o ? 'rgba(147,201,15,.5)' : 'rgba(227,28,121,.5)')),
      zeroBase: true, fmt: 'compact',
    }],
  },
  rsidiv: {
    label: 'RSI + divergence', pane: 'own',
    /* `oversold`/`overbought` are the DIVERGENCE search thresholds, not the
       bands you read. They stay at 40/60 because that is what findDivergences
       was measured with; changing them changes which divergences exist, not
       just what the pane looks like.

       `bandHigh/Mid/Low` are the READING guides -- 70 / 50 / 20 -- and are
       drawn instead. Keeping both sets of lines put five horizontals in a pane
       this short, which is more furniture than signal. */
    inputs: { length: 14, strength: 3, minRsiGap: 2, oversold: 40, overbought: 60,
              bandHigh: 80, bandMid: 50, bandLow: 20, emaLen: 9,
              includeHidden: 0 },
    calc: (bars, o) => {
      const rsi = rsiSeries(bars, o.length);
      const divs = findDivergences(bars, {
        rsiLen: o.length, strength: o.strength, minRsiGap: o.minRsiGap,
        oversold: o.oversold, overbought: o.overbought,
        includeHidden: !!o.includeHidden,
      });
      /* Hidden divergence is drawn DIMMER and dashed, and that is a judgement
         about evidence rather than taste. Measured at the confirmation bar
         against matched control candles over three eras, hidden returned
         -1.51 / -0.24 / +0.07 pp -- no information at all, and regular is
         barely better (+0.12 / +0.71 / +4.79, significant only in the era the
         work was developed on). Drawing them identically would imply the two
         carry comparable weight; neither carries much, and hidden carries
         less. */
      const hidden = (d) => d.kind.endsWith('_hidden');
      const col = (d) => (d.kind.startsWith('bullish') ? C.green : C.pink);
      // Two legs per divergence: the RSI leg in this pane and the PRICE leg on
      // the candles, because the whole point is that the two disagree.
      const rsiLegs = divs.map((d) => ({
        t1: d.prevT, v1: d.prevRsi, t2: d.t, v2: d.rsi, color: col(d),
        width: hidden(d) ? 1.2 : 2.2, dash: hidden(d) ? [3, 3] : undefined,
      }));
      const priceLegs = divs.map((d) => ({
        t1: d.prevT, v1: d.prevPrice, t2: d.t, v2: d.price, color: col(d),
        width: hidden(d) ? 1 : 1.4, dash: hidden(d) ? [2, 4] : [5, 3],
      }));
      // A pivot is only visible `strength` bars after it happens, so the bar the
      // divergence could first be ACTED on is marked separately from the swing
      // the line is drawn to. Hiding that lag is how divergence flatters itself.
      const marks = divs.map((d) => {
        const k = Math.min(d.confirmedI, bars.length - 1);
        return { t: bars[k].t, v: rsi[k] === null ? d.rsi : rsi[k],
                 color: col(d), up: d.kind === 'bullish' };
      });
      return [
        { type: 'range', min: 0, max: 100 },
        // the window divergences are searched in, shaded rather than outlined
        { type: 'zone', from: o.oversold, to: o.overbought, color: 'rgba(73,89,255,.06)' },
        /* The reading bands, NAMED. These are labels only: the numbers that
           decide which divergences exist are `oversold`/`overbought` above and
           they stay at 40/60, because moving them changes the detection rather
           than the picture. So the pane says overbought 80 / oversold 20 while
           the shaded 40-60 window is still where divergences are searched. */
        { type: 'level', value: o.bandHigh, color: 'rgba(227,28,121,.45)',
          dash: [3, 3], label: 'overbought ' + o.bandHigh, labelColor: C.pink },
        { type: 'level', value: o.bandMid, color: 'rgba(177,179,179,.40)',
          label: String(o.bandMid), labelColor: C.grey },
        { type: 'level', value: o.bandLow, color: 'rgba(147,201,15,.45)',
          dash: [3, 3], label: 'oversold ' + o.bandLow, labelColor: C.green },
        /* A SMOOTHED RSI, AND NOTHING MORE. Drawn thinner and dimmer than the
           RSI it follows, because it is context and not a second opinion: an
           EMA of RSI cannot carry information RSI does not, being a linear
           filter over a series that is already an average of averages.

           It is NOT a signal here, and that was measured rather than assumed.
           `rsi_ema_cross` -- long on RSI crossing above this line, short below
           -- was run through the full gates on XAUUSD 1h/4h/1d in and out of
           sample: 0 of 6 cells passed, -0.0079 R over 907 trades on the 4h cell
           where Donchian makes +0.1923 over 231. Four times the trading for
           less than nothing. See sim/strategies/rsi_ema_cross.py. */
        { type: 'line', label: 'EMA ' + o.emaLen, color: C.orange,
          data: emaAfterNulls(rsi, o.emaLen), width: 1 },
        // `tag` puts the live value on the axis, the way price has always had
        { type: 'line', label: 'RSI ' + o.length, color: C.violet, data: rsi,
          width: 1.5, tag: true, tagDigits: 2 },
        { type: 'segment', label: 'div', segs: rsiLegs },
        { type: 'mark', points: marks },
        { type: 'segment', label: 'div-price', pane: 'main', segs: priceLegs },
      ];
    },
  },

  macd: {
    label: 'MACD', pane: 'own', inputs: { fast: 12, slow: 26, signal: 9 },
    calc: (bars, o) => {
      const v = src(bars);
      const f = ema(v, o.fast), s = ema(v, o.slow);
      const macd = f.map((x, i) => (x === null || s[i] === null ? null : x - s[i]));
      const defined = macd.map((x) => (x === null ? 0 : x));
      const sig = ema(defined, o.signal).map((x, i) => (macd[i] === null ? null : x));
      const hist = macd.map((x, i) => (x === null || sig[i] === null ? null : x - sig[i]));
      return [
        {
          type: 'histogram', label: 'hist', data: hist, zeroBase: true,
          colors: hist.map((h) => (h >= 0 ? 'rgba(147,201,15,.55)' : 'rgba(227,28,121,.55)')),
        },
        { type: 'line', label: 'macd', color: C.cyan, data: macd, width: 1.5 },
        { type: 'line', label: 'signal', color: C.orange, data: sig, width: 1.5 },
        { type: 'level', value: 0, color: 'rgba(177,179,179,.28)' },
      ];
    },
  },
  atr: {
    label: 'ATR', pane: 'own', inputs: { length: 14 },
    calc: (bars, o) => [{
      type: 'line', label: `ATR ${o.length}`, color: C.orange,
      data: wilder(trueRange(bars), o.length), width: 1.5,
    }],
  },
  stoch: {
    label: 'Stochastic', pane: 'own', inputs: { k: 14, d: 3, smooth: 3 },
    calc: (bars, o) => {
      const raw = bars.map((b, i) => {
        if (i < o.k - 1) return null;
        let hi = -Infinity, lo = Infinity;
        for (let j = i - o.k + 1; j <= i; j++) { hi = Math.max(hi, bars[j].h); lo = Math.min(lo, bars[j].l); }
        return hi === lo ? 50 : ((b.c - lo) / (hi - lo)) * 100;
      });
      const k = sma(raw.map((x) => (x === null ? 0 : x)), o.smooth).map((x, i) => (raw[i] === null ? null : x));
      const d = sma(k.map((x) => (x === null ? 0 : x)), o.d).map((x, i) => (k[i] === null ? null : x));
      return [
        { type: 'line', label: '%K', color: C.cyan, data: k, width: 1.5 },
        { type: 'line', label: '%D', color: C.pink, data: d, width: 1.3 },
        { type: 'level', value: 80, color: 'rgba(227,28,121,.4)', dash: [3, 3] },
        { type: 'level', value: 20, color: 'rgba(147,201,15,.4)', dash: [3, 3] },
        { type: 'range', min: 0, max: 100 },
      ];
    },
  },
};

/** Run one configured study, returning its plots plus display metadata. */
export function runStudy(study, bars) {
  const def = INDICATORS[study.kind];
  if (!def || !bars.length) return null;
  const opts = { ...def.inputs, ...(study.inputs || {}) };
  return {
    id: study.id, kind: study.kind, label: def.label, pane: def.pane, opts,
    plots: def.calc(bars, opts),
  };
}

export const studyTitle = (study) => {
  const def = INDICATORS[study.kind];
  if (!def) return study.kind;
  const opts = { ...def.inputs, ...(study.inputs || {}) };
  const vals = Object.values(opts);
  return def.label + (vals.length ? ' ' + vals.join(' ') : '');
};
