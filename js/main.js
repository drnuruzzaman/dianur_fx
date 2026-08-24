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
import { liveChannels } from './chart/channels.js';
import { calibrate } from './chart/sensitivity.js';
import { liveZones } from './chart/zones.js';
import { liveSDZones } from './chart/supplydemand.js';
import { detect as detectMS } from './chart/marketstructure.js';
import { swingPoints } from './chart/structure.js';
import { build as buildSegments } from './chart/segments.js';
import { $, $$, TF, TF_LABEL, TF_MS, drop, el, load, money, num, save, setZone, signed } from './util.js';
import { closeMenu, openMenu, toast } from './ui/menu.js';
import { SymbolSearch } from './ui/search.js';
import { Watchlist } from './ui/watchlist.js';
import { Panels } from './ui/panels.js';
import { TrendRead, readTfs } from './ui/trendread.js';
import { SignalPanel } from './ui/signalpanel.js';

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
  /* OPEN CHARTS, MT5-style. `tabs` is every chart you have open; `slots` says
     which tab each visible cell is showing. The layout decides how many cells
     there are, the tabs decide what goes in them -- so a 4-chart layout shows
     four tabs at once and a 1-chart layout shows one, without either concept
     owning the other.

     `cells` was the old name and held only the VISIBLE charts, so anything past
     the layout count was lost on save. Migrated here rather than dropped. */
  tabs: load('tabs', null)
    || load('cells', [{ symbol: 'EURUSD', tf: '15m', type: 'candles', studies: [] }]),
  slots: load('slots', [0]),
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
    /* Per-instrument calibration (js/chart/sensitivity.js). Measured at
       +0.85 pp placebo-adjusted over three eras -- which makes the detector
       less bad rather than good, so it is offered rather than imposed. */
    adaptive: false,
    sdZones: false,        // impulse-origin supply/demand zones
    ms: false,             // BOS / CHoCH marks
    msMax: 12,             // most recent N events
    swings: false,         // HH / HL / LH / LL markers
  }),
};

/* ------------------------------------------------------------------ grid */

/* Drawings are keyed by INSTRUMENT, not instrument+timeframe.
 *
 * They are stored as {t, price} and rendered through idxOfTime(), so they were
 * always portable across timeframes — the key was the only thing stopping a
 * line drawn on H1 from appearing on M15. A trendline is a claim about the
 * market, not about the resolution you happened to be looking at when you
 * drew it.
 *
 * `migrateDrawings` folds any legacy draw.SYMBOL.TF keys into the new one, so
 * nothing saved under the old scheme is lost. Dedupes on id, because the same
 * drawing may have been saved under several timeframes. */
const drawKey = (c) => `draw.${typeof c === 'string' ? c : c.symbol}`;

const LEGACY_TFS = ['1m', '5m', '15m', '30m', '1h', '4h', '1d', '1w'];

function migrateDrawings(symbol) {
  const key = drawKey(symbol);
  const merged = load(key, []);
  const seen = new Set(merged.map((d) => d.id));
  let moved = 0;
  for (const tf of LEGACY_TFS) {
    const old = `draw.${symbol}.${tf}`;
    const items = load(old, null);
    if (!items || !items.length) continue;
    for (const d of items) {
      if (d && d.id != null && !seen.has(d.id)) { seen.add(d.id); merged.push(d); moved++; }
    }
    drop(old);          // namespaced — a raw removeItem misses the prefix
  }
  if (moved) save(key, merged);
  return merged;
}

function cellCount(layout) { return layout === '4' ? 4 : layout === '1' ? 1 : 2; }

