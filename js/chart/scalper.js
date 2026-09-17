/* scalper.js — the scalper ticket, computed from the bars already on screen.
 *
 * A MIRROR OF tools/scalper.py, and the risk that comes with one. Two
 * implementations of one rule is exactly the situation that produces a panel
 * quietly disagreeing with the backtest that graded it, which is why the twin
 * here is kept as small and as literal as it can be: same swing window, same
 * EMAs, same ATR, same shift-by-one, same 0.10 ATR break offset, same ladder.
 * If they ever diverge, `python tools/scalper.py --live` is the reference.
 *
 * NO BRIDGE CALL. The chart already holds the bars and the rule needs nothing
 * else — no FX rate, no equity, no lot size. Adding an endpoint for it would
 * have meant another bridge restart and a round trip per repaint, to compute
 * three moving averages the page can do in a millisecond.
 *
 * WHAT THIS PANEL IS FOR, and it is not a recommendation. Measured on gold 5m
 * over 2024-2026, resolved on 1m bars, one live order at a time:
 *
 *     break  5803 fills  30% win  gross +0.0101 R  cost 0.1023 R  NET -0.0922 R
 *     fade   4078 fills  29% win  gross -0.0237 R  cost 0.1035 R  NET -0.1272 R
 *
 * The break side has a real edge and it is a TENTH of the spread it has to pay.
 * So the panel prints the net expectation beside every ticket it draws. A
 * signal panel that shows an entry, a stop and three targets and does NOT show
 * that number is the thing this project spent the week not building.
 */

const TPS = [0.9, 1.5, 2.4];

/** The stop, in ATR of the execution frame. Mirrors DEFAULTS in rayo.py.
 *
 * EXPORTED BECAUSE THE UI USED TO SPELL IT OUT. "Stop 4 ATR" was written into
 * the panel, the risk row, the hover and the Signal Board's SL tooltip, so
 * moving 4.0 to 5.0 meant finding five copies of a number that is one fact.
 * Read it from here and a future change is one line. */
export const STOP_ATR = 5.0;      // the ladder, matching tools/scalper.py

/**
 * Which rung the trade is scored on. 1 = TP1 (0.9R), mirroring `exit_tp` in
 * rayo.py DEFAULTS and `scalper.measurement.exit_tp` in configs/alerts.json.
 *
 * IT WAS TP3 HERE AND TP1 EVERYWHERE ELSE, and that is not a cosmetic gap.
 * `signals()` raced the stop against tp[2] -- 2.4R -- while the Python
 * backtest, the registry and the live alerter all close at 0.9R. A trade that
 * reached TP1 and then reversed into the stop counted as a WIN worth +0.9R in
 * every measured number and was drawn on the chart as a LOSS, with its band
 * running all the way down to the stop. The chart disagreed with the registry
 * most often in exactly the chop where the difference decides the trade.
 *
 * Exported for the same reason STOP_ATR is: so the study that colours the
 * outcome reads the rung from the rule instead of hard-coding a second
 * opinion about it.
 */
export const EXIT_TP = 1;

/**
 * The session window, or null for 24 hours — matching `DEFAULTS['session']` in
 * sim/strategies/rayo.py, which is the rule this file re-implements.
 *
 * NULL, i.e. THE GATE IS OFF, by request. It was measured worth +42% on total R
 * per year, so this is a deliberate trade: the quiet hours back, at that cost.
 *
 * ONE CONSTANT, READ BY EVERYONE. `ticket()` gates on it and the panel decides
 * what to say about an empty result from it, so the two cannot drift into the
 * panel announcing a closed session while the rule is posting tickets — which
 * is exactly the bug that was fixed once already, when the panel checked the
 * wall clock against hours the rule read off the decision bar.
 */
export const SESSION = null;

/** Last Sunday of `month` in `year`, as a UTC Date. */
function lastSunday(year, month) {
  const d = new Date(Date.UTC(year, month, 0));      // month is 1-based here
  d.setUTCDate(d.getUTCDate() - d.getUTCDay());
  return d;
}

