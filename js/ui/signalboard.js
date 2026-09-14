/* signalboard.js — the Signal Board tab: every watched cell on one screen.
 *
 * WHAT IT IS. The browser twin of tools/signals_board.py, and deliberately the
 * same thing: a BOARD, not an order ticket. It answers one question — what does
 * the rule say about each cell right now — and for the cell that is asking for
 * something, it shows the rule's own numbers so the trade can be placed by hand
 * in MetaTrader.
 *
 * EVERY CELL IS ASKED ON A FLAT BASIS, whatever the terminal happens to hold.
 * /signal defaults to reading the live position because the rule's answer
 * genuinely depends on it — a breakout is an entry only when flat — and that is
 * correct for an order ticket and wrong for a board. Eleven cells share two
 * account positions, so four gold rows all reported the one `long` sitting in
 * MetaTrader and none of them could ever say BUY while any gold was open. The
 * board reports the market; execution is the reader's call, and the Book column
 * is there so both facts are on screen without either standing in for the other.
 *
 * THE CELL LIST COMES FROM configs/alerts.json, exactly as the Python board and
 * the scheduled alerter do. Three lists of watched cells would be three chances
 * for the page, the alert and the journal to disagree about what is being
 * tested, and a forward test that cannot say what it was testing is not one.
 *
 * THE GRADE IS ON EVERY ROW for the reason the Telegram message carries it:
 * `below` means the cell was measured under its own friction floor. A row that
 * looks identical to a validated one while being nothing of the kind is how a
 * forward test turns into a way to lose money on cells you already knew about.
 *
 * NOTHING HERE COMPUTES A SIGNAL. Every number arrives from the bridge's
 * /signal, which runs sim.signal.evaluate in Python — the same function the
 * backtest ran. The lot size especially: sizing needs an FX rate, and the
 * browser's rate and the engine's differ by enough to move a lot step.
 *
 * ONE CELL AT A TIME, on purpose. A 5m cell wants a 950-bar channel, and MT5
 * serves history one request at a time anyway; eight parallel asks would just
 * queue inside the terminal while the page looked hung. Rows paint as each
 * answer lands, so the board fills in rather than appearing all at once.
 */

import { api } from '../api.js';
import { gated, loadGate, loadMeasurement, loadScalperCells } from '../chart/graded.js';
import { MIN_LIVE_N, loadScored } from '../chart/scored.js';
import { attach as hoverCard } from './hovercard.js';
import { ticket as scalpTicket, orderPhrase, STOP_ATR } from '../chart/scalper.js';
import { TF, el, load, num, px, relTime, save, stamp } from '../util.js';

const ALERTS = 'configs/alerts.json';
const REFRESH_MS = 60000;

/* A newline, as a constant. Twice now a backslash-n written into this file by a
   patch script arrived as a REAL newline inside a string literal and took the
   whole app down -- and `node --check` accepted it both times, so only loading
   the page caught it. Naming it removes the escape from the source entirely. */
const NL = String.fromCharCode(10);


const GRADE_CLASS = {
  validated: 'sb-g-ok',
  marginal: 'sb-g-mid',
  below: 'sb-g-low',
};

const fx = (v, d = 3) => (typeof v === 'number' && isFinite(v) ? px(v, d) : '—');

/* THE EYE REACHES THE LOT COLUMN and nothing else on this board. Everything
   else here is a statement about the market -- true whether or not the account
   holds anything -- but a lot size is equity times risk, so printing it while
   the account is covered hands back the number the eye was pressed to hide. */
const covered = () => document.body.classList.contains('acct-hidden');

/**
 * HOW FAR THE MARKET IS FROM SAYING SOMETHING, in ATR.
 *
 * The board's most common answer is "no signal", and "no signal" is not one
 * fact but two: a market sitting on the trigger and a market nowhere near it
 * are both `hold`, and only one of them is worth watching today. ATR rather
 * than price so gold and yen can be compared in the same column.
 */
function distance(r) {
  if (!r || typeof r.bar_close !== 'number') return null;
  const { bar_close: c, upper, lower, atr } = r;
  if (!(atr > 0)) return null;
  const up = typeof upper === 'number' ? (upper - c) / atr : Infinity;
  const dn = typeof lower === 'number' ? (c - lower) / atr : Infinity;
  return dn < up ? { atr: dn, dir: 'down' } : { atr: up, dir: 'up' };
}

/**
 * WHEN A SIGNAL HAPPENED, as a real UTC epoch.
 *
 * `bar_time` is the bridge's UTC string and is the one field that cannot carry
 * a broker offset by accident. `ts_ms` did: the server offset was folded into
 * it, so a signal from an hour ago rendered as "in 1h 58m" — Pepperstone runs
 * UTC+3 and the number sat three hours ahead of the clock it was compared with.
 * The bridge now emits a clean epoch, and reading the string keeps this right
 * against a bridge that has not been restarted yet.
 */
