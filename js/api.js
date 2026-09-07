/* api.js — thin client for the read-only MT5 bridge (bridge/mt5_bridge.py).
 *
 * Every call goes to 127.0.0.1. The bridge exposes no write endpoints, so
 * nothing here can move money or touch a position — by construction, not by
 * convention. When the bridge is not running the page falls back to a clearly
 * labelled synthetic feed so the UI is still explorable.
 */

import { DEMO } from './demo.js';

const LS_BASE = 'dnfx.bridge';
export const base = () => localStorage.getItem(LS_BASE) || 'http://127.0.0.1:8765';
export const setBase = (url) => localStorage.setItem(LS_BASE, url.replace(/\/$/, ''));

export const status = {
  mode: 'connecting',   // 'live' | 'mock' | 'demo' | 'down' | 'connecting'
  health: null,
  error: null,
};

const qs = (params) => {
  const u = new URLSearchParams();
  for (const [k, v] of Object.entries(params || {})) {
    if (v !== undefined && v !== null && v !== '') u.set(k, v);
  }
  const s = u.toString();
  return s ? '?' + s : '';
};

async function get(path, params, { timeout = 12000 } = {}) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeout);
  try {
    const res = await fetch(base() + path + qs(params), {
      signal: ctrl.signal, cache: 'no-store', mode: 'cors',
    });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(body.error || `${path} → HTTP ${res.status}`);
    return body;
  } finally {
    clearTimeout(timer);
  }
}

/* Wrap every data call: if the bridge is unreachable, serve the demo feed
   instead of throwing the whole UI away. */
function guarded(name, fn, demoFn) {
  return async (...args) => {
    if (status.mode === 'demo') return demoFn(...args);
    try {
      const out = await fn(...args);
      status.error = null;
      return out;
    } catch (err) {
      if (status.mode === 'live' || status.mode === 'mock') {
        status.error = String(err.message || err);
      }
      if (demoFn && status.mode !== 'live' && status.mode !== 'mock') {
        return demoFn(...args);
      }
      throw err;
    }
  };
}

export const api = {
  /** Probe the bridge and set `status.mode`. Safe to call on a timer. */
  async health() {
    try {
      const h = await get('/health', null, { timeout: 4000 });
      status.health = h;
      status.mode = h.mock ? 'mock' : (h.connected ? 'live' : 'down');
      status.error = h.error || null;
      return h;
    } catch (err) {
      status.health = null;
      status.mode = 'demo';
      status.error = 'bridge offline — showing synthetic data (' + (err.message || err) + ')';
      return { ok: false, demo: true, error: status.error };
    }
  },

  account:   guarded('account', () => get('/account'), () => DEMO.account()),
  positions: guarded('positions', async () => (await get('/positions')).positions || [], () => DEMO.positions()),
  orders:    guarded('orders', async () => (await get('/orders')).orders || [], () => DEMO.orders()),
  deals:     guarded('deals', async (days = 7) => (await get('/deals', { days })).deals || [], (d) => DEMO.deals(d)),
  symbols:   guarded('symbols', async () => (await get('/symbols')).symbols || [], () => DEMO.symbols()),
  spec:      guarded('spec', (symbol) => get('/spec', { symbol }), (s) => DEMO.spec(s)),
  /* The rule's own statement, computed in PYTHON. The lot size is the reason:
     sizing needs an FX rate, and the browser's rate and the backtest's differ
     by enough to move a lot step (see the /signal comment in the bridge). No
     demo fallback -- a fabricated trading instruction is worse than none, so
     without a bridge this simply reports unavailable. */
  signalNow: (symbol, tf, opts = {}) =>
    get('/signal', { symbol, tf, ...opts }),
  quotes:    guarded('quotes', async (symbols) => (await get('/quotes', { symbols: symbols.join(',') })).quotes || {},
                     (s) => DEMO.quotes(s)),
  ticks:     guarded('ticks', (symbol, from_ms, limit = 500) => get('/ticks', { symbol, from_ms, limit }),
                     (s, f, l) => DEMO.ticks(s, f, l)),
  /* Bars get a long timeout on purpose: MetaTrader only holds history it has
     already downloaded, so the FIRST deep request for a symbol/timeframe can
     block for a minute while the terminal fetches it from the broker. Measured
     on a live Pepperstone feed: 38 years of weeklies took >60s cold, 312ms
     once primed. */
  bars:      guarded('bars', (symbol, tf, count = 1200, months = 0) =>
                       get('/bars', { symbol, tf, count, months }, { timeout: 90000 }),
                     (s, tf, c) => DEMO.bars(s, tf, c)),
  calendar:  guarded('calendar', async () => (await get('/calendar')).events || [], () => DEMO.calendar()),
  cot:       guarded('cot', (symbol) => get('/cot', { symbol }), () => ({ available: false })),
};
