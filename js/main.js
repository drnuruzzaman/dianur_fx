/* main.js — DiaNurFx workspace.
 *
 * Owns the chart grid, the polling loops against the read-only MT5 bridge, and
 * the toolbar. Everything the user arranges (layout, symbols, timeframes,
 * studies, drawings, watchlist) is persisted to localStorage so a reload comes
 * back to the same desk.
 */

import { api, status, setBase, base } from './api.js';
import { CHART_TYPES, Chart, DRAW_TOOLS } from './chart/engine.js';
import { INDICATORS } from './chart/indicators.js';
// The chart draws from the SAME lifecycle engine the backtest uses
// (js/chart/tlengine.js is a port of sim/tl/engine.py, kept honest by
// tests/test_tl_parity.py). It previously used the batch scorer in
// trendlines.js, so the lines on screen were not the lines traded.
import { liveLines } from './chart/tlengine.js';
import { $, $$, TF, TF_LABEL, TF_MS, el, load, money, num, save, signed } from './util.js';
import { closeMenu, openMenu, toast } from './ui/menu.js';
import { SymbolSearch } from './ui/search.js';
import { Watchlist } from './ui/watchlist.js';
import { Panels, Tape, renderSpec } from './ui/panels.js';

/* Sensitivity presets, expressed as engine parameters. Pivot strength is the
   real control: 2 finds minor swings, 6 finds structural ones. */
const SENS = {
  fine: { label: 'Fine (minor swings)', strength: 2 },
  normal: { label: 'Normal', strength: 3 },
  major: { label: 'Major structure', strength: 6 },
};

const LAYOUTS = { 1: 'Single', '2h': 'Two columns', '2v': 'Two rows', 4: 'Grid of four' };

const app = {
  layout: load('layout', '1'),
  cells: load('cells', [{ symbol: 'EURUSD', tf: '15m', type: 'candles', studies: [] }]),
  charts: [],
  active: null,
  tool: 'cursor',
  quotes: {},
  refCloses: {},
  spec: null,
  symbols: [],
  downloading: false,
  auto: load('auto', {
    on: true,
    sens: 'normal',
    maxLines: 3,          // per side, per source timeframe
    own: true,            // detect on the chart's own timeframe
    htf: ['1h', '4h', '1d'],   // project from these when they are higher
  }),
};

/* ------------------------------------------------------------------ grid */

const drawKey = (c) => `draw.${c.symbol}.${c.tf}`;

function cellCount(layout) { return layout === '4' ? 4 : layout === '1' ? 1 : 2; }

function buildGrid() {
  const grid = $('#grid');
  app.charts.forEach((c) => c.destroy());
  app.charts = [];
  grid.innerHTML = '';
  grid.dataset.layout = app.layout;

  const n = cellCount(app.layout);
  while (app.cells.length < n) app.cells.push({ ...structuredClone(app.cells[0] || {}), });
  const cells = app.cells.slice(0, n);

  // 'rsi' was folded into 'rsidiv' (a superset, same RSI series). Migrate saved
  // layouts so a persisted RSI pane upgrades instead of silently disappearing.
  for (const state of cells) {
    if (state && Array.isArray(state.studies)) {
      state.studies = state.studies.map(
        (s) => (s.kind === 'rsi' ? { ...s, kind: 'rsidiv' } : s));
    }
  }

  cells.forEach((state, i) => {
    const host = el('div', { class: 'cell' });
    grid.append(host);
    const chart = new Chart(host, {
      symbol: state.symbol || 'EURUSD',
      tf: state.tf || '15m',
      type: state.type || 'candles',
      studies: state.studies,
      drawings: load(`draw.${state.symbol}.${state.tf}`, []),
      onChange: (ch) => { persist(); loadBarsIfNeeded(ch); },
      onActivate: (ch) => setActive(ch),
    });
    chart.view.span = state.span || 160;
    chart.onNewBar = (ch) => computeAuto(ch, 400);   // refit as each bar closes
    chart._loadDrawings = () => load(drawKey(chart), []);
    chart.setTool(app.tool);
    app.charts.push(chart);
    loadBars(chart);
  });

  setActive(app.charts[0]);
  persist();
}

function persist() {
  app.cells = app.charts.map((c) => c.state());
  save('cells', app.cells);
  save('layout', app.layout);
  for (const c of app.charts) save(drawKey(c), c.drawings);
}

