/* rules.js — one trade lifecycle, many strategies.
 *
 * WHY THIS IS SHARED. The fill-at-next-open, gap-fills-at-open,
 * stop-checked-on-the-range sequence is not strategy logic, it is what the
 * simulator does, and every strategy has to agree with it exactly or the panel
 * quotes trades the backtest never took. Duplicating it per strategy is how the
 * ATR bug happened: three files each carried their own Wilder seeding, they
 * diverged by up to 0.86 price units, and the chart was drawing stops the
 * backtest had never used. One walker, one place to be wrong.
 *
 * A STRATEGY IS A DATA OBJECT. To add one, define:
 *
 *   key, label      identifiers
 *   defaults        parameter object
 *   warmup(p)       bars needed before the first signal
 *   prepare(bars,p) precomputed causal series, as a plain object
 *   decide(i,c)     what to ask for at the close of bar i, given
 *                   { series, close, high, low, open, pos, p }
 *   exitLevel(i,c)  where an open position leaves on a close, or null
 *
 * `decide` returns null, `{ side: FLAT }`, or `{ side, stop, tag }`. It must
 * read nothing past index i -- that is the whole causality contract, and
 * tests/test_*_parity.py checks it against the Python engine per strategy.
 *
 * A TRAILING EXIT IS AVAILABLE AND A TAKE-PROFIT IS NOT, and the difference is
 * the whole reason one survived and the other did not. `exitTrail` returns a
 * PRICE BEHIND the trade that can only ratchet toward it; it never caps how far
 * the trade may run. A target does exactly that, which is a bet against the
 * tail a trend rule is paid from.
 *
 * THERE IS NO TAKE-PROFIT, and the walker has no way to express one. It had:
 * `takeProfitR` for a multiple, `takeProfitAt` for a price, and
 * `takeProfitFraction` for a scale-out, built when a target was asked for and
 * removed when it was withdrawn. `logs/tp_struct_eval.txt` holds the run they went on the strength of: across
 * twelve cells out of sample, no target beat the trailing exit on net R. Rules leave on their stop or
 * on a close through their exit level, and nothing else.
 */

export const LONG = 1;
export const SHORT = -1;
export const FLAT = 0;

/** Rolling extreme over the `n` bars ENDING BEFORE i. NaN until there are n. */
export function rollingShifted(values, n, pick) {
  const out = new Array(values.length).fill(NaN);
  for (let i = n; i < values.length; i++) {
    let best = values[i - n];
    for (let k = i - n + 1; k < i; k++) best = pick(best, values[k]);
    out[i] = best;
  }
  return out;
}

/**
 * Exponential moving average, matching pandas `ewm(span, adjust=False,
 * min_periods=span)`.
 *
 * The recursion runs from the first bar but output is masked until `n` of them
 * exist -- which is what pandas does, and getting that wrong shifts every
 * signal by a few bars without ever looking wrong on a chart.
 */
export function emaSeries(values, n) {
  const a = 2 / (n + 1);
  const out = new Array(values.length).fill(NaN);
  if (!values.length) return out;
  let prev = values[0];
  for (let i = 0; i < values.length; i++) {
    prev = i === 0 ? values[0] : a * values[i] + (1 - a) * prev;
    if (i >= n - 1) out[i] = prev;
  }
  return out;
}

/**
 * Walk `rule` over bars[0..upto]. Pure function of that slice.
 *
 * Returns { params, bars, series, trades, position, pending, state, exitLevel,
 * asOf } -- the same shape for every strategy, so a panel never branches on
 * which one is loaded.
 */