function signalMs(x) {
  const t = String((x && x.bar_time) || '').trim();
  if (t) {
    /* 'YYYY-MM-DD HH:MM:SS' is parsed as LOCAL time by every browser. The T and
       the Z are what make it the UTC instant the bridge actually meant. */
    const iso = t.replace(' ', 'T')
                + (/[Zz]$|[+-]\d\d:?\d\d$/.test(t) ? '' : 'Z');
    const ms = Date.parse(iso);
    if (Number.isFinite(ms)) return ms;
  }
  return Number.isFinite(x && x.ts_ms) ? x.ts_ms : 0;
}

/**
 * THE MOST RECENT ENTRY THIS RULE ASKED FOR, and how long ago.
 *
 * WHY THE BOARD NEEDED THIS. A Donchian entry lives for exactly one bar, and
 * the gold cells are far quieter than their timeframes suggest: measured over
 * 2016-2026 the median gap between signals is 1.6 days on 5m, 7.2 on 4h and
 * 37.5 on 1d. So a board that only ever asks about the current bar shows six
 * dashes almost always -- correct, and useless. It read as "this never fires"
 * when the six gold cells between them produce about 415 signals a year.
 *
 * THE SCAN IS THE RULE'S OWN STATE MACHINE, checked bar for bar against
 * sim/core.py over 2024-2026: identical trade counts on all six cells, every
 * simulator entry matched. It is not a filter for bars that satisfy the entry
 * test -- price can sit outside the channel for thirty bars and only the first
 * is a signal.
 */
function lastSignalCell(r) {
  if (!r) return el('td', { class: 'dim', text: '…' });
  if (r.error) return el('td', { class: 'dim', title: r.error, text: '—' });
  const list = r.signals || [];
  if (!list.length) {
    /* NOT A DASH. "Nothing in the last 60 days" is a fact about the market and
       a dash reads as "not asked". The window is named so the reader can tell
       the two apart. */
    const days = r.scanned_days || 60;
    return el('td', { class: 'dim', title: `no entry in the last ${days} days`,
                      text: `none / ${days}d` });
  }
  const last = list[list.length - 1];
  const buy = last.action === 'buy';
  /* THE HOVER IS THE LEDGER, and it shows the LOSERS. Every signal in the
     window, newest first, with how it ended and what it made — not the last
     three winners. That difference is the whole reason to build this rather
     than copy a signal-room panel. */
  const line = (x) => {
    const when = (x.bar_time_server || x.bar_time || '').slice(0, 16);
    const r = Number.isFinite(x.r_net_est)
      ? `${x.r_net_est >= 0 ? '+' : ''}${x.r_net_est.toFixed(2)}R ${x.exit_reason}`
      : (x.open ? 'open' : '');
    return `${x.action.toUpperCase().padEnd(4)} ${when}  @ ${x.close}  ${r}`;
  };
  const closed = list.filter((x) => Number.isFinite(x.r_net_est));
  const wins = closed.filter((x) => x.r_net_est > 0).length;
  const tot = closed.reduce((a, x) => a + x.r_net_est, 0);
  const summary = closed.length
    ? `${closed.length} closed in ${r.scanned_days || '?'}d · `
      + `${wins} up / ${closed.length - wins} down · `
      + `${tot >= 0 ? '+' : ''}${tot.toFixed(2)}R net of an estimated `
      + `${(r.cost_floor_r ?? 0).toFixed(3)}R cost per trade`
    : 'no closed signals in the window';
  return el('td', {
    class: buy ? 'up' : 'down',
    title: [summary, ''].concat(list.slice(-8).reverse().map(line)).join(NL),
  }, `${buy ? 'BUY' : 'SELL'} · ${relTime(signalMs(last))}`);
}

/**
 * THE WINDOW'S RECORD, ON THE FACE OF THE BOARD.
 *
 * Wins, losses and total R for every signal in the scan window. On the face
 * rather than in a tooltip because this is the one number a signal panel is
 * usually built to hide: show three recent winners and the reader supplies the
 * rest. A cell that has lost over the window should say so without being asked.
 *
 * NET OF THE CELL'S OWN ESTIMATED COST. The scanner reports gross R; `r_net_est`
 * has already had the measured floor subtracted, which on 5m gold is 0.151 R
 * per trade -- more than the whole edge. Displaying gross here would flatter
 * every fast cell on the board by exactly the amount that makes it untradeable.
 */
