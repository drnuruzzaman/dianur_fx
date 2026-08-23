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

export const hhmm = (ms) => { const d = new Date(ms); return pad(d.getHours()) + ':' + pad(d.getMinutes()); };
export const hhmmss = (ms) => { const d = new Date(ms); return hhmm(ms) + ':' + pad(d.getSeconds()); };
const DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
export const dmy = (ms) => {
  const d = new Date(ms);
  return pad(d.getDate()) + ' ' + d.toLocaleString('en-AU', { month: 'short' });
};
export const stamp = (ms) => dmy(ms) + ' ' + hhmm(ms);

/** Axis label appropriate to the timeframe. */
export function axisTime(ms, tf) {
  const d = new Date(ms);
  if (tf === '1w' || tf === '1d') {
    return d.getDate() === 1 || tf === '1w'
      ? d.toLocaleString('en-AU', { month: 'short', year: '2-digit' })
      : pad(d.getDate()) + ' ' + d.toLocaleString('en-AU', { month: 'short' });
  }
  if (tf === '4h' || tf === '1h') {
    return d.getHours() === 0 ? dmy(ms) : hhmm(ms);
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
export const save = (key, value) => {
  try { localStorage.setItem(NS + key, JSON.stringify(value)); } catch { /* quota / private mode */ }
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
 * A session's open (or close) hour on the viewer's own clock.
 *
 * The table above is in UTC because that is the only frame in which the four
 * windows keep fixed hours -- but "London opens at 07:00 UTC" is a sum the
 * reader should not have to do at 5am. Anchored to today's date so it picks up
 * the local DST rule in force right now.
 */
export function sessionLocal(hourUtc, now = new Date()) {
  const d = new Date(now);
  d.setUTCHours(hourUtc, 0, 0, 0);
  return hhmm(d.getTime());
}

/* ---- calendar clocks -----------------------------------------------------
 * Three clocks are in play and they are hours apart: the browser's LOCAL time,
 * UTC, and the BROKER's server time (+3h), which is what every chart candle and
 * every downloaded bar is stamped in. A news event and a candle both reading
 * 08:30 can be seven hours apart, so calendar times are rendered through an
 * explicit, labelled zone rather than through "whatever Date happens to do".
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