export function runRule(bars, rule, opts = {}) {
  /* THREE LAYERS, and the order is the whole point.
   *
   * defaults  what the rule is when nobody says otherwise.
   * paramsFor the rule's own answer for a NAMED timeframe -- donchian's
   *           horizon map, which turns "N=20" into the 3.3-day channel that
   *           was actually validated on that timeframe. A rule without one
   *           simply keeps its defaults, so the walker never learns any
   *           strategy's names.
   * opts      what the caller explicitly asked for, last, so a sweep or a
   *           fixture passing entry/exit is never quietly overridden.
   *
   * This existed as a gap once and was invisible: the replay called runRule
   * with rule.defaults while the panel used the map, so on 15m the replay
   * stepped a five-hour channel measured at -0.0756 R while the chart drew the
   * 3.3-day one that passed every gate. Both looked correct; neither said
   * which rule it was running. tests/test_horizon_parity.py drives all three
   * surfaces through this function for that reason.
   */
  const tfParams = (opts.tf && rule.paramsFor) ? rule.paramsFor(opts.tf) : null;
  const p = { ...rule.defaults, ...(tfParams || {}), ...opts };
  const end = p.upto === null || p.upto === undefined ? bars.length - 1 : p.upto;
  const n = end + 1;
  const view = bars.slice(0, n);

  const close = view.map((b) => b.c);
  const open = view.map((b) => b.o);
  const high = view.map((b) => b.h);
  const low = view.map((b) => b.l);
  const series = rule.prepare(view, p);
  const warmup = rule.warmup(p);

  const trades = [];
  let pos = null;
  let pending = null;

  /* ONE PLACE THAT COMPUTES `r`. It was three at one point and they disagreed
     the moment a fourth exit was added; three call sites that each derive the
     same number is three chances to derive it differently. */
  const closeAt = (i, px, reason) => {
    trades.push({
      ...pos, exitI: i, exitTime: view[i].t, exitPrice: px, reason,
      r: (px - pos.entryPrice) * pos.side / pos.risk,
    });
    pos = null;
  };

  for (let i = 0; i < n; i++) {
    /* 1. fill what the previous close ordered, at THIS bar's open, before any
          of this bar's own logic runs. */
    if (pending) {
      if (pending.side === FLAT) {
        if (pos) closeAt(i, open[i], pending.reason || 'signal');
      } else if (!pos) {
        const px = open[i];
        const risk = Math.abs(px - pending.stop);
        /* A gap through the stop makes the trade unplaceable -- the simulator's
           `stop < px` check skips it rather than resizing, so this does too. */
        const valid = pending.side === LONG ? pending.stop < px : pending.stop > px;
        if (valid && risk > 0) {
          pos = { side: pending.side, entryI: i, entryTime: view[i].t,
                  entryPrice: px, stop: pending.stop, risk, tag: pending.tag,
                  signalI: pending.signalI, signalPrice: pending.signalPrice };
        }
      }
      pending = null;
    }

    /* 2. the stop, on this bar's range. Pessimistic: a gap past it fills at
          the open. Nothing races it -- there is no target. */
    if (pos) {
      const gapped = pos.side === LONG ? open[i] <= pos.stop : open[i] >= pos.stop;
      const touched = pos.side === LONG ? low[i] <= pos.stop : high[i] >= pos.stop;
      if (gapped || touched) {
        closeAt(i, gapped ? open[i] : pos.stop, gapped ? 'stop_gap' : 'stop');
      }
    }

    /* 2b. THE SAME EXIT, CHECKED INTRABAR -- opt-in, off in everything that
          ships, and present only so `tools/exittouch_eval.py` can measure the
          question "why wait for the close?" against the SAME lifecycle rather
          than a second copy of it. A private walker in the research script was
          the obvious alternative and is exactly how the ATR divergence got in.

          THE LEVEL IS KNOWN BEFORE THE BAR OPENS. `exitLo`/`exitHi` are
          rollingShifted -- extremes over the n bars ENDING BEFORE i -- so the
          price checked here could have been a resting order placed at the
          previous close. That is what makes this a fair comparison with the
          close-based exit: same number, same information, only the moment of
          acting on it differs.

          TIES GO TO THE STOP, which is why this sits after it: on a bar that
          reaches both, the loss is taken. The trail is folded in on the same
          `tighter wins` rule the close path uses, and a gap through the level
          fills at the open, pessimistic like the stop above. */
    if (pos && p.exitTouch && i >= warmup) {
      let lvl = rule.exitLevel(i, { series, pos, p });
      if (Number.isFinite(pos.trail)) {
        lvl = !Number.isFinite(lvl) ? pos.trail
          : (pos.side === LONG ? Math.max(lvl, pos.trail) : Math.min(lvl, pos.trail));
      }
      if (Number.isFinite(lvl)) {
        const gapped = pos.side === LONG ? open[i] <= lvl : open[i] >= lvl;
        const touched = pos.side === LONG ? low[i] <= lvl : high[i] >= lvl;
        if (gapped || touched) {
          closeAt(i, gapped ? open[i] : lvl, gapped ? 'exit_gap' : 'exit_touch');
        }
      }
    }

    if (i < warmup) continue;

    /* 3. decide on THIS close, to be acted on at the next open */
    let asked = rule.decide(i, { series, close, open, high, low, pos, p });

    /* 3b. THE ENTRY GATE, which may only ever say NO TO AN ENTRY.
          It is not shown exits and it is not shown FLAT intents, and that
          restriction is the whole discipline: a filter that could suppress an
          exit would be a second exit rule competing with the channel that
          carries the edge, and sim/strategies/emafilter.py records what
          happened the last time a gate was allowed to reach further than its
          name suggested -- two strategies silently became different
          strategies, and nothing failed loudly.

          A gate that throws is treated as no gate rather than as a rejection.
          A filter erroring on one bar must not quietly turn into "take no
          trades", which is a configuration that looks flat rather than
          broken. */
    if (asked && asked.side !== FLAT && typeof p.entryFilter === 'function') {
      let ok = true;
      try {
        ok = p.entryFilter({ ...asked, i, signalPrice: close[i],
                             view, series, close, open, high, low, p, tf: p.tf });
      } catch { ok = true; }
      if (!ok) asked = null;
    }

    /* 3c. THE TRAILING EXIT, which is NOT a take-profit and must not be read as
          one. A target caps the winner -- it says "this is far enough" -- and
          that is the bet twelve cells said not to take. A trail says nothing
          about how far a move can go; it only decides when one is over. It can
          never limit the upside, only shorten the give-back.

          THE TIGHTER OF THE TWO ALWAYS WINS. The rule's own exit stays exactly
          as it is and this can only sit inside it, so the effective exit is
          monotone: adding a trail can never produce a looser exit than the
          rule already had. That is what makes the comparison interpretable --
          any difference is the trail acting, never the rule being weakened.

          RATCHET ONLY, and checked on the CLOSE like the channel it competes
          with. An intrabar check would be a different kind of exit and the
          comparison would be measuring two things at once. */
    if (pos && typeof p.exitTrail === 'function') {
      let cand = null;
      try {
        cand = p.exitTrail({ ...pos, i, view, series, close, high, low, p });
      } catch { cand = null; }
      if (Number.isFinite(cand)) {
        const better = pos.trail === undefined || pos.trail === null
          || (pos.side === LONG ? cand > pos.trail : cand < pos.trail);
        if (better) pos.trail = cand;
      }
      if (Number.isFinite(pos.trail) && !(asked && asked.side === FLAT)) {
        const through = pos.side === LONG
          ? close[i] < pos.trail : close[i] > pos.trail;
        if (through) asked = { side: FLAT, reason: 'trail' };
      }
    }

    if (asked) {
      /* Stamp the bar the decision was made on. It is always one before the
         fill, but recording it beats deriving it: the panel marks both, and a
         reader needs to see that the entry price belongs to a bar the signal
         could not have known. */
      asked.signalI = i;
      asked.signalPrice = close[i];
    }
    pending = asked || null;
  }

  const last = n - 1;
  return {
    params: p,
    rule: rule.key,
    bars: n,
    series,
    trades,
    position: pos,
    pending,
    state: pos ? pos.side : FLAT,
    exitLevel: pos ? rule.exitLevel(last, { series, pos, p }) : null,
    asOf: view[last] ? view[last].t : null,
  };
}