function windowCell(r) {
  if (!r) return el('td', { class: 'dim', text: '…' });
  if (r.error) return el('td', { class: 'dim', text: '—' });
  const closed = (r.signals || []).filter((x) => Number.isFinite(x.r_net_est));
  if (!closed.length) return el('td', { class: 'dim', text: '—' });
  const wins = closed.filter((x) => x.r_net_est > 0).length;
  const tot = closed.reduce((a, x) => a + x.r_net_est, 0);
  return el('td', { class: tot >= 0 ? 'up' : 'down' },
    `${wins}/${closed.length}  ${tot >= 0 ? '+' : ''}${tot.toFixed(1)}R`);
}

/** Digits worth showing: gold moves in cents, yen in tenths of a pip. */
/**
 * The `Live` cell: what this cell has done, or how far off saying so it is.
 *
 * THE COUNT IS THE POINT BELOW THE FLOOR. A mean over three trades is not a
 * worse estimate of the edge, it is not an estimate of the edge, and rendering
 * it next to a nine-year figure invites precisely the comparison it cannot
 * support. So under MIN_LIVE_N this shows `3 fills` -- true, useful, and
 * impossible to misread as performance.
 */
/* THE VOCABULARY, IN ONE PLACE. Five words describe a ticket's whole life and
   none of them mean anything on their own -- "expired" sounds like a loss,
   "open" sounds like an opportunity, and "fill" is the only one that can carry
   a result. Defining them separately on each surface is how two surfaces end up
   defining them differently. */
const WORDS = {
  posted: 'POSTED — the rule proposed a pending order at a price away from the '
        + 'market. Nothing has been risked yet; it reposts on nearly every bar '
        + 'until something happens to it.',
  fill: 'FILL — price traded through the entry and the order opened. Only a '
      + 'fill can have a result, so every average on this board is over fills '
      + 'and nothing else.',
  expired: 'EXPIRED — price never reached the entry within the 12-bar expiry '
         + 'window, so the order was cancelled. NOT a loss: no position ever '
         + 'existed. About half of all tickets end this way, which is normal '
         + 'for a resting-order rule and not a sign anything is wrong.',
  won: 'WON — the fill reached the first target (0.9R) before the stop.',
  stopped: 'STOPPED — the fill reached the stop first. When a single 1m bar '
         + 'contains both the stop and the target, the 1m bar cannot say which '
         + 'came first and it is scored as the LOSS.',
  open: 'STILL OPEN — the order filled and has reached neither the target nor '
      + 'the stop yet. It has no result, so it is counted but excluded from '
      + 'every average until it ends.',
  none: 'NONE FILLED — this cell has posted tickets and not one of them opened; '
      + 'they all expired. Nothing was risked and there is nothing to score.',
  never: 'No ticket from this cell has been scored under the current rule yet. '
       + 'That means the rule changed recently or the cell has not signalled, '
       + 'not that it lost.',
};

/** The lifecycle in order -- the sentence that makes the five words cohere.
 *
 * TWO CASINGS, ON PURPOSE. The tooltip version shouts the five terms because
 * they are the thing being defined; the strip version is a sentence a reader
 * skims, and `.toLowerCase()` on the first was not the answer -- it produced
 * "...expires. a fill ends...", lowercasing the start of the second sentence. */
const LIFECYCLE = 'A ticket is POSTED, then either FILLS or EXPIRES. '
  + 'A fill ends WON, STOPPED, or is STILL OPEN.';
const LIFECYCLE_INLINE = 'a ticket is posted, then either fills or expires; '
  + 'a fill ends won, stopped, or still open';

function liveCell(scored, c, expected) {
  const v = scored ? scored.byCell.get(`${c.symbol}|${c.tf}`) : null;
  const has = !!(v && (v.n || v.expired || v.open));

  let text = '—';
  let cls = 'dim';
  if (has && v.mean !== null) {
    text = `${v.mean >= 0 ? '+' : ''}${v.mean.toFixed(4)} (${v.n})`;
    cls = v.mean >= 0 ? 'up' : 'down';
  } else if (has && v.n) {
    text = `${v.n} fill${v.n === 1 ? '' : 's'}`;
  } else if (has && v.open) {
    text = `${v.open} open`;
  } else if (has && v.expired) {
    text = 'none filled';
  }

  const td = el('td', { class: `${cls} sb-why`, text });
  /* aria-label, NOT title. A `title` here would make the OS draw its own plain
     tooltip on top of the card. */
  td.setAttribute('aria-label', `live record for ${c.symbol} ${c.tf}`);
  hoverCard(td, () => liveCard(c, v, expected));
  return td;
}

