/* scored.js — the FORWARD record: what the live tickets actually did.
 *
 * `tools/score_scalper.py` runs hourly, resolves every journalled ticket on 1m
 * bars from the bridge and writes data/scalper_scored.jsonl. Nothing read it.
 * This module is the reader, so the Signal Board can put what a cell HAS DONE
 * beside what it was REGISTERED to do -- the question a reader has the moment
 * they see an `Expected` column.
 *
 * NOTHING IS SCORED HERE, exactly as in graded.js. Outcomes, costs and swap are
 * decided by the Python, which reuses the backtest's own resolver; a second
 * implementation in the browser would be a second chance for two surfaces to
 * disagree about what happened.
 *
 * ONLY THE CURRENT RULE'S TICKETS COUNT. The ledger spans a rule change -- every
 * ticket before 2026-09-14 was posted at a 4.0 ATR stop -- and each row carries
 * the `stop_atr` it was posted under. Rows from another stop are dropped rather
 * than pooled, because showing the old rule's results against the new rule's
 * expectation is not a comparison, it is a category error with a number on it.
 *
 * A MEAN IS WITHHELD UNTIL THERE IS SOMETHING TO AVERAGE. `MIN_LIVE_N` exists
 * because the honest failure mode of this feature is not being wrong, it is
 * being READ. A cell with one winning trade shows +0.886 against a registered
 * +0.0604 and looks like it is beating the backtest fourteen-fold; it means one
 * trade won. Under the floor the board shows the COUNT and no average, so the
 * column reports progress towards an answer instead of impersonating one.
 */

const LEDGER = 'data/scalper_scored.jsonl';

/** Fills a cell needs before its live mean is shown rather than its count. */
export const MIN_LIVE_N = 30;

/** Outcomes that moved money. `expired` did not: the order never opened. */
const CLOSED = new Set(['sl', 'tp1', 'tp2', 'tp3']);

let _scored = null;

/**
 * Read the ledger once per page.
 *
 * Resolves to `{ byCell, totals, since, rows }`. A missing or unreadable
 * ledger is an EMPTY RECORD, not an error: the scorer may simply not have run
 * yet, and a board that refused to paint because there were no forward results
 * would be broken on exactly the day the feature shipped.
 */
export function loadScored(stopAtr, url = LEDGER) {
  if (!_scored) {
    _scored = fetch(url, { cache: 'no-store' })
      .then((r) => (r.ok ? r.text() : ''))
      .then((txt) => txt.split('\n').map((l) => l.trim()).filter(Boolean)
        .map((l) => { try { return JSON.parse(l); } catch (e) { return null; } })
        .filter(Boolean))
      .catch(() => []);
  }
  return _scored.then((rows) => summarise(rows, stopAtr));
}

function summarise(rows, stopAtr) {
  const mine = stopAtr == null ? rows
    : rows.filter((r) => r.stop_atr === stopAtr);
  const byCell = new Map();
  const totals = { tp: 0, sl: 0, expired: 0, open: 0 };
  let since = null;

  for (const r of mine) {
    if (r.ms && (since === null || r.ms < since)) since = r.ms;
    if (r.outcome === 'expired') totals.expired += 1;
    else if (r.outcome === 'open') totals.open += 1;
    else if (r.outcome === 'sl') totals.sl += 1;
    else if (CLOSED.has(r.outcome)) totals.tp += 1;

    const k = `${r.symbol}|${r.tf}`;
    if (!byCell.has(k)) byCell.set(k, { n: 0, wins: 0, sum: 0, expired: 0, open: 0 });
    const c = byCell.get(k);
    if (r.outcome === 'expired') c.expired += 1;
    else if (r.outcome === 'open') c.open += 1;
    else if (typeof r.net_r === 'number') {
      c.n += 1;
      c.sum += r.net_r;
      if (r.gross_r > 0) c.wins += 1;
    }
  }
  for (const c of byCell.values()) {
    /* `mean` is null below the floor -- not 0, and not a number the caller has
       to remember to guard. The type says "no answer yet". */
    c.mean = c.n >= MIN_LIVE_N ? c.sum / c.n : null;
    c.winPct = c.n ? (100 * c.wins) / c.n : null;
  }
  return { byCell, totals, since, rows: mine };
}