/**
 * The hour on the BROKER'S clock for a true-UTC timestamp.
 *
 * WHY THE RULE IS GATED ON THIS AND NOT ON UTC. The session window was measured
 * on the stored research bars, and those are broker server time — EET in
 * winter, EEST in summer — deliberately not converted, because one constant
 * offset is wrong across twenty years of the broker's own DST. The bridge, by
 * contrast, subtracts the measured offset and serves TRUE UTC. So the only way
 * this file can apply the SAME window the backtest measured is to put the bar
 * back on the broker's clock first.
 *
 * DETERMINISTIC, not the live /health offset. `signals()` walks historical bars
 * to draw the TP bands, and those need the offset that was in force THEN, not
 * the one measured this morning. The EU rule below is the same arithmetic as
 * tools/_brokerclock.py, which was verified against payrolls spikes: +180
 * minutes in summer, +120 in winter.
 *
 * Incidentally this makes the window session-stable rather than clock-stable —
 * EET follows EU DST exactly as London does, so broker 07:00-21:00 is London
 * local 05:00-19:00 all year, where a fixed UTC window would slide an hour
 * twice a year against the session it is trying to select.
 */
export function brokerHour(msUtc) {
  const d = new Date(msUtc);
  const y = d.getUTCFullYear();
  const start = lastSunday(y, 3);   start.setUTCHours(1, 0, 0, 0);
  const end = lastSunday(y, 10);    end.setUTCHours(1, 0, 0, 0);
  const summer = d >= start && d < end;
  return new Date(msUtc + (summer ? 3 : 2) * 3600000).getUTCHours();
}

/* THE MEASURED TABLE USED TO LIVE HERE, hand-typed, gold-only, per timeframe:
   `{'30m': {net: +0.0434, worst: -0.046, ...}}`. It was a SECOND COPY of numbers
   that configs/alerts.json already holds per symbol AND timeframe, and the
   re-registration on 2026-09-14 moved every one of them -- which is exactly the
   drift js/chart/graded.js exists to prevent ("NOTHING IS COMPUTED HERE").

   The panel now reads the cell it is actually showing, through
   `loadScalperCells()`, so gold's numbers can no longer be displayed over a yen
   chart and a re-registration cannot leave the rail quoting last month's
   measurement. */

/** What the panel says when the registry has nothing for this symbol/frame. */
export const UNMEASURED_NOTE = 'this frame is not registered in '
  + 'configs/alerts.json; only 30m and 1h clear the cost fraction on any '
  + 'instrument, and nothing faster survives a wider spread';

function emaLast(vals, n, endExclusive) {
  const k = 2 / (n + 1);
  let e = vals[0];
  for (let i = 1; i < endExclusive; i++) e = vals[i] * k + e * (1 - k);
  return e;
}

/**
 * Bars since EMA(fast) last crossed EMA(slow), as at bar `endExclusive - 1`.
 *
 * CAUSAL: it only needs to know when the CURRENT run began, which is past
 * information. Walking the EMAs forward once and remembering the last flip is
 * the same arithmetic as the Python's groupby-cumsum, and is the twin of
 * `age` in sim/strategies/rayo.py.
 *
 * WHAT IT IS FOR, and it is NOT a filter here. Trend age was the only one of
 * nine signal-time scores with a consistent relationship to outcome, and it
 * held up on USDJPY across both halves of history and three EMA pairs -- but on
 * XAUUSD it failed roughly half those checks. So the panel SHOWS it and does
 * not act on it. See the docstring in rayo.py.
 */
function ageLast(vals, fast, slow, endExclusive) {
  const kf = 2 / (fast + 1), ks = 2 / (slow + 1);
  const d = endExclusive - 1;              // the bar being decided
  let ef = vals[0], es = vals[0], up = null, since = -1;
  /* STOPS AT d-1, NOT d. The Python compares SHIFTED EMAs, so the cross state
     it sees on bar d is the one computed through bar d-1. Running this loop one
     bar further read the decision bar's own close and came out exactly 1 too
     low -- look-ahead of a single bar, which is small, wrong, and precisely how
     two implementations of one rule start to drift. */
  for (let i = 1; i < endExclusive - 1; i++) {
    ef = vals[i] * kf + ef * (1 - kf);
    es = vals[i] * ks + es * (1 - ks);
    const u = ef > es;
    if (up === null) up = u;
    else if (u !== up) { up = u; since = i; }
  }
  // since = -1 when the run reaches back to the first bar, which matches the
  // Python's fillna(True) starting every series in a fresh run.
  return d - since - 1;
}