function setActive(chart) {
  app.active = chart;
  app.charts.forEach((c) => c.host.classList.toggle('active', c === chart));
  syncToolbar();
  wl.setActive(chart.symbol);
  tape.reset(chart.symbol);
  loadSpec(chart.symbol);
}

/* ------------------------------------------------------------- data load */

const inflight = new Map();

/* How many bars to ask for, per timeframe. Asking 2000 of everything means
   asking for 38 years of weeklies, which makes MetaTrader go and download them;
   these counts keep every timeframe to a sane span (~2-20 years on the highs). */
const BAR_COUNT = {
  '1m': 3000, '5m': 2500, '15m': 2000, '30m': 2000,
  '1h': 1500, '4h': 1200, '1d': 1000, '1w': 800,
};

async function loadBars(chart) {
  const token = Symbol('req');
  inflight.set(chart, token);
  chart.message = 'loading…';
  chart.draw();
  // a cold history download is slow and silent; say so rather than look hung
  const slow = setTimeout(() => {
    if (inflight.get(chart) === token && !chart.bars.length) {
      chart.message = 'asking MetaTrader for history — the first fetch of a symbol can take a minute';
      chart.draw();
    }
  }, 4000);
  try {
    const payload = await api.bars(chart.symbol, chart.tf, BAR_COUNT[chart.tf] || 2000);
    if (inflight.get(chart) !== token) return;                  // superseded
    chart.setData(payload);
    adoptResolved(chart);
    chart.setPositions(panels.data.positions);
    computeAuto(chart, 0);
    if (!payload.bars || !payload.bars.length) {
      chart.message = 'no history — open the symbol in MT5 once so the terminal downloads it';
      chart.draw();
    }
  } catch (err) {
    if (inflight.get(chart) !== token) return;
    chart.bars = [];
    chart.message = String(err.message || err);
    chart.draw();
  } finally {
    clearTimeout(slow);
  }
}

/* Brokers suffix their symbols (Pepperstone serves EURUSD.a, not EURUSD). The
   bridge resolves a plain name by prefix, so a request works either way — but
   once we know the broker's real ticker, adopt it everywhere so the legend,
   watchlist and saved drawings all agree with the terminal. */
function adoptResolved(chart) {
  const real = chart.resolved;
  if (!real || real === chart.symbol) return;
  const old = chart.symbol;
  chart.symbol = real;
  chart.drawings = load(drawKey(chart), chart.drawings);
  wl.rename(old, real);
  if (chart === app.active) {
    wl.setActive(real);
    tape.reset(real);
    loadSpec(real);
    syncToolbar();
  }
  persist();
  chart.draw();
}

function loadBarsIfNeeded(chart) {
  if (!chart.bars.length) loadBars(chart);
}

async function loadSpec(symbol) {
  try {
    const spec = await api.spec(symbol);
    app.spec = spec;
    renderSpec(spec, app.quotes[symbol]);
  } catch { renderSpec(null); }
}

/* The stock watchlist ships plain names, but a broker serves its own tickers
   (Pepperstone: EURUSD.a). Once we know the real list, swap each plain name for
   the shortest ticker that starts with it — EURUSD -> EURUSD.a, never
   EURUSD.a-something-else. Symbols with no match are left alone so the row
   still shows its own "not offered" state rather than vanishing. */
function reconcileWatchlist() {
  if (!app.symbols.length) return;
  const names = app.symbols.map((s) => s.name);
  const have = new Set(names);
  for (const want of [...wl.symbols]) {
    if (have.has(want)) continue;
    const match = names
      .filter((n) => n.toUpperCase().startsWith(want.toUpperCase()))
      .sort((a, b) => a.length - b.length)[0];
    if (match) wl.rename(want, match);
  }
}

/* ------------------------------------------------------- auto trendlines */

/* Lines are detected per instrument × timeframe. A chart shows its own
   timeframe's lines plus, optionally, lines detected on HIGHER timeframes of
   the same instrument — which is why those series are fetched and cached here
   rather than borrowed from whatever other cells happen to be open. */

const htfCache = new Map();          // `${symbol}|${tf}` -> { at, bars }
const autoTimers = new Map();

