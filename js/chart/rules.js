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

  for (let i = 0; i < n; i++) {
    /* 1. fill what the previous close ordered, at THIS bar's open, before any
          of this bar's own logic runs. */
    if (pending) {
      if (pending.side === FLAT) {
        if (pos) {
          trades.push({ ...pos, exitI: i, exitTime: view[i].t, exitPrice: open[i],
                        reason: pending.reason || 'signal',
                        r: (open[i] - pos.entryPrice) * pos.side / pos.risk });
          pos = null;
        }
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
          the open. None of these rules sets a target, so nothing races. */
    if (pos) {
      const gapped = pos.side === LONG ? open[i] <= pos.stop : open[i] >= pos.stop;
      const touched = pos.side === LONG ? low[i] <= pos.stop : high[i] >= pos.stop;
      if (gapped || touched) {
        const px = gapped ? open[i] : pos.stop;
        trades.push({ ...pos, exitI: i, exitTime: view[i].t, exitPrice: px,
                      reason: gapped ? 'stop_gap' : 'stop',
                      r: (px - pos.entryPrice) * pos.side / pos.risk });
        pos = null;
      }
    }

    if (i < warmup) continue;

    /* 3. decide on THIS close, to be acted on at the next open */
    const asked = rule.decide(i, { series, close, open, high, low, pos, p });
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
    out.note = 'stop is fixed at entry; the exit level moves with each bar';
  }
  return out;
}
