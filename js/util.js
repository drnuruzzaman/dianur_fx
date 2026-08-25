/* util.js — formatting, DOM and persistence helpers. */

export const TF = ['1m', '5m', '15m', '30m', '1h', '4h', '1d', '1w'];
export const TF_MS = {
  '1m': 60e3, '5m': 300e3, '15m': 900e3, '30m': 1800e3,
  '1h': 3600e3, '4h': 14400e3, '1d': 86400e3, '1w': 604800e3,
};
export const TF_LABEL = {
  '1m': 'M1', '5m': 'M5', '15m': 'M15', '30m': 'M30',
  '1h': 'H1', '4h': 'H4', '1d': 'D1', '1w': 'W1',
};

export const clamp = (v, lo, hi) => (v < lo ? lo : v > hi ? hi : v);

export function px(v, digits = 5) {
  if (v === null || v === undefined || Number.isNaN(v)) return '—';
  return Number(v).toFixed(digits);
}

export function num(v, d = 2) {
  if (v === null || v === undefined || Number.isNaN(v)) return '—';
  return Number(v).toLocaleString('en-AU', { minimumFractionDigits: d, maximumFractionDigits: d });
}

export function money(v, ccy = '', d = 2) {
  if (v === null || v === undefined || Number.isNaN(v)) return '—';
  const s = num(Math.abs(v), d);
  return (v < 0 ? '-' : '') + (ccy ? ccy + ' ' : '') + s;
}

export function signed(v, d = 2) {
  if (v === null || v === undefined || Number.isNaN(v)) return '—';
  return (v > 0 ? '+' : v < 0 ? '-' : '') + num(Math.abs(v), d);
}

/** Compact volume for the axis: 1.2k, 3.4M. */
export function compact(v) {
  const a = Math.abs(v);
  if (a >= 1e9) return (v / 1e9).toFixed(1) + 'B';
  if (a >= 1e6) return (v / 1e6).toFixed(1) + 'M';
  if (a >= 1e3) return (v / 1e3).toFixed(1) + 'k';
  return String(Math.round(v));
}

const pad = (n) => String(n).padStart(2, '0');
const DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

/* ---- the display clock ---------------------------------------------------
 *
 * ONE zone for every rendered timestamp in the app. Formatters below shift the
 * epoch by the zone offset and then read it with getUTC*, so "which zone" is a
 * single number applied in a single place rather than an implicit property of
 * whatever machine the browser is on.
 *
 * BROKER SERVER TIME is the default, because it is the frame the instrument
 * itself is quoted in: an MT5 daily candle opens and closes on the broker's
 * midnight, not on UTC midnight, so a chart drawn in any other zone shows
 * daily bars starting at an arbitrary-looking hour. Matching the broker means
 * a bar boundary on screen is a real bar boundary.
 *
 * The cost, stated because it is real: broker time moves with the BROKER's
 * DST, so the four session windows in SESSIONS -- which keep fixed hours only
 * in UTC -- shift by an hour twice a year against the displayed clock. The
 * session strip therefore names its zone rather than assuming one.
 *
 * Two surfaces deliberately opt out. The CALENDAR keeps its own selector,
 * because a news time is published in UTC and reading it against your own
 * clock is the point. A SNAPSHOT renders in UTC, because an exported image
 * outlives the session that made it and "14:00" means nothing to whoever
 * opens the file unless the frame is universal.
 *
 * This governs DISPLAY only. Stored research bars stay in broker server time
 * (tools/dataset.py, sim/tl/clockguard.py) because converting them needs a
 * time-varying offset and one constant is wrong for half of a 27-year history.
 */
const ZONE = { mode: 'broker', brokerOffsetMs: 0 };

export function setZone(mode, brokerOffsetMs = ZONE.brokerOffsetMs) {
  if (mode && TZ_MODES.includes(mode)) ZONE.mode = mode;
  ZONE.brokerOffsetMs = brokerOffsetMs || 0;
}
export const getZone = () => ({ ...ZONE });
/** Run `fn` with the display zone forced to `mode`, then restore it. */
export function withZone(mode, fn) {
  const prev = ZONE.mode;
  ZONE.mode = TZ_MODES.includes(mode) ? mode : prev;
  try { return fn(); } finally { ZONE.mode = prev; }
}
/** Label for the zone every formatter here is currently rendering in. */
export const zoneLabel = () => tzLabel(ZONE.mode, ZONE.brokerOffsetMs);
/** Epoch shifted into the display zone; read the result with getUTC* only. */
const shifted = (ms) =>
  new Date(ms + tzOffsetMin(ZONE.mode, ZONE.brokerOffsetMs) * 60000);

export const hhmm = (ms) => {
  const d = shifted(ms);
  return pad(d.getUTCHours()) + ':' + pad(d.getUTCMinutes());
};
export const hhmmss = (ms) =>
  hhmm(ms) + ':' + pad(shifted(ms).getUTCSeconds());
