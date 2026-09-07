/**
 * smcretest.js -- an INTRADAY candidate built from structure, not from a
 * channel. Nothing here is validated. Read the last section before using it.
 *
 * WHY IT EXISTS. The Donchian rule holds its horizon fixed at 3.3 days, so on
 * 5m that is a 950-bar channel and the chart sits flat through moves a reader
 * can see all day. Three sweeps say the fix is NOT to shorten that channel:
 * the literal 20/10 loses by ~3,600 R on 5m and ~1,230 R on 15m against the
 * shipped horizon, in both eras, on symbols that fed none of the tuning
 * (logs/horizon_5m_eval.txt, logs/horizon_eval.txt, logs/horizon_holdout.txt).
 * A shorter channel is not a different idea; it is the same idea taking worse
 * trades more often.
 *
 * So this is a different idea, assembled from detectors the app already has and
 * already draws: market structure decides the direction, a supply/demand base
 * decides where the trade is allowed to start, and the base's own edge decides
 * the stop. It is the shape a discretionary trader describes -- break of
 * structure, pull back into the zone that caused it, continue -- written down
 * precisely enough to be measured.
 *
 * THE RULE, IN FULL.
 *
 *   BIAS.     js/chart/marketstructure.js keeps a running bias: BULL after a
 *             close above the last swing high, BEAR after a close below the
 *             last swing low. Long trades are taken only while BULL, short only
 *             while BEAR. The bias array is built forward, one bar at a time,
 *             from pivots confirmed `strength` bars after they print -- so bar
 *             i's bias is knowable at bar i.
 *
 *   ZONE.     The newest supply/demand base created at or before bar i-1, on
 *             the side the bias wants (demand for a long), still alive, and no
 *             older than `maxAgeBars`. `bases()` in js/chart/supplydemand.js is
 *             the creation half of that detector with the forward scan removed;
 *             see its note for why using `detect()` here would be survivorship
 *             bias rather than a strategy.
 *
 *   ALIVE.    A demand base dies the first time a close prints below its low
 *             (supply, above its high). Computed here, forward from creation,
 *             and consulted only for bars at or after the bar being decided --
 *             so a zone that dies at bar 900 is still tradeable at bar 800.
 *
 *   TRIGGER.  Price trades INTO the zone on bar i (the low reaches its high for
 *             a long) and the bar closes back OUT of it, above the zone's high.
 *             One bar, no waiting: an entry that needs a second confirming bar
 *             is a different rule and would need measuring as one.
 *
 *   ENTRY.    The next open, like every other rule here. The walker does it.
 *
 *   STOP.     `stopBufferAtr` beyond the FAR edge of the zone -- below the
 *             demand base's low for a long. Not 2 ATR: the whole claim of a
 *             zone entry is that the zone is where the trade is wrong, and a
 *             stop measured in ATR instead would make the zone decorative.
 *             Trades whose stop is further than `maxRiskAtr` are skipped rather
 *             than resized; a base that wide is not a level.
 *
 *   EXIT.     An opposite CHoCH -- structure changing against the trade -- and
 *             the stop. Plus whatever trail the caller supplies, which is how
 *             the structural trail already measured in js/chart/trailmode.js
 *             can be applied to this rule too. NO TARGET, for the reason
 *             recorded in logs/tp_struct_eval.txt.
 *
 * WHAT WOULD HAVE TO BE TRUE FOR THIS TO WORK, stated before any measurement,
 * because it is the thing the numbers have to beat: that a base price left in a
 * hurry marks a price other participants will defend again, that structure
 * tells you which side to take, and that the two together pick entries better
 * than the same number of coin flips at the same stop distance would.
 *
 * ELEVEN GATES ALREADY FAILED THAT TEST. tools/entry_filter_eval.py measured
 * headroom, thrust, ADX, EMA regime and combinations, each against a random
 * gate held to the same retention rate: none beat its matched control. The
 * durable finding there was that the EDGE IS THE CELL, not the signal -- gold
 * and yen positive in both eras, EURUSD negative, GBPUSD flipping. That is the
 * prior this rule is up against, and tools/smc_eval.py is where it gets tested,
 * with a matched random-entry control as the row that decides.
 *
 * WHAT THE MEASUREMENT SAID -- run before this file was used for anything, and
 * recorded here rather than in a commit message. tools/smc_eval.py, four
 * symbols, 5m and 15m, both eras, ~2.5M bars per frame, gross
 * (logs/smc_eval.txt). Pooled, against the matched coin flip:
 *
 *     15m   smc      -0.4 R [-215.9,+222.9]     -27.9 R [-317.4,+256.8]
 *     5m    smc    +395.3 R [ -62.5,+835.6]    +582.1 R [ +59.5,+1083.8]
 *
 * NOT DEMONSTRATED on either frame: 15m straddles zero twice, and 5m only
 * clears zero in the recent era. That alone would be the twelfth idea to die
 * against a matched control in this project.
 *
 * BUT THE CONTROL ROWS SAY SOMETHING WORSE THAN "NO EDGE". On 5m the
 * fully-random control -- random side AND random bar -- beat the same coin flip
 * by +317.1 and +557.0 R, which is within noise of what the rule managed. The
 * rule's apparent gain therefore has nothing to do with the zone or the
 * trigger: both it and a coin flip did better than the row that followed the
 * STRUCTURAL BIAS with random timing. On these frames, taking the side market
 * structure points at was the worst of the three, in both eras.
 *
 * AND EVERY INTRADAY ROW LOSES IN ABSOLUTE TERMS. Pooled 5m net R: smc -2.5 and
 * -404.9, the controls worse. Those are GROSS; at ~4,000 trades per cell and
 * 0.061-0.122 R of spread on gold, each owes several hundred R more. The
 * Donchian rule on the same bars -- 1,182 trades against the rule's 3,608 --
 * returned +554.6 and +140.5 and is the ONLY row in the study that beat the
 * coin flip in both eras.
 *
 * WHAT IS NOT SHOWN. This is one formulation: newest live zone, one-bar
 * rejection, stop at the zone edge, CHoCH exit. Other assemblies of the same
 * detectors -- a trendline retest, an S/R level, an EMA regime filter -- are
 * untested and would each need their own control. But the pattern across three
 * independent studies now (eleven entry gates, the structural trail, this) is
 * that structure-based SELECTION keeps failing matched controls while the dull
 * channel keeps not failing them.
 *
 * SO THIS IS NOT A STRATEGY, it is a hypothesis with an implementation and a
 * negative result. It is deliberately not registered in js/chart/strategies.js
 * and nothing on any chart runs it.
 */

