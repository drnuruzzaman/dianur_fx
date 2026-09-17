/* reversal.js — has the open trade's own thesis turned against it?
 *
 * A WARNING. NEVER A CLOSE. That is not a disclaimer, it is the measured
 * result, and the shape of this file follows from it.
 *
 * WHAT WAS MEASURED, 2026-09-17, gold 2017-2026, one position at a time
 * (tools/rayo_flip_exit_eval.py, tools/rayo_flip_stage1.py,
 * tools/rayo_random_control_probe.py):
 *
 *   - Closing the trade when EMA20/50 turns against it beat holding to the
 *     stop or TP1 on 0 of 5 frames.
 *   - The exit alone, measured PAIRED on identical trades, loses on 4 of 5
 *     frames and improves the outcome on only 14-26% of them.
 *   - It loses to closing at a RANDOM bar on 4 of 5 frames, because "the trend
 *     has flipped against me" is mechanically the same event as "price has
 *     already moved against me" -- the rule selects the worst moment in the
 *     trade to leave.
 *   - Worst of all, the trades the freed cell then takes score BELOW the ones
 *     it would have taken anyway on 5 of 5 frames. The account holds one
 *     position, so releasing it early is itself the expensive part.
 *
 * So this file computes the witnesses and says what they say. It does not
 * close anything, it does not free a cell, and nothing downstream may read it
 * as an instruction to. What it is FOR is the thing the numbers do support:
 * knowing what your open position is sitting in, so the next DISCRETIONARY
 * decision -- sizing, whether to take the next ticket, whether to sit in front
 * of the screen -- is made with the information rather than without it.
 *
 * NO BRIDGE CALL, exactly as scalper.js takes none: everything but the
 * higher-frame trend comes off the bars already on screen, and the higher-frame
 * sign is INJECTED by the caller rather than imported. That keeps this module
 * pure -- same bars in, same verdict out -- so it can be driven under node in a
 * parity test without a live chart or a running bridge.
 *
 * CLOSED BARS ONLY. The forming bar is dropped before anything is computed,
 * the same one-bar convention conditions.js states at length: a witness that
 * flickers on and off inside the current bar is not evidence, and a warning
 * that appears and vanishes twice a minute trains the reader to ignore it.
 */

import { rsiSeries } from './divergence.js';
import { BULL, BEAR, CHOCH, detect } from './marketstructure.js';

export const REVERSAL_DEFAULTS = {
  /* THE EMA PAIR THE RULE ITSELF TRADES, not a second opinion about trend.
     20/50 is `fast`/`slow` in sim/strategies/rayo.py DEFAULTS, and this
     witness is deliberately the exact one the measurement above rejected as an
     exit -- showing a DIFFERENT flip from the one that was tested would be
     quoting a number about one rule beside a light driven by another. */
  fast: 20,
  slow: 50,
  rsiLen: 14,
  rsiMid: 50,
  msStrength: 3,
  /* NOTHING COUNTS THIS SOON AFTER THE FILL. The entry is a breakout beyond a
     swing, so price pulling back into the EMAs in the first bars is the normal
     shape of a winner, not a reversal. A DISPLAY choice, not a measured one --
     no arm in any of the three studies used a grace period, so this must not be
     read as tuned. */
  graceBars: 3,
  /* HOW MANY WITNESSES BEFORE THE TICKET SAYS ANYTHING. One is chop: the EMA
     pair alone fired on 23-41% of all fills depending on frame, which is far
     too often to mean anything on a face. Two is the k>=2 vote the rejected
     Stage 2 design would have used. */
  warnAt: 2,
};

export const NONE = 'none';
export const WATCH = 'watch';
export const WARN = 'warn';

/** EMA, seeded on the first value — the recurrence `emaLast` in scalper.js and
 *  `ema()` in sim/strategies/rayo.py both use (pandas ewm, adjust=False). */
function emaSeries(vals, n) {
  const k = 2 / (n + 1);
  const out = new Array(vals.length);
  let e = vals[0];
  for (let i = 0; i < vals.length; i++) {
    e = i === 0 ? vals[0] : vals[i] * k + e * (1 - k);
    out[i] = e;
  }
  return out;
}

