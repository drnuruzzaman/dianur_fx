/* DOES AN `external` BREAK BREAK A RINGED SWING?
 *
 *     node --max-old-space-size=8192 tools/external_ring_agree.mjs [maxBars]
 *
 * WHY THIS EXISTS. Two layers on the chart both claim to say "this is major
 * structure", and they stopped agreeing by construction when the swing marks
 * changed:
 *
 *   external   js/main.js runs detectMS a SECOND time at MAJOR_STRENGTH (6) and
 *              marks a break external when the wider fractal pass broke the
 *              same level. A SHAPE test: is this the highest of 13 bars?
 *   ringed     js/chart/structure.js nestedSwings, rank >= RING_FROM.normal.
 *              A PRICE test: did the leg into this turn run 3 ATR?
 *
 * They used to coincide because a ringed swing WAS "survives strength 6". The
 * rings are now ZigZag turns and this pass was deliberately left alone -- the
 * comment in main.js says so and says why: making them match would be a claim
 * about BOS/CHoCH, and a claim needs evidence. This is the evidence.
 *
 * WHAT AGREEMENT WOULD MEAN. If `external` and `ringed` pick out the same
 * breaks, one of the two definitions is redundant and the chart is drawing the
 * same distinction twice in two vocabularies. If they disagree, the `i` prefix
 * on an internal break and the ring on a swing are telling a reader two
 * different things, and the label `iBOS` beside a ringed swing is not the
 * contradiction it looks like.
 *
 * MATCHING IS ON THE BROKEN LEVEL'S BAR AND ITS KIND. `levelI` plus whether
 * the break took a swing HIGH (a bullish event) or a swing LOW. Matching on the
 * breaking bar would be wrong for the same reason main.js does not: the two
 * passes can notice one break a bar apart while agreeing entirely about which
 * swing was taken.
 *
 * THE CAUSAL COLUMN. A ZigZag turn is only known once the retracement that
 * confirms it completes, so `conf%` reports how often the ringed turn was
 * already confirmed at the bar the break fired. A ring that only becomes a ring
 * afterwards cannot have informed anything, and any future attempt to gate
 * breaks on rings has to survive that column, not the raw one.
 */

import fs from 'node:fs';
import zlib from 'node:zlib';
import path from 'node:path';
import { detect as detectMS, BULL } from '../js/chart/marketstructure.js';
import { nestedSwings, zigzag, fold, SWING_TIERS, RING_FROM } from '../js/chart/structure.js';
import { atrSeries } from '../js/chart/tlengine.js';
import { SENSITIVITY } from '../js/chart/trendlines.js';

const MAX = Number(process.argv[2] || 40000);
const STRENGTH = 3;                                   // the shipped default
const MAJOR_STRENGTH = SENSITIVITY.major.strength;    // 6
const ROOT = path.join(path.dirname(new URL(import.meta.url).pathname.slice(1)), '..');
const CELLS = [['XAUUSD.a', '5m'], ['XAUUSD.a', '15m'], ['XAUUSD.a', '1h'],
               ['XAUUSD.a', '4h'], ['EURUSD.a', '15m'], ['EURUSD.a', '1h'],
               ['GBPUSD.a', '1h'], ['USDJPY.a', '1h']];

function loadBars(dir, max) {
  const rows = [];
  for (const f of fs.readdirSync(dir).filter((x) => x.endsWith('.csv.gz')).sort().reverse()) {
    const text = zlib.gunzipSync(fs.readFileSync(path.join(dir, f))).toString('utf8');
    const lines = text.split('\n');
    const ix = Object.fromEntries(lines[0].trim().split(',').map((k, i) => [k, i]));
    for (let i = 1; i < lines.length; i++) {
      const c = lines[i].trim().split(',');
      if (c.length < 5) continue;
      rows.push({ t: Number(c[ix.ts]) * 1000, o: +c[ix.open], h: +c[ix.high],
                  l: +c[ix.low], c: +c[ix.close], v: 0 });
    }
    if (rows.length >= max) break;
  }
  rows.sort((a, b) => a.t - b.t);
  return rows.length > max ? rows.slice(-max) : rows;
}

const K = (i, isHigh) => i + '|' + isHigh;

