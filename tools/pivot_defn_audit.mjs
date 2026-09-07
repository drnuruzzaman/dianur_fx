/* Does the PIVOT DEFINITION change what BOS/CHoCH says?
 *
 *     node --max-old-space-size=6144 tools/pivot_defn_audit.mjs [maxBars]
 *
 * THE QUESTION THIS ANSWERS, AND WHY IT COMES FIRST. Converting BOS/CHoCH and
 * zones to the ZigZag is a change to what the chart claims, so it needs
 * evidence. The cheapest evidence available needs no new detector at all:
 * js/chart/sensitivity.js already widens the fractal window per timeframe and
 * per volatility regime, and BOS/CHoCH and zones ignore it, taking a flat 3 on
 * every instrument and every frame.
 *
 * If moving them onto the definition this project ALREADY calibrated changes
 * their output and their measured edge, then the definition matters and the
 * ZigZag conversion is worth measuring properly. If it changes almost nothing,
 * that is evidence these detectors are not sensitive to the definition, and
 * the conversion is cosmetics.
 *
 * THREE ARMS, one per definition of "where did price turn":
 *
 *   fixed     strength 3 everywhere         what ships today
 *   adaptive  BASE_STRENGTH[tf] + regime    js/chart/sensitivity.js
 *   zigzag    rank >= 1 turns               the proposed conversion
 *
 * WHAT IS MEASURED. Two things, and the second is the one that decides:
 *
 *   AGREEMENT  how many events survive the change, matched on the BROKEN
 *              LEVEL's bar rather than the breaking bar -- two definitions can
 *              notice the same break a bar apart while agreeing entirely about
 *              which swing was taken.
 *
 *   EDGE       the hit rate after an event: did price close further in the
 *              event's direction HORIZON bars later. Reported against the
 *              unconditional rate on the same bars, which is the matched
 *              control -- an event that fires in an uptrend inherits the
 *              uptrend's drift and has to beat it, not zero.
 *
 * TWO ERAS, always, because a result that does not survive both is one era's
 * accident. Split at 2021-01-01 to match every other measurement here.
 */

import fs from 'node:fs';
import zlib from 'node:zlib';
import path from 'node:path';
import { detect, BULL, BEAR } from '../js/chart/marketstructure.js';
import { BASE_STRENGTH, volRegime, strengthFor } from '../js/chart/sensitivity.js';
import { atrSeries } from '../js/chart/tlengine.js';
import { nestedSwings } from '../js/chart/structure.js';

const MAX = Number(process.argv[2] || 200000);
const HORIZON = 20;
const ERA_SPLIT = Date.UTC(2021, 0, 1);
const ROOT = path.join(path.dirname(new URL(import.meta.url).pathname.slice(1)), '..');
const BARS = path.join(ROOT, 'data', 'bars');
const CELLS = [['XAUUSD.a', '15m'], ['XAUUSD.a', '1h'], ['XAUUSD.a', '4h'],
               ['EURUSD.a', '15m'], ['EURUSD.a', '1h'], ['EURUSD.a', '4h'],
               ['USDJPY.a', '1h'], ['GBPUSD.a', '1h']];

function loadBars(dir, max) {
  const rows = [];
  const files = fs.readdirSync(dir).filter((x) => x.endsWith('.csv.gz')).sort();
  for (const f of files.slice().reverse()) {
    const text = zlib.gunzipSync(fs.readFileSync(path.join(dir, f))).toString('utf8');
    const lines = text.split('\n');
    const head = lines[0].trim().split(',');
    const ix = Object.fromEntries(head.map((k, i) => [k, i]));
    for (let i = 1; i < lines.length; i++) {
      const c = lines[i].trim().split(',');
      if (c.length < 5) continue;
      rows.push({ t: Number(c[ix.ts]) * 1000, o: +c[ix.open], h: +c[ix.high],
                  l: +c[ix.low], c: +c[ix.close] });
    }
    if (rows.length >= max) break;
  }
  rows.sort((a, b) => a.t - b.t);
  return rows.length > max ? rows.slice(-max) : rows;
}

/* The adaptive window is a PER-BAR quantity and `detect` takes one number, so
   the honest whole-series version is a mixture: run each strength the regime
   actually selects, and keep an event only on the bars whose regime selected
   that strength. Causal, because volRegime(i) reads bars <= i. */
function adaptiveEvents(bars, tf, atr) {
  const want = new Array(bars.length);
  const mix = new Map();
  for (let i = 0; i < bars.length; i++) {
    const s = strengthFor(tf, volRegime(atr, i, 500)[0]);
    want[i] = s;
    mix.set(s, (mix.get(s) || 0) + 1);
  }
  const out = [];
  for (const s of [...mix.keys()].sort()) {
    for (const e of detect(bars, { strength: s }).events) {
      if (want[e.i] === s) out.push(e);
    }
  }
  out.sort((a, b) => a.i - b.i);
  const share = {};
  for (const [s, n] of mix) share[s] = (100 * n / bars.length).toFixed(0) + '%';
  return { events: out, share };
}

/* BOS/CHoCH from ZigZag turns: the SAME RULE -- a close through the last
   confirmed swing -- with the swings coming from the ranked ZigZag instead of
   a fractal. Written here rather than in marketstructure.js because this is a
   measurement, not yet a change to what any chart draws. */