/**
 * The `Live` hover: the ticket lifecycle as a table, for one cell.
 *
 * THE ROWS ARE ORDERED AS THE LIFECYCLE RUNS -- posted, filled, expired, then
 * how the fills ended -- because the counts only mean anything in that order.
 * Read top to bottom it answers "what happened to everything this cell
 * proposed", which is the question a bare average cannot answer at all.
 */
function liveCard(c, v, expected) {
  const box = el('div', { class: 'hc' });
  box.append(el('div', { class: 'hc-head' },
    el('b', { text: `${c.symbol} ${c.tf}` }),
    el('span', { class: 'dim', text: 'live record' })));

  if (!v || (!v.n && !v.expired && !v.open)) {
    box.append(el('div', { class: 'hc-empty', text: WORDS.never }));
    return box;
  }

  const posted = v.n + v.expired + v.open;
  const rows = [
    /* NOT "posted". The rule reposts the same pending order on nearly every
       bar, so tickets POSTED runs to thousands; the scorer takes ONE AT A TIME
       and skips the reposts, and this is that count. Calling it "posted" would
       overstate the sample by two orders of magnitude. */
    ['tickets taken', posted,
     'one live order at a time — reposts of the same idea are skipped'],
    ['— filled', v.n + v.open, 'price traded through the entry'],
    ['— expired', v.expired, 'entry never reached; no position existed'],
    [null, null, null],
    ['won', v.wins, 'reached the 0.9R target first'],
    ['stopped', Math.max(0, v.n - v.wins), 'reached the stop first'],
    ['still open', v.open, 'filled, not finished — excluded from the average'],
  ];
  const t = el('table', { class: 'hc-t' });
  for (const [k, n, why] of rows) {
    if (k === null) { t.append(el('tr', { class: 'hc-rule' }, el('td', { colspan: '3' }))); continue; }
    t.append(el('tr', {},
      el('td', { class: 'hc-k', text: k }),
      el('td', { class: 'hc-n', text: String(n) }),
      el('td', { class: 'hc-y', text: why })));
  }
  box.append(t);

  const t2 = el('table', { class: 'hc-t hc-t2' });
  t2.append(el('tr', {},
    el('td', { class: 'hc-k', text: 'mean net R' }),
    el('td', { class: 'hc-n' + (v.mean === null ? ' dim' : (v.mean >= 0 ? ' up' : ' down')),
               text: v.mean === null ? 'withheld'
                     : `${v.mean >= 0 ? '+' : ''}${v.mean.toFixed(4)}` }),
    el('td', { class: 'hc-y',
               text: v.mean === null
                 ? `needs ${MIN_LIVE_N} fills, has ${v.n}`
                 : `over ${v.n} fills, ${v.winPct.toFixed(0)}% won` })));
  t2.append(el('tr', {},
    el('td', { class: 'hc-k', text: 'registered' }),
    el('td', { class: 'hc-n',
               text: Number.isFinite(expected)
                 ? `${expected >= 0 ? '+' : ''}${expected.toFixed(4)}` : '—' }),
    el('td', { class: 'hc-y', text: 'median of four sub-eras, same cost basis' })));
  box.append(t2);

  box.append(el('div', { class: 'hc-foot', text: LIFECYCLE }));
  return box;
}

const digitsFor = (sym) => (/JPY/i.test(sym) ? 3 : /XAU|XAG/i.test(sym) ? 2 : 5);

export class SignalBoard {
  constructor() {
    this.cells = null;          // from alerts.json, loaded once
    /* No `rows` or `recent` map any more: both held /signal payloads for the
       retired Donchian. `scalp` is the board's only state. */
    this.scalp = new Map();     // 'SYM|tf' -> live Rayo ticket
    this.scalpCells = null;
    /* NO GATE UNTIL THE CONFIG SAYS SO. An unloaded gate must not hide rows. */
    this.gate = { enforce: false, maxAge: null };
    loadGate().then((g) => { this.gate = g; });
    /* THE FORWARD RECORD, filtered to the stop the registry was measured at so
       the board never shows the old rule's outcomes against the new rule's
       expectation. Null until it lands; the board paints without it. */
    this.scored = null;
    loadMeasurement()
      .then((m) => loadScored(m.stopAtr))
      .then((sc) => { this.scored = sc; })
      .catch(() => { this.scored = null; });
    /* Off by default: the journalled and control cells are part of the
       forward test and hiding them by default would make the board look
       like a list of recommendations. */
    this.tradeableOnly = load('sbTradeableOnly', false);
    this.busy = false;
    this.asOf = 0;
    this.error = null;
    /* Which cells to show survives a reload like every other panel setting.
       Someone watching one instrument should not have to re-hide the rest of
       the board every morning. */
    this.showDisabled = load('sigShowDisabled', false);
    this.entriesOnly = load('sigEntriesOnly', false);
    this.open = new Set();      // expanded rows, by key
  }

