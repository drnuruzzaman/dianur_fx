/* Driver for tools/horizon_eval.py — see that file for what is compared and how
   the comparison is kept honest. Run by hand as:

     node --max-old-space-size=6144 tools/horizon_runner.mjs <cfg.json>

   where cfg.json is {"barsPath": "...", "tf": "1h", "cell": "XAUUSD.a|1h"}. */

import fs from 'node:fs';
import { runRule, rollingShifted } from '../js/chart/rules.js';
import { BARS_PER_DAY, HORIZON_DAYS, donchianRule, paramsForTf } from '../js/chart/donchian.js';
import { atrSeries } from '../js/chart/tlengine.js';

const cfg = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const bars = JSON.parse(fs.readFileSync(cfg.barsPath, 'utf8'));
const tf = cfg.tf;

/* THE LADDER IS RELATIVE TO WHAT SHIPS, which is the only way one sweep can
   cover every timeframe at once.
 *
 * An absolute ladder in hours cannot: 2h is a 5-bar channel on 4h (noise) and a
 * 24-bar one on 5m, and on 1w every entry below a month rounds to nothing. A
 * ladder in MULTIPLES of the shipped horizon asks the same question everywhere
 * -- "is the duration this frame ships too short, right, or too long" -- and
 * the printed row carries the bar count and the duration so nobody has to take
 * the multiple on trust.
 *
 * `native 20/10` is the exception and is not a duration at all: it is the
 * literal parameter pair, what a reader sees when they say "there are trades
 * here". On 4h and above it IS the shipped rule, and the report says so rather
 * than printing the same row twice. */
const MULTIPLES = [0.1, 0.25, 0.5, 1, 2, 4];
const MIN_N = 8;                     // below this a "channel" is a few candles

const shipped = paramsForTf(tf);
const rows = [];
const seen = new Set();

function add(key, entry, exit) {
  /* A channel longer than a tenth of the series leaves too little history for
     20 calendar blocks to say anything -- and on 1w, where ten years is 520
     bars, a fifth would have dropped the 2x row that is the whole question up
     there. */
  if (!(entry >= MIN_N) || entry > bars.length / 10) return;
  const sig = `${entry}/${exit}`;
  if (seen.has(sig)) return;
  seen.add(sig);
  rows.push({ key, entry, exit });
}

add('native 20/10', 20, 10);
for (const m of MULTIPLES) {
  const n = Math.round(shipped.entry * m);
  add(m === 1 ? 'shipped' : `${m}x shipped`, n, Math.max(2, Math.floor(n / 2)));
}
/* AND ON A FRAME THE MAP DOES NOT COVER, THE MAP ITSELF IS A CANDIDATE.
 *
 * 1m and 1d fall outside HORIZON_TFS, so `paramsForTf` hands them the base
 * 20/10 -- on 1m that is a TWENTY-MINUTE channel, and it is what makes M1 the
 * one intraday chart that signals all day. The ladder above is multiples of
 * that 20, so without this row the sweep would never ask the obvious question:
 * how does the 20-minute channel compare with the 3.3-day one every other
 * intraday frame runs? On 1m that is N=4752. */
const perDay = BARS_PER_DAY[tf];
if (perDay) {
  const n = Math.max(5, Math.round(HORIZON_DAYS * perDay));
  if (n !== shipped.entry) add(`${HORIZON_DAYS}d horizon`, n, Math.max(2, Math.floor(n / 2)));
}

/* THE BASELINE MUST EXIST, whatever the guards above did with it: it is what
   every other row is measured against. Two cases the loop cannot cover.
 *
 * On 4h and above the shipped rule IS 20/10 -- `paramsForTf` returns the base
 * parameters outside HORIZON_TFS, and on 4h the 3.3-day channel happens to BE
 * 20 bars. The dedup then folds `1x shipped` into `native 20/10`, and the
 * baseline is that row rather than a missing one. Printing both would be the
 * same numbers twice under two names.
 *
 * And a short series can trip the `bars.length / 20` guard for the shipped row
 * itself, which would leave nothing to compare against. */
let baselineKey = (rows.find((r) => r.key === 'shipped')
                   || rows.find((r) => r.entry === shipped.entry
                                       && r.exit === shipped.exit) || {}).key;
