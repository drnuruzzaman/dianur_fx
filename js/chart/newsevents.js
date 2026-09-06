/* newsevents.js — scheduled US macro releases, for marking on a replay.
 *
 * WHY THIS IS NOT JUST "READ THE CALENDAR". The app's calendar endpoint is
 * `ff_calendar_thisweek.json` (bridge/mt5_bridge.py): ONE WEEK, refetched. The
 * strategy replay walks a decade. There is no historical calendar anywhere in
 * this project, so marking a 2019 CPI print cannot be done by looking it up --
 * it has to come from somewhere else, and this file is explicit about where.
 *
 * TWO SOURCES, DRAWN DIFFERENTLY, AND THE DIFFERENCE IS THE POINT.
 *
 *   DERIVED   NFP, and only NFP. ISM manufacturing and services were derived
 *             here too -- first and third business day, 10:00 New York -- and
 *             were removed by request: three marks a month on every chart is a
 *             picket fence, and the one release people actually read price
 *             against is the payrolls print.
 *
 *   SOURCED   data/calendar/history.json, and it is now the main source. Two
 *             importers write it:
 *
 *               tools/fetch_fred_calendar.py -- CPI, PPI, NFP, unemployment and
 *               GDP, from FRED's own release-date history, back to 2016.
 *               tools/fetch_macro_calendar.py -- FOMC, ECB, BOE and BOJ meeting
 *               dates from a public iCal feed, which is forward-looking and so
 *               reaches back only a few months.
 *
 *             So US data marks are real across a whole replay; central bank
 *             marks are real only near its right edge. FRED publishes DATA
 *             releases, not meeting calendars, which is why the split exists.
 *
 * WHAT THE REAL DATES SHOWED ABOUT THE DERIVED ONES. NFP was generated as the
 * first Friday of the month, and the FRED import proves that rule wrong often
 * enough to matter: only 125 of 134 releases fell on a Friday at all, and in
 * January 2016 the first Friday was the 1st while the release was the 8th. The
 * derived mark therefore survives only where no file covers its month, and it
 * still draws dashed -- a dashed mark says "a release was scheduled near here",
 * a solid one says "this is when it happened".
 *
 * TIMES ARE NEW YORK, CONVERTED EXACTLY. US DST since 2007 is the second
 * Sunday in March to the first Sunday in November, both at 02:00 local. A fixed
 * -5 or -4 offset would put every summer release an hour wrong, which on a 5m
 * chart is twelve bars -- more than enough to attribute a move to the wrong
 * cause.
 */

export const NFP = 'NFP', FOMC = 'FOMC', CPI = 'CPI', GDP = 'GDP', PMI = 'PMI';
export const PPI = 'PPI', UNEMPLOYMENT = 'UNEMPLOYMENT';
export const ECB = 'ECB', BOE = 'BOE', BOJ = 'BOJ';

/** Everything a sourced file may carry, most market-moving first. */
export const KINDS = [FOMC, NFP, CPI, PPI, UNEMPLOYMENT, ECB, BOE, BOJ, GDP, PMI];

/** Where each kind's file lives, and how far it reaches. See loadSourced(). */
export const SOURCE_URL = 'data/calendar/history.json';

/** UTC ms for a given New York wall-clock time, DST included. */
export function nyToUtc(y, m, d, hh, mm) {
  const secondSundayMarch = nthWeekday(y, 2, 0, 2);      // March, Sunday, 2nd
  const firstSundayNov = nthWeekday(y, 10, 0, 1);        // November, Sunday, 1st
  const t = Date.UTC(y, m, d, hh, mm);
  /* DST switches at 02:00 LOCAL; comparing in UTC against 07:00 on those dates
     is the same boundary and avoids a circular conversion. */
  const dstFrom = Date.UTC(y, 2, secondSundayMarch, 7);
  const dstTo = Date.UTC(y, 10, firstSundayNov, 6);
  const naive = Date.UTC(y, m, d, hh, mm);
  const isDst = naive >= dstFrom && naive < dstTo;
  return t + (isDst ? 4 : 5) * 3600000;
}