/** Wilder ATR, same alpha as the Python side (1/n, not 2/(n+1)). */
function atrLast(bars, n, endExclusive) {
  const a = 1 / n;
  let e = bars[0].h - bars[0].l;
  for (let i = 1; i < endExclusive; i++) {
    const pc = bars[i - 1].c;
    const tr = Math.max(bars[i].h - bars[i].l,
                        Math.abs(bars[i].h - pc), Math.abs(bars[i].l - pc));
    e = tr * a + e * (1 - a);
  }
  return e;
}

/**
 * EVERY TICKET THAT ACTUALLY BECAME A TRADE, with where it started and ended.
 *
 * WHY THE BANDS NEEDED THIS. `ticket()` answers "what would the rule post right
 * now", and the rule posts something on almost every bar -- so drawn straight
 * it produced a band that slid along under price forever, which is a picture of
 * a proposal, not of a signal. These are the tickets whose pending order was
 * TOUCHED: they begin at the fill and end at the stop or TP3.
 *
 * ONE LIVE ORDER AT A TIME, the same discipline the backtest uses. Without it
 * an uptrend proposes the same buy stop a hundred times and the chart draws a
 * hundred overlapping ladders for what was one idea.
 */
export function signals(bars, {
  mode = 'break', swing = 20, fast = 20, slow = 50, stopAtr = STOP_ATR, expire = 12,
  session = SESSION, max = 6,
} = {}) {
  const out = [];
  if (!bars || bars.length < Math.max(swing, slow) + 4) return out;
  let busy = -1;
  for (let i = Math.max(swing, slow) + 3; i < bars.length; i++) {
    if (i < busy) continue;
    const t = ticket(bars.slice(0, i + 2),
                     { mode, swing, fast, slow, stopAtr, expire, session });
    if (!t) continue;
    const side = t.side === 'buy' ? 1 : -1;

    // ---- the pending order, inside its expiry window ----
    let fill = -1;
    for (let k = i; k < Math.min(bars.length, i + expire); k++) {
      if (bars[k].l <= t.entry && bars[k].h >= t.entry) { fill = k; break; }
    }
    if (fill < 0) { busy = i + expire; continue; }

    /* ---- then the stop against THE RUNG THE RULE EXITS ON ----
       Not the top of the ladder. TP2 and TP3 are drawn because the ticket
       carries them, and the registry says so in as many words -- "Drawn, not
       measured as an exit" -- so scoring against TP3 invented an exit nobody
       trades and nobody measured. */
    const target = t.tp[Math.min(Math.max(EXIT_TP, 1), t.tp.length) - 1];
    let end = bars.length - 1, why = 'open';
    for (let k = fill; k < bars.length; k++) {
      const hitSl = side > 0 ? bars[k].l <= t.stop : bars[k].h >= t.stop;
      const hitTp = side > 0 ? bars[k].h >= target : bars[k].l <= target;
      if (hitSl) { end = k; why = 'stop'; break; }        // ties go to the loss
      if (hitTp) { end = k; why = `tp${EXIT_TP}`; break; }
    }
    out.push({ ...t, fillIndex: fill, endIndex: end, outcome: why });
    busy = end + 1;
  }
  return out.slice(-max);
}

/**
 * The ticket the rule would post on the LAST CLOSED bar, or null.
 *
 * EVERY INPUT IS SHIFTED BY ONE, exactly as the Python does: the swing levels,
 * both EMAs and the ATR are computed to the bar BEFORE the one being decided.
 * Getting this wrong would let the panel draw a level off the bar it is
 * reacting to, which reads as prescience and is just look-ahead.
 */