/** Cache a higher-timeframe series for a quarter of its own bar interval. */
async function getSeries(symbol, tf) {
  const key = `${symbol}|${tf}`;
  const ttl = Math.max(30000, Math.min((TF_MS[tf] || 60e3) / 4, 300000));
  const hit = htfCache.get(key);
  if (hit && Date.now() - hit.at < ttl) return hit.bars;
  const payload = await api.bars(symbol, tf, Math.min(BAR_COUNT[tf] || 1000, 1200));
  const bars = payload.bars || [];
  htfCache.set(key, { at: Date.now(), bars });
  return bars;
}

/** Which series feed one chart: its own timeframe, then anything above it. */
function autoSources(chart) {
  const rank = TF.indexOf(chart.tf);
  const out = app.auto.own ? [chart.tf] : [];
  for (const tf of app.auto.htf) {
    if (TF.indexOf(tf) > rank) out.push(tf);
  }
  return out;
}

function computeAuto(chart, delay = 250) {
  clearTimeout(autoTimers.get(chart));
  autoTimers.set(chart, setTimeout(() => runAuto(chart), delay));
}

async function runAuto(chart) {
  if (!app.auto.on) { chart.setAutoLines([]); return; }
  if (!chart.bars.length) return;
  // the engine keeps its own holding pool and offers its best few; the budget
  // below then keeps the best across all source timeframes
  const opts = { ...(SENS[app.auto.sens] || SENS.normal) };
  const symbol = chart.symbol;
  const tf = chart.tf;
  const lines = [];

  for (const source of autoSources(chart)) {
    try {
      const bars = source === chart.tf ? chart.bars : await getSeries(symbol, source);
      // the chart may have moved on while a higher timeframe was fetching
      if (chart.symbol !== symbol || chart.tf !== tf) return;
      if (!bars || bars.length < 40) continue;
      for (const l of liveLines(bars, source, { params: opts })) lines.push(l);
    } catch { /* a missing higher timeframe just contributes nothing */ }
  }

  /* "Lines per side" is a budget for the CHART, not for each source, or four
     timeframes would quietly put two dozen lines on the screen. */
  // ACTIVE (retested since confirming) outranks merely CONFIRMED at equal score
  const rank = (l) => l.score + (l.status === 'ACTIVE' ? 2 : 0);
  lines.sort((a, b) => rank(b) - rank(a));
  const budget = { support: app.auto.maxLines, resistance: app.auto.maxLines };
  const keep = lines.filter((l) => (budget[l.kind]-- > 0));
  chart.setAutoLines(keep);
}

function recomputeAll() { app.charts.forEach((c) => computeAuto(c, 0)); }

/* ---------------------------------------------------------------- header */

function paintHealth() {
  const pill = $('#healthPill');
  const map = {
    live: ['ok', `live · ${status.health?.server || 'mt5'}`],
    mock: ['mock', 'bridge mock'],
    demo: ['down', 'demo data'],
    down: ['down', 'mt5 not connected'],
    connecting: ['', 'connecting…'],
  };
  // the calendar renders in a chosen zone and needs the broker's offset for it
  panels.brokerOffsetMs = status.health?.time_offset_ms || 0;
  const [state, text] = map[status.mode] || ['', status.mode];
  pill.dataset.state = state;
  pill.textContent = text;
  pill.title = status.error || `bridge: ${base()}`;
}

function paintAccount(a) {
  if (!a) return;
  const c = a.currency || '';
  $('#acBal').textContent = money(a.balance, c);
  $('#acEq').textContent = money(a.equity, c);
  const pl = $('#acPl');
  pl.textContent = signed(a.profit);
  pl.className = a.profit >= 0 ? 'up' : 'down';
  $('#acFree').textContent = money(a.margin_free, c);
  panels.currency = c;
}

/* -------------------------------------------------------------- toolbar */

function buildTfGroup() {
  const g = $('#tfGroup');
  g.innerHTML = '';
  TF.forEach((tf, i) => {
    g.append(el('button', {
      class: 'tb', dataset: { tf }, text: TF_LABEL[tf], title: `${tf} (${i + 1})`,
      onclick: () => setTf(tf),
    }));
  });
}