/**
 * The witnesses against an open trade, as at the last CLOSED bar.
 *
 * `bars`   the execution frame's bars, oldest first, forming bar LAST.
 * `held`   { side: 'buy'|'sell', fillMs }.
 * `opts`   REVERSAL_DEFAULTS overrides, plus `htfSign` (+1/-1/0) for the
 *          higher-frame trend the caller has and this module does not.
 *
 * Returns null when there is nothing honest to say — no trade, too few bars,
 * or still inside the grace window. Otherwise:
 *
 *   { level, votes, warnAt, witnesses, fired, barsHeld, asOfMs }
 *
 * `witnesses` is every witness with its verdict, so a caller can show what is
 * still INTACT as readily as what has turned; `fired` is just the ones against.
 */
export function reversalFor(bars, held, opts = {}) {
  const p = { ...REVERSAL_DEFAULTS, ...opts };
  if (!bars || !held || !held.side) return null;

  /* THE FORMING BAR IS NOT EVIDENCE. See the header. */
  const closed = bars.slice(0, -1);
  if (closed.length < p.slow + 2) return null;

  const up = held.side === 'buy';
  const sign = up ? 1 : -1;
  const last = closed.length - 1;
  const asOfMs = closed[last].t;

  /* THE FILL BAR, on this frame. A trade filled intrabar sits inside a bar;
     the bar that CONTAINS the fill is the first one that can testify. */
  let fillIdx = -1;
  for (let i = last; i >= 0; i--) {
    if (closed[i].t <= held.fillMs) { fillIdx = i; break; }
  }
  if (fillIdx < 0) return null;
  const barsHeld = last - fillIdx;
  if (barsHeld < p.graceBars) return null;

  const since = (i) => ({ sinceBars: last - i, sinceMs: closed[i].t });
  const witnesses = [];

  /* 1. THE EMA PAIR — the witness the measurement actually tested. */
  const close = closed.map((b) => b.c);
  const ef = emaSeries(close, p.fast);
  const es = emaSeries(close, p.slow);
  /* `ef > es`, THEN NEGATED — not `ef <= es`. The two differ only where the
     EMAs are exactly equal, which is every series' first bar (both seed on
     vals[0]) and is otherwise never; writing it the other way round made the
     JS disagree with `up = ef > es` in tools/rayo_flip_stage1.py on that one
     bar, and a parity test that tolerates "only bar 0" is how a real gap gets
     in later. */
  const upTrend = (i) => ef[i] > es[i];
  const emaAgainst = (i) => (up ? !upTrend(i) : upTrend(i));
  if (emaAgainst(last)) {
    /* WHEN IT TURNED, not just that it has: a pair that crossed forty bars ago
       and a pair that crossed on the last close are not the same news. */
    let i = last;
    while (i > fillIdx && emaAgainst(i - 1)) i -= 1;
    witnesses.push({ key: 'ema', label: 'EMA', on: true, ...since(i) });
  } else {
    witnesses.push({ key: 'ema', label: 'EMA', on: false });
  }

  /* 2. CHoCH — Change of Character against the trade, since the fill.
        CHoCH AND NOT BOS, which is the distinction marketstructure.js exists
        to keep: a BOS is the trend making another leg, i.e. the trade WORKING.
        Reading a break of structure as a reversal warning would light the lamp
        hardest exactly when the position is going right. */
  const ms = detect(closed, { strength: p.msStrength });
  const want = up ? BEAR : BULL;
  let choch = null;
  for (const e of ms.events) {
    if (e.kind === CHOCH && e.direction === want && e.i >= fillIdx) choch = e;
  }
  witnesses.push(choch
    ? { key: 'choch', label: 'CHoCH', on: true, ...since(choch.i) }
    : { key: 'choch', label: 'CHoCH', on: false });

  /* 3. RSI back through the midline, against the trade, since the fill.
        THE MIDLINE, NOT 30/70. Overbought on a long is the trade working;
        momentum crossing back through 50 is the thing that would precede it
        failing. RSS -- the 30/70 levels this project already tested as an exit
        -- lost on every variant tried, which is why they are not used here. */
  const rsi = rsiSeries(closed, p.rsiLen);
  let cross = null;
  for (let i = Math.max(fillIdx + 1, 1); i <= last; i++) {
    const a = rsi[i - 1], b = rsi[i];
    if (!Number.isFinite(a) || !Number.isFinite(b)) continue;
    if (up ? (a >= p.rsiMid && b < p.rsiMid) : (a <= p.rsiMid && b > p.rsiMid)) cross = i;
  }
  /* AND IT HAS TO STILL BE THERE. A cross back that has since been reclaimed is
     history, not a live witness. */
  const rsiNow = rsi[last];
  const rsiOn = cross !== null && Number.isFinite(rsiNow)
    && (up ? rsiNow < p.rsiMid : rsiNow > p.rsiMid);
  witnesses.push(rsiOn
    ? { key: 'rsi', label: 'RSI', on: true, ...since(cross) }
    : { key: 'rsi', label: 'RSI', on: false });

  /* 4. THE HIGHER FRAME, injected. 0 means "the caller does not know" — a
        frame still loading, or one with no higher frame to agree with — and an
        unknown is NOT a vote against. `htfSince` is optional and comes from the
        caller for the same reason the sign does: this module never touches the
        higher frame's bars. */
  const htf = Number(p.htfSign) || 0;
  const htfOn = !!(htf && htf * sign < 0);
  const htfW = { key: 'htf', label: 'HTF', on: htfOn };
  if (htfOn && Number.isFinite(p.htfSince) && p.htfSince >= closed[fillIdx].t) {
    htfW.sinceMs = p.htfSince;
  }
  witnesses.push(htfW);

  const fired = witnesses.filter((w) => w.on);
  const votes = fired.length;

  /* WHEN IT BECAME CONFIRMED, which is NOT when the last witness turned and not
     when the first did. Witnesses turn on at their own times; the count first
     reaches `warnAt` at the warnAt-th EARLIEST of them. One witness 40 bars ago
     and a second 5 bars ago is a reversal confirmed 5 bars ago, not 40.

     NULL WHEN IT CANNOT BE PINNED. A fired witness with no time of its own
     (the higher frame, when the caller did not supply `htfSince`) could have
     turned at any point, so the crossing cannot be located and this says so
     rather than guessing a bar. The chart draws no marker in that case. */
  /* THE FIRST BAR THE VOTE EVER CROSSED, walked rather than remembered.
     -------------------------------------------------------------------------
     The first version took the warnAt-th earliest `sinceMs` of the witnesses
     firing RIGHT NOW, which moved: witnesses come and go (only CHoCH latches),
     so as the firing set changed the answer changed with it and the chart's
     marker wandered to a different bar. "Confirmed" names a moment; a marker
     that drifts denies the one thing it is there to say.

     SO IT IS DERIVED, NOT CACHED. Walking the trade's own history and taking
     the FIRST bar whose vote reached `warnAt` is deterministic: same bars in,
     same bar out, on every repaint and after a reload. Caching it on the module
     would have been fewer lines and would have died on the next page load, or
     worse, survived into a different trade.

     IT OUTLIVES THE LEVEL, ON PURPOSE. This is set whenever the crossing
     happened at all, even if the reversal has since de-escalated to `watch` --
     so the lamp can tell you the truth about NOW while the marker keeps telling
     you the truth about WHEN. It clears only when the trade does.

     THE HIGHER FRAME IS A STEP, NOT A SERIES. Only its LAST flip is known
     (`htfSince`), so for this walk it counts as against from that moment on and
     not before. With more than one flip inside the trade that can date the
     crossing LATE; it cannot invent one. When the flip time is unknown the
     witness cannot vote in the walk at all, and a crossing that depended on it
     simply is not located -- the caller then draws no marker, which is the same
     rule the rest of this file follows: say nothing rather than guess a bar. */
  const chochFirstI = choch
    ? (ms.events.find((e) => e.kind === CHOCH && e.direction === want
                             && e.i >= fillIdx) || {}).i
    : undefined;
  let rsiFirstI = null;
  for (let i = Math.max(fillIdx + 1, 1); i <= last; i++) {
    const a = rsi[i - 1], b = rsi[i];
    if (!Number.isFinite(a) || !Number.isFinite(b)) continue;
    if (up ? (a >= p.rsiMid && b < p.rsiMid) : (a <= p.rsiMid && b > p.rsiMid)) {
      rsiFirstI = i;
      break;
    }
  }
  const htfFrom = (htfOn && Number.isFinite(p.htfSince)) ? p.htfSince : null;
  const onAt = (i) => {
    const k = [];
    if (emaAgainst(i)) k.push('EMA');
    if (Number.isFinite(chochFirstI) && i >= chochFirstI) k.push('CHoCH');
    if (rsiFirstI !== null && i >= rsiFirstI && Number.isFinite(rsi[i])
        && (up ? rsi[i] < p.rsiMid : rsi[i] > p.rsiMid)) k.push('RSI');
    if (htfFrom !== null && closed[i].t >= htfFrom) k.push('HTF');
    return k;
  };

  let confirmedAt = null;
  let confirmedPrice = null;
  let confirmedHigh = null;
  let confirmedLow = null;
  let confirmedBy = [];
  for (let i = Math.max(fillIdx + p.graceBars, 0); i <= last; i++) {
    const on = onAt(i);
    if (on.length >= p.warnAt) {
      confirmedBy = on;
      confirmedAt = closed[i].t;
      confirmedPrice = closed[i].c;
      /* THE BAR'S EXTREMES TOO, so a caller can place a marker CLEAR of the
         candle instead of on top of it. The close alone forces the mark into
         the body. */
      confirmedHigh = closed[i].h;
      confirmedLow = closed[i].l;
      break;
    }
  }

  /* THE CONFIRMATION LATCHES FOR THE LIFE OF THE TRADE.
     -------------------------------------------------------------------------
     `level` is WARN from the crossing onwards, whatever the witnesses are doing
     now. Only CHoCH latches on its own, so without this the lamp fell back to
     `watch` the moment RSI reclaimed 50 or the EMA pair crossed back -- and a
     thing that announces itself CONFIRMED and then quietly un-announces is
     worse than one that never spoke.

     THE PRICE OF IT, STATED. A reversal that fails and a trade that recovers
     both keep the label; `votes` and `fired` below stay LIVE for exactly that
     reason, so a reader (and the tooltip) can still see that the evidence has
     since thinned. What latches is the CLAIM that it happened, not the claim
     that it is still happening. */
  return {
    level: confirmedAt !== null ? WARN : (votes > 0 ? WATCH : NONE),
    votes,
    warnAt: p.warnAt,
    total: witnesses.length,
    witnesses,
    fired,
    barsHeld,
    asOfMs,
    confirmedAt,
    confirmedPrice,
    confirmedHigh,
    confirmedLow,
    /* WHICH WITNESSES CROSSED IT, captured at the confirmation bar -- not the
       ones firing now. The label names an event, so it has to name what caused
       that event; quoting today's witnesses beside "confirmed" would describe
       one moment with another moment's evidence. */
    confirmedBy,
  };
}