export function ticket(bars, {
  mode = 'break', swing = 20, fast = 20, slow = 50, stopAtr = STOP_ATR, expire = 12,
  session = SESSION,
} = {}) {
  if (!bars || bars.length < Math.max(swing, slow) + 4) return null;
  /* DROP THE FORMING BAR. The bridge serves it as the last element and the
     Python does `df.iloc[:-1]` before deciding anything, so the decision bar is
     the last CLOSED one -- index length-2, not length-1. Deciding on length-1
     made every level here one bar more recent than the reference and put the
     ATR out by 1.6%, which is small, wrong, and exactly how two implementations
     of one rule start drifting apart. */
  const i = bars.length - 2;               // the last CLOSED bar
  const vals = bars.map((b) => b.c);

  const A = atrLast(bars, 14, i);          // ...to i-1
  if (!(A > 0) || !Number.isFinite(A)) return null;
  const ef = emaLast(vals, fast, i);
  const es = emaLast(vals, slow, i);

  let H = -Infinity, L = Infinity;
  for (let k = i - swing; k < i; k++) {
    if (k < 0) return null;
    H = Math.max(H, bars[k].h);
    L = Math.min(L, bars[k].l);
  }
  if (!Number.isFinite(H) || !Number.isFinite(L)) return null;

  /* THE SESSION GATE, ON THE BROKER'S CLOCK. Measured +42% on total R per year;
     outside those hours the rule posts nothing rather than a ticket the
     backtest never took.

     brokerHour, NOT getUTCHours, and the difference was a live bug. The stored
     research bars this was measured on are BROKER SERVER TIME (EET/EEST) and
     `bars.index.hour` in rayo.py reads them as such, but the bridge subtracts
     the offset and hands the browser TRUE UTC. Gating UTC hours 7-21 here
     against broker hours 7-21 there put the two three hours apart: the panel
     posted tickets from 19:00-21:00 UTC that the backtest never took, and sat
     silent from 04:00-07:00 UTC while the backtest was trading. */
  if (session) {
    const h = brokerHour(bars[i].t);
    if (!(h >= session[0] && h < session[1])) return null;
  }

  const up = ef > es;
  let side, order, entry;
  if (mode === 'break') {
    side = up ? 1 : -1;
    order = 'stop';
    entry = up ? H + 0.10 * A : L - 0.10 * A;
  } else {
    side = up ? 1 : -1;
    order = 'limit';
    entry = up ? L : H;
  }
  const stop = entry - side * stopAtr * A;
  const risk = Math.abs(entry - stop);
  if (!(risk > 0)) return null;

  return {
    mode,
    side: side > 0 ? 'buy' : 'sell',
    order,
    entry,
    stop,
    risk,
    atr: A,
    trend: up ? 'up' : 'down',
    age: ageLast(vals, fast, slow, i + 1),
    tp: TPS.map((k) => entry + side * k * risk),
    level: up ? H : L,        // the swing the ticket is built on
    swingHi: H,
    swingLo: L,
    barTime: bars[i].t,
    barIndex: i,
    expire,
    /* The live cost, from the spread the chart knows and this rule's own risk.
       Not the Donchian cells' floor -- they risk 2 ATR and this risks 1.5, so
       the same spread is a bigger fraction here. */
    costR: (spreadPx) => (spreadPx + 2 * 0.02 * A) / risk,
  };
}


/**
 * The ticket's order type in plain words: "Sell the pullback to the 20-bar
 * high", not "SELL LIMIT".
 *
 * WRITTEN BECAUSE THE BADGE READS AS A CONTRADICTION. `side` and `order` are
 * independent -- side is the direction, order is only where the entry sits
 * relative to the market -- but a SELL LIMIT resting ABOVE price looks, to
 * anyone who has not memorised broker vocabulary, like it must be a buy. It is
 * not: a sell limit sells.
 *
 * ONE LINE, AND IT WAS THREE. The first version spelt out the fill condition,
 * the expiry and the difference between a stop and a limit -- all true, all
 * already on the panel or in the "To trigger" column, and together a paragraph
 * hanging off a two-word badge. The verb is what was being asked for, so the
 * verb is what this returns. Naming the side in the first word answers the
 * question without a vocabulary lesson.
 *
 *     BUY  STOP   Buy on a break above the 20-bar high
 *     SELL STOP   Sell on a break below the 20-bar low
 *     BUY  LIMIT  Buy the pullback to the 20-bar low
 *     SELL LIMIT  Sell the pullback to the 20-bar high
 *
 * @param {object} t      a ticket from `ticket()`
 * @param {function} [fmt] price formatter; omit to leave the price out
 * @returns {string} one line, for a `title` attribute
 */
export function orderPhrase(t, fmt) {
  if (!t || !t.side || !t.order) return '';
  const buy = t.side === 'buy';
  const stop = t.order === 'stop';

  /* `level` IS NOT THE SWING THE ORDER SITS ON IN FADE MODE. The field is
     `H if up else L` in both twins -- the swing the TREND is read off -- and
     fade rests the order at the OPPOSITE swing, which is the entry itself. */
  const at = fmt ? ` ${fmt(stop ? t.level : t.entry)}` : '';

  return stop
    ? (buy ? `Buy on a break above the 20-bar high${at}`
           : `Sell on a break below the 20-bar low${at}`)
    : (buy ? `Buy the pullback to the 20-bar low${at}`
           : `Sell the pullback to the 20-bar high${at}`);
}