  key(c) { return `${c.symbol}|${c.tf}`; }

  /** Ask the bridge for every visible cell, one after another. */
  async poll(repaint) {
    if (this.busy) return;
    this.busy = true;
    repaint();
    try {
      /* THE /signal POLL IS GONE with the Donchian. It ran sim.Strategy
         objects over the bridge, one cell at a time, and there is no longer a
         cell for it to run: `signals.watch` is retired and disabled. Leaving
         the loop in would have cost a bridge round trip per repaint to learn
         nothing. */

      /* THE RAYO CELLS, computed in the browser from bars. No /signal call:
         that endpoint runs sim.Strategy objects and the scalper is not one. */
      for (const c of (this.scalpCells || [])) {
        try {
          /* `api.bars` returns the ENVELOPE, not the array -- {bars, digits,
             ...}. Passing the envelope straight to the rule gave
             "bars.map is not a function" on every cell. */
          const payload = await api.bars(c.symbol, c.tf, 400);
          const bars = (payload && payload.bars) || [];
          const t = scalpTicket(bars, { mode: 'break' });
          /* THE LIVE PRICE RIDES ALONG, and it is deliberately the FORMING
             bar's close rather than the decision bar's. The ticket is decided
             on the last CLOSED bar -- that is the rule, and it must not drift
             -- but "how far is this from triggering" is a question about now,
             and answering it with a price up to one bar old would put a 1h
             cell an hour behind the market it is being judged against. The
             rule stays pure; the distance is presentation. */
          const now = bars.length ? bars[bars.length - 1].c : null;
          this.scalp.set(`${c.symbol}|${c.tf}`, t ? { ...t, cell: c, now } : null);
        } catch (exc) {
          this.scalp.set(`${c.symbol}|${c.tf}`, { error: String(exc.message || exc) });
        }
        repaint();
      }
    } finally {
      this.busy = false;
      this.asOf = Date.now();
      repaint();
    }
  }

