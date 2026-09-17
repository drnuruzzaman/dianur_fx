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
import { MIN_LIVE_N, loadScored, refreshScored } from '../chart/scored.js';
import { restingRayo } from '../chart/rayorule.js';
import { rayoLedgerFor, refreshRayoLedger } from '../chart/rayoledger.js';
import { loadJournal } from '../chart/journal.js';
import { endedOn } from '../chart/opentrades.js';
import { attach as hoverCard } from './hovercard.js';
import { ticket as scalpTicket, orderPhrase, STOP_ATR } from '../chart/scalper.js';
import { TF, el, load, num, px, relTime, save, stamp } from '../util.js';

const ALERTS = 'configs/alerts.json';
/* HOW OFTEN THE PENDING ORDERS ARE RE-ASKED.
 *
 * 30s, DOWN FROM 60. A pending order changes when a bar closes, and the fastest
 * cells on this board close a bar every minute -- so a 60s cycle could show a
 * minute-old order on a minute timeframe, which is the whole lifetime of the
 * thing. The floor is not this number anyway: the poll walks every cell with
 * one bridge request each, sequentially, because MT5 serves history one request
 * at a time. Thirty cells is a cycle of real seconds, and asking more often
 * than a cycle takes would only queue inside the terminal. Rows paint as each
 * answer lands, so a cell is current the moment its own answer does. */
const REFRESH_MS = 30000;

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

