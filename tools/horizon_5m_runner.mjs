/* Driver for tools/horizon_5m_eval.py — see that file for what is compared and
   how the comparison is kept honest. Run by hand as:

     node --max-old-space-size=4096 tools/horizon_5m_runner.mjs <cfg.json>

   where cfg.json is {"barsPath": "...", "tf": "5m", "cell": "XAUUSD.a|5m"}. */

import fs from 'node:fs';
import { runRule, rollingShifted } from '../js/chart/rules.js';
import { BARS_PER_DAY, donchianRule, paramsForTf } from '../js/chart/donchian.js';
import { atrSeries } from '../js/chart/tlengine.js';

const cfg = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const bars = JSON.parse(fs.readFileSync(cfg.barsPath, 'utf8'));
const tf = cfg.tf || '5m';
const perDay = BARS_PER_DAY[tf];

/* THE CHANNEL LENGTHS, NAMED IN TIME. The rule's own map turns a timeframe into
   a 3.3-day channel; this asks whether some SHORTER duration is what an
   intraday frame wants. Every row is a duration first and a bar count second,
   because comparing "N=144" across timeframes is the mistake the horizon map
   exists to stop. `native 20/10` is the exception and is not a duration at all
   -- it is what a reader sees when they say "there are trades here". */
const HOURS = [
  ['native 20/10', null],
  ['2h', 2], ['4h', 4], ['8h', 8], ['12h', 12],
  ['24h', 24], ['48h', 48],
  ['79.2h (3.3d)', 3.3 * 24],
];

/* AN O(n) ROLLING EXTREME, CHECKED AGAINST THE APP'S OWN O(n*N) ONE.
 *
 * `rollingShifted` in js/chart/rules.js is what every surface uses, and it
 * rescans the whole window at each bar: on 700k 5m bars with N=950 that is
 * ~660M comparisons per series, four series per variant, eight variants per
 * symbol. A monotonic deque gives the same answer in one pass.
 *
 * THE SUBSTITUTION IS THE RISK, so it is not taken on trust: `check()` runs
 * both over the first 40k bars of the real series and throws on the first
 * disagreement. A faster implementation that quietly differed would move every
 * signal and look perfectly normal on the way past. */
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

check(bars.map((b) => b.h), 950, true);
check(bars.map((b) => b.l), 475, false);
check(bars.map((b) => b.h), 20, true);
process.stderr.write(`  rolling parity ok (${bars.length} bars)\n`);

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

const out = { bars: bars.length, tf, cell: cfg.cell, runs: {}, params: {} };

for (const [name, hours] of HOURS) {
  let p;
  if (hours === null) {
    p = { entry: 20, exit: 10 };
  } else {
    const n = Math.max(5, Math.round((hours / 24) * perDay));
    p = { entry: n, exit: Math.max(2, Math.floor(n / 2)) };
  }
  process.stderr.write(`  ${cfg.cell} ${name}: N=${p.entry}/${p.exit}\n`);
  const r = runRule(bars, fastRule, { tf, ...p });
  out.params[name] = p;
  out.runs[name] = r.trades.map((t) => ({
    side: t.side, r: t.r, reason: t.reason,
    entryI: t.entryI, entryTime: t.entryTime, bars: t.exitI - t.entryI,
  }));
}

/* What the app actually runs on this frame, recorded so the report can state
   whether the baseline row IS the shipped configuration rather than assuming
   it. */
out.shipped = paramsForTf(tf);

console.log(JSON.stringify(out));