if (!baselineKey) {
  rows.push({ key: 'shipped', entry: shipped.entry, exit: shipped.exit });
  baselineKey = 'shipped';
}

/* AN O(n) ROLLING EXTREME, CHECKED AGAINST THE APP'S OWN O(n*N) ONE.
 *
 * `rollingShifted` in js/chart/rules.js is what every surface uses, and it
 * rescans the whole window at each bar: on 740k 5m bars with N=950 that is
 * ~660M comparisons per series, four series per variant, seven variants per
 * symbol. A monotonic deque gives the same answer in one pass.
 *
 * THE SUBSTITUTION IS THE RISK, so it is not taken on trust: `check()` runs
 * both over the first 40k bars of the real series, at every N this cell will
 * actually use, and throws on the first disagreement. A faster implementation
 * that quietly differed would move every signal and look perfectly normal on
 * the way past. */
function rollingExtreme(values, n, wantMax) {
  const out = new Array(values.length).fill(NaN);
  const dq = [];                          // indices; their values stay monotonic
  let head = 0;
  for (let i = 0; i < values.length; i++) {
    /* The window for bar i is [i-n, i-1] -- STRICTLY BEFORE i, which is the
       shift(1) that keeps `close > highestHigh` satisfiable. */
    if (i > 0) {
      const v = values[i - 1];
      while (dq.length > head
             && (wantMax ? values[dq[dq.length - 1]] <= v
                         : values[dq[dq.length - 1]] >= v)) dq.pop();
      dq.push(i - 1);
    }
    while (dq.length > head && dq[head] < i - n) head += 1;
    if (i >= n) out[i] = values[dq[head]];
  }
  return out;
}

function check(values, n, wantMax) {
  const head = values.slice(0, Math.min(values.length, 40000));
  const slow = rollingShifted(head, n, wantMax ? Math.max : Math.min);
  const fast = rollingExtreme(head, n, wantMax);
  for (let i = 0; i < head.length; i++) {
    const a = slow[i];
    const b = fast[i];
    if (Number.isNaN(a) !== Number.isNaN(b) || (!Number.isNaN(a) && a !== b)) {
      throw new Error(`rolling mismatch at ${i} (n=${n}): ${a} vs ${b}`);
    }
  }
}

const highs = bars.map((b) => b.h);
const lows = bars.map((b) => b.l);
for (const r of rows) {
  check(highs, r.entry, true);
  check(lows, r.exit, false);
}
process.stderr.write(`  ${cfg.cell}: rolling parity ok over ${rows.length} `
                     + `channel lengths (${bars.length} bars)\n`);

/* The rule with its channels precomputed. `prepare` is overridden ONLY to swap
   the rolling implementation; ATR is the shared `atrSeries` the app uses, and
   `decide`, `warmup` and the whole trade lifecycle are untouched. `emaLen` is 0
   in the validated rule and this study does not turn it on. */
const fastRule = {
  ...donchianRule,
  prepare(view, p) {
    const h = view.map((b) => b.h);
    const l = view.map((b) => b.l);
    return {
      hi: rollingExtreme(h, p.entry, true),
      lo: rollingExtreme(l, p.entry, false),
      exitHi: rollingExtreme(h, p.exit, true),
      exitLo: rollingExtreme(l, p.exit, false),
      atr: atrSeries(view, p.atrLen),
    };
  },
};

const out = { bars: bars.length, tf, cell: cfg.cell, shipped, baselineKey,
              order: rows.map((r) => r.key), runs: {}, params: {} };

for (const r of rows) {
  process.stderr.write(`  ${cfg.cell} ${r.key}: N=${r.entry}/${r.exit}\n`);
  const res = runRule(bars, fastRule, { tf, entry: r.entry, exit: r.exit });
  out.params[r.key] = { entry: r.entry, exit: r.exit };
  out.runs[r.key] = res.trades.map((t) => ({
    side: t.side, r: t.r, reason: t.reason,
    entryI: t.entryI, entryTime: t.entryTime, bars: t.exitI - t.entryI,
  }));
}

console.log(JSON.stringify(out));