function syncToolbar() {
  const c = app.active;
  if (!c) return;
  $('#symbolLabel').textContent = c.symbol;
  $$('#tfGroup .tb').forEach((b) => b.classList.toggle('active', b.dataset.tf === c.tf));
  $('#typeBtn').textContent = CHART_TYPES[c.type] + ' ▾';
  $('#indBtn').classList.toggle('active', c.studies.length > 0);
  $('#drawBtn').classList.toggle('active', app.tool !== 'cursor');
  $('#autoBtn').classList.toggle('active', app.auto.on);
  document.title = `${c.symbol} ${TF_LABEL[c.tf]} — DiaNurFx`;
}

function setTf(tf) {
  if (!app.active) return;
  app.active.setTimeframe(tf);
  app.active.drawings = load(drawKey(app.active), []);
  syncToolbar();
  loadBars(app.active);
  persist();
}

function setSymbol(sym) {
  if (!app.active) return;
  app.active.setSymbol(sym);
  app.active.drawings = load(drawKey(app.active), []);
  syncToolbar();
  wl.setActive(sym);
  tape.reset(sym);
  loadSpec(sym);
  loadBars(app.active);
  persist();
}

function wireToolbar() {
  $('#symbolBtn').addEventListener('click', () => search.open());

  $('#typeBtn').addEventListener('click', (e) => {
    openMenu(e.currentTarget, [
      { kind: 'cap', label: 'Chart type' },
      ...Object.entries(CHART_TYPES).map(([k, label]) => ({
        label, value: k, checked: app.active?.type === k,
      })),
    ], (v) => { app.active.setType(v); syncToolbar(); });
  });

  $('#indBtn').addEventListener('click', (e) => {
    const c = app.active;
    const items = [
      { kind: 'cap', label: 'Overlays' },
      ...['ema', 'sma', 'bb', 'vwap'].map((k) => ({ label: INDICATORS[k].label, value: k, checked: c.hasStudy(k) })),
      { kind: 'cap', label: 'Panes' },
      ...['volume', 'rsidiv', 'macd', 'atr', 'stoch'].map((k) => ({ label: INDICATORS[k].label, value: k, checked: c.hasStudy(k) })),
    ];
    if (c.studies.length) {
      items.push({ kind: 'sep' }, { label: 'Remove all studies', value: '__clear' });
    }
    openMenu(e.currentTarget, items, (v) => {
      if (v === '__clear') { c.studies = []; c._recalc(); c.draw(); persist(); return; }
      c.toggleStudy(v);
      syncToolbar();
    });
  });

  $('#drawBtn').addEventListener('click', (e) => {
    openMenu(e.currentTarget, [
      { kind: 'cap', label: 'Drawing tools' },
      ...Object.entries(DRAW_TOOLS).map(([k, label]) => ({
        label, value: k, checked: app.tool === k,
        hint: { cursor: 'esc', hline: 'h', trend: 't', ray: 'y', rect: 'r', fib: 'g' }[k],
      })),
      { kind: 'sep' },
      { label: 'Clear drawings on this chart', value: '__clear' },
    ], (v) => {
      if (v === '__clear') { app.active.drawings = []; persist(); app.active.draw(); return; }
      setTool(v);
    });
  });

  $('#autoBtn').addEventListener('click', (e) => {
    const c = app.active;
    const rank = TF.indexOf(c.tf);
    const higher = TF.filter((tf) => TF.indexOf(tf) > rank);
    const items = [
      { kind: 'cap', label: 'Algorithmic trendlines' },
      { label: app.auto.on ? 'On' : 'Off', value: 'on', checked: app.auto.on },
      { kind: 'cap', label: 'Sensitivity' },
      ...Object.entries(SENS).map(([k, v]) => ({
        label: v.label, value: 'sens:' + k, checked: app.auto.sens === k,
      })),
      { kind: 'cap', label: 'Lines per side' },
      ...[2, 3, 4, 6].map((n) => ({ label: String(n), value: 'max:' + n, checked: app.auto.maxLines === n })),
      { kind: 'cap', label: 'Sources' },
      { label: `Own timeframe (${TF_LABEL[c.tf]})`, value: 'own', checked: app.auto.own, keepOpen: true },
      ...higher.map((tf) => ({
        label: `Project from ${TF_LABEL[tf]}`, value: 'htf:' + tf,
        checked: app.auto.htf.includes(tf), keepOpen: true,
      })),
    ];
    if (!higher.length) items.push({ kind: 'cap', label: 'already the highest timeframe' });
    items.push({ kind: 'sep' }, { label: 'Recompute now', value: 'recompute' });

    openMenu(e.currentTarget, items, (v, item) => {
      if (v === 'on') app.auto.on = !app.auto.on;
      else if (v === 'own') app.auto.own = !app.auto.own;
      else if (v.startsWith('sens:')) app.auto.sens = v.slice(5);
      else if (v.startsWith('max:')) app.auto.maxLines = Number(v.slice(4));
      else if (v.startsWith('htf:')) {
        const tf = v.slice(4);
        app.auto.htf = app.auto.htf.includes(tf)
          ? app.auto.htf.filter((x) => x !== tf)
          : [...app.auto.htf, tf];
      }
      save('auto', app.auto);
      syncToolbar();
      if (item && item.keepOpen) $('#autoBtn').click();   // re-render the ticks
      recomputeAll();
    });
  });

  $('#layoutBtn').addEventListener('click', (e) => {
    openMenu(e.currentTarget, [
      { kind: 'cap', label: 'Layout' },
      ...Object.entries(LAYOUTS).map(([k, label]) => ({ label, value: k, checked: app.layout === k })),
    ], (v) => { app.layout = v; buildGrid(); });
  });

  $('#healthPill').addEventListener('click', (e) => {
    openMenu(e.currentTarget, [
      { kind: 'cap', label: status.error ? 'Bridge problem' : 'Bridge' },
      { label: `Reconnect (${base().replace('http://', '')})`, value: 'retry' },
      { label: 'Change bridge URL…', value: 'url' },
      { kind: 'sep' },
      { label: 'Reload all charts', value: 'reload' },
    ], async (v) => {
      if (v === 'retry') { await tick.health(); toast(status.error || `bridge ${status.mode}`); }
      if (v === 'url') {
        const next = window.prompt('Bridge URL', base());
        if (next) { setBase(next); await tick.health(); boot(true); }
      }
      if (v === 'reload') app.charts.forEach(loadBars);
    });
  });

  $('#wlAdd').addEventListener('click', () => search.open());
}

