/* rayorule.js — the Rayo Scalper as a Strategy Replay rule.
 *
 * WHY IT HAS ITS OWN WALKER. js/chart/rules.js walks one lifecycle: decide on a
 * close, fill at the NEXT OPEN, leave on the stop or a close through an exit
 * level. Rayo is not that rule. It rests a PENDING stop order a little beyond
 * the swing, which fills INTRABAR when price trades through it, stays live for
 * `expire` bars, and exits on the stop or at TP1 (0.9R). Forcing it through the
 * Donchian walker would replay a different rule under Rayo's name. So this
 * exports `run(bars, opts)`, which runRule delegates to, and returns the same
 * shape runRule does so the panel never branches on which rule is loaded.
 *
 * THE TICKET IS THE LIVE ONE. Every decision calls `ticket()` from
 * js/chart/scalper.js -- the function the right-rail panel, the TP bands and
 * the Signal Board call -- with the same higher-frame trend filter
 * (js/chart/htftrend.js: 1m/3m/5m agree with 15m, 15m/30m with
 * 1h+4h, 1h with 4h, 4h with the daily, 1d/1w disabled). What the replay shows at a cursor is
 * what the live surfaces would have shown at that bar.
 *
 * ON THE CHART'S OWN BARS, NOT 1m. tools/scalper.py resolves fills and exits
 * on 1m bars; this resolves them on the bars being replayed, with the same
 * tie rule -- a bar that reaches both the stop and the target is a loss. On a
 * 1m chart that is identical; on slower frames it is a coarser picture of the
 * same trade, which is the right trade-off for a stepping sandbox.
 *
 * CAUSAL. The decision at bar i sees bars 0..i only, and the higher-frame
 * trend is read from bars CLOSED by bar i's own time.
 *
 * FIXED 12, AS THE LIVE SCORER. A ticket rests with its original levels for
 * 12 bars and only then (or once its trade closes) is the next one taken --
 * see run(). Re-issuing the order every closed bar measured worse on every
 * gold frame (logs/rayo_reissue_eval.txt). The right-rail panel and the TP
 * bands read the same walker through restingRayo(), so all three agree.
 *
 * NEVER ON THE FORMING BAR. The bridge serves the unfinished bar last; the live
 * panel decides on the last CLOSED bar, so this does too: a bar whose close
 * time is still in the future is walked for fills and exits but decides
 * nothing.
 */

import { LONG, SHORT, FLAT } from './rules.js';
import { EXIT_TP, STOP_ATR, ticket } from './scalper.js';
import { trendAt, trendFrames } from './htftrend.js';
import { TF_MS } from '../util.js';

/** Replace-into ticket(): the decision bar i as the last CLOSED bar. */
function ticketAt(bars, i, mode, trend) {
  /* ticket() treats the LAST element as the forming bar and decides on the one
     before it. Appending a copy of bar i as a stand-in forming bar makes bar i
     the decision bar without letting ticket() see bar i+1. */
  return ticket([...bars.slice(0, i + 1), bars[i]], { mode, trend });
}