function buildGrid() {
  const grid = $('#grid');
  /* Which CELL had focus, so a rebuild does not throw it back to the first one.
     Switching a tab in the right-hand cell rebuilds the grid, and without this
     the focus jumps left -- so the next tab click would load into the wrong
     cell. */
  const keepSlot = Math.max(0, app.charts.indexOf(app.active));
  app.charts.forEach((c) => c.destroy());
  app.charts = [];
  grid.innerHTML = '';
  grid.dataset.layout = app.layout;

  const n = cellCount(app.layout);
  // enough tabs to fill the layout, and a slot per cell pointing at a real tab
  while (app.tabs.length < n) {
    app.tabs.push(structuredClone(app.tabs[app.tabs.length - 1] || app.tabs[0]));
  }
  app.slots = app.slots.slice(0, n).filter((i) => i >= 0 && i < app.tabs.length);
  for (let k = 0; app.slots.length < n; k++) {
    if (!app.slots.includes(k) && k < app.tabs.length) app.slots.push(k);
    else if (k > app.tabs.length) break;
  }
  while (app.slots.length < n) app.slots.push(0);
  const cells = app.slots.map((i) => app.tabs[i]);

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
      // per INSTRUMENT, and migrating any legacy per-timeframe keys
      drawings: migrateDrawings(state.symbol),
      onChange: (ch) => { persist(); loadBarsIfNeeded(ch); },
      onActivate: (ch) => setActive(ch),
    });
    chart.view.span = state.span || 160;
    chart.onNewBar = (ch) => {
      computeAuto(ch, 400);                        // refit as each bar closes
      if (ch === app.active) loadTrendRead(ch);    // and re-read the panels
    };
    chart._loadDrawings = () => migrateDrawings(chart.symbol);
    chart.setTool(app.tool);
    app.charts.push(chart);
    loadBars(chart);
  });

  setActive(app.charts[Math.min(keepSlot, app.charts.length - 1)] || app.charts[0]);
  renderTabs();
  persist();
}

/* ------------------------------------------------------------ chart tabs */

function renderTabs() {
  const list = $('#ctList');
  if (!list) return;
  list.innerHTML = '';
  const activeSlot = Math.max(0, app.charts.indexOf(app.active));
  app.tabs.forEach((t, i) => {
    const shown = app.slots.indexOf(i);
    const btn = el('button', {
      class: 'ct' + (shown >= 0 ? ' shown' : '') + (shown === activeSlot ? ' active' : ''),
      title: `${t.symbol} ${TF_LABEL[t.tf] || t.tf}`,
      onclick: () => showTab(i),
    }, el('span', { text: `${t.symbol},${TF_LABEL[t.tf] || t.tf}` }));
    // a single remaining tab has nothing to fall back to, so it keeps no close
    if (app.tabs.length > 1) {
      btn.append(el('button', {
        class: 'ct-x', title: 'Close',
        onclick: (e) => { e.stopPropagation(); closeTab(i); },
      }, '×'));
    }
    list.append(btn);
  });
}

/** Load tab `i` into the FOCUSED cell — tabs and tiling coexist. */
function showTab(i) {
  if (i < 0 || i >= app.tabs.length) return;
  const slot = Math.max(0, app.charts.indexOf(app.active));
  if (app.slots[slot] === i) return;
  persist();                       // keep the outgoing chart's state
  app.slots[slot] = i;
  save('slots', app.slots);
  buildGrid();
}

function closeTab(i) {
  if (app.tabs.length <= 1) return;
  persist();
  app.tabs.splice(i, 1);
  // every slot after the removed tab shifts down; a slot ON it falls back
  app.slots = app.slots.map((s) => (s === i ? Math.max(0, i - 1) : s > i ? s - 1 : s));
  save('tabs', app.tabs);
  save('slots', app.slots);
  buildGrid();
}

function persist() {
  /* Only the rendered charts write back, and each into ITS OWN tab. The old
     version replaced the whole array with the visible charts, which silently
     deleted every tab beyond the layout count on the next save. */
  app.charts.forEach((c, k) => {
    const i = app.slots[k];
    if (i != null && i < app.tabs.length) app.tabs[i] = c.state();
  });
  save('tabs', app.tabs);
  save('slots', app.slots);
  save('layout', app.layout);
  for (const c of app.charts) save(drawKey(c), c.drawings);
  /* The strip shows SYMBOL,TF, so it is stale the moment either changes.
     persist() runs on every such change, which makes it the one place that
     cannot miss one. */
  renderTabs();
}