  /**
   * @param host    the panel body
   * @param getPos  a FUNCTION returning the panel's current positions, not the
   *                array itself. The panel re-polls the terminal on its own
   *                clock; capturing the array here would freeze the Book column
   *                at whatever was held when the tab was opened.
   */
  render(host, getPos = () => []) {
    clearInterval(this.timer);
    host.innerHTML = '';
    const head = el('div', { class: 'sb-head' });
    const wrap = el('div', { class: 'sb-wrap' });
    host.append(head, wrap);

    /* Net lots per SYMBOL, summed over its legs — the same arithmetic the
       bridge does for /signal, done here because the answer is per symbol and
       the board has eleven rows over two symbols. */
    const bookOf = (symbol) => {
      const legs = (getPos() || []).filter((x) => x.symbol === symbol);
      if (!legs.length) return 'flat';
      const net = legs.reduce(
        (a, x) => a + (String(x.side).toLowerCase() === 'buy' ? 1 : -1) * (+x.volume || 0), 0);
      return Math.abs(net) < 1e-9 ? 'flat'
           : `${net > 0 ? '+' : ''}${num(net, 2)}`;
    };

    const paint = () => {
      if (!wrap.isConnected) return;
      head.innerHTML = '';
      wrap.innerHTML = '';
      if (this.error) {
        head.append(el('div', { class: 'sb-note down', text: this.error }));
        return;
      }

      /* THE LIVE LIST IS THE SCALPER'S. `visible()` reads the retired Donchian
         cells, every one of which is now disabled, so testing IT for emptiness
         would hide the board behind a rule nobody runs. */
      const cells = this.scalpCells || [];
      /* ONLY THE TRADEABLE CELLS REACH THE HEADLINE. Every cell has a ticket
         almost every bar -- that is what a pending-order rule does -- so
         listing all twenty would be a wall of orders, half of them from cells
         the measurement says not to trade. The line answers the question the
         reader actually has: what is there to do right now. */
      const acting = cells.filter((c) => {
        const t = this.scalp.get(`${c.symbol}|${c.tf}`);
        return c.tradeable && t && !t.error && t.side;
      });
      /* THE GATE IS ON THE HEADLINE, because the headline answers "what is
         there to do right now" and a gated ticket is one nothing is sent for.
         Counting all ten while Telegram sends five would make this line a
         different claim from the one the alerts make. */
      const sending = acting.filter(
        (c) => !gated(this.gate, this.scalp.get(`${c.symbol}|${c.tf}`)));
      const held = acting.length - sending.length;

      /* ---- the one line that matters, pinned ----
         A COUNT AND THE NEAREST ONE, not a list of all of them. This line was
         written for the Donchian, which signals rarely enough that naming
         every acting cell WAS the headline -- its empty state, "every one is
         inside its channel", says so. Rayo is the opposite: it rests a pending
         order on almost every cell on almost every bar, so the list was always
         ten items long and therefore told the reader nothing, while looking
         like ten recommendations. It is not ten recommendations: only one
         position is held at a time, roughly 3% of proposed tickets ever fill,
         and 30m and 1h on one symbol agree on direction 84% of the time.

         What varies, and so what is worth pinning, is which ticket is CLOSE TO
         FIRING. Distance is in ATR so gold and the yen are comparable. */
      const near = sending
        .map((c) => {
          const t = this.scalp.get(`${c.symbol}|${c.tf}`);
          const d = (t && t.now && t.atr > 0)
            ? Math.abs(t.entry - t.now) / t.atr : Infinity;
          return { c, t, d };
        })
        .sort((a, b) => a.d - b.d)[0];

      head.append(sending.length
        ? el('div', { class: 'sb-call' },
            el('span', { class: 'sb-k', text: `${sending.length} resting` }),
            ...(near && Number.isFinite(near.d) ? [
              el('span', { class: 'sb-k', text: 'nearest' }),
              el('b', { class: near.t.side === 'sell' ? 'down' : 'up',
                        /* WHAT THE BADGE MEANS, ON HOVER. "SELL LIMIT" reads
                           like a contradiction to anyone who has not memorised
                           broker vocabulary -- it rests ABOVE the market, so it
                           looks like a buy. It is not. */
                        title: orderPhrase(near.t, (v) => fx(v, digitsFor(near.c.symbol))),
                        text: `${near.c.symbol} ${near.c.tf} `
                              + `${near.t.side.toUpperCase()} ${near.t.order.toUpperCase()}` }),
              el('span', { class: 'dim',
                           text: `${near.d.toFixed(2)} ATR away` }),
            ] : []),
            ...(held ? [el('span', {
              class: 'dim',
              title: `The trend-age gate is on: a ticket whose EMA20/50 cross is `
                     + `more than ${this.gate.maxAge} bars old is journalled, not `
                     + `sent. Measured on a one-position account: +20.7% to `
                     + `+23.7% CAGR, chance of a losing year 22% to 16%.`,
              text: `${held} held back (stale trend)` })] : []),
            el('span', { class: 'dim',
                         text: '— pending orders, not positions. One at a time.' }))
        : el('div', { class: 'sb-call dim',
                      text: this.busy ? 'computing tickets…'
                            : acting.length
                              ? `all ${acting.length} tradeable tickets are held back `
                                + `by the trend-age gate (> ${this.gate.maxAge} bars `
                                + `since the cross)`
                              : 'no tradeable cell has a ticket — outside the '
                                + 'session, or no trend to join' }));

      const btn = (label, on, fn) => el('button', {
        class: 'sb-btn' + (on ? ' on' : ''), onclick: fn,
      }, label);

      head.append(el('div', { class: 'sb-tools' },
        el('span', { class: 'sb-k',
                     text: this.asOf ? `as of ${stamp(this.asOf)}` : 'not asked yet' }),
        /* `entries only` and `show disabled` WENT WITH THE DONCHIAN. Both were
           about that rule's vocabulary: `entries only` filtered on an `action`
           of buy/sell, which a Rayo cell does not have -- it always has a
           pending ticket or nothing -- and `show disabled` revealed cells the
           /signal poll had skipped, a poll that no longer exists. Keeping a
           toggle that silently does nothing is worse than not having it. */
        btn('tradeable only', this.tradeableOnly, () => {
          this.tradeableOnly = !this.tradeableOnly;
          save('sbTradeableOnly', this.tradeableOnly); paint();
        }),
        el('button', { class: 'sb-btn', onclick: () => this.poll(paint) },
           this.busy ? 'asking…' : 'refresh')));


      /* THE DONCHIAN TABLE USED TO BE HERE, and it was the board's main
         table until 2026-09-10. The horizon-matched Donchian is retired: every
         cell in `signals.watch` is disabled, its grades and pre-registered
         OOS/IS numbers kept for the record, and the two Windows tasks that
         polled it are stopped. The Rayo Scalper is the project's only signal
         generator, so it is no longer a second table below a first one -- it
         is the board.

         The Donchian columns, the /signal poll that fed them and the channel
         vocabulary they printed (upper, lower, exit_long, exit_short) went with
         it. Nothing here calls the bridge's /signal any more; a Rayo ticket is
         computed in the browser from bars the chart already has. */

      /* THE BOARD. One rule, one table. */
      const cells2 = this.scalpCells || [];
      if (!cells2.length) {
        wrap.append(el('div', { class: 'empty',
          text: `no scalper cells in ${ALERTS} — register the forward test first` }));
        return;
      }
      {
        wrap.append(el('div', { class: 'sb-sub' }, 'Rayo Scalper',
          el('span', { class: 'sb-sub-note',
            text: 'pending orders — fills only if price trades through the entry. '
                  + 'Greyed rows are journalled, not traded.' })));

        /* WHAT HAS ACTUALLY HAPPENED, IN COUNTS. Counts are honest at any
           sample size -- they do not pretend to estimate anything -- which is
           why this strip ships even though the per-cell averages beside it are
           still withheld. `expired` is deliberately shown: half of all tickets
           never fill, and a reader who does not know that reads the fill count
           as the signal count. */
        if (this.scored && this.scored.rows.length) {
          const t = this.scored.totals;
          const when = this.scored.since
            ? new Date(this.scored.since).toISOString().slice(0, 10) : null;
          const NLc = String.fromCharCode(10);
          wrap.append(el('div', { class: 'sb-scored' },
            el('span', { class: 'sb-k',
                         title: LIFECYCLE + NLc + NLc + WORDS.posted + NLc + NLc
                                + WORDS.fill + NLc + NLc + WORDS.expired,
                         text: 'forward record' }),
            when ? el('span', { class: 'dim',
                                title: 'The first ticket scored under the '
                                       + 'CURRENT rule. Earlier tickets were '
                                       + 'posted under a different stop width '
                                       + 'and are not pooled with these.',
                                text: `since ${when}` }) : null,
            el('b', { class: 'up', title: WORDS.won, text: `${t.tp} won` }),
            el('b', { class: 'down', title: WORDS.stopped,
                      text: `${t.sl} stopped` }),
            el('span', { class: 'dim', title: WORDS.expired,
                         text: `${t.expired} expired` }),
            el('span', { class: 'dim', title: WORDS.open,
                         text: `${t.open} still open` }),
            el('span', { class: 'dim', title: LIFECYCLE,
                         text: '— ' + LIFECYCLE_INLINE })));
        }
        const SC = [
          ['Symbol', 'Broker ticker'],
          ['TF', 'Timeframe. Greyed rows are journalled, not traded -- either controls (5m, negative on every instrument) or cells too close to the spread to trust (15m)'],
          ['Expected', 'Pre-registered net R per fill: the MEDIAN of four '
                     + 'sub-eras, not the best of them'],
          ['Live', 'What this cell has actually done since the current rule was '
                 + 'registered, on the same cost basis as Expected. '
                 + LIFECYCLE + ' '
                 + `The AVERAGE is withheld until ${MIN_LIVE_N} fills, because one `
                 + 'winning trade reads as beating the backtest tenfold and '
                 + 'means one trade won -- until then the cell shows how many '
                 + 'fills it has'],
          ['Order', 'A pending order, and where it rests relative to the market. '
                  + 'STOP joins the break, LIMIT fades it -- neither reverses the '
                  + 'side. Hover a cell for that ticket in words'],
          ['Age', 'Bars since EMA20 crossed EMA50 on this frame. Above the '
                  + 'configured limit the alert is withheld -- a stale trend is '
                  + 'not a worse trade on its own, it is one more likely to be '
                  + 'the trade another cell is already in'],
          ['Entry', 'Fills only if price trades through this within 12 bars'],
          ['To trigger', 'How far price must travel to fill this order, in ATR. '
                       + 'In ATR so instruments are comparable -- 0.2 is close, '
                       + '1.0 probably expires unfilled'],
          ['SL', `${STOP_ATR} ATR from the entry — that is 1R`],
          ['TP1', 'The rung the measurement exits on (0.9R)'],
          ['TP2', 'Drawn, not measured as an exit'],
          ['TP3', 'Drawn, not measured as an exit'],
          ['Bar', 'The closed bar the ticket was posted on'],
        ];
        const t2 = el('table', { class: 'grid sb-grid' });
        t2.append(el('thead', {}, el('tr', {}, SC.map(
          ([l, why]) => el('th', { text: l, title: why, class: 'sb-th' })))));
        const r2 = [];
        for (const c of cells2) {
          if (this.tradeableOnly && !c.tradeable) continue;
          const k = `${c.symbol}|${c.tf}`;
          const x = this.scalp.get(k);
          const d = digitsFor(c.symbol);
          const exp = Number.isFinite(c.expected)
            ? `${c.expected >= 0 ? '+' : ''}${c.expected.toFixed(4)}` : '—';
          if (!x || x.error) {
            r2.push(el('tr', { class: 'sb-row' },
              el('td', { class: 'sym', text: c.symbol }),
              el('td', { text: c.tf }),
              el('td', { class: 'dim', text: exp }),
              el('td', { class: 'dim', colspan: '10',
                         text: x ? x.error : '…' })));
            continue;
          }
          const buy = x.side === 'buy';
          /* THE WHOLE ROW GOES DIM WHEN THE CELL IS NOT TRADEABLE, because the
             sign of Expected is no longer enough to say so. With every
             instrument on the board there are cells that are positive and still
             must not be traded -- XAU 15m is +0.0075 at today's spread, zero at
             24 points, with a drawdown the size of its lifetime profit. Reading
             the side and the entry off a row like that is the mistake this
             column exists to prevent, so the ticket itself is greyed and only
             the tradeable rows are shown in live colours. */
          const live = c.tradeable;
          const trg = (x.now && x.atr > 0)
            ? Math.abs(x.entry - x.now) / x.atr : null;
          r2.push(el('tr', { class: live ? 'sb-row' : 'sb-row sb-muted',
                             title: c.note },
            el('td', { class: 'sym', text: c.symbol }),
            el('td', { text: c.tf }),
            el('td', { class: live ? 'up' : 'down', text: exp }),
            liveCell(this.scored, c, c.expected),
            /* THE BADGE IN WORDS, ON HOVER -- "Sell at the 20-bar high",
               not "SELL LIMIT". Asked directly whether a sell limit executes a
               buy; it does not, and the answer belongs on the thing that
               prompted the question. `title` on the cell beats the row's note
               here, which is what we want. */
            el('td', { class: (!live ? 'dim' : (buy ? 'up' : 'down')) + ' sb-why',
                       title: orderPhrase(x, (v) => fx(v, d)),
                       text: `${x.side.toUpperCase()} ${x.order.toUpperCase()}` }),
            /* WHY A TRADEABLE ROW IS NOT IN THE COUNT. Without this the board
               shows a green ticket and the headline silently omits it. */
            el('td', {
              class: !live ? 'dim' : (gated(this.gate, x) ? 'down' : 'up'),
              title: gated(this.gate, x)
                ? `Held back: ${x.age} bars since the EMA cross, over the `
                  + `${this.gate.maxAge}-bar limit. Journalled, not sent.`
                : 'Bars since the EMA20/50 cross',
              text: x.age == null ? '—'
                    : (gated(this.gate, x) ? `${x.age} held` : `${x.age}`) }),
            el('td', { text: fx(x.entry, d) }),
            /* THE COLUMN THAT SAYS WHICH ROW TO LOOK AT. Every row has an
               entry; almost none of them fill. This is the difference. */
            el('td', { class: trg === null ? 'dim' : (trg < 0.25 ? 'up' : 'dim'),
                       text: trg === null ? '—' : `${trg.toFixed(2)} ATR` }),
            el('td', { class: 'down', text: fx(x.stop, d) }),
            el('td', { class: 'up', text: fx(x.tp[0], d) }),
            el('td', { class: 'dim', text: fx(x.tp[1], d) }),
            el('td', { class: 'dim', text: fx(x.tp[2], d) }),
            el('td', { class: 'dim', text: new Date(x.barTime).toISOString().slice(0, 16) })));
        }
        t2.append(el('tbody', {}, r2));
        wrap.append(t2);
      }
    };

    paint();
    /* THE POLL WAITS FOR THE CELLS IT POLLS, and it did not always have to.
       This used to fire off the Donchian load, and the Donchian poll ran a
       bridge round trip per cell before it ever reached the scalper loop --
       which gave `loadScalperCells()` all the time in the world to resolve
       underneath it. Removing that loop removed the accidental delay, and the
       poll started reaching `this.scalpCells` while it was still null: twenty
       rows painted with their expectations and an ellipsis where every ticket
       should be, because the loop had quietly iterated an empty list. */
    loadScalperCells().then((cs) => {
      this.scalpCells = cs;
      paint();
      if (!this.asOf) this.poll(paint);
    }).catch((exc) => {
      this.error = `could not read ${ALERTS}: ${exc.message || exc}`;
      paint();
    });

    /* Same guard the calendar uses: the timer dies with the view rather than
       polling a bridge for a tab nobody is looking at. */
    this.timer = setInterval(() => {
      if (wrap.isConnected) this.poll(paint);
      else clearInterval(this.timer);
    }, REFRESH_MS);
  }

  hide() { clearInterval(this.timer); }
}