function cell(bars) {
  const r = detectMS(bars, { strength: STRENGTH });
  const major = detectMS(bars, { strength: MAJOR_STRENGTH });
  const majorLevels = new Set(major.events.map((e) => e.levelI));

  /* The ring set exactly as the chart draws it. */
  const ringed = new Set(nestedSwings(bars, { sens: 'normal' })
    .filter((s) => (s.rank || 0) >= RING_FROM.normal)
    .map((s) => K(s.i, s.isHigh)));

  /* The same tier again, rebuilt from zigzag+fold, only to recover
     `confirmedI` -- nestedSwings drops it on the way through labelSwings. */
  const atr = atrSeries(bars, 14);
  let tier = zigzag(bars, { atrLen: 14, atrMult: SWING_TIERS[0] });
  for (let k = 1; k <= RING_FROM.normal; k++) tier = fold(tier, atr, SWING_TIERS[k]);
  const confAt = new Map(tier.map((z) => [K(z.i, z.isHigh), z.confirmedI]));

  /* Was the level a ZigZag turn AT ALL, at any tier? Separates "the ring test
     said no" from "the ZigZag never saw this bar as a turn". */
  const anyTurn = new Set(zigzag(bars, { atrLen: 14, atrMult: SWING_TIERS[0] })
    .map((z) => K(z.i, z.isHigh)));

  let n = 0, ee = 0, rr = 0, both = 0, neither = 0, extOnly = 0, ringOnly = 0;
  let ringConf = 0, notATurn = 0;
  for (const e of r.events) {
    const isHigh = e.direction === BULL;      // a bullish break takes a swing HIGH
    const k = K(e.levelI, isHigh);
    const ext = majorLevels.has(e.levelI);
    const ring = ringed.has(k);
    n++;
    if (ext) ee++;
    if (ring) rr++;
    if (ext && ring) both++;
    else if (ext) extOnly++;
    else if (ring) ringOnly++;
    else neither++;
    if (ring && confAt.get(k) !== undefined && confAt.get(k) <= e.i) ringConf++;
    if (!anyTurn.has(k)) notATurn++;
  }
  const agree = both + neither;
  /* Cohen's kappa: agreement above what two labels of these base rates would
     reach by chance. Raw agreement alone flatters any pair where both labels
     are rare, and both of these are. */
  const pe = ((ee / n) * (rr / n)) + (((n - ee) / n) * ((n - rr) / n));
  const kappa = (agree / n - pe) / (1 - pe);
  return { n, ext: 100 * ee / n, ring: 100 * rr / n, both: 100 * both / n,
           extOnly: 100 * extOnly / n, ringOnly: 100 * ringOnly / n,
           neither: 100 * neither / n, agree: 100 * agree / n, kappa,
           conf: rr ? 100 * ringConf / rr : NaN, notATurn: 100 * notATurn / n };
}

const COLS = [14, 6, 7, 7, 7, 9, 9, 9, 8, 7, 7, 7];
const row = (...c) => c.map((v, i) => (i ? String(v).padStart(COLS[i]) : String(v).padEnd(COLS[i]))).join(' ');
const f1 = (v) => (Number.isFinite(v) ? v.toFixed(1) : '--');

console.log('EXTERNAL vs RINGED  strength %d against MAJOR %d  maxBars=%d',
            STRENGTH, MAJOR_STRENGTH, MAX);
/* One string, no format args. console.log only collapses %% while it is
   FORMATTING, so the escaped form printed literally in these four lines. */
console.log("ext%/ring% are the two labels' own rates; both/extOnly/ringOnly/neither");
console.log('are the 2x2. kappa is agreement above chance: 0 = independent, 1 = identical.');
console.log('conf% = of ringed matches, how many were CONFIRMED by the break bar.');
console.log('turn0% = breaks whose level the ZigZag never saw as a turn at all.');
console.log('');
console.log(row('cell', 'n', 'ext%', 'ring%', 'both%', 'extOnly%', 'ringOnly%',
                'neither%', 'agree%', 'kappa', 'conf%', 'turn0%'));
console.log('-'.repeat(107));

for (const [sym, tf] of CELLS) {
  const dir = path.join(ROOT, 'data', 'bars', sym, tf);
  if (!fs.existsSync(dir)) { console.log(row(sym + ' ' + tf, 'no bars')); continue; }
  const bars = loadBars(dir, MAX);
  if (bars.length < 2000) { console.log(row(sym + ' ' + tf, bars.length)); continue; }
  const c = cell(bars);
  console.log(row(sym.replace('.a', '') + ' ' + tf, c.n, f1(c.ext), f1(c.ring),
                  f1(c.both), f1(c.extOnly), f1(c.ringOnly), f1(c.neither),
                  f1(c.agree), c.kappa.toFixed(3), f1(c.conf), f1(c.notATurn)));
}