function setActive(chart) {
  app.active = chart;
  app.charts.forEach((c) => c.host.classList.toggle('active', c === chart));
  renderTabs();                     // the active highlight follows the focus
  syncToolbar();
  wl.setActive(chart.symbol);
  loadSpec(chart.symbol);
  loadTrendRead(chart);
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
  chart.drawings = migrateDrawings(chart.symbol);
  wl.rename(old, real);
  if (chart === app.active) {
    wl.setActive(real);
    loadSpec(real);
    loadTrendRead(app.active);
    syncToolbar();
  }
  persist();
  chart.draw();
}

function loadBarsIfNeeded(chart) {
  if (!chart.bars.length) loadBars(chart);
}

/* The Trend read panel follows the focused chart's instrument. It reads three
   fixed timeframes regardless of what that chart is showing, because the whole
   value of the panel is that it does NOT change its mind when you change
   timeframe — the market has one structure, and the chart is just where you
   happen to be looking. Series come from the same cache the auto-trendlines
   use, so opening the panel costs no extra bridge traffic on the timeframes
   already fetched. */
const trendRead = new TrendRead($('#trendread'), $('#trSym'));
/* The signal engine reads the EXECUTION frame only — it is a statement
   about the next few bars of the chart you are on, not about context. */
const signalPanel = new SignalPanel($('#signalpanel'), $('#sigSym'));
let panelTick = 0;
let trendReadSeq = 0;

/* Reads the FOCUSED chart, in both axes: its instrument and its timeframe. The
   frames are the chart's own plus the two above it (readTfs), so switching to
   H1 re-reads at 1h/4h/1d instead of staying pinned to the scalping frames.

   Series come from the same cache the auto-trendlines use, so the frames a
   chart is already projecting from cost no extra bridge traffic. */
async function loadTrendRead(chart) {
  if (!chart || !chart.symbol) return;
  const { symbol, tf } = chart;
  const tfs = readTfs(tf);
  const seq = ++trendReadSeq;

  const series = new Map();
  trendRead.update(symbol, series, tfs, { execTf: tf });   // pending rows first
  await Promise.all(tfs.map(async (t) => {
    /* The chart's own bar array for the execution frame, not a second fetched
       copy. `applyQuote` advances chart.bars tick by tick, while getSeries
       caches for up to a quarter of a bar interval -- so the panel was reading
       a snapshot that could be minutes stale while the chart beside it moved.
       Higher frames still come from the cache: they change slowly and are
       shared across cells. */
    if (t === tf && chart.bars && chart.bars.length) { series.set(t, chart.bars); return; }
    try { series.set(t, await getSeries(symbol, t)); } catch { /* leave pending */ }
  }));
  /* A slower fetch for a symbol or timeframe you have already navigated away
     from must not repaint the panel with a stale read. */
  if (seq !== trendReadSeq) return;

  /* Invalidation and R:R are measured against the lines the chart is DRAWING,
     from the same engine, so a level quoted in the panel is a level you can see.
     Every frame read contributes: a 4h line two pips under a 15m line stops the
     trade at the same moment, and which timeframe found it is not the
     interesting part. */
  const lines = [];
  for (const t of tfs) {
    const bars = series.get(t);
    if (!bars || bars.length < 40) continue;
    /* Same threshold the chart draws at, so an Invalidation the panel quotes is
       a line you can actually see. A panel naming a level that is not on screen
       is worse than one naming no level at all. */
    try {
      lines.push(...liveLines(bars, t, {
        params: SENS[app.auto.sens] || SENS.normal, minDraw: app.auto.minDraw ?? 70,
      }));
    }
    catch { /* a frame that cannot be detected on simply contributes nothing */ }
  }

  trendRead.update(symbol, series, tfs, {
    lines,
    digits: (app.spec && app.spec.digits != null) ? app.spec.digits : 3,
    execTf: tf,
  });
  /* The chart's own frame, not tfs[0]: on an H4 chart the ladder starts at 1h,
     and feeding those bars here labelled "H4" would compute the whole signal
     engine on a series the user is not looking at. */
  signalPanel.update(symbol, TF_LABEL[tf] || tf, series.get(tf));
}