export const rayoRule = {
  key: 'rayo',
  label: 'Rayo Scalper',
  summary: 'Posts a stop order 0.10 ATR beyond the 20-bar swing in the EMA20/50 '
    + 'direction. Each ticket rests with its own levels for 12 bars, one at a time, '
    + 'as the live scorer takes it. Stop 5 ATR, exit at TP1 (0.9R).',
  defaults: { mode: 'break', swing: 20, fast: 20, slow: 50, expire: 12 },
  warmup: (p) => Math.max(p.swing, p.slow) + 4,

  /**
   * Walk bars[0..upto]. `opts.trend` is what trendFor(symbol, tf) returns:
   * an object of refs, `null` while loading, `false` when the frame is
   * disabled. Anything other than an object posts no tickets.
   */
  run(bars, opts = {}) {
    const p = { ...rayoRule.defaults, ...opts };
    const frameMs = TF_MS[p.tf] || 0;
    const nowMs = Number.isFinite(p.nowMs) ? p.nowMs : Date.now();
    const closed = (bar) => !frameMs || bar.t + frameMs <= nowMs;
    const end = p.upto === null || p.upto === undefined ? bars.length - 1 : p.upto;
    const n = end + 1;
    const view = bars.slice(0, n);
    const exitIdx = Math.min(Math.max(EXIT_TP, 1), 3) - 1;
    const warm = rayoRule.warmup(p);
    /* `false` is a disabled frame (1d/1w). ticket() reads no trend refs, and
       neither does the live scorer, so nothing else holds a ticket back. */
    const canTrade = p.trend !== false;

    const trades = [];
    let pos = null;      // filled trade
    let order = null;    // the resting ticket
    /* THE BUSY CURSOR, as tools/score_scalper.py keeps it: once a ticket is
       taken nothing else is considered until it has expired or its trade has
       closed. `freeFrom` is the first bar a new ticket may come from. */
    let freeFrom = 0;

    /* FIXED 12, THE LIVE SCORER'S RULE -- adopted for the chart on 2026-09-17
       after tools/rayo_reissue_eval.py measured it against re-issuing the
       order every closed bar: re-issue was better on 0 of 8 gold frames.
       The ticket of closed bar i is taken when the cursor is free, rests WITH
       ITS ORIGINAL LEVELS for `expire` bars counted from bar i itself (its
       inputs end at bar i-1, so that is causal), fills when a bar trades
       through the entry, then races the stop against TP1, the fill bar
       included, a tie being the loss.

       WHERE THE LIVE RECORD EXISTS, IT DECIDES. `p.ledger` (rayoledger.js) is
       this cell's journal joined to the scored ledger. Inside the journalled
       span the ticket comes from the journal, and one the scorer resolved
       fills and exits on the bars the scorer named -- so the chart shows the
       trades the live record holds, not a simulation that started in a
       different phase. Before the journal, and after its last ticket, the
       same lifecycle is simulated. A simulated order or trade still live when
       the record begins is dropped: the live run was not holding it. */
    const L = p.mode === 'break' ? p.ledger : null;
    const inBar = (ms, bar) => Number.isFinite(ms) && ms >= bar.t && ms < bar.t + (frameMs || 1);
    const asOrder = (t, i, b) => ({
      side: t.side === 'buy' ? LONG : SHORT, entry: t.entry, stop: t.stop,
      tp: t.tp, order: t.order, risk: t.risk, age: t.age, signalI: i,
      signalPrice: b.c, expiresI: i + (t.expire || p.expire || 12),
      scored: t.scored || null, id: t.id || null, ticket: t,
    });

    for (let i = 0; i < n; i++) {
      const b = view[i];
      const live = !!L && b.t >= L.start && b.t <= L.lastMs;
      if (L && i > 0 && b.t >= L.start && view[i - 1].t < L.start) {
        pos = null; order = null; freeFrom = i;
      }

      // 1. an unfilled ticket runs out `expire` bars after its own bar
      if (!pos && order) {
        const sc = order.scored;
        const gone = sc ? (sc.outcome === 'expired' && b.t + frameMs > sc.endMs)
                        : i >= order.expiresI;
        if (gone) { order = null; freeFrom = Math.max(freeFrom, i); }
      }

      /* TWICE AT MOST: a ledger trade that ended exactly at this bar's open
         frees the cursor for THIS bar's ticket, as `ms >= end_ms` does. */
      for (let pass = 0; pass < 2; pass++) {
        const freedAt = freeFrom;

        // 2. flat, free, bar closed: take this bar's ticket
        if (!pos && !order && i >= freeFrom && i >= warm && canTrade && closed(b)) {
          let t = null;
          if (live) {
            const j = L.tickets.get(b.t);
            /* Inside the scored span only a ticket the scorer TOOK is taken;
               past it, any journalled ticket, under the same cursor. */
            if (j && (j.scored || b.t > L.scoredUntil)) t = j;
          } else {
            t = ticketAt(view, i, p.mode, p.trend);
          }
          if (t) order = asOrder(t, i, b);
        }

        // 3. the fill, from the ticket's own bar
        const fills = order && (order.scored
          ? (order.scored.outcome !== 'expired' && inBar(order.scored.fillMs, b))
          : (b.l <= order.entry && b.h >= order.entry));
        if (!pos && order && fills) {
          pos = { side: order.side, entryI: i, entryTime: b.t, entryPrice: order.entry,
                  stop: order.stop, risk: Math.abs(order.entry - order.stop),
                  tp: order.tp, target: order.tp[exitIdx], tag: 'rayo', age: order.age,
                  signalI: order.signalI, signalPrice: order.signalPrice,
                  scored: order.scored, id: order.id, ticket: order.ticket };
          order = null;
        }

        // 4. the race, the fill bar included. TIES GO TO THE LOSS.
        if (pos) {
          const long = pos.side === LONG;
          let hitSl = long ? b.l <= pos.stop : b.h >= pos.stop;
          let hitTp = long ? b.h >= pos.target : b.l <= pos.target;
          const ps = pos.scored;
          if (ps && ps.outcome !== 'open') {
            const ends = inBar(ps.endMs, b);
            hitSl = ends && ps.outcome === 'sl';
            hitTp = ends && ps.outcome !== 'sl';
          }
          if (hitSl || hitTp) {
            const win = hitTp && !hitSl;
            const px = win ? pos.target : pos.stop;
            trades.push({ ...pos, exitI: i, exitTime: b.t, exitPrice: px,
                          reason: win ? 'tp1' : 'stop',
                          r: win ? Math.abs(pos.target - pos.entryPrice) / pos.risk : -1 });
            pos = null;
            freeFrom = (ps && ps.endMs === b.t) ? i : i + 1;
          }
        }
        if (pass || freeFrom !== i || freedAt === i) break;
      }
    }

    /* WHY THERE IS NO ORDER, the same sentence the live panel gives: the raw
       rule wanted a trade on the last closed bar and the higher-frame trend
       held it back, or the frame is disabled. */
    let heldBack = null;
    if (!pos && !order) {
      let k = n - 1;
      while (k >= 0 && !closed(view[k])) k--;
      if (p.trend === false) {
        heldBack = { disabled: true, tf: p.tf };
      } else if (k >= warm) {
        const raw = ticketAt(view, k, p.mode, undefined);
        if (raw && p.trend && typeof p.trend === 'object') {
          const frames = {};
          for (const f of Object.keys(p.trend)) frames[f] = trendAt(p.trend[f], view[k].t);
          heldBack = { side: raw.side, frames };
        } else if (raw && p.trend === null) {
          heldBack = { loading: true, frames: trendFrames(p.tf) || [] };
        }
      }
    }

    const pending = order ? {
      side: order.side, stop: order.stop, entry: order.entry, tp: order.tp,
      order: order.order, risk: order.risk, age: order.age,
      signalI: order.signalI, signalPrice: order.signalPrice,
      expiresI: order.expiresI, id: order.id, ticket: order.ticket,
    } : null;
    return {
      params: p,
      rule: rayoRule.key,
      bars: n,
      series: {},
      trades,
      position: pos,
      pending,
      state: pos ? pos.side : FLAT,
      /* Rayo has no close-based exit level: it leaves at the stop or TP1. */
      exitLevel: null,
      heldBack,
      asOf: view[n - 1] ? view[n - 1].t : null,
      stopAtr: STOP_ATR,
    };
  },
};


/**
 * What the live chart shows for Rayo at the right edge: the trade the fixed-12
 * walker is in, or the ticket it has resting, or neither. The right-rail panel
 * and the TP bands both call this, so they cannot disagree with each other or
 * with the Strategy Replay.
 */
export function restingRayo(bars, { tf, mode = 'break', ledger = null, nowMs } = {}) {
  if (!bars || bars.length < rayoRule.warmup(rayoRule.defaults) + 2) return null;
  const sig = rayoRule.run(bars, { tf, mode, ledger, nowMs });
  const last = bars.length - 1;
  const pend = sig.pending;
  return {
    position: sig.position,
    pending: pend ? { ...pend.ticket, barIndex: pend.signalI,
                      expire: pend.expiresI - pend.signalI,
                      barsLeft: Math.max(0, pend.expiresI - last),
                      fromLedger: !!pend.id } : null,
  };
}