/** Day-of-month of the `n`th `weekday` (0=Sun) in month `m` (0-based). */
export function nthWeekday(y, m, weekday, n) {
  const first = new Date(Date.UTC(y, m, 1)).getUTCDay();
  return 1 + ((weekday - first + 7) % 7) + (n - 1) * 7;
}

/** Day-of-month of the `n`th business day, skipping weekends only. */
function nthBusinessDay(y, m, n) {
  let d = 1, seen = 0;
  for (; d <= 31; d++) {
    const dt = new Date(Date.UTC(y, m, d));
    if (dt.getUTCMonth() !== m) break;
    const wd = dt.getUTCDay();
    if (wd !== 0 && wd !== 6) seen++;
    if (seen === n) return d;
  }
  return null;
}

/**
 * The derivable releases between two timestamps.
 *
 * `nthBusinessDay` is kept though nothing calls it now: the ISM rules were
 * correct and only the drawing was unwanted, so throwing the arithmetic away
 * would mean rediscovering it if a PMI mark is ever asked for again.
 */
export function derived(fromMs, toMs) {
  const out = [];
  if (!(toMs > fromMs)) return out;
  const start = new Date(fromMs), end = new Date(toMs);
  for (let y = start.getUTCFullYear(); y <= end.getUTCFullYear(); y++) {
    for (let m = 0; m < 12; m++) {
      const push = (day, hh, mm, kind, label) => {
        if (day == null) return;
        const t = nyToUtc(y, m, day, hh, mm);
        if (t >= fromMs && t <= toMs) {
          out.push({ t, kind, label, approx: true, source: 'derived' });
        }
      };
      push(nthWeekday(y, m, 5, 1), 8, 30, NFP, 'NFP');
    }
  }
  return out.sort((a, b) => a.t - b.t);
}

/**
 * Merge a sourced file over the derived schedule.
 *
 * A SOURCED EVENT WINS over a derived one within an hour of it: the file knows
 * the real minute and the rule only knows the day, so keeping both would draw
 * the same release twice a few bars apart.
 *
 * The file is `[{t, kind, label}]` with `t` in UTC ms -- deliberately the
 * plainest shape possible, so importing one from any calendar export is a
 * mapping job rather than a parsing job.
 */
export function merge(derivedEvents, sourced) {
  if (!Array.isArray(sourced) || !sourced.length) return derivedEvents;
  const clean = sourced
    .filter((e) => Number.isFinite(e.t) && KINDS.includes(e.kind))
    .map((e) => ({ ...e, approx: false, source: 'file' }));

  /* A SOURCED MONTH REPLACES THE DERIVED ONE ENTIRELY, and it took real data to
     see why an hour was not enough. The FRED import shows only 125 of 134
     payrolls releases landed on a Friday, and January 2016 is the clearest
     case: the first Friday was the 1st, the release was the 8th. Dropping the
     derived mark only when a sourced one sits within an hour of it would have
     drawn BOTH -- a dashed guess a week early beside the solid truth -- which
     is worse than either alone, because the dashed one looks like a second
     event rather than a mistake.

     Keyed on year-month per kind: within a month, whatever the file says is the
     whole story for that release. */
  const covered = new Set();
  for (const s of clean) {
    const d = new Date(s.t);
    covered.add(`${s.kind}|${d.getUTCFullYear()}-${d.getUTCMonth()}`);
  }
  const kept = derivedEvents.filter((d) => {
    const t = new Date(d.t);
    return !covered.has(`${d.kind}|${t.getUTCFullYear()}-${t.getUTCMonth()}`);
  });
  return kept.concat(clean).sort((a, b) => a.t - b.t);
}

/**
 * The sourced file, fetched once and shared by every surface.
 *
 * ONE FETCH, CACHED AS A PROMISE, because three charts ask for it and a
 * per-surface fetch would pull the same file three times on load. A failure
 * resolves to an empty list rather than rejecting: a missing calendar file
 * should cost the marks it would have drawn, never the chart.
 */