async function loadSpec(symbol) {
  try {
    /* The Contract panel is gone, but the spec is still read: `digits` is what
       formats the Invalidation price in the Trend read, and getting that wrong
       shows 1.16 where the instrument quotes 1.16393. */
    app.spec = await api.spec(symbol);
  } catch { app.spec = null; }
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
  if (!app.auto.on) {
    chart.setAutoLines([]); chart.setChannels([]); chart.setZones([]);
    chart.setSegments([]); chart.setSdZones([]);
    chart.setMsEvents([]); chart.setSwings([]);
    return;
  }
  if (!chart.bars.length) return;
  // the engine keeps its own holding pool and offers its best few; the budget
  // below then keeps the best across all source timeframes
  const opts = { ...(SENS[app.auto.sens] || SENS.normal) };
  /* Draw down to 70 but keep flagging at the engine's measured 90 -- see
     liveLines(). Structure you can see, quality you can judge. */
  const minDraw = app.auto.minDraw ?? 70;

  /* Two calibrations of the same instrument.
     
     DETECTION IS UNCHANGED. Turning this on never removes a line: the engine
     still walks on the preset window with no prominence bar, so the chart draws
     exactly what it drew before. Measured on live data, applying the calibrated
     window to drawing cost XAUUSD 1h all three of its lines and USDJPY 4h a
     quarter of them -- the strict setting is right for what a strategy ACTS on
     and wrong for what a chart SHOWS, which is the same lesson min_quality
     taught.

     What it changes is the `offered` FLAG: a line is flagged only if the
     measured calibration (+0.85 pp over three eras) would have built it at all
     -- both anchors clearing its prominence bar at its window -- and cleared
     its quality bar. Unflagged lines still draw, at half weight. */
  const offerSens = (bars) => calibrate(bars, chart.tf, chart.symbol);
  const symbol = chart.symbol;
  const tf = chart.tf;
  const lines = [];

  for (const source of autoSources(chart)) {
    try {
      const bars = source === chart.tf ? chart.bars : await getSeries(symbol, source);
      // the chart may have moved on while a higher timeframe was fetching
      if (chart.symbol !== symbol || chart.tf !== tf) return;
      if (!bars || bars.length < 40) continue;
      /* Calibration is per SERIES: a 4h projection onto a 15m chart must be
         calibrated on 4h bars, or its prominence bar is measured in the wrong
         instrument's units. */
      const ll = liveLines(bars, source, {
        params: opts, minDraw,
        offerSensitivity: app.auto.adaptive ? offerSens(bars) : null,
      });
      for (const l of ll) lines.push(l);
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

  /* Channels come from the chart's OWN line population only. A corridor
     projected down from 4h onto a 15m chart would be drawn from rails whose
     containment was measured on 4h bars -- a different claim than the band
     appears to make. Detected from the engine's own population rather than the
     budgeted `keep`, because a rail dropped by the per-side line budget is
     still a real rail and its corridor should not vanish with it. */
  try {
    chart.setChannels(liveChannels(chart.bars, chart.tf, { params: opts }));
  } catch { chart.setChannels([]); }

  /* Zones come from the chart's own pivots at its own timeframe. They are
     horizontal by definition, so unlike trendlines there is nothing to project
     from a higher frame -- a 4h zone and a 15m zone at the same price are the
     same band, and drawing both would double-count one level. */
  try {
    chart.setZones(liveZones(chart.bars, chart.tf,
                             { strengthPivots: opts.strength ?? 3 }));
  } catch { chart.setZones([]); }

  /* BOS / CHoCH. A close through the last confirmed swing carries ~+4 pp
     against matched candles across three eras -- the second-strongest structural
     finding here. The BOS/CHoCH LABEL, however, does not replicate: CHoCH beat
     BOS in two eras and lost decisively in the third, so the state machine is
     bookkeeping rather than information. Both are drawn; neither is ranked. */
  try {
    if (app.auto.ms) {
      const r = detectMS(chart.bars, { strength: opts.strength ?? 3 });
      chart.setMsEvents(r.events.slice(-(app.auto.msMax ?? 12)));
    } else {
      chart.setMsEvents([]);
    }
  } catch { chart.setMsEvents([]); }

  /* Swing highs and lows with their HH / HL / LH / LL label -- the raw material
     everything else here is built on. Same `strength` as the rest of the AUTO
     stack, so what you SEE marked as a swing is literally what the trendline
     scorer, BOS/CHoCH and the Trend read panel are all working from. Giving
     this its own strength dial would let the picture drift from the engine. */
  try {
    chart.setSwings(app.auto.swings
      ? swingPoints(chart.bars, { strength: opts.strength ?? 3 })
      : []);
  } catch { chart.setSwings([]); }

  /* Supply/demand zones: the base an impulse departed from. A second, unrelated
     way of finding a zone -- measured at +5.55 / +6.87 / +3.83 pp against the
     same placebo, versus +5.50 / +5.00 / +5.09 for pivot clusters. Two
     detectors that barely overlap converging on the same ~5pp is why both are
     drawn rather than one replacing the other. */
  try {
    chart.setSdZones(app.auto.sdZones ? liveSDZones(chart.bars, chart.tf) : []);
  } catch { chart.setSdZones([]); }

  /* Regime episodes, from the chart's own frame. */
  try {
    chart.setSegments(buildSegments(chart.bars));
  } catch { chart.setSegments([]); }
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
  const off = status.health?.time_offset_ms;
  if (off != null) {
    panels.brokerOffsetMs = off;
    setZone(null, off);
    /* Remember it. The whole app displays broker time, so a boot that cannot
       reach the bridge would otherwise render every timestamp 3 hours out
       while looking entirely normal. Last known beats silently wrong. */
    save('bridge.offsetMs', off);
  }
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
  /* Margin level = equity / margin used. The number that actually predicts a
     margin call, which "margin free" does not: free margin can look healthy on
     a large account while the level sits near the stop-out threshold. Coloured
     against the usual broker bands, and '—' when nothing is open (MT5 reports
     0, which would otherwise render as a catastrophic 0%). */
  const lvl = $('#acLevel');
  const ml = Number(a.margin_level);
  if (!Number.isFinite(ml) || ml <= 0) {
    lvl.textContent = '—';
    lvl.className = '';
  } else {
    lvl.textContent = num(ml, 1) + '%';
    lvl.className = ml < 100 ? 'down' : ml < 200 ? 'warn' : 'up';
  }
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
  app.active.drawings = migrateDrawings(app.active.symbol);
  syncToolbar();
  loadTrendRead(app.active);          // the context ladder rises with the chart
  loadBars(app.active);
  persist();
}

/**
 * Show `sym` in a chart WITHOUT discarding what is already open.
 *
 * Picking an instrument used to overwrite the focused chart, which quietly
 * threw away its timeframe, studies and view -- the chart you were reading was
 * simply gone. Now it behaves like MT5's Market Watch: an existing tab for that
 * symbol is brought forward, and only if there is none is a new one opened.
 *
 * The new tab inherits the focused chart's timeframe, type and studies, because
 * "show me EURUSD" almost always means "the way I am looking at things now".
 */
function openSymbol(sym) {
  const existing = app.tabs.findIndex((t) => t.symbol === sym);
  if (existing >= 0) {
    const slot = app.slots.indexOf(existing);
    if (slot >= 0) {                       // already on screen: just focus it
      setActive(app.charts[slot]);
      wl.setActive(sym);
      loadSpec(sym);
      return;
    }
    showTab(existing);
    wl.setActive(sym);
    loadSpec(sym);
    return;
  }
  persist();
  const from = app.active ? app.active.state() : structuredClone(app.tabs[0]);
  app.tabs.push({ ...structuredClone(from), symbol: sym });
  save('tabs', app.tabs);
  showTab(app.tabs.length - 1);
  wl.setActive(sym);
  loadSpec(sym);
}

/* The rail headings double as timeframe pickers.
 *
 * They are not a second, independent timeframe: the panels describe the chart
 * you are looking at, so picking here MOVES THE CHART. Two timeframes on screen
 * -- one the chart's, one the panel's -- is how a reader ends up trading a
 * signal computed on a series they were not looking at. */
function wireRailTf() {
  for (const id of ['#trSym', '#sigSym']) {
    const btn = $(id);
    if (!btn) continue;
    btn.addEventListener('click', (e) => {
      if (!app.active) return;
      openMenu(e.currentTarget, [
        { kind: 'cap', label: 'Chart timeframe' },
        ...TF.map((tf) => ({
          label: TF_LABEL[tf], value: tf, checked: app.active.tf === tf,
        })),
      ], (v) => setTf(v));
    });
  }
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
      { kind: 'sep' },
      { label: 'Adaptive sensitivity', value: 'adaptive',
        checked: app.auto.adaptive, keepOpen: true,
        hint: 'per-instrument' },
      { label: 'Supply / demand zones', value: 'sdzones',
        checked: app.auto.sdZones, keepOpen: true,
        hint: 'impulse origin' },
      { label: 'Swing points (HH/HL/LH/LL)', value: 'swings',
        checked: app.auto.swings, keepOpen: true,
        hint: 'the pivots everything else is built on' },
      { label: 'BOS / CHoCH', value: 'ms',
        checked: app.auto.ms, keepOpen: true,
        hint: 'structure breaks' },
      { kind: 'sep' },
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
      else if (v === 'adaptive') app.auto.adaptive = !app.auto.adaptive;
      else if (v === 'sdzones') app.auto.sdZones = !app.auto.sdZones;
      else if (v === 'swings') app.auto.swings = !app.auto.swings;
      else if (v === 'ms') app.auto.ms = !app.auto.ms;
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

  $('#snapBtn')?.addEventListener('click', snapshot);

  /* Collapsible rails. The chart is the point of the page; on a laptop the two
     rails take 478px of it. Collapsing sets a grid variable rather than hiding
     the panels, so they keep their scroll position and their live updates and
     simply come back instantly. */
  const rails = [
    ['#sidebarToggle', '#sidebarStub', '#sidebar',
     'side-collapsed', 'side-peek', 'side.collapsed', '‹'],
    ['#rightbarToggle', '#rightbarStub', '#rightbar',
     'right-collapsed', 'right-peek', 'right.collapsed', '›'],
  ];
  for (const [bSel, sSel, pSel, cls, peekCls, key, ch] of rails) {
    const btn = $(bSel), stub = $(sSel), panel = $(pSel);
    if (!btn || !stub || !panel) continue;

    const apply = (collapsed) => {
      document.body.classList.toggle(cls, collapsed);
      if (!collapsed) document.body.classList.remove(peekCls);
      btn.textContent = ch;
      btn.setAttribute('aria-expanded', String(!collapsed));
      stub.setAttribute('aria-expanded', String(!collapsed));
      save(key, collapsed);
      /* Only a PIN changes the grid track, so only a pin needs a canvas
         resize. Peeking overlays and leaves the backing store alone -- doing
         this on hover would re-render every chart on every mouse-over. */
      setTimeout(() => app.charts.forEach((c) => c.resize()), 200);
    };

    /* The stub and the peeked panel are one hover region with a gap between
       them in event terms: leaving the stub to enter the panel fires a
       mouseleave before the mouseenter. A short grace window bridges that,
       otherwise the panel snaps shut under the cursor on its way in. */
    let shut = null;
    const hold = () => { clearTimeout(shut); shut = null; };
    const release = () => {
      hold();
      shut = setTimeout(() => document.body.classList.remove(peekCls), 140);
    };
    const peek = () => {
      hold();
      if (document.body.classList.contains(cls)) document.body.classList.add(peekCls);
    };
    for (const el of [stub, panel]) {
      el.addEventListener('mouseenter', peek);
      el.addEventListener('mouseleave', release);
    }
    stub.addEventListener('focus', peek);
    stub.addEventListener('blur', release);

    // clicking the spine PINS it open; the handle closes it again
    stub.addEventListener('click', () => apply(false));
    stub.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); apply(false); }
    });
    btn.addEventListener('click', () => apply(!document.body.classList.contains(cls)));
    apply(load(key, false) === true);
  }

  /* Balances are hidden until the icon is pressed, and the choice sticks. A
     balance is the one number on screen that is nobody else's business, and
     this app is often on a shared or recorded screen. */
  const acct = $('#acct'), acctBtn = $('#acctToggle');
  const showAcct = (on) => {
    acct.hidden = !on;
    acctBtn.setAttribute('aria-expanded', String(on));
    acctBtn.title = on ? 'Hide account balances' : 'Show account balances';
    save('acct.shown', on);
  };
  showAcct(load('acct.shown', false) === true);
  acctBtn.addEventListener('click', () => showAcct(acct.hidden));

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