/** The zero-cost median, coloured by sign, with R/yr and the eras on hover. */
function zeroCell(z) {
  if (!z || !Number.isFinite(z.expected_net_r)) {
    return el('td', { class: 'dim', text: '—',
                      title: 'Not measured at zero cost yet' });
  }
  const v = z.expected_net_r;
  const eras = Object.entries(z.eras || {})
    .map(([k, x]) => `${k} ${x >= 0 ? '+' : ''}${Number(x).toFixed(4)}`).join('  ·  ');
  return el('td', {
    class: v > 0 ? 'up' : 'down',
    text: `${v >= 0 ? '+' : ''}${v.toFixed(4)}`,
    title: `Zero cost (no spread, slippage or swap; commission not charged)
`
      + `median ${v >= 0 ? '+' : ''}${v.toFixed(4)} R per fill  ·  `
      + `${z.total_r_per_year >= 0 ? '+' : ''}${Number(z.total_r_per_year).toFixed(1)} R/yr  ·  `
      + `${z.n} fills  ·  ${z.win_pct}% win  ·  drawdown ${z.drawdown_r} R
${eras}`,
  });
}

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
    /* THE BARS THE POLL ALREADY PAID FOR. The loop below fetches 400 bars per
       cell to compute its ticket and threw them away; an `open` row can be
       checked against them for nothing, which is the difference between the
       board waiting an hour for the scorer and the board being right now. */
    this.cellBars = new Map();  // 'SYM|tf' -> bars from the last poll
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
    /* EAGERLY NOW, not on the first click. The journal carries the stop price,
       and without a stop price an `open` row cannot be re-checked -- so the
       headline counts need it, not just the list. It is still one fetch per
       page and still the largest thing this tab asks for. */
    loadJournal().then((j) => { this.journal = j; if (this.repaint) this.repaint(); })
      .catch(() => { this.journal = new Map(); });
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
    /* THE RESULTS LIST. Closed by default: it is a log, and a board that
       opens with a log pushed between the summary and the cells buries the
       thing the reader came for. `resultsFilter` is one of
       all/won/stopped/expired/open and is set by clicking the count. */
    this.resultsOpen = load('sbResultsOpen', false);
    this.resultsFilter = load('sbResultsFilter', 'all');
    /* THE POSTED PRICES, fetched only once the list is opened -- see
       journal.js. Null means "not asked for yet", an empty Map means "asked
       and there was nothing", and the two must not collapse or the table
       would retry the fetch on every repaint. */
    this.journal = null;
    /* THE SYMBOL FILTER, shared by the results list and the cell table.
       Stored lower-cased and matched as a substring, so "xau", "jpy" and
       "USDJPY.a" all work and nobody has to remember the `.a` suffix. */
    this.symQ = load('sbSymQ', '');
  }

  key(c) { return `${c.symbol}|${c.tf}`; }

  /**
   * The instrument filter.
   *
   * IT FILTERS ON EVERY KEYSTROKE and does NOT repaint the whole board to do
   * it. A full `paint()` rebuilds the header, which destroys the input the
   * reader is typing into and takes the caret with it -- the first version did
   * exactly that and dropped every character after the first. So the handler
   * hides rows directly and only calls `paint` when the results list needs
   * rebuilding, which cannot steal focus because the list is below the header.
   *
   * NOT DEBOUNCED. Nothing here is fetched; it is a substring test over at most
   * a few thousand rows already in memory.
   */
  searchBox(paint) {
    const input = el('input', {
      class: 'sb-search',
      type: 'search',
      value: this.symQ || '',
      placeholder: 'filter symbol…',
      title: 'Show only these instruments, on the cell table and in the '
             + 'results list. Matches any part of the ticker, so "xau" finds '
             + 'XAUUSD.a and the ".a" suffix is optional. Comma-separate for '
             + 'more than one: "xau, jpy". Empty shows everything.',
    });
    input.oninput = () => {
      this.symQ = input.value;
      save('sbSymQ', this.symQ);
      paint();
      /* PUT THE CARET BACK. `paint` replaced this element with a new one, so
         focus is restored on the node that now exists rather than on the one
         this closure is holding. */
      const live = document.querySelector('.sb-search');
      if (live && live !== input) {
        live.focus();
        live.setSelectionRange(live.value.length, live.value.length);
      }
    };
    return input;
  }

  /**
   * What this scored row ACTUALLY is, given bars the ledger has not seen.
   *
   * THE LEDGER IS HOURLY AND THE MARKET IS NOT. `score_scalper.py` runs at :20,
   * so a trade stopped at 10:05 stays written as `open` until 11:20 and the
   * board reported a live position for over an hour after the candles had gone
   * through the stop. The chart already re-checks this against its own bars;
   * this is the same check on the same rule, so the two surfaces cannot say
   * different things about one trade.
   *
   * ONLY `open` ROWS ARE RE-EXAMINED, and they can only CLOSE. Nothing here
   * invents a trade, reopens a closed one, or revises a number the scorer
   * wrote: the ledger stays the authority on everything except the one question
   * it is provably late on.
   *
   * NO PRICES OR NO BARS MEANS NO CHANGE. A cell that has not been polled yet,
   * or a row whose journal ticket is missing, reads exactly as the ledger wrote
   * it -- late, but never invented.
   */
  effective(r) {
    if (r.outcome !== 'open') return r.outcome;
    const j = this.journal && this.journal.get(r.id);
    if (!j || typeof j.sl !== 'number') return r.outcome;
    const bars = this.cellBars.get(`${r.symbol}|${r.tf}`);
    if (!bars || !bars.length) return r.outcome;
    const end = endedOn({ side: r.side, sl: j.sl, tp: j.tp || [],
                          fillMs: r.fill_ms || r.ms }, bars, 0);
    /* `tp` here is the EXIT rung, which is TP1 -- the same rung the rule trades
       and the same one the ledger would have written. */
    return end === 'sl' ? 'sl' : end === 'tp' ? 'tp1' : 'open';
  }

  /**
   * Fetch bars for every cell holding an `open` row, board or no board.
   *
   * THE RE-CHECK MUST COVER THE WHOLE LEDGER, not just what is on screen. The
   * poll walks `scalpCells`, which is the ENABLED cells; the ledger also holds
   * open trades from cells since switched off, and from frames the reader has
   * filtered out of view. Those rows were reading "open" for ever -- correct
   * against the ledger and wrong against the market, which is the failure this
   * whole check exists to remove. Every instrument, every timeframe, one rule.
   *
   * ONE FETCH PER CELL PER PAGE, and only for cells that actually have an open
   * row: thirteen of thirty-five today. Cells the poll has already paid for are
   * skipped, so this adds nothing for anything on the board.
   */
  async resolveOpen(repaint) {
    if (!this.scored || this.resolving) return;
    const want = new Set();
    for (const r of this.scored.rows) {
      if (r.outcome !== 'open') continue;
      const k = `${r.symbol}|${r.tf}`;
      if (!this.cellBars.has(k)) want.add(k);
    }
    if (!want.size) return;
    this.resolving = true;
    try {
      for (const k of want) {
        const [symbol, tf] = k.split('|');
        try {
          const payload = await api.bars(symbol, tf, 400);
          this.cellBars.set(k, (payload && payload.bars) || []);
        } catch (exc) {
          /* AN EMPTY ARRAY IS NOT A RESULT. `effective` treats "no bars" as
             "no change", and caching [] here would make that permanent for the
             page -- the row would read open for ever because one fetch failed
             once. Left unset, so the next pass retries. */
        }
        repaint();
      }
    } finally { this.resolving = false; }
  }

  /** Did `effective` overrule the ledger on this row? */
  late(r) { return r.outcome === 'open' && this.effective(r) !== 'open'; }

  /** Does this symbol pass the search box? An empty box passes everything. */
  matches(symbol) {
    const q = (this.symQ || '').trim().toLowerCase();
    if (!q) return true;
    /* COMMA-SEPARATED IS AN OR, so "xau, jpy" is two instruments rather than a
       query that matches nothing. Whitespace alone is not a separator: a bare
       space is far more likely to be a typo than an intent to widen. */
    return q.split(',').map((t) => t.trim()).filter(Boolean)
      .some((t) => symbol.toLowerCase().includes(t));
  }


  /**
   * One clickable count in the forward-record strip.
   *
   * CLICKING A NUMBER FILTERS THE LIST TO THAT NUMBER, and clicking the one
   * already selected closes the list again -- so the same control opens,
   * switches and dismisses, and there is no separate "show results" button
   * competing with it for the reader's attention.
   */
  countBtn(kind, n, cls, why) {
    const on = this.resultsOpen && this.resultsFilter === kind;
    const b = el('button', {
      class: `sb-count ${cls}${on ? ' on' : ''}`,
      title: why + NL + NL + (n ? 'Click to list them.' : 'Nothing to list yet.'),
      text: `${n} ${kind === 'open' ? 'still open' : kind}`,
    });
    /* A ZERO IS NOT A BUTTON. Disabled rather than hidden: "0 stopped" is
       information, and removing it would make a clean run look like a
       rendering fault. */
    if (!n) b.disabled = true;
    else {
      b.onclick = () => {
        if (on) this.resultsOpen = false;
        else {
          this.resultsOpen = true;
          this.resultsFilter = kind;
          /* THE PRICES ARRIVE AFTER THE LIST DOES, and that is fine: the
             table renders dashes in the price columns for the fraction of a
             second the fetch takes, then repaints. Blocking the open would
             make the click feel broken to save a flicker. */
          if (!this.journal) {
            loadJournal().then((j) => { this.journal = j; this.repaint(); })
              .catch(() => { this.journal = new Map(); });
          }
        }
        save('sbResultsOpen', this.resultsOpen);
        save('sbResultsFilter', this.resultsFilter);
        this.repaint();
      };
    }
    return b;
  }

  /**
   * Every scored ticket, newest first, as a table.
   *
   * THIS READS THE SAME ROWS THE COUNTS ARE COUNTED FROM -- `scored.rows`,
   * already filtered to the current stop width by scored.js. A second query
   * against the ledger would be a second chance for the list and the total
   * above it to disagree, which is exactly the bug this board exists to avoid.
   *
   * TRADED AND JOURNALLED ARE MARKED, NOT MIXED. Much of the forward record
   * comes from cells the registry does not mark tradeable -- the 1m and 5m
   * controls, which fire tens of times more often than anything else. A list
   * that pooled them would report the controls' record as the rule's.
   */
  resultsTable(cells) {
    if (!this.resultsOpen || !this.scored) return null;
    /* FETCH HERE, NOT ONLY ON THE CLICK. `resultsOpen` is restored from the
       last session, so a reader who left the list open came back to a table
       with three columns of dashes -- the click that would have asked for the
       journal never happened. Asking where the table is BUILT covers both
       paths, and `loadJournal` is memoised so the click path costs nothing. */
    if (!this.journal) {
      loadJournal().then((j) => { this.journal = j; this.repaint(); })
        .catch(() => { this.journal = new Map(); });
    }
    const eff = (r) => this.effective(r);
    const KIND = {
      won: (r) => eff(r) === 'tp1' || eff(r) === 'tp2' || eff(r) === 'tp3',
      stopped: (r) => eff(r) === 'sl',
      expired: (r) => eff(r) === 'expired',
      open: (r) => eff(r) === 'open',
      all: () => true,
    };
    const want = KIND[this.resultsFilter] || KIND.all;
    const trade = new Map(cells.map((c) => [`${c.symbol}|${c.tf}`, !!c.tradeable]));
    const rows = this.scored.rows.filter(want)
      .filter((r) => this.matches(r.symbol))
      /* Sorted by the FILL, not the post: two tickets posted an hour apart can
         fill in the other order, and the question this list answers is when
         money was at risk. An expiry never filled, so it falls back to `ms`. */
      .sort((a, b) => (b.fill_ms || b.ms) - (a.fill_ms || a.ms));

    const box = el('div', { class: 'sb-results' });
    if (!rows.length) {
      box.append(el('div', { class: 'empty', text: 'nothing in this category' }));
      return box;
    }

    const HEAD = [
      ['Filled', 'When the order opened, on the same clock as the rest of the app. '
               + 'An expired ticket '
               + 'never opened, so for those this is when it was posted'],
      ['Symbol', 'Broker ticker'],
      ['TF', 'The cell that posted it'],
      ['Side', 'BUY or SELL'],
      ['Result', LIFECYCLE],
      ['Entry', 'The price the pending order rested at. It filled here, or '
              + 'expired without ever being touched'],
      ['SL', `The stop: ${STOP_ATR} ATR from the entry, which is 1R by definition`],
      ['TP1', 'The rung the rule exits on, 0.9R from the entry. Hover for the '
            + 'two rungs above it, which are drawn but never traded'],
      ['Net R', 'Result in units of the risk taken, AFTER spread, slippage and '
              + 'overnight financing. About +0.9 is the target, about -1.0 the stop'],
      ['Traded', 'Whether the registry marks this cell tradeable. A journalled '
               + 'cell is measured and recorded but is not a recommendation. '
               + 'A dash means the cell has since been switched off the board'],
    ];
    const t = el('table', { class: 'grid sb-grid sb-res-grid' });
    t.append(el('thead', {}, el('tr', {}, HEAD.map(
      ([l, why]) => el('th', { text: l, title: why, class: 'sb-th' })))));

    const body = el('tbody');
    let sum = 0;
    let closed = 0;
    for (const r of rows) {
      /* THREE STATES, NOT TWO. `loadScalperCells` drops DISABLED cells, so a
         ticket scored before its cell was switched off has no entry here at
         all -- and `undefined` read as `false` labelled real tradeable cells
         "journal", which is the one thing this column exists to get right. */
      const isTrade = trade.has(`${r.symbol}|${r.tf}`)
        ? trade.get(`${r.symbol}|${r.tf}`) : null;
      const o = this.effective(r);
      const won = o !== 'sl' && o !== 'expired' && o !== 'open';
      /* NO NET R ON A ROW THE LEDGER HAS NOT SCORED YET. The bars can say a
         trade is over; only the scorer can say what it cost, because the
         number owes spread, slippage and swap that the browser does not
         model. An estimate here would be a second opinion on a figure that
         must have exactly one. */
      const hasR = typeof r.net_r === 'number' && !this.late(r)
                   && r.outcome !== 'open' && r.outcome !== 'expired';
      if (hasR) { sum += r.net_r; closed += 1; }
      /* THE TICKET BEHIND THE RESULT. Absent only while the journal is still
         in flight, or if a scored row ever outlives its journal row. */
      const j = this.journal ? this.journal.get(r.id) : null;
      const d = digitsFor(r.symbol);
      const price = (v) => (typeof v === 'number' ? v.toFixed(d) : '\u2014');
      const word = won ? 'won' : (o === 'sl' ? 'stopped'
                                  : (o === 'expired' ? 'expired' : 'open'));
      body.append(el('tr', { class: isTrade === false ? 'sb-journal' : '' },
        el('td', { class: 'dim', text: stamp(r.fill_ms || r.ms) }),
        el('td', { text: r.symbol }),
        el('td', { text: r.tf }),
        el('td', { class: r.side === 'buy' ? 'up' : 'down',
                   text: String(r.side || '').toUpperCase() }),
        /* THE RUNG IS PART OF THE ANSWER. The rule exits at TP1, so a `tp2` in
           here means the resolver saw the ladder run past the measured exit --
           worth seeing, not worth flattening into "won". */
        el('td', { class: won ? 'up' : (o === 'sl' ? 'down' : 'dim'),
                   title: WORDS[word] + (this.late(r)
                     ? NL + NL + 'Read off this chart’s own bars: price '
                       + 'reached this level before the scorer last ran (every 5 minutes), '
                       + 'so the ledger still says open. The result is settled; '
                       + 'the net R is not, and is withheld until the scorer '
                       + 'agrees.' : ''),
                   /* AN ASTERISK, NOT A SILENT REWRITE. The row is ahead of the
                      ledger and a reader comparing it against the .jsonl needs
                      to know why they differ. */
                   text: (won ? `won ${o}` : (o === 'sl' ? 'stopped' : o))
                         + (this.late(r) ? ' *' : '') }),
        el('td', { class: 'num', text: price(j && j.entry) }),
        el('td', { class: 'num down', text: price(j && j.sl) }),
        /* THE LADDER IS ON THE HOVER, NOT IN THREE MORE COLUMNS. TP2 and TP3
           are drawn on the chart and were never measured as exits, so putting
           them on the face of a RESULTS table would invite reading them as
           outcomes that were available. */
        el('td', { class: 'num up',
                   title: (j && j.tp && j.tp.length > 1)
                     ? `Drawn but not traded — TP2 ${price(j.tp[1])}, `
                       + `TP3 ${price(j.tp[2])}` : null,
                   text: price(j && j.tp && j.tp[0]) }),
        el('td', { class: `num ${hasR ? (r.net_r >= 0 ? 'up' : 'down') : 'dim'}`,
                   text: hasR
                     ? `${r.net_r >= 0 ? '+' : ''}${r.net_r.toFixed(3)}`
                     : '—' }),
        el('td', { class: 'dim',
                   title: isTrade === null
                     ? 'This cell is no longer on the board -- it was switched '
                       + 'off after this ticket was scored, so the registry has '
                       + 'nothing to say about it now.' : null,
                   text: isTrade === null ? '—'
                         : (isTrade ? 'traded' : 'journal') })));
    }
    t.append(body);
    box.append(t);
    /* THE TOTAL IS OVER FILLS THAT ENDED, and says so. Summing R over a list
       that includes expiries and open trades would divide by the wrong thing
       in the reader's head. */
    box.append(el('div', { class: 'sb-res-foot' },
      el('span', { class: 'dim', text: `${rows.length} shown` }),
      closed ? el('span', {
        class: sum >= 0 ? 'up' : 'down',
        title: `Sum of net R over the ${closed} of these that reached the `
               + 'target or the stop. Expiries and open trades contribute '
               + 'nothing, because nothing was booked.',
        text: `${sum >= 0 ? '+' : ''}${sum.toFixed(2)} R over ${closed} closed`,
      }) : null));
    return box;
  }


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
      /* THE LIVE RECORD, RE-READ EVERY POLL. The forward record used the
         ledger as it was when the page opened, so a trade the scorer opened an
         hour later never appeared; and the fixed-12 walker below needs the
         journal as of now to know which ticket is resting. */
      refreshScored();
      try {
        const m = await loadMeasurement();
        this.scored = await loadScored(m.stopAtr);
      } catch { /* keep the previous copy */ }
      await refreshRayoLedger(true);
      for (const c of (this.scalpCells || [])) {
        try {
          /* `api.bars` returns the ENVELOPE, not the array -- {bars, digits,
             ...}. Passing the envelope straight to the rule gave
             "bars.map is not a function" on every cell. */
          const payload = await api.bars(c.symbol, c.tf, 400);
          const bars = (payload && payload.bars) || [];
          this.cellBars.set(`${c.symbol}|${c.tf}`, bars);
          /* FIXED 12, AS THE PANEL, TP BANDS, REPLAY AND TELEGRAM: the ticket
             actually RESTING (taken once, levels fixed for 12 bars) or the
             trade it filled into -- not a fresh ticket every poll. */
          const rest = restingRayo(bars, { tf: c.tf, mode: 'break',
                                           ledger: rayoLedgerFor(c.symbol, c.tf) });
          let t = null;
          if (rest && rest.position) {
            const p = rest.position, tk = p.ticket || {};
            t = { ...tk, held: true, side: p.side > 0 ? 'buy' : 'sell',
                  entry: p.entryPrice, stop: p.stop, tp: p.tp,
                  fillMs: p.entryTime, barTime: tk.barTime ?? bars[p.signalI]?.t,
                  fromLedger: !!p.id };
          } else if (rest && rest.pending) {
            t = rest.pending;
          }
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
    /* AFTER the cells on the board, never instead of them: the board's own
       rows are what the reader is waiting for, and the ledger re-check is
       catching up on history. */
    this.resolveOpen(repaint);
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

    /* PAINT, EXPOSED. The results list is toggled from a button inside the
       board rather than from the tab that owns it, so the handler needs the
       same repaint every other control here calls. Assigned rather than made
       a method because `paint` closes over `head` and `wrap`. */
    const paint = () => {
      if (!wrap.isConnected) return;
      head.innerHTML = '';
      wrap.innerHTML = '';
      if (this.error) {
        head.append(el('div', { class: 'sb-note down', text: this.error }));
        return;
      }

      /* THE STOP-LOSS WARNING, as on Positions, Orders and History. The rule's
         own tickets always carry a 5 ATR stop, so the warning here is about the
         ACCOUNT: broker positions (on the symbols the search box shows) that
         have none. Hidden with the account eye -- `.acct-private`, see
         css/app.css -- because a count of positions is the account talking. */
      const bare = (getPos() || []).filter((p) => !p.sl && this.matches(p.symbol));
      if (bare.length) {
        const bySym = bare.reduce((a, p) => { a[p.symbol] = (a[p.symbol] || 0) + 1; return a; }, {});
        head.append(el('div', {
          class: 'sb-note down acct-private',
          title: Object.entries(bySym).map(([s, n]) => `${s}: ${n}`).join(NL)
                 + NL + NL + 'Open the Positions tab for the list.',
          text: `⚠ ${bare.length} broker position${bare.length === 1 ? '' : 's'} `
                + 'without a stop loss',
        }));
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
        /* An IN TRADE row is not an order to place; it is counted apart. */
        return c.tradeable && t && !t.error && t.side && !t.held;
      });
      const inTrade = cells.filter((c) => {
        const t = this.scalp.get(`${c.symbol}|${c.tf}`);
        return c.tradeable && t && t.held;
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
            ...(inTrade.length ? [el('span', { class: 'sb-k',
              title: inTrade.map((c) => {
                const t = this.scalp.get(`${c.symbol}|${c.tf}`);
                return `${c.symbol} ${c.tf} ${t.side.toUpperCase()} from `
                  + fx(t.entry, digitsFor(c.symbol));
              }).join(NL),
              text: `${inTrade.length} in trade` })] : []),
            el('span', { class: 'dim',
                         text: '— resting orders (fixed 12), not positions. One at a time.' }))
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
        this.searchBox(paint),
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
            text: 'the project’s only signal generator' })));

        /* WHAT HAS ACTUALLY HAPPENED, IN COUNTS. Counts are honest at any
           sample size -- they do not pretend to estimate anything -- which is
           why this strip ships even though the per-cell averages beside it are
           still withheld. `expired` is deliberately shown: half of all tickets
           never fill, and a reader who does not know that reads the fill count
           as the signal count. */
        if (this.scored && this.scored.rows.length) {
          /* THE COUNTS FOLLOW THE FILTER. Leaving them at the full totals while
             the list below showed a subset would have the strip and the table
             disagreeing on screen, which is the one thing this board must not
             do. Recomputed rather than stored, because the filter is a string
             the reader is still typing. */
          /* COUNTED OFF `effective`, NEVER off `scored.totals`. The stored
             totals are the ledger's, and the ledger is up to an hour behind on
             `open`; a strip that said "5 still open" over a list showing four
             would be the board disagreeing with itself. */
          const t = this.scored.rows.filter((r) => this.matches(r.symbol))
            .reduce((acc, r) => {
              const o = this.effective(r);
              if (o === 'expired') acc.expired += 1;
              else if (o === 'open') acc.open += 1;
              else if (o === 'sl') acc.sl += 1;
              else acc.tp += 1;
              return acc;
            }, { tp: 0, sl: 0, expired: 0, open: 0 });
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
            this.countBtn('won', t.tp, 'up', WORDS.won),
            this.countBtn('stopped', t.sl, 'down', WORDS.stopped),
            this.countBtn('expired', t.expired, 'dim', WORDS.expired),
            this.countBtn('open', t.open, 'dim', WORDS.open),
            el('span', { class: 'dim', title: LIFECYCLE,
                         text: '— ' + LIFECYCLE_INLINE })));
          /* THE LIST, UNDER THE COUNTS. A count answers "how did it go"; the
             only follow-up anyone ever has is "which ones", and until this
             existed the answer was to open a .jsonl in a text editor. */
          /* GUARDED, because `ParentNode.append(null)` does not skip the
             argument -- it inserts the literal text "null", which is exactly
             what the closed list rendered above the cell table. */
          const list = this.resultsTable(cells2);
          if (list) wrap.append(list);
        }
        const SC = [
          ['Symbol', 'Broker ticker'],
          ['TF', 'Timeframe. Greyed rows are journalled, not traded -- either controls (5m, negative on every instrument) or cells too close to the spread to trust (15m)'],
          ['Expected', 'Pre-registered net R per fill: the MEDIAN of four '
                     + 'sub-eras, not the best of them'],
          ['Zero cost', 'The same measurement with NO spread, NO slippage and NO '
                      + 'swap (a raw ECN, swap-free account): median of four '
                      + 'sub-eras, net R per fill. Commission is not charged, so '
                      + 'this is a ceiling. Hover for R/yr and the eras'],
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
          if (!this.matches(c.symbol)) continue;
          const k = `${c.symbol}|${c.tf}`;
          const x = this.scalp.get(k);
          const d = digitsFor(c.symbol);
          const exp = Number.isFinite(c.expected)
            ? `${c.expected >= 0 ? '+' : ''}${c.expected.toFixed(4)}` : '—';
          const zc = zeroCell(c.zero);
          if (!x || x.error) {
            r2.push(el('tr', { class: 'sb-row' },
              el('td', { class: 'sym', text: c.symbol }),
              el('td', { text: c.tf }),
              el('td', { class: 'dim', text: exp }),
              zc,
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
          const trg = (!x.held && x.now && x.atr > 0)
            ? Math.abs(x.entry - x.now) / x.atr : null;
          r2.push(el('tr', { class: live ? 'sb-row' : 'sb-row sb-muted',
                             title: c.note },
            el('td', { class: 'sym', text: c.symbol }),
            el('td', { text: c.tf }),
            el('td', { class: live ? 'up' : 'down', text: exp }),
            zc,
            liveCell(this.scored, c, c.expected),
            /* THE BADGE IN WORDS, ON HOVER -- "Sell at the 20-bar high",
               not "SELL LIMIT". Asked directly whether a sell limit executes a
               buy; it does not, and the answer belongs on the thing that
               prompted the question. `title` on the cell beats the row's note
               here, which is what we want. */
            x.held
              ? el('td', { class: (!live ? 'dim' : (buy ? 'up' : 'down')) + ' sb-why',
                           title: `The resting ticket filled at ${fx(x.entry, d)} on `
                             + `${stamp(x.fillMs)}. No new `
                             + 'order until it reaches its stop or TP1.'
                             + (x.fromLedger ? '' : ' Not in the scored ledger yet '
                                + '(the scorer runs every 5 minutes); seen on these bars.'),
                           text: `IN TRADE ${x.side.toUpperCase()}` })
              : el('td', { class: (!live ? 'dim' : (buy ? 'up' : 'down')) + ' sb-why',
                           title: orderPhrase(x, (v) => fx(v, d))
                             + (Number.isFinite(x.barsLeft)
                               ? ` -- ${x.barsLeft} bar(s) left, levels fixed` : ''),
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
            x.held
              ? el('td', { class: 'dim', title: 'Filled',
                           text: `filled ${stamp(x.fillMs)}` })
              : el('td', { class: trg === null ? 'dim' : (trg < 0.25 ? 'up' : 'dim'),
                           text: trg === null ? '—' : `${trg.toFixed(2)} ATR` }),
            el('td', { class: 'down', text: fx(x.stop, d) }),
            el('td', { class: 'up', text: fx(x.tp[0], d) }),
            el('td', { class: 'dim', text: fx(x.tp[1], d) }),
            el('td', { class: 'dim', text: fx(x.tp[2], d) }),
            el('td', { class: 'dim', text: stamp(x.barTime) })));
        }
        t2.append(el('tbody', {}, r2));
        /* THE TABLE SAYS WHAT IT IS. It had carried the rule's name and the
           reader's reasonable conclusion was that it listed the rule's
           SIGNALS -- so an open trade drawn on the chart, or a filled one in
           the forward record, looked like something the table had lost. It
           lists PENDING ORDERS: one row per cell, the order the rule wants
           next, and nothing that has already filled. Naming it that is the
           whole fix. */
        wrap.append(el('div', { class: 'sb-sub' }, 'Orders and open trades',
          el('span', { class: 'sb-sub-note',
            text: 'one per cell, fixed 12: the ticket resting (levels fixed for '
                  + '12 bars from its own bar) or IN TRADE when it has filled -- '
                  + 'the same order the chart panel and Telegram name. Greyed '
                  + 'rows are journalled, not traded.' })));
        wrap.append(t2);
      }
    };

    this.repaint = paint;
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