let _sourced = null;
export function loadSourced(url = SOURCE_URL) {
  if (!_sourced) {
    /* `no-store`, because a weekly job rewrites this file and a cached copy
       would keep last week's calendar on screen with nothing saying so. It is
       one small JSON fetched once per page load; there is nothing to save by
       caching it. */
    _sourced = fetch(url, { cache: 'no-store' })
      .then((r) => (r.ok ? r.json() : []))
      .then((v) => (Array.isArray(v) ? v : []))
      .catch(() => []);
  }
  return _sourced;
}

/**
 * Clip to a window. Callers pass the bars they are drawing.
 *
 * WHY THIS IS NEEDED SEPARATELY FROM `upTo`. `derived` already generates only
 * inside its range, but a sourced file covers a decade whatever the chart is
 * showing, and `merge` does not know the window. Without this a nine-month 4h
 * replay carried 131 payrolls marks -- the whole file -- and relied on the
 * renderer's off-screen test to hide them. They never drew, so nothing looked
 * wrong, but every repaint walked hundreds of events that could not appear and
 * `newsMarks.length` reported a number that meant nothing.
 */
export function within(events, fromMs, toMs) {
  return events.filter((e) => e.t >= fromMs && e.t <= toMs);
}

/** Everything at or before `asOfMs` -- the replay may not see tomorrow's print. */
export function upTo(events, asOfMs) {
  return events.filter((e) => e.t <= asOfMs);
}

/* ------------------------------------------------------------ sentiment ---
 *
 * A DIRECTION FROM `actual` AGAINST `previous`, because there is nothing
 * better to compare against. Checked on 2026-09-06, every calendar reachable
 * from this project returns an EMPTY forecast column -- xoomar (both the CSV
 * and the JSON view), QuantGist (2% coverage, and its sentiment endpoints
 * answer 402), Finnhub (403, no access), and FRED, which publishes
 * observations and has no consensus field at all. So "beat expectations"
 * cannot be computed here. "Beat last month" can.
 *
 * THE TWO ARE NOT THE SAME THING and the difference is the whole subject: a
 * strong print into a market already positioned for a stronger one sells off.
 * This is the weaker reading, and it is labelled as such wherever it is shown.
 *
 * MEASURED, AND IT DID NOT PREDICT. tools/release_surprise_eval.py runs this
 * exact sign against EURUSD and XAUUSD at 5, 15 and 60 minutes, both eras,
 * against a 2000-draw permutation null: not one cell clears p=0.05 in both
 * eras. It is shown because it summarises the print, not because it forecasts
 * the next hour, and the tooltip says so.
 */

/* kind -> does a HIGHER number favour the release's own currency?
   More jobs and faster growth do. A higher jobless rate does not. Hotter
   inflation is treated as currency-positive on the tightening read, which is
   an assumption about the regime rather than an arithmetic fact. */
const HIGHER_IS_STRONG = {
  [NFP]: true, [GDP]: true, [CPI]: true, [PPI]: true, [PMI]: true,
  [UNEMPLOYMENT]: false,
  [FOMC]: true, [ECB]: true, [BOE]: true, [BOJ]: true,
};

const toNum = (v) => {
  if (v === null || v === undefined || v === '') return NaN;
  const f = parseFloat(String(v).replace(/[, ]/g, '').replace('%', ''));
  return Number.isFinite(f) ? f : NaN;
};

/**
 * `{ dir, word, actual, previous, delta }`, or null when it cannot be said.
 *
 * `dir` is +1 when the print favours the currency, -1 against it, 0 when the
 * two are equal. Null -- not zero -- when either number is missing, so a
 * caller can tell "unchanged" from "unknown"; a release with no actual yet is
 * upcoming, and calling that neutral would be a claim about a number nobody
 * has.
 */
export function sentimentOf(ev) {
  if (!ev) return null;
  const a = toNum(ev.actual), p = toNum(ev.previous);
  if (!Number.isFinite(a) || !Number.isFinite(p)) return null;
  const strongUp = HIGHER_IS_STRONG[ev.kind];
  if (strongUp === undefined) return null;
  const delta = a - p;
  const dir = delta === 0 ? 0 : ((delta > 0) === strongUp ? 1 : -1);
  return {
    dir, actual: a, previous: p, delta,
    word: dir === 0 ? 'in line with last' : (dir > 0 ? 'positive' : 'negative'),
  };
}
