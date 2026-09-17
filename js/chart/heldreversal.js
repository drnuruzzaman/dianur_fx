/* heldreversal.js — the open trade on a cell, and whether it has reversed.
 *
 * ONE ANSWER, TWO READERS. The `tpbands` study draws this on the chart and the
 * Trend read panel prints it in the rail, and before this module existed the
 * study computed it inline. A panel that re-derived it would be a second
 * implementation of "what is this cell in and has it turned", and this project
 * has already paid for that mistake twice -- the chart drawing a gold sell from
 * 4272.18 while the ledger held one from 4279.28 (see opentrades.js), and a
 * JS/Python pair disagreeing about which rung the rule exits on (scalper.js
 * EXIT_TP). Two surfaces disagreeing about a REVERSAL, in public, on the same
 * screen, is the same failure with a louder symptom.
 *
 * So the chart and the panel call this, and neither one owns it.
 *
 * IT DECIDES NOTHING. `reversalFor` is a reading, not an instruction: closing
 * on a trend flip was measured and lost on every gold frame, and lost to a
 * RANDOM exit on four of five. See the header of reversal.js for the numbers.
 */

import { endedOn, openTradeFor } from './opentrades.js';
import { rayoLedgerFor } from './rayoledger.js';
import { restingRayo } from './rayorule.js';
import { reversalFor } from './reversal.js';
import { trendAt, trendFor } from './htftrend.js';

/**
 * The trade a cell is currently in, from the SAME walker the chart draws.
 *
 * `bars`  the cell's own frame, oldest first.
 * `opts`  { symbol, tf, mode, cutN, exitIdx } -- `cutN` cuts the walk at a
 *         replay cursor, `exitIdx` is the rung the rule exits on.
 *
 * Returns { side, entry, sl, tp, fillMs } or null.
 */
export function walkCell(bars, { symbol, tf, mode, cutN, exitIdx = 0 }) {
  const out = { rest: null, held: null, pending: null };
  if (!bars || !bars.length) return out;
  const n = Number.isFinite(cutN) ? Math.min(bars.length, cutN) : bars.length;
  if (!n) return out;
  const rest = restingRayo(bars.slice(0, n), {
    tf, mode, ledger: rayoLedgerFor(symbol, tf) });
  out.rest = rest;
  if (rest && rest.position) {
    const wp = rest.position;
    out.held = { side: wp.side > 0 ? 'buy' : 'sell', entry: wp.entryPrice,
                 sl: wp.stop, tp: wp.tp, fillMs: wp.entryTime };
    return out;
  }
  if (rest) {
    /* THE RESTING ORDER, and only while the cell is FLAT. A pending ticket is a
       proposal; once the cell is in a trade the proposal is moot and the caller
       has a position to draw instead. */
    out.pending = rest.pending || null;
    return out;
  }
  /* Too few bars for the walker: fall back to the ledger's open row, and only
     if these bars still agree it is open. */
  const listed = openTradeFor(symbol, tf);
  const reached = listed && bars[n - 1].t >= listed.fillMs;
  out.held = reached && !endedOn(listed, bars.slice(0, n), exitIdx) ? listed : null;
  return out;
}

/** Just the open trade. */
export function heldTrade(bars, opts) {
  return walkCell(bars, opts).held;
}

/**
 * The higher-frame trend for a cell as {sign, since}, or {sign: 0} when it is
 * unknown — still loading, no higher frame, or two frames disagreeing.
 *
 * AGREEMENT OR NOTHING, and an unknown is NOT a vote against. `since` is the
 * LATEST flip among the agreeing frames, which is the moment they agreed.
 */
export function htfState(symbol, tf, atMs) {
  const refs = trendFor(symbol, tf);
  if (!refs || typeof refs !== 'object') return { sign: 0 };
  const list = Object.values(refs);
  const signs = list.map((r) => trendAt(r, atMs));
  if (!signs.length || !signs.every((x) => x === signs[0])) return { sign: 0 };
  let flip = -Infinity;
  for (const r of list) {
    for (let i = r.sign.length - 1; i > 0; i--) {
      if (r.sign[i] !== r.sign[i - 1]) { flip = Math.max(flip, r.close[i]); break; }
    }
  }
  return { sign: signs[0], since: Number.isFinite(flip) ? flip : undefined };
}

/**
 * { held, rev } for a cell — null `held` when it is flat, null `rev` when there
 * is nothing honest to say yet (too few bars, or still inside the grace window).
 */
export function heldReversal(bars, opts) {
  const { held, pending, rest } = walkCell(bars, opts);
  if (!held) return { held: null, rev: null, pending, rest };
  const n = Number.isFinite(opts.cutN) ? Math.min(bars.length, opts.cutN) : bars.length;
  const at = bars[n - 1] ? bars[n - 1].t : 0;
  const { sign, since } = htfState(opts.symbol, opts.tf, at);
  return { held, pending, rest,
           rev: reversalFor(bars.slice(0, n), held,
                            { htfSign: sign, htfSince: since }) };
}
