/* journal.js — the POSTED tickets, read back so a scored row can show prices.
 *
 * WHY THIS EXISTS. data/scalper_scored.jsonl records what HAPPENED -- outcome,
 * gross, cost, swap, net -- and deliberately not what was proposed: the scorer
 * already had the ticket in front of it and writing the prices twice would let
 * the two files disagree about the same order. That is the right call for the
 * ledger and it leaves the Signal Board's results list unable to say where the
 * entry was. The join is the fix: every scored row carries the `id` of the
 * journal row it came from, one to one, so the prices are one lookup away and
 * still have exactly one home.
 *
 * READ LAZILY, ON PURPOSE. The journal is the biggest file the page can ask
 * for -- 2,000 tickets and about a megabyte today, growing by a few hundred a
 * day and never truncated -- against 74 KB for the scored ledger. The board
 * paints without it, so it is fetched the first time someone actually opens the
 * results list and never on load. A panel nobody expands costs nothing.
 *
 * IF IT GROWS PAST COMFORT the answer is a server-side join, not a bigger
 * fetch: tools/score_scalper.py has the ticket in hand and could carry the four
 * prices into each scored row at write time. That would be a schema change and
 * a backfill of every row already written, which is why it is a note here
 * rather than a change today.
 *
 * IDS ARE UNIQUE AND COMPLETE, verified before this shipped: 2,039 journal ids
 * with no duplicate, and not one scored row without its journal row. A missing
 * id is still handled -- the caller gets `undefined` and prints a dash -- but it
 * is a fault, not an expected case.
 */

const LEDGER = 'data/scalper_journal.jsonl';

let _journal = null;

/**
 * `Map` of ticket id -> the posted ticket, fetched once per page.
 *
 * A MISSING OR UNREADABLE JOURNAL IS AN EMPTY MAP, not an error, for the same
 * reason scored.js tolerates a missing ledger: the list it feeds is a
 * convenience on top of counts that are already correct, and a results table
 * that refused to open because one file was absent would be a worse failure
 * than one showing dashes in three columns.
 */
export function loadJournal(url = LEDGER) {
  if (!_journal) {
    _journal = fetch(url, { cache: 'no-store' })
      .then((r) => (r.ok ? r.text() : ''))
      .then((txt) => {
        const m = new Map();
        for (const line of txt.split('\n')) {
          const s = line.trim();
          if (!s) continue;
          try {
            const t = JSON.parse(s);
            if (t && t.id) m.set(t.id, t);
          } catch (e) { /* one bad line must not lose the other two thousand */ }
        }
        return m;
      })
      .catch(() => new Map());
  }
  return _journal;
}
