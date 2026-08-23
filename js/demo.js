/* demo.js — deterministic synthetic feed used ONLY when the bridge is offline.
 *
 * This exists so the UI can be developed and demonstrated without a MetaTrader
 * terminal. It is fabricated data: nothing here is a price, an account or a
 * position. The header shows a DEMO badge whenever it is in play.
 */

const SPEC = {
  EURUSD: { digits: 5, px: 1.0865, vol: 0.00055 },
  GBPUSD: { digits: 5, px: 1.2712, vol: 0.00070 },
  USDJPY: { digits: 3, px: 149.42, vol: 0.085 },
  AUDUSD: { digits: 5, px: 0.6588, vol: 0.00048 },
  USDCAD: { digits: 5, px: 1.3585, vol: 0.00050 },
  NZDUSD: { digits: 5, px: 0.6082, vol: 0.00045 },
  USDCHF: { digits: 5, px: 0.8825, vol: 0.00046 },
  EURJPY: { digits: 3, px: 162.35, vol: 0.095 },
  GBPJPY: { digits: 3, px: 189.90, vol: 0.130 },
  XAUUSD: { digits: 2, px: 2385.40, vol: 2.35 },
  XAGUSD: { digits: 3, px: 28.145, vol: 0.045 },
  US30:   { digits: 1, px: 39420.0, vol: 28.0 },
  NAS100: { digits: 1, px: 18240.0, vol: 22.0 },
  BTCUSD: { digits: 2, px: 64210.0, vol: 180.0 },
};

const TF_MS = {
  '1m': 60e3, '5m': 300e3, '15m': 900e3, '30m': 1800e3,
  '1h': 3600e3, '4h': 14400e3, '1d': 86400e3, '1w': 604800e3,
};

/* mulberry32 — a small deterministic PRNG so a symbol's history is stable
   across reloads instead of jumping around on every render. */
function rng(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6D2B79F5) >>> 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
const seedOf = (s) => [...String(s)].reduce((h, c) => (h * 31 + c.charCodeAt(0)) | 0, 7);

function gauss(rand) {
  let u = 0, v = 0;
  while (u === 0) u = rand();
  while (v === 0) v = rand();
  return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
}

const round = (v, d) => Number(v.toFixed(d));
const spec = (name) => SPEC[name] || { digits: 5, px: 1.1, vol: 0.0005 };

/* A price must be a function of ABSOLUTE TIME, not of a per-series random walk:
   otherwise M15 bars, H1 bars and the live quote each wander off to their own
   number and the chart shows a cliff where the quote meets the history. Summed
   sines at trading-relevant periods give a continuous, seeded, reproducible
   curve that every timeframe agrees on. */
const WAVES = [[1440, 1.0], [360, 0.55], [90, 0.3], [21, 0.16], [6, 0.08]];

function priceAt(name, t) {
  const s = spec(name);
  const h = Math.abs(seedOf(name));
  const mins = t / 60000;
  let x = 0;
  for (const [period, amp] of WAVES) {
    const phase = ((h % 211) + period) * 0.37;
    x += amp * Math.sin((mins / period) * 2 * Math.PI + phase);
  }
  return s.px * (1 + x * 0.0032);
}

/** Deterministic 0..1 hash of (symbol, bar time, salt). */
function hash01(name, t, salt) {
  let a = (seedOf(name) ^ Math.imul(t & 0x7fffffff, 2654435761) ^ Math.imul(salt, 40503)) >>> 0;
  a = Math.imul(a ^ (a >>> 15), 2246822507);
  a = Math.imul(a ^ (a >>> 13), 3266489909);
  return ((a ^ (a >>> 16)) >>> 0) / 4294967296;
}

/** OHLC sampled from priceAt, with per-bar noise for wicks and volume. */
function series(name, tf, count) {
  const s = spec(name);
  const step = TF_MS[tf] || 60e3;
  const now = Math.floor(Date.now() / step) * step;
  const scale = Math.sqrt(step / 60e3);
  const bars = [];
  for (let i = count - 1; i >= 0; i--) {
    const t = now - i * step;
    const noise = (k) => (hash01(name, t, k) - 0.5) * 2 * s.vol * scale * 0.6;
    const o = priceAt(name, t) + noise(1);
    const c = priceAt(name, t + step) + noise(2);
    const wick = (0.15 + hash01(name, t, 3) * 0.85) * s.vol * scale;
    bars.push({
      t,
      o: round(o, s.digits),
      h: round(Math.max(o, c) + wick, s.digits),
      l: round(Math.min(o, c) - wick * (0.4 + hash01(name, t, 4)), s.digits),
      c: round(c, s.digits),
      v: Math.round(400 + hash01(name, t, 5) * 2600),
      ticks: Math.round(120 + hash01(name, t, 6) * 900),
    });
  }
  // the forming bar should close at "now", not at the end of its slot
  if (bars.length) {
    const last = bars[bars.length - 1];
    last.c = round(priceAt(name, Date.now()), s.digits);
    last.h = round(Math.max(last.h, last.c), s.digits);
    last.l = round(Math.min(last.l, last.c), s.digits);
  }
  return bars;
}

const lastPx = (name) => round(priceAt(name, Date.now()), spec(name).digits);

