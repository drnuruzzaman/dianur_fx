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
    _sourced = fetch(url)
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