function setTool(t) {
  app.tool = t;
  app.charts.forEach((c) => c.setTool(t));
  syncToolbar();
}

/* ------------------------------------------------------------- keyboard */

function wireKeys() {
  document.addEventListener('keydown', (e) => {
    if (search.isOpen) return;
    const typing = /input|textarea/i.test(e.target.tagName);
    if (typing) return;

    if (e.key === '/') { e.preventDefault(); search.open(); return; }
    if (e.key === 'Escape') { setTool('cursor'); app.charts.forEach((c) => c.cancelTool()); closeMenu(); return; }
    if (e.key >= '1' && e.key <= '8') { setTf(TF[Number(e.key) - 1]); return; }
    const tools = { h: 'hline', t: 'trend', y: 'ray', r: 'rect', g: 'fib' };
    if (tools[e.key.toLowerCase()]) { setTool(tools[e.key.toLowerCase()]); return; }
    if (e.key.toLowerCase() === 'f') { $('#indBtn').click(); return; }
    if (e.key.toLowerCase() === 'a') { app.active?.fitAll(); return; }
    if (e.key.toLowerCase() === 'u') {
      const back = wl.undoRemove();
      if (back) { toast(`${back} restored`, 1600); tick.quotes(); tick.daily(); }
      return;
    }
    if (e.key.toLowerCase() === 'l') {
      app.auto.on = !app.auto.on;
      save('auto', app.auto);
      syncToolbar();
      recomputeAll();
      toast(`auto trendlines ${app.auto.on ? 'on' : 'off'}`, 1400);
      return;
    }
  });
}

/* -------------------------------------------------------------- polling */

/* While the bridge is bulk-downloading it holds the MetaTrader lock, so every
   data call blocks. Polling through that just stacks up timeouts and leaves the
   page looking broken -- which is exactly the failure we hit once before, a
   green LIVE pill over a dead feed. So the loops stand down and say so. */
async function downloadGuard() {
  try {
    const res = await fetch(base() + '/download/status', { cache: 'no-store' });
    const st = await res.json();
    const was = app.downloading;
    app.downloading = !!st.running;
    if (app.downloading) {
      const pill = $('#healthPill');
      pill.dataset.state = 'mock';
      pill.textContent = `downloading ${st.pct || 0}%`;
      pill.title = `${st.phase} ${st.symbol || ''} ${st.item || ''} — live data paused`;
    } else if (was) {
      toast('download finished — live data resumed', 4000);
      tick.health();
      app.charts.forEach(loadBars);
    }
  } catch { app.downloading = false; }
  return app.downloading;
}