import { atrSeries } from './tlengine.js';
import { FLAT, LONG, SHORT, emaSeries } from './rules.js';
import { BULL, BEAR, CHOCH, detect as detectMs } from './marketstructure.js';
import { DEMAND, SUPPLY, bases } from './supplydemand.js';

export { FLAT, LONG, SHORT } from './rules.js';

export const DEFAULTS = {
  atrLen: 14,
  /* How far beyond the zone's far edge the stop sits. The same quarter-ATR
     concession the levels and the trail use: price overshoots a level before it
     turns, and a stop exactly on the edge is taken by the wick that confirms
     the entry. */
  stopBufferAtr: 0.25,
  /* A zone older than this has been sitting there while the market changed its
     mind about everything else. 200 bars is 16 hours on 5m, two days on 15m. */
  maxAgeBars: 200,
  /* A stop further than this is not a level, it is a region. Skipped rather
     than resized -- the walker sizes to the stop, so accepting these would let
     one enormous zone dominate the sample. */
  maxRiskAtr: 3.0,
  /* Off, and it stays off unless a measurement asks for it. Any positive length
     is a DIFFERENT rule that has passed nothing -- the same discipline
     js/chart/donchian.js applies to its own EMA filter. */
  emaLen: 0,
  /* Market-structure pivot strength. 3 is what the chart draws with. */
  strength: 3,
};