export const dmy = (ms) => {
  const d = shifted(ms);
  return pad(d.getUTCDate()) + ' ' + MONTHS[d.getUTCMonth()];
};
export const stamp = (ms) => dmy(ms) + ' ' + hhmm(ms);

/** Axis label appropriate to the timeframe. */
export function axisTime(ms, tf) {
  const d = shifted(ms);
  if (tf === '1w' || tf === '1d') {
    return d.getUTCDate() === 1 || tf === '1w'
      ? MONTHS[d.getUTCMonth()] + ' ' + String(d.getUTCFullYear()).slice(2)
      : pad(d.getUTCDate()) + ' ' + MONTHS[d.getUTCMonth()];
  }
  if (tf === '4h' || tf === '1h') {
    return d.getUTCHours() === 0 ? dmy(ms) : hhmm(ms);
  }
  return hhmm(ms);
}

/** Decimal places to show, inferred from the price when digits are unknown. */
export function inferDigits(price) {
  const a = Math.abs(price || 0);
  if (a >= 10000) return 1;
  if (a >= 1000) return 2;
  if (a >= 100) return 3;
  if (a >= 10) return 3;
  return 5;
}

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

export function el(tag, attrs = {}, ...kids) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === 'class') node.className = v;
    else if (k === 'text') node.textContent = v;
    else if (k === 'html') node.innerHTML = v;
    else if (k.startsWith('on')) node.addEventListener(k.slice(2), v);
    else if (k === 'dataset') Object.assign(node.dataset, v);
    else node.setAttribute(k, v);
  }
  for (const kid of kids.flat()) {
    if (kid === null || kid === undefined || kid === false) continue;
    node.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  }
  return node;
}

export function debounce(fn, ms = 180) {
  let t;
  return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}

/* ---- localStorage with a namespace and safe fallbacks ---- */
const NS = 'dnfx.';
/* THE WORKSPACE FILE.
 *
 * localStorage is scoped to a browser profile: clear the site data, switch
 * browser, or open the app from another host and every setting is gone. The
 * workspace belongs to the PROJECT, so it is mirrored to `data/workspace.json`
 * through the dev server.
 *
 * localStorage stays the working store -- every `load()` in the app reads it
 * synchronously and none of them had to change. The file is a DURABLE COPY:
 * written after each change, read once at boot to repopulate an empty or
 * out-of-date browser.
 *
 * Debounced, because a single drag can fire dozens of saves and the file only
 * needs the settled result.
 */
let flushTimer = null;
/* Nothing is written to the file until the file has been READ. A save that
   fires during module init would otherwise flush a pre-hydration snapshot over
   the durable copy -- the same shape as the priceLock bug, where the app
   erased the value it was about to ask for. */
let hydrated = false;
/* Keys removed since the last flush. The file only shrinks when a client says
   so explicitly -- see the merge in serve.py. */
const pendingDeletes = new Set();

function snapshot() {
  const out = {};
  try {
    for (const k of Object.keys(localStorage)) {
      if (k.startsWith(NS)) out[k.slice(NS.length)] = localStorage.getItem(k);
    }
  } catch { /* private mode */ }
  return out;
}

function scheduleFlush() {
  if (!hydrated) return;
  clearTimeout(flushTimer);
  flushTimer = setTimeout(() => {
    const set = snapshot();
    const del = [...pendingDeletes];
    pendingDeletes.clear();
    if (!Object.keys(set).length && !del.length) return;
    try {
      fetch('/workspace', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ set, del }),
      }).catch(() => { /* no server: localStorage still holds everything */ });
    } catch { /* ditto */ }
  }, 600);
}

/**
 * Load the project's workspace file into localStorage, once, before boot.
 *
 * LOCALSTORAGE WINS where it already has the key. It is the working store and
 * therefore never STALER than the file -- the file trails it by the 600ms
 * debounce and nothing else. The file's job is to fill what the browser is
 * missing: a fresh profile, cleared site data, a different machine.
 *
 * Taking the file first would be wrong in the one case that matters. A browser
 * mid-session holds changes the file has not received yet, and overwriting them
 * on reload would undo the last thing you did.
 *
 * Keys the file does not mention are left alone rather than deleted -- a
 * half-written file should not erase settings it never knew about.
 *
 * Returns the number of keys adopted FROM THE FILE, or -1 when there is no
 * server.
 */
export async function hydrateWorkspace() {
  try {
    hydrated = false;
    const res = await fetch('/workspace', { cache: 'no-store' });
    if (!res.ok) { hydrated = true; return -1; }
    const data = await res.json();
    if (!data || typeof data !== 'object') { hydrated = true; return 0; }
    let n = 0;
    for (const [k, v] of Object.entries(data)) {
      if (typeof v !== 'string') continue;
      if (localStorage.getItem(NS + k) !== null) continue;   // browser wins
      localStorage.setItem(NS + k, v);
      n += 1;
    }
    hydrated = true;
    return n;
  } catch { hydrated = true; return -1; }
}