const tick = {
  async health() {
    if (app.downloading) return;
    await api.health();
    paintHealth();
  },

  async account() {
    if (app.downloading) return;
    try {
      const [a, pos, ord] = await Promise.all([api.account(), api.positions(), api.orders()]);
      paintAccount(a);
      panels.set('positions', pos);
      panels.set('orders', ord);
      app.charts.forEach((c) => c.setPositions(pos));
    } catch (err) { /* health pill already tells the story */ }
  },

  async quotes() {
    if (app.downloading) return;
    const names = [...new Set([...wl.symbols, ...app.charts.map((c) => c.symbol)])];
    if (!names.length) return;
    try {
      const q = await api.quotes(names);
      app.quotes = { ...app.quotes, ...q };
      wl.update(q, app.refCloses);
      search.setQuotes(app.quotes);
      for (const c of app.charts) if (q[c.symbol]) c.applyQuote(q[c.symbol]);
      if (app.spec) renderSpec(app.spec, app.quotes[app.spec.symbol]);
    } catch { /* transient */ }
  },

  async tape() {
    if (app.downloading) return;
    const c = app.active;
    if (!c || document.hidden) return;
    try {
      const payload = await api.ticks(c.symbol, tape.lastTs || Date.now() - 15000, 400);
      if (payload && payload.symbol === c.symbol) tape.push(payload);
    } catch { /* transient */ }
  },

  async daily() {
    if (app.downloading) return;
    // previous daily close per watchlist symbol, for the change column
    const names = [...new Set([...wl.symbols, ...app.charts.map((c) => c.symbol)])];
    for (const name of names) {
      try {
        const b = await api.bars(name, '1d', 3);
        const bars = b.bars || [];
        if (bars.length >= 2) app.refCloses[name] = bars[bars.length - 2].c;
      } catch { /* skip */ }
    }
    wl.update({}, app.refCloses);
  },

  async deals() {
    if (app.downloading) return;
    try { panels.set('deals', await api.deals(7)); } catch { /* skip */ }
  },

  async calendar() {
    if (app.downloading) return;
    try { panels.set('calendar', await api.calendar()); } catch { /* skip */ }
  },
};

function startLoops() {
  const every = (ms, fn) => { fn(); return setInterval(() => { if (!document.hidden) fn(); }, ms); };
  every(2000, downloadGuard);      // must run even while everything else pauses
  every(5000, tick.health);
  every(2000, tick.account);
  every(1000, tick.quotes);
  every(1200, tick.tape);
  every(60000, tick.daily);
  every(30000, tick.deals);
  every(300000, tick.calendar);
  setInterval(() => app.charts.forEach((c) => c.draw()), 1000);   // bar-close countdown
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) { tick.health(); tick.quotes(); tick.account(); }
  });
}

/* ----------------------------------------------------------------- boot */

const panels = new Panels();
const tape = new Tape();
const wl = new Watchlist({
  onPick: (sym) => setSymbol(sym),
  onRemove: (sym) => toast(`${sym} removed — press u to undo`, 5000),
});
const search = new SymbolSearch({
  onPick: (sym) => { wl.add(sym); setSymbol(sym); },
});

async function boot(refresh = false) {
  buildTfGroup();
  if (!refresh) { wireToolbar(); wireKeys(); }
  await tick.health();
  if (status.mode === 'demo') {
    toast('MT5 bridge not reachable — showing synthetic demo data. Start it with: python bridge/mt5_bridge.py', 8000);
  } else if (status.mode === 'down') {
    toast(`Bridge is up but MetaTrader is not connected: ${status.error || 'unknown'}`, 8000);
  }
  try {
    app.symbols = await api.symbols();
  } catch { app.symbols = []; }
  reconcileWatchlist();
  search.setSymbols(app.symbols.length ? app.symbols : wl.symbols.map((n) => ({ name: n, path: '' })));

  buildGrid();
  panels.render();
  if (!refresh) startLoops();
  tick.daily();
}

boot();

// exposed for console poking during development
window.DiaNurFx = { app, api, status, tick, boot };