function zigzagEvents(bars, sens) {
  const sw = nestedSwings(bars, { sens: sens || 'normal' })
    .filter((s) => (s.rank || 0) >= 1);
  const byConf = Array.from({ length: bars.length + 1 }, () => []);
  for (const s of sw) if (s.confirmedI < bars.length) byConf[s.confirmedI].push(s);

  let bias = 'neutral', sh = null, sl = null;
  const events = [];
  for (let i = 0; i < bars.length; i++) {
    for (const q of byConf[i]) {
      if (q.isHigh) sh = { price: q.price, i: q.i };
      else sl = { price: q.price, i: q.i };
    }
    const c = bars[i].c;
    if (sh && c > sh.price) {
      events.push({ kind: bias === BULL ? 'bos' : 'choch', direction: BULL,
                    i, level: sh.price, levelI: sh.i });
      bias = BULL; sh = null;
    } else if (sl && c < sl.price) {
      events.push({ kind: bias === BEAR ? 'bos' : 'choch', direction: BEAR,
                    i, level: sl.price, levelI: sl.i });
      bias = BEAR; sl = null;
    }
  }
  return events;
}

/* Hit rate after an event, against the unconditional rate on the same bars. */
function edge(bars, events, from, to) {
  const inEra = (i) => bars[i].t >= from && bars[i].t < to;
  let hit = 0, n = 0, nBull = 0;
  for (const e of events) {
    const j = e.i + HORIZON;
    if (j >= bars.length || !inEra(e.i)) continue;
    const rose = bars[j].c > bars[e.i].c;
    const bull = e.direction === BULL;
    if (rose === bull) hit++;
    if (bull) nBull++;
    n++;
  }
  /* THE CONTROL IS EVERY BAR IN THE ERA -- AND IT HAS TO BE TAKEN IN THE
     EVENT'S OWN DIRECTION.
     
     The first version of this compared every event against P(up), bearish ones
     included, so in a rising market a bearish detector was scored against the
     rate of the thing it was betting against and came out looking terrible for
     free. Gold's era-2 P(up) over 20 bars is well above 50, and that alone
     moved the pooled number by several points.
     
     The control is therefore the direction-weighted mixture: bullish events
     answer to P(up), bearish to P(down), each weighted by how many of that
     kind actually fired. That is the drift an event inherits simply by firing
     in that direction, and it is what it has to beat. */
  let up = 0, bn = 0;
  for (let i = 0; i + HORIZON < bars.length; i++) {
    if (!inEra(i)) continue;
    if (bars[i + HORIZON].c > bars[i].c) up++;
    bn++;
  }
  const pUp = bn ? up / bn : NaN;
  const base = n ? 100 * (nBull * pUp + (n - nBull) * (1 - pUp)) / n : NaN;
  return { n, nBull, rate: n ? 100 * hit / n : NaN, base };
}

const key = (e) => e.levelI + '|' + e.direction;
function agree(a, b) {
  const B = new Set(b.map(key));
  return a.length ? 100 * a.filter((e) => B.has(key(e))).length / a.length : NaN;
}

console.log('horizon ' + HORIZON + ' bars; eras split 2021-01-01; edge = hit% - unconditional%');
console.log('');
console.log('cell             arm         events  agree   era1                 era2');
for (const [sym, tf] of CELLS) {
  const dir = path.join(BARS, sym, tf);
  if (!fs.existsSync(dir)) continue;
  const bars = loadBars(dir, MAX);
  const atr = atrSeries(bars, 14);
  const t0 = bars[0].t, t1 = bars[bars.length - 1].t;

  const fixed = detect(bars).events;
  const ad = adaptiveEvents(bars, tf, atr);
  const zz = zigzagEvents(bars);

  const show = (name, ev, ref) => {
    /* z, so a 2pp move on 250 events is not read like a 2pp move on 10,000.
       SE of a proportion at p~0.5 is 50/sqrt(n) in points; the control comes
       from the whole era and carries far more samples, so its own error is
       ignored -- which makes this z slightly optimistic, not conservative. */
    const fmt = (e) => (Number.isFinite(e.rate)
      ? (e.rate - e.base >= 0 ? '+' : '') + (e.rate - e.base).toFixed(1) + 'pp'
        + ' z=' + ((e.rate - e.base) / (50 / Math.sqrt(e.n))).toFixed(1)
        + ' n=' + String(e.n).padEnd(6)
      : '—'.padEnd(24));
    const ag = ref ? agree(ev, ref).toFixed(0) + '%' : '';
    console.log((sym + ' ' + tf).padEnd(16) + name.padEnd(11)
      + String(ev.length).padStart(6) + ag.padStart(7) + '   '
      + fmt(edge(bars, ev, t0, ERA_SPLIT)) + ' ' + fmt(edge(bars, ev, ERA_SPLIT, t1 + 1)));
  };
  show('fixed', fixed, null);
  show('adaptive', ad.events, fixed);
  show('zigzag', zz, fixed);
  console.log(''.padEnd(16) + 'window mix ' + JSON.stringify(ad.share)
    + '   base ' + BASE_STRENGTH[tf]);
}