/** Running expectancy over a trade list, for a scorecard. */
export function tally(trades) {
  if (!trades.length) return { n: 0 };
  const rs = trades.map((t) => t.r);
  const wins = rs.filter((r) => r > 0);
  const gross = wins.reduce((a, b) => a + b, 0);
  const bad = -rs.filter((r) => r <= 0).reduce((a, b) => a + b, 0);
  let streak = 0, worst = 0, eq = 0, peak = 0, dd = 0;
  for (const r of rs) {
    streak = r <= 0 ? streak + 1 : 0;
    worst = Math.max(worst, streak);
    eq += r; peak = Math.max(peak, eq); dd = Math.min(dd, eq - peak);
  }
  return {
    n: rs.length,
    winPct: (100 * wins.length) / rs.length,
    avgR: rs.reduce((a, b) => a + b, 0) / rs.length,
    netR: rs.reduce((a, b) => a + b, 0),
    pf: bad > 0 ? gross / bad : Infinity,
    maxDDr: dd,
    worstStreak: worst,
    byReason: trades.reduce((m, t) => ({ ...m, [t.reason]: (m[t.reason] || 0) + 1 }), {}),
  };
}

/**
 * What to do at the next open, in words, with nothing implied that is not known.
 *
 * The entry price is deliberately absent: a signal fires on a close and fills at
 * the next open, so the price does not exist yet and quoting one would be
 * inventing it.
 */
export function instruction(sig) {
  const out = { action: 'wait', side: null, stop: null,
                exitLevel: sig.exitLevel, note: '' };
  if (sig.pending && sig.pending.side !== FLAT) {
    out.action = 'enter';
    out.side = sig.pending.side === LONG ? 'BUY' : 'SELL';
    out.stop = sig.pending.stop;
    out.note = 'at the next bar open; the entry price does not exist yet';
  } else if (sig.pending && sig.pending.side === FLAT) {
    out.action = 'exit';
    out.side = 'CLOSE';
    out.note = 'at the next bar open — the exit condition triggered on this close';
  } else if (sig.position) {
    out.action = 'hold';
    out.side = sig.position.side === LONG ? 'LONG' : 'SHORT';
    out.stop = sig.position.stop;
    out.note = 'stop is fixed; exit moves with each bar';
  }
  return out;
}