export const smcRetestRule = {
  key: 'smc_retest',
  label: 'Structure retest (candidate)',
  defaults: DEFAULTS,
  summary: 'While structure is bullish, buy the first bar that trades into the '
    + 'newest live demand base and closes back above it; stop under the base. '
    + 'Short the mirror. Exit on an opposite CHoCH, the stop, or a trail. '
    + 'NOT VALIDATED.',

  warmup: (p) => Math.max(p.atrLen, p.emaLen || 0, p.strength * 4, 60) + 2,

  /* NO STANDING EXIT LEVEL, and the walker asks for one whenever a position is
     open on the last bar. Every other rule here can answer with a price -- the
     Donchian channel has one on every bar -- but this rule's exit is an EVENT,
     a CHoCH against the trade, and there is no price at which that is true. A
     rule without this crashes only on the cells that happen to end mid-trade,
     which is how five of eight cells in the first run died after the other
     three had already printed a table. */
  exitLevel: () => null,

  prepare(bars, p) {
    const atr = atrSeries(bars, p.atrLen);
    const ms = detectMs(bars, { strength: p.strength });
    const created = bases(bars, atr, {});

    /* WHEN EACH ZONE DIES, computed once forward from its own creation bar.
       This is precomputation, not look-ahead: `decide` compares it against the
       bar it is deciding, so a zone that dies later is alive now. */
    const n = bars.length;
    for (const z of created) {
      z.deadAt = n;                       // never, unless proven otherwise
      for (let m = z.confirmedI + 1; m < n; m++) {
        const dead = z.kind === DEMAND ? bars[m].c < z.low : bars[m].c > z.high;
        if (dead) { z.deadAt = m; break; }
      }
    }

    /* THE NEWEST LIVE ZONE PER SIDE, PER BAR. Built in one pass with a stack of
       candidates so `decide` stays O(1): walking the zone list on every bar of
       a 700k-bar series is how an eval turns into an overnight job. */
    const demand = new Array(n).fill(null);
    const supply = new Array(n).fill(null);
    const byBar = new Map();
    for (const z of created) {
      if (!byBar.has(z.confirmedI)) byBar.set(z.confirmedI, []);
      byBar.get(z.confirmedI).push(z);
    }
    let dStack = [];
    let sStack = [];
    const prune = (stack, i, p2) => {
      while (stack.length) {
        const top = stack[stack.length - 1];
        if (top.deadAt <= i || (i - top.confirmedI) > p2.maxAgeBars) stack.pop();
        else break;
      }
      return stack.length ? stack[stack.length - 1] : null;
    };
    for (let i = 0; i < n; i++) {
      for (const z of (byBar.get(i) || [])) {
        (z.kind === DEMAND ? dStack : sStack).push(z);
      }
      demand[i] = prune(dStack, i, p);
      supply[i] = prune(sStack, i, p);
    }

    const out = { atr, msBias: ms.bias, msEvent: ms.event, msDir: ms.eventDir,
                  demand, supply, zones: created };
    if (p.emaLen) out.ema = emaSeries(bars.map((b) => b.c), p.emaLen);
    return out;
  },

  decide(i, { series, close, high, low, pos, p }) {
    const a = series.atr[i];
    if (!(a > 0)) return null;

    /* EXIT FIRST, and it is structure changing its mind: a CHoCH against the
       trade is the event that says the reason for being here is gone. BOS in
       the same direction is not an exit -- that is the trade working. */
    if (pos) {
      const ev = series.msEvent[i];
      const dir = series.msDir[i];
      if (ev === CHOCH
          && ((pos.side === LONG && dir === BEAR) || (pos.side === SHORT && dir === BULL))) {
        return { side: FLAT, reason: 'choch' };
      }
      return null;
    }

    const bias = series.msBias[i];
    const side = bias === BULL ? LONG : (bias === BEAR ? SHORT : FLAT);
    if (side === FLAT) return null;

    /* The zone must have existed BEFORE this bar. A base confirmed on the same
       bar that trades into it is one bar doing both jobs, and the impulse that
       created it is the move this bar would be buying the pullback of. */
    const z = side === LONG ? series.demand[i] : series.supply[i];
    if (!z || z.confirmedI >= i) return null;

    if (p.emaLen) {
      const e = series.ema[i];
      if (!Number.isFinite(e)) return null;
      if (side === LONG ? close[i] < e : close[i] > e) return null;
    }

    /* INTO IT AND BACK OUT, on this bar. */
    const touched = side === LONG ? low[i] <= z.high : high[i] >= z.low;
    const rejected = side === LONG ? close[i] > z.high : close[i] < z.low;
    if (!touched || !rejected) return null;

    const stop = side === LONG ? z.low - p.stopBufferAtr * a
                               : z.high + p.stopBufferAtr * a;
    const risk = Math.abs(close[i] - stop);
    if (!(risk > 0) || risk > p.maxRiskAtr * a) return null;

    return { side, stop, tag: 'sd_retest' };
  },
};