export const save = (key, value) => {
  try { localStorage.setItem(NS + key, JSON.stringify(value)); } catch { /* quota / private mode */ }
  scheduleFlush();
};
/** Delete one namespaced key. Callers must not touch localStorage directly:
    every key here is prefixed, and a raw removeItem silently does nothing. */
export const drop = (key) => {
  try { localStorage.removeItem(NS + key); } catch { /* private mode */ }
  pendingDeletes.add(key);
  scheduleFlush();
};

export const load = (key, fallback = null) => {
  try {
    const raw = localStorage.getItem(NS + key);
    return raw === null ? fallback : JSON.parse(raw);
  } catch { return fallback; }
};

/** FX session windows in UTC hours, used by the session strip. */
export const SESSIONS = [
  { name: 'Sydney', open: 21, close: 6 },
  { name: 'Tokyo', open: 0, close: 9 },
  { name: 'London', open: 7, close: 16 },
  { name: 'New York', open: 12, close: 21 },
];

export function sessionOpen(s, now = new Date()) {
  const h = now.getUTCHours() + now.getUTCMinutes() / 60;
  return s.open < s.close ? h >= s.open && h < s.close : h >= s.open || h < s.close;
}

/**
 * A session's open (or close) hour rendered in the DISPLAY zone.
 *
 * The table above is in UTC because that is the only frame in which the four
 * windows keep fixed hours. This renders one of those hours through whatever
 * zone the app is set to, so in the default UTC mode it is a pass-through and
 * in `local` mode it answers "what time is that at my desk" without the reader
 * doing the sum at 5am. Anchored to today's date so a local rendering picks up
 * the DST rule in force right now.
 */
export function sessionClock(hourUtc, now = new Date()) {
  const d = new Date(now);
  d.setUTCHours(hourUtc, 0, 0, 0);
  return hhmm(d.getTime());
}

/* ---- zone plumbing -------------------------------------------------------
 * Three clocks exist and they are hours apart: the browser's LOCAL time, UTC,
 * and the BROKER's server time (+3h at the time of writing).
 *
 * WHICH IS WHICH, because this comment previously got it wrong and a wrong
 * comment here costs someone a three-hour correction they did not need:
 *
 *   live bars over the bridge   TRUE UTC. mt5_bridge.py subtracts
 *                               time_offset_ms before sending, so `t` on a
 *                               bar and `time_ms` on a position are both UTC.
 *   stored research bars        BROKER SERVER TIME, tz-naive, index named
 *                               `server_time` (tools/dataset.py). NOT UTC,
 *                               and deliberately not converted -- see
 *                               sim/tl/clockguard.py for why one constant
 *                               offset cannot be right across 27 years.
 *   calendar events             TRUE UTC from the provider.
 *
 * So everything the browser touches is already UTC; the zone below is a
 * DISPLAY choice applied at the last moment, never a change of storage.
 */
export const TZ_MODES = ['local', 'utc', 'broker'];

/** Minutes to add to UTC for the chosen mode. */
export function tzOffsetMin(mode, brokerOffsetMs = 0) {
  if (mode === 'utc') return 0;
  if (mode === 'broker') return Math.round(brokerOffsetMs / 60000);
  return -new Date().getTimezoneOffset();          // east of UTC is positive
}

export function tzLabel(mode, brokerOffsetMs = 0) {
  const mins = tzOffsetMin(mode, brokerOffsetMs);
  const h = Math.floor(Math.abs(mins) / 60);
  const m = Math.abs(mins) % 60;
  const off = `UTC${mins < 0 ? '-' : '+'}${h}${m ? ':' + pad(m) : ''}`;
  return mode === 'local' ? `local ${off}` : mode === 'broker' ? `broker ${off}` : 'UTC';
}

/**
 * Format an epoch ms in the chosen zone, e.g. "Fri 22 Aug 18:30".
 *
 * The weekday earns its place: a calendar spanning a whole trading week is read
 * by which session an event lands in, and "Fri" answers that where "22" does
 * not. It shifts with the zone too -- a Monday 08:00 Tokyo release is still
 * Sunday evening in New York.
 */
export function stampTz(ms, mode, brokerOffsetMs = 0) {
  if (!ms) return '\u2014';
  const d = new Date(ms + tzOffsetMin(mode, brokerOffsetMs) * 60000);
  return `${DAYS[d.getUTCDay()]} ${pad(d.getUTCDate())} ${MONTHS[d.getUTCMonth()]} ` +
         `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`;
}

/** "in 45m" / "2h 10m ago" / "now", so the list needs no mental arithmetic. */
export function relTime(ms, now = Date.now()) {
  if (!ms) return '';
  const diff = ms - now;
  if (Math.abs(diff) < 60000) return 'now';
  const mins = Math.round(Math.abs(diff) / 60000);
  const txt = mins < 60 ? `${mins}m`
    : mins < 1440 ? `${Math.floor(mins / 60)}h${mins % 60 ? ' ' + (mins % 60) + 'm' : ''}`
    : `${Math.round(mins / 1440)}d`;
  return diff > 0 ? `in ${txt}` : `${txt} ago`;
}