export const DEMO = {
  isDemo: true,
  bars(name, tf, count = 1200) {
    const bars = series(name, tf, Math.max(30, Math.min(count, 5000)));
    return {
      symbol: name, digits: spec(name).digits, count: bars.length,
      span_days: bars.length ? (bars[bars.length - 1].t - bars[0].t) / 864e5 : 0,
      bars, demo: true,
    };
  },
  quotes(names) {
    const out = {};
    for (const n of names) {
      const s = spec(n);
      const mid = lastPx(n);
      const half = s.vol * 0.12;
      out[n] = {
        symbol: n, bid: round(mid - half, s.digits), ask: round(mid + half, s.digits),
        last: round(mid, s.digits), digits: s.digits, point: 10 ** -s.digits,
        time_ms: Date.now(), demo: true,
      };
    }
    return out;
  },
  ticks(name, fromMs, limit = 400) {
    const s = spec(name);
    const now = Date.now();
    const from = fromMs || now - 20000;
    const rand = rng(seedOf(name) ^ Math.floor(now / 1000));
    const ticks = [];
    const n = Math.min(limit, 26);
    let px = lastPx(name);
    for (let i = n; i > 0; i--) {
      const ts = now - i * 380;
      if (ts <= from) continue;
      px += gauss(rand) * s.vol * 0.06;
      const half = s.vol * 0.12;
      ticks.push({
        ts, price: round(px, s.digits), bid: round(px - half, s.digits),
        ask: round(px + half, s.digits), size: Math.round(1 + rand() * 8),
        side: rand() > 0.5 ? 'buy' : 'sell',
      });
    }
    return { symbol: name, digits: s.digits, point: 10 ** -s.digits, ticks, demo: true };
  },
  symbols() {
    return Object.keys(SPEC).map((n) => ({
      name: n, digits: SPEC[n].digits, visible: true,
      path: (n.startsWith('XA') ? 'Metals\\' : /USD|JPY|CHF|CAD/.test(n.slice(3)) ? 'Forex\\' : 'Indices\\') + n,
    }));
  },
  spec(name) {
    const s = spec(name);
    return {
      symbol: name, digits: s.digits, point: 10 ** -s.digits, tick_size: 10 ** -s.digits,
      tick_value: 1, contract_size: name.startsWith('XAU') ? 100 : 100000,
      volume_min: 0.01, volume_max: 100, volume_step: 0.01,
      spread_current: s.vol * 0.24, margin_per_lot: 3500, currency_profit: 'AUD',
      demo: true,
    };
  },
  account() {
    return {
      login: 0, server: 'DEMO-FEED', name: 'Synthetic', currency: 'AUD', leverage: 30,
      balance: 25000, equity: 25184.35, profit: 184.35, margin: 1210,
      margin_free: 23974.35, margin_level: 2081.3, credit: 0,
      trade_allowed: false, demo: true,
    };
  },
  positions() {
    const mk = (t, sym, side, vol, open) => {
      const q = DEMO.quotes([sym])[sym];
      const cur = side === 'buy' ? q.bid : q.ask;
      const sign = side === 'buy' ? 1 : -1;
      const s = spec(sym);
      const mult = sym.startsWith('XAU') ? 100 : (s.digits <= 3 ? 1000 : 100000);
      return {
        ticket: t, symbol: sym, side, volume: vol, price_open: open, price_current: cur,
        sl: 0, tp: 0, swap: -0.42, comment: 'demo',
        profit: round(sign * (cur - open) * vol * mult / (s.digits <= 3 ? 10 : 1), 2),
        time_ms: Date.now() - 3600e3 * (t % 7 + 1),
      };
    };
    return [
      mk(50011, 'EURUSD', 'buy', 0.50, 1.08420),
      mk(50014, 'XAUUSD', 'sell', 0.10, 2391.80),
      mk(50019, 'USDJPY', 'buy', 0.20, 149.120),
    ];
  },
  orders() {
    return [{
      ticket: 60021, symbol: 'GBPUSD', type: 'buy limit', volume: 0.30,
      price_open: 1.26400, sl: 1.25900, tp: 1.27600, comment: 'demo',
      time_ms: Date.now() - 7200e3,
    }];
  },
  deals(days = 7) {
    const rand = rng(4711);
    const out = [];
    const syms = ['EURUSD', 'XAUUSD', 'USDJPY', 'GBPUSD', 'NAS100'];
    for (let i = 0; i < 22; i++) {
      const sym = syms[Math.floor(rand() * syms.length)];
      out.push({
        ticket: 70000 + i, symbol: sym, side: rand() > 0.5 ? 'buy' : 'sell',
        volume: round(0.1 + rand() * 0.9, 2), price: lastPx(sym),
        profit: round(gauss(rand) * 90, 2), commission: -1.2, swap: 0,
        comment: 'demo', time_ms: Date.now() - rand() * days * 864e5,
      });
    }
    return out.sort((a, b) => b.time_ms - a.time_ms);
  },
  calendar() {
    const now = Date.now();
    // same shape the bridge returns: ts + title + impact
    return [
      { ts: now + 45 * 60e3, currency: 'USD', impact: 'high', title: 'Core CPI m/m', forecast: '0.3%', previous: '0.4%' },
      { ts: now + 3 * 3600e3, currency: 'AUD', impact: 'medium', title: 'RBA Minutes', forecast: '', previous: '' },
      { ts: now + 6 * 3600e3, currency: 'EUR', impact: 'low', title: 'Trade Balance', forecast: '18.2B', previous: '17.9B' },
    ];
  },
};