/**
 * Save the focused chart as a PNG, TradingView-style.
 *
 * Canvas only, so the file is exactly what is rendered — candles, studies,
 * drawings, panels — with none of the surrounding page. The filename carries
 * symbol, timeframe and a UTC stamp so a folder of these stays sortable and
 * self-describing.
 */
function snapshot() {
  const c = app.active;
  if (!c) return;
  const url = c.snapshot({ scale: 2, ink: 'light' });
  if (!url) { toast('Could not capture the chart'); return; }
  const stamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
  const name = `${c.symbol}_${TF_LABEL[c.tf] || c.tf}_${stamp}.png`;
  const a = el('a', { href: url, download: name });
  document.body.append(a);
  a.click();
  a.remove();
  toast(`Saved ${name}`);
}

function wireKeys() {
  document.addEventListener('keydown', (e) => {
    if (search.isOpen) return;
    const typing = /input|textarea/i.test(e.target.tagName);
    if (typing) return;

    if (e.key === '/') { e.preventDefault(); search.open(); return; }
    if (e.key === 'Escape') {
      setTool('cursor');
      app.charts.forEach((c) => { c.cancelTool(); c.clearSelection(); });
      closeMenu();
      return;
    }
    /* Delete removes the SELECTED drawing (click one with the cursor tool
       first). Right-click still deletes what is under the pointer, and
       ⌫ in the cell toolbar still clears everything — three levels of
       destructiveness, each needing more intent than the last. */
    if (e.key === 'Delete' || e.key === 'Backspace') {
      if (app.active?.deleteSelected()) { persist(); e.preventDefault(); }
      return;
    }
    if (e.key >= '1' && e.key <= '8') { setTf(TF[Number(e.key) - 1]); return; }
    const tools = { h: 'hline', t: 'trend', y: 'ray', r: 'rect', g: 'fib' };
    if (tools[e.key.toLowerCase()]) { setTool(tools[e.key.toLowerCase()]); return; }
    if (e.key.toLowerCase() === 'f') { $('#indBtn').click(); return; }
    if (e.key.toLowerCase() === 'a') { app.active?.fitAll(); return; }
    if (e.key.toLowerCase() === 's' && !e.ctrlKey && !e.metaKey) { snapshot(); return; }
    if (e.key === '\\') { $('#sidebarToggle')?.click(); return; }
    if (e.key === ']') { $('#rightbarToggle')?.click(); return; }
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'w') {
      e.preventDefault();
      closeTab(app.slots[Math.max(0, app.charts.indexOf(app.active))]);
      return;
    }
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
    } catch { /* transient */ }
  },

  panels() {
    if (document.hidden || !app.active) return;
    trendRead.repaint(app.active.bars);
    panelTick = (panelTick + 1) % 4;
    if (panelTick === 0) signalPanel.repaint(app.active.bars);
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
  /* Repaint the panels from the bars already in hand. No fetch and no engine
     walk -- regime and structure cost ~3ms, and the signal engine's
     walk-forward is the expensive part at ~53ms, so it is throttled to every
     fourth pass rather than run every second. */
  every(4000, tick.panels);
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
const wl = new Watchlist({
  onPick: (sym) => openSymbol(sym),
  onRemove: (sym) => toast(`${sym} removed — press u to undo`, 5000),
});
const search = new SymbolSearch({
  onPick: (sym) => { wl.add(sym); openSymbol(sym); },
});

async function boot(refresh = false) {
  /* Seed the display clock from the last known broker offset BEFORE anything
     renders, so the first paint is not three hours out while /health is still
     in flight. tick.health() overwrites it a moment later with the measured
     value. */
  setZone('broker', load('bridge.offsetMs', 0));
  buildTfGroup();
  if (!refresh) { wireToolbar(); wireRailTf(); wireKeys(); }
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