/**
 * The one short line the chart puts on a ticket. Names the witnesses while
 * they are few and counts them once they are many, because four names on a
 * price line is a paragraph nobody reads at a glance.
 *
 * NO R, NO EXPECTANCY, NO WIN RATE. Measurement belongs in the config and the
 * hover, not on the face of a signal — asked for three times and settled.
 */
export function reversalTag(rev) {
  if (!rev || rev.level === NONE) return '';
  const names = rev.fired.map((w) => w.label);
  const what = names.length <= 2 ? names.join('+') : `${rev.votes}/${rev.total}`;
  /* "REVERSAL CONFIRMED", NOT "REVERSING". At `warn` this tag REPLACES the
     entry label rather than qualifying it -- the caller drops the
     "sell entry (open)" prefix -- so it has to read as a finished statement on
     its own. "reversing" described a process and left the reader waiting for
     the end of the sentence. The witnesses stay because which ones turned is
     the only part that is checkable against the chart. */
  if (rev.level !== WARN) return `watch ${what}`;
  /* CONFIRMED BY, not firing now -- see `confirmedBy`. */
  const by = (rev.confirmedBy && rev.confirmedBy.length) ? rev.confirmedBy : names;
  const byWhat = by.length <= 2 ? by.join('+') : `${by.length}/${rev.total}`;
  return `reversal confirmed · ${byWhat}`;
}
