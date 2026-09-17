/* opentrades.js — the rule's OPEN trades, per cell, with their prices.
 *
 * WHAT THIS ANSWERS. "What is this cell currently in?" The Signal Board's
 * forward record can already list it -- XAUUSD.a 15m SELL, entry 4279.28, SL
 * 4327.12, TP1 4236.23, still open -- and the chart could not, so the chart drew
 * something else and the two disagreed in public twice in one afternoon.
 *
 * THE SOURCE IS THE LEDGER, NOT A REPLAY. `data/scalper_scored.jsonl` is what
 * tools/score_scalper.py resolved from the journalled tickets on 1m bars from
 * the bridge; a row with outcome `open` is a ticket that filled and has reached
 * neither its target nor its stop. The study used to walk the rule over the
 * chart's loaded window instead and draw whatever trades THAT walk picked --
 * with its own cursor, from the left edge of a window the bridge caps at 1000
 * bars. It picked different trades from the scheduled run's, so the chart showed
 * a gold sell open from 4272.18 while the ledger held one from 4279.28.
 *
 * NOR THE BROKER'S POSITIONS, which was the previous attempt at this and was a
 * different thing wearing the same name. The account holds twenty gold
 * positions opened by hand between 4073 and 4611, none of them with a stop and
 * none of them this rule's; drawing those on a chart of the RULE answers a
 * question nobody asked here. Broker positions have their own dashed lines --
 * see the note in main.js -- and this is the rule's ledger.
 *
 * PRICES COME FROM THE JOURNAL, joined on `id`, exactly as the results table
 * does: the scored ledger deliberately records what HAPPENED and not what was
 * proposed, so the two files cannot disagree about one order. See journal.js.
 *
 * ONE ENTRY PER CELL. The rule holds one position at a time per cell, so a
 * second open row for the same symbol and frame would be a fault in the scorer
 * rather than a case to render; the newest wins and the older is dropped.
 */

import { loadMeasurement } from './graded.js';
import { loadJournal } from './journal.js';
import { loadScored } from './scored.js';

let _open = null;
const listeners = new Set();

function build(scored, journal) {
  const m = new Map();
  for (const r of scored.rows) {
    if (r.outcome !== 'open') continue;
    const j = journal.get(r.id);
    /* NO PRICES, NO ENTRY. A row whose journal ticket is missing cannot be
       drawn, and a half-drawn trade -- an entry with no stop -- is worse on a
       chart than no trade at all. */
    if (!j || typeof j.entry !== 'number' || typeof j.sl !== 'number') continue;
    const k = `${r.symbol}|${r.tf}`;
    const when = r.fill_ms || r.ms;
    const prev = m.get(k);
    if (prev && prev.fillMs >= when) continue;
    m.set(k, {
      id: r.id,
      symbol: r.symbol,
      tf: r.tf,
      side: r.side,
      entry: j.entry,
      sl: j.sl,
      tp: Array.isArray(j.tp) ? j.tp : [],
      fillMs: when,
      signalMs: r.ms,
    });
  }
  return m;
}

/**
 * `Map` of 'SYMBOL|tf' -> the open trade, loaded once per page.
 *
 * AN EMPTY MAP ON ANY FAILURE, for the reason scored.js and journal.js both
 * give: the chart must paint whether or not the scorer has ever run.
 */
export function loadOpenTrades() {
  if (!_open) {
    _open = loadMeasurement()
      .then((mm) => Promise.all([loadScored(mm.stopAtr), loadJournal()]))
      .then(([scored, journal]) => build(scored, journal))
      .catch(() => new Map());
    /* TELL THE CHARTS WHEN IT LANDS. Studies are synchronous, so the first
       `calc` after a page load always runs before this resolves and draws no
       trade; without this the chart would stay empty until something else
       happened to trigger a restudy. */
    _open.then((m) => {
      for (const fn of listeners) {
        try { fn(m); } catch (e) { /* a destroyed chart is the expected case */ }
      }
    });
  }
  return _open;
}

/** Synchronous read for study `calc`. Null until the ledger has landed. */
let cache = null;
loadOpenTrades().then((m) => { cache = m; });

export function openTradeFor(symbol, tf) {
  if (!cache || !symbol || !tf) return null;
  return cache.get(`${symbol}|${tf}`) || null;
}

/**
 * Has this trade already ended on these bars? `'sl'`, `'tp'`, or null.
 *
 * WHY THE CHART RE-CHECKS A LEDGER IT JUST READ. The scorer runs hourly at
 * :20, so a trade stopped at 10:05 stays written as `open` until 11:20 -- and
 * the band kept drawing a live position on a chart whose own candles had gone
 * clean through the stop over an hour earlier. The ledger is not wrong, it is
 * LATE, and the chart is holding the evidence.
 *
 * IT CAN ONLY CLOSE A TRADE, NEVER OPEN ONE. A trade absent from the ledger is
 * not drawn whatever the bars say; this only removes a band the bars have
 * already disproved. Wrong in the safe direction: the failure mode is a band
 * that vanishes slightly early, not a position shown that was never taken.
 *
 * COARSER BARS ARE STILL SOUND. The rule resolves on 1m and a chart may be on
 * H1, but a bar's high and low are the true extremes of everything inside it,
 * so a touch seen here is a real touch. What a coarse bar CANNOT do is order
 * two touches inside itself -- and it does not have to, because the tie already
 * goes to the stop.
 *
 * THE STOP WINS A TIE, exactly as tools/scalper.py resolves it: when one bar
 * holds both levels, nothing can say which came first and it is scored as the
 * LOSS.
 */
export function endedOn(held, bars, exitIdx = 0) {
  if (!held || !bars || !bars.length) return null;
  const exit = held.tp && held.tp[exitIdx];
  const up = held.side === 'buy';
  for (let i = 0; i < bars.length; i++) {
    const b = bars[i];
    if (b.t < held.fillMs) continue;
    const stopped = up ? b.l <= held.sl : b.h >= held.sl;
    if (stopped) return 'sl';
    if (typeof exit === 'number') {
      const won = up ? b.h >= exit : b.l <= exit;
      if (won) return 'tp';
    }
  }
  return null;
}

/** Call `fn` once the ledger has loaded. Returns an unsubscribe. */
export function onOpenTrades(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}
