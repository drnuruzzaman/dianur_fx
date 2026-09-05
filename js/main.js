/* main.js — DiaNurFx workspace.
 *
 * Owns the chart grid, the polling loops against the read-only MT5 bridge, and
 * the toolbar. Everything the user arranges (layout, symbols, timeframes,
 * studies, drawings, watchlist) is persisted to localStorage so a reload comes
 * back to the same desk.
 */

import { api, status, setBase, base } from './api.js';
import { CHART_TYPES, Chart, DRAW_TOOLS, defaultSpan } from './chart/engine.js';
import { INDICATORS } from './chart/indicators.js';
// The chart draws from the SAME lifecycle engine the backtest uses
// (js/chart/tlengine.js is a port of sim/tl/engine.py, kept honest by
// tests/test_tl_parity.py). It previously used the batch scorer in
// trendlines.js, so the lines on screen were not the lines traded.
import { atrSeries, liveLines } from './chart/tlengine.js';
import { liveChannels } from './chart/channels.js';
import { calibrate } from './chart/sensitivity.js';
import { liveZones } from './chart/zones.js';
import { liveSDZones } from './chart/supplydemand.js';
import { detect as detectMS } from './chart/marketstructure.js';
import { swingPoints } from './chart/structure.js';
import { derived as derivedNews, loadSourced as loadNews, merge as mergeNews,
         within as newsWithin } from './chart/newsevents.js';
import { build as buildSegments } from './chart/segments.js';
import { $, $$, AUTO_DEFAULTS, BAR_COUNT, resolveAuto, TF, TF_LABEL, TF_MS, drop, el, load, money, num, save, setZone, signed, hydrateWorkspace } from './util.js';
import { closeMenu, openMenu, toast } from './ui/menu.js';
import { SymbolSearch, registerSymbolSearch } from './ui/search.js';
import { Watchlist } from './ui/watchlist.js';
import { Panels } from './ui/panels.js';
import { TrendRead, readTfs } from './ui/trendread.js';
import { SignalPanel } from './ui/signalpanel.js';
import { RulePanel } from './ui/rulepanel.js';
import { structuralTrail } from './chart/exittrail.js';
import { installTips } from './ui/tips.js';
import { installChat } from './ui/chat.js';

/* Sensitivity presets, expressed as engine parameters. Pivot strength is the
   real control: 2 finds minor swings, 6 finds structural ones. */
/* The strength that defines a MAJOR swing, taken from the menu's own `major`
   preset so the mark and the setting cannot drift apart. */
const MAJOR_STRENGTH = 6;

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
    /* THE DEFAULTS THE STRATEGY REPLAY ALSO USES, by request, so the two
       surfaces draw the same lines. They differed before -- the chart ran
       `normal` over three higher frames at three lines a side while the replay
       ran `normal` on its own frame at three -- and two pictures of the same
       market disagreeing is worse than either picture being wrong, because
       there is no way to tell from the screen which one to believe.
       js/ui/strategyreplay.js reads AUTO_DEFAULTS below rather than repeating
       these numbers. */
    ...AUTO_DEFAULTS,
    /* Per-instrument calibration (js/chart/sensitivity.js). Measured at
       +0.85 pp placebo-adjusted over three eras -- which makes the detector
       less bad rather than good, so it is offered rather than imposed. */
    adaptive: false,
    zones: true,           // horizontal support/resistance (pivot clusters)
    sdZones: false,        // impulse-origin supply/demand zones
    ms: false,             // BOS / CHoCH marks
    msMax: 12,             // most recent N events
    swings: false,         // HH / HL / LH / LL markers
    /* Both of these DREW UNCONDITIONALLY until now -- the flags existed in
       saved state but nothing read them, and there was no menu entry to change
       them either. On a 15m gold chart that put 6 channel rails and 12 regime
       segments on the canvas alongside the 4 lines the budget allows, so the
       thing the line budget exists to prevent was happening anyway, just from
       two other sources. Off by default: they answer questions the reader has
       not asked yet. */
    channels: false,       // parallel corridors around a line
    segments: false,       // regime episodes, drawn as sloped runs
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

/* PER-SYMBOL CHART SETTINGS, kept independently of any open tab.
 *
 * Drawings were already keyed by instrument, but timeframe, chart type, studies
 * and zoom lived only on the tab -- so closing a chart threw them away and
 * reopening the symbol gave you a default. Now the symbol remembers how you
 * look at it, and the tab is just a window onto that.
 *
 * `span` is included deliberately: how far you zoom out is part of how you read
 * an instrument, and a daily chart of gold wants a different span from 5m
 * EURUSD. */
const symKey = (sym) => `sym.${sym}`;

/* MERGES rather than replaces. The record holds fields the chart does not own
   -- `sens` is set from the menu, not from chart state -- and a wholesale
   overwrite silently dropped them on the next repaint. */
function saveSymbolSettings(chart) {
  const st = chart.state();
  const prev = load(symKey(st.symbol), null) || {};
  save(symKey(st.symbol), {
    ...prev,
    tf: st.tf, type: st.type, studies: st.studies, span: st.span,
    priceLock: st.priceLock || null,
  });
}

/* SENSITIVITY IS PER INSTRUMENT.
 *
 * It was one global, so picking `Major structure` to read gold also re-read
 * every FX pair at strength 6 -- and strength is not a preference, it is a
 * claim about how big a swing has to be before it counts. Gold at 42 ATR and
 * EURUSD at 0.0008 do not answer that question the same way.
 *
 * `app.auto.sens` stays as the DEFAULT for an instrument that has never been
 * set, so nothing changes until you choose. */
/* THE WHOLE AUTO TL MENU IS PER INSTRUMENT.
 *
 * It started as one global, then sensitivity alone was split out -- which left
 * the menu half per-symbol and half not, a worse state than either. Every entry
 * in it is now a claim about ONE instrument: how big a swing has to be, how
 * many lines are worth drawing, which frames are worth projecting from. Gold at
 * 42 ATR and EURUSD at 0.0008 do not answer any of those the same way.
 *
 * `app.auto` is the DEFAULT for an instrument never configured, and stays the
 * thing the menu falls back to, so nothing moves until you choose. Overrides
 * live in the per-symbol record under `auto`.
 */
/* Overrides as a map of timeframe -> settings, with the legacy shape handled.
 *
 * The first version keyed on the instrument alone, which is already wrong for
 * the reason the instrument split was right: strength 3 is the sensible read on
 * H1 and says nothing about what M15 or D1 want. Measured on XAUUSD H1, `major`
 * turns HALF its BOS marks into sub-quarter-ATR closes while `normal` is the
 * strength every backtest on record was run at -- and neither fact transfers to
 * another frame.
 */
function autoByTf(symbol) {
  const v = load(symKey(symbol), null) || {};
  const a = v.auto;
  if (!a || typeof a !== 'object') return {};
  /* LEGACY: a flat settings object from when overrides were per-instrument.
     It was chosen while the symbol was on `v.tf`, so that is the frame it
     belongs to -- attributing it to every frame would spread a decision the
     reader made once. */
  if ('sens' in a || 'on' in a || 'maxLines' in a) return { [v.tf || '15m']: a };
  return a;
}

/* Delegates to util.js `resolveAuto` so js/ui/strategyreplay.js resolves the
   IDENTICAL settings -- the two surfaces drew different lines while each had
   its own copy of this logic. */
function autoFor(symbol, tf) {
  if (!symbol || !tf) return app.auto;
  return resolveAuto(symbol, tf, app.auto);
}

/** The auto settings of the chart in front of you: its symbol AND its frame. */
function activeAuto() {
  return app.active ? autoFor(app.active.symbol, app.active.tf) : app.auto;
}

function setAutoFor(symbol, tf, patch) {
  if (!symbol || !tf) { Object.assign(app.auto, patch); save('auto', app.auto); return; }
  const prev = load(symKey(symbol), null) || {};
  const byTf = autoByTf(symbol);
  const cur = { ...app.auto, ...(byTf[tf] || {}) };
  save(symKey(symbol), { ...prev, auto: { ...byTf, [tf]: { ...cur, ...patch } } });
}

/* Price locks as they were on disk AT STARTUP.
 *
 * Reading them at restore time does not work: `persist()` runs while the
 * workspace is being built, before any bars have arrived, and writes the fresh
 * chart's empty lock over the saved one. By the time `loadBars` looked, the
 * value it wanted had already been erased by its own app. Snapshotting once at
 * module load is the only point that is guaranteed to be before that. */
const BOOT_LOCKS = (() => {
  const out = new Map();
  try {
    for (const k of Object.keys(localStorage)) {
      if (!k.startsWith('dnfx.sym.')) continue;
      const v = JSON.parse(localStorage.getItem(k) || 'null');
      if (v && v.priceLock) out.set(k.slice('dnfx.sym.'.length),
        { lock: v.priceLock, tf: v.tf });
    }
  } catch { /* a corrupt entry just means no lock to restore */ }
  return out;
})();

function loadSymbolSettings(sym) {
  const v = load(symKey(sym), null);
  return v && typeof v === 'object' && v.tf ? v : null;
}

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
      /* Scrolling into history re-runs the AUTO stack AS OF the bar at the
         right edge -- see runAuto. 200ms so a drag recomputes when it settles
         rather than on every frame; one refit is ~19ms per source and there can
         be five of them. */
      onView: (ch) => {
        if (autoFor(ch.symbol, ch.tf).on) computeAuto(ch, 200);
        if (ch === app.active) queuePanelRead(ch);
        /* Scrolled INTO the old edge, tested by where the RIGHT edge is.
           `fitAll` leaves the right edge at the live bar, so it never asks for
           more; panning left moves it back, and that is the gesture that means
           "show me earlier". The previous test compared span to bar count,
           which stopped the fetch after fitAll -- correctly -- and then also
           stopped it when the reader panned left FROM that view, which was the
           whole point of having it. */
        if (ch.wantsHistory
          || (ch.view.right < ch.bars.length - 1
            && ch.i0 < Math.max(50, ch.view.span * 0.25))) extendHistory(ch);
      },
    });
    chart.view.span = state.span || defaultSpan(state.tf || '15m');
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
  /* Per-symbol settings, written for every rendered chart. The ACTIVE chart is
     written LAST so it wins: with the same symbol open in two cells on
     different timeframes there is no single right answer, and "the one you are
     looking at" is the least surprising tiebreak. */
  for (const c of app.charts) if (c !== app.active) saveSymbolSettings(c);
  if (app.active) saveSymbolSettings(app.active);
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
/* Moved to js/util.js: js/ui/strategyreplay.js fetches its higher frames with
   the same counts, and two copies of this table is two charts drawing lines
   fitted on different amounts of history. */

/* Scrolling past the oldest loaded bar showed blank, because the initial fetch
   is all there ever was -- 1200 bars on 4h is nine months, and there was no
   path to more. The counts above stay small so a chart OPENS fast; history is
   fetched on demand when you actually scroll into it.

   The cap is generous because detection cost does not scale with the array:
   `detectTrendlines` reads only the last `window` bars, zones use their own
   lookback, and the swing walk is O(n) but trivially so. What does scale is
   memory and the as-of slice, hence a ceiling rather than none. */
const MAX_BARS = 20000;
const extending = new WeakSet();

/**
 * Double the loaded history for this chart, keeping the reader in place.
 *
 * Prepending shifts every bar INDEX, and the view is expressed in indices, so
 * `view.right` has to move by the same delta or the chart jumps to a different
 * era the moment more data arrives.
 */
async function extendHistory(chart) {
  if (extending.has(chart) || !chart.bars.length) return;
  const cur = chart.bars.length;
  const want = Math.min(cur * 2, MAX_BARS);
  if (want <= cur) return;                       // already at the ceiling
  extending.add(chart);
  try {
    const payload = await api.bars(chart.symbol, chart.tf, want);
    const got = (payload && payload.bars) ? payload.bars.length : 0;
    if (got > cur) {
      const right = chart.view.right;
      chart.setData(payload);
      chart.view.right = right + (got - cur);    // same bars under the cursor
      computeAuto(chart, 0);
      chart.draw();
    }
  } catch { /* leave the chart as it is; the next pan can try again */ }
  finally { extending.delete(chart); }
}

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
    /* AFTER setData, not before. `setData` calls `resetView` on the first
       payload and resetView clears the price lock -- correctly, because a
       timeframe change should re-scale -- so a lock restored at construction
       would be wiped by the data arriving. Applied only when the saved
       timeframe matches, since a scale set on M15 means nothing on D1. */
    const boot = BOOT_LOCKS.get(chart.symbol);
    if (boot && boot.tf === chart.tf) {
      chart.view.priceLock = boot.lock;
      BOOT_LOCKS.delete(chart.symbol);      // restore once, not on every refetch
      saveSymbolSettings(chart);            // put it back on disk
    }
    adoptResolved(chart);
    /* The zone overlay quotes a money amount for the target and the stop, and
       needs the contract's tick value to do it. Attached here rather than
       fetched in the renderer: the chart is a drawing surface and should not
       be making network calls mid-frame. Rows without it simply omit the
       amount and still show the price and the percentage. */
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
const rulePanel = new RulePanel($('#rulepanel'), $('#rpSym'));
/* A debug handle, the same convenience window.dnfx gives the live app. */
window.dnfxRule = rulePanel;
let panelTick = 0;
let panelReadTimer = null;

/** Same 200ms settle the AUTO stack uses, for the same reason. */
function queuePanelRead(chart) {
  clearTimeout(panelReadTimer);
  panelReadTimer = setTimeout(() => loadTrendRead(chart), 200);
}
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
  const { scrolled, ownBars, asOfT, cutHtf } = asOfCut(chart);

  const series = new Map();
  trendRead.update(symbol, series, tfs, { execTf: tf });   // pending rows first
  await Promise.all(tfs.map(async (t) => {
    /* The chart's own bar array for the execution frame, not a second fetched
       copy. `applyQuote` advances chart.bars tick by tick, while getSeries
       caches for up to a quarter of a bar interval -- so the panel was reading
       a snapshot that could be minutes stale while the chart beside it moved.
       Higher frames still come from the cache: they change slowly and are
       shared across cells. */
    if (t === tf && ownBars.length) { series.set(t, ownBars); return; }
    try { series.set(t, cutHtf(await getSeries(symbol, t))); }
    catch { /* leave pending */ }
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
        params: SENS[autoFor(symbol, t).sens] || SENS.normal,
        minDraw: autoFor(symbol, t).minDraw ?? 70,
      }));
    }
    catch { /* a frame that cannot be detected on simply contributes nothing */ }
  }

  trendRead.update(symbol, series, tfs, {
    lines,
    digits: (app.spec && app.spec.digits != null) ? app.spec.digits : 3,
    execTf: tf,
    asOf: scrolled ? asOfT : null,
  });
  /* The chart's own frame, not tfs[0]: on an H4 chart the ladder starts at 1h,
     and feeding those bars here labelled "H4" would compute the whole signal
     engine on a series the user is not looking at. */
  signalPanel.update(symbol, TF_LABEL[tf] || tf, series.get(tf),
                     scrolled ? asOfT : null);
  /* Same frame as the signal engine, for the same reason: the rule was
     validated on 4h gold and running it on a series the user is not looking at
     would show levels that belong to a different chart. */
  rulePanel.update(symbol, TF_LABEL[tf] || tf, series.get(tf), {
    tf,                                  // the bridge's vocabulary, not the label
    live: !scrolled,                     // so the forming bar is dropped
    asOf: scrolled ? asOfT : null,
    digits: (app.spec && app.spec.digits != null) ? app.spec.digits : 2,
  });
  /* AFTER the update, not before: rulePanel.sig is what update() recomputes, so
     drawing first paints the previous bar's signal onto the current chart. */
  paintRuleSignal(app.active, rulePanel.sig);
  app.active.draw();
}

async function loadSpec(symbol) {
  try {
    /* The Contract panel is gone, but the spec is still read: `digits` is what
       formats the Invalidation price in the Trend read, and getting that wrong
       shows 1.16 where the instrument quotes 1.16393. */
    app.spec = await api.spec(symbol);
  } catch { app.spec = null; }
  /* The rule panel prices its levels at 0.01 lots and needs the contract to do
     it. Attached rather than fetched there: the panel redraws on every tick and
     should not be making network calls to label a row. */
  rulePanel.spec = app.spec;
  if (rulePanel.sig) rulePanel.render();
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
  const auto = autoFor(chart.symbol, chart.tf);
  const rank = TF.indexOf(chart.tf);
  const out = auto.own ? [chart.tf] : [];
  for (const tf of auto.htf) {
    if (TF.indexOf(tf) > rank) out.push(tf);
  }
  return out;
}

/* AS OF THE RIGHT EDGE, not as of now.
 *
 * The detectors used to run on the full series regardless of where the chart
 * was scrolled, so panning back through history showed TODAY's lines drawn over
 * year-old bars. That is unfalsifiable by construction: the lines were fitted
 * knowing what the bars to their right did, so of course they looked well
 * placed. You could not use the chart to check the detector.
 *
 * The visible right edge is treated as "the present" instead. Slice the series
 * there and the chart draws what the engine WOULD have drawn standing on that
 * bar, with no knowledge of anything after it -- the same rule the backtests
 * run under, which makes scrolling a genuine test rather than a tour of
 * hindsight.
 *
 * Shared by the AUTO stack and the side panels, so the two cannot disagree
 * about what "now" means -- which they did, visibly, when only the chart was
 * cut and the panels went on reading the live edge beside it.
 *
 * At the live edge the slice is the whole array and nothing changes.
 */
/* How far the right edge must travel before the detectors re-run.
 *
 * Re-detecting on EVERY bar of a pan made the chart unusable to look at:
 * dragging 100 bars left changed the channel set at all five sample points --
 * two corridors, then the same two with their anchors moved 5 bars, then one
 * different corridor, then three, then one, then none. The corridor you were
 * examining moved while you examined it.
 *
 * The jitter is not noise in the drawing, it is the detector genuinely
 * re-estimating from a sliding window, and each answer is correct at its own
 * instant. But 25 is the cadence the MEASUREMENTS use -- zone_remeasure.py and
 * zone_rank.py both re-detect every 25 bars -- so a chart refitting every bar
 * was showing something no backtest here has ever tested. Snapping to the same
 * grid makes the picture hold still while you read it AND makes it the picture
 * that was measured.
 *
 * The live edge is exempt and stays exact: "now" is a real bar, not a grid
 * point, and rounding it would make the newest bars stop updating the read.
 */
const ASOF_STEP = 25;

function asOfCut(chart) {
  const bars = chart.bars || [];
  if (!bars.length) {
    return { scrolled: false, ownBars: bars, asOfT: null, cutHtf: (b) => b };
  }
  const raw = Math.round(chart.view.right);
  const live = raw >= bars.length - 1;
  const asOf = live
    ? bars.length - 1
    : Math.max(60, Math.min(bars.length - 1,
      Math.floor(raw / ASOF_STEP) * ASOF_STEP));
  const scrolled = asOf < bars.length - 1;
  const ownBars = scrolled ? bars.slice(0, asOf + 1) : bars;
  const asOfT = ownBars[ownBars.length - 1].t;
  /* Higher timeframes are cut by TIME, not by index: 500 bars back on 15m is
     not 500 bars back on 4h, and slicing both by the same count would show a
     4h line built from bars the 15m chart has not reached yet. */
  const cutHtf = (b) => (scrolled ? b.filter((x) => x.t <= asOfT) : b);
  return { scrolled, ownBars, asOfT, cutHtf };
}

function computeAuto(chart, delay = 250) {
  clearTimeout(autoTimers.get(chart));
  autoTimers.set(chart, setTimeout(() => runAuto(chart), delay));
}

async function runAuto(chart) {
  // FIRST line: the on/off guard immediately below reads it
  const auto = autoFor(chart.symbol, chart.tf);
  if (!auto.on) {
    chart.setAutoLines([]); chart.setChannels([]); chart.setZones([]);
    chart.setSegments([]); chart.setSdZones([]);
    chart.setMsEvents([]); chart.setSwings([]); chart.setNewsMarks([]);
    return;
  }
  if (!chart.bars.length) return;

  const { scrolled, ownBars, asOfT, cutHtf } = asOfCut(chart);
  // the engine keeps its own holding pool and offers its best few; the budget
  // below then keeps the best across all source timeframes
  const opts = { ...(SENS[auto.sens] || SENS.normal) };
  /* Draw down to 70 but keep flagging at the engine's measured 90 -- see
     liveLines(). Structure you can see, quality you can judge. */
  const minDraw = auto.minDraw ?? 70;

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
      const raw = source === chart.tf ? chart.bars : await getSeries(symbol, source);
      const bars = source === chart.tf ? ownBars : cutHtf(raw);
      // the chart may have moved on while a higher timeframe was fetching
      if (chart.symbol !== symbol || chart.tf !== tf) return;
      if (!bars || bars.length < 40) continue;
      /* Calibration is per SERIES: a 4h projection onto a 15m chart must be
         calibrated on 4h bars, or its prominence bar is measured in the wrong
         instrument's units. */
      const ll = liveLines(bars, source, {
        params: opts, minDraw,
        offerSensitivity: auto.adaptive ? offerSens(bars) : null,
      });
      for (const l of ll) lines.push(l);
    } catch { /* a missing higher timeframe just contributes nothing */ }
  }

  /* "Lines per side" is a budget for the CHART, not for each source, or four
     timeframes would quietly put two dozen lines on the screen. */
  // ACTIVE (retested since confirming) outranks merely CONFIRMED at equal score
  const rank = (l) => l.score + (l.status === 'ACTIVE' ? 2 : 0);
  lines.sort((a, b) => rank(b) - rank(a));

  /* TWO LINES THAT ARRIVE AT THE SAME PRICE ARE ONE LINE.
   *
   * The engine's own dedupe runs per SOURCE and asks whether two candidates
   * agree at both their endpoint and the midpoint of their own span. Lines that
   * converge on today from different angles pass it -- measured on gold H1, a
   * pair 0.21 ATR apart at the current bar and 2.96 ATR apart 250 bars back --
   * and lines from DIFFERENT source timeframes never meet that dedupe at all.
   * Both cases put a second line on top of the first exactly where the reader
   * is looking.
   *
   * So the last filter before drawing is about NOW: whatever a line did in the
   * past, its job on the right-hand edge is to say where the level is, and two
   * lines saying the same thing there are one signal drawn twice. The
   * better-scoring one survives.
   *
   * WHAT THIS DISCARDS is the convergence itself -- a wedge closing into the
   * current price reads as a single line here. That is a real loss, and it is
   * the price of the chart being legible; the geometry is still in the engine
   * for anything that wants to measure it.
   *
   * Applied BEFORE the budget, not after, so a dropped duplicate frees its slot
   * for a genuinely different line rather than simply leaving the chart emptier.
   *
   * The 0.35 ATR threshold is not delicate: across 12 symbol/timeframe cells,
   * 0.25 and 0.75 removed the same lines in all but one. Duplicates sit far
   * inside the band and real neighbours far outside it, so there is no edge for
   * the number to sit on.
   */
  const atrNow = (() => {
    try {
      const a = atrSeries(ownBars, 14);
      const v = a[a.length - 1];
      return Number.isFinite(v) && v > 0 ? v : null;
    } catch { return null; }
  })();
  const tNow = asOfT;
  /* NEAR ENOUGH TO MATTER, MEASURED ON THIS CHART.
   *
   * The engine drops a line "the market has walked away from" at
   * `maxDistanceAtr`, and computes that distance in the ATR of the series it
   * detected on. For the chart's own frame that is the same thing. For a
   * projected line it is not, and the gap is enormous -- measured on XAUUSD H4:
   *
   *     source   its own ATR   its allowance, in CHART ATR
   *     4h            42.4          10.0
   *     1d           100.2          23.6
   *     1w           223.6          52.7
   *
   * So a weekly line may sit FIFTY-TWO chart-ATRs from price and still pass its
   * own proximity test. That is what put twelve lines on an H4 gold chart with
   * the nearest one 6.3 ATR (270 points) away and the furthest 13.4 -- lines
   * crowding the axis, none of them reachable, which reads as the detector
   * being broken when it is the units that are.
   *
   * Same family as `max_distance_atr` in zones.py and `dedupeAtr` in
   * channels.js: a fixed ATR budget means different things at different scales.
   *
   * The threshold is the engine's OWN detection threshold, applied in the
   * chart's units, so every source obeys the rule the chart's own frame already
   * obeys. When that leaves nothing, nothing is drawn -- an empty chart is the
   * honest answer when price has just travelled into open space, and is better
   * than a dozen lines that cannot be reached.
   */
  const DRAW_MAX_ATR = 5;          // = trendlines.js DEFAULTS.maxDistanceAtr
  /* The AS-OF close, not today's. Reading `chart.bars` here compared a line
     valued at `tNow` against the price at the LIVE edge, so scrolling back made
     every line look astronomically far away and the whole set vanished -- on
     M15 gold, 1000 bars back is ~300 points, which is ~40 ATR, against a 5 ATR
     budget. Everything else in this block already uses the cut (`tNow`,
     `atrNow`); this one line did not, and that is exactly the class of bug the
     shared `asOfCut` exists to prevent. */
  const spot = ownBars.length ? ownBars[ownBars.length - 1].c : NaN;

  const distinct = [];
  for (const l of lines) {
    if (atrNow && tNow != null) {
      const v = l.valueAt(tNow);
      if (Number.isFinite(v) && Number.isFinite(spot)
        && Math.abs(v - spot) / atrNow > DRAW_MAX_ATR) continue;
      const dup = distinct.some((k) => k.kind === l.kind
        && Math.abs(k.valueAt(tNow) - v) / atrNow < 0.35);
      if (dup) continue;
    }
    distinct.push(l);
  }

  const budget = { support: auto.maxLines, resistance: auto.maxLines };
  const keep = distinct.filter((l) => (budget[l.kind]-- > 0));
  chart.setAutoLines(keep);

  /* Channels come from the chart's OWN line population only. A corridor
     projected down from 4h onto a 15m chart would be drawn from rails whose
     containment was measured on 4h bars -- a different claim than the band
     appears to make. Detected from the engine's own population rather than the
     budgeted `keep`, because a rail dropped by the per-side line budget is
     still a real rail and its corridor should not vanish with it. */
  try {
    /* `live` marks the corridors the current detection returned. It is what
       "still forming" means -- a bar-count test on `tEnd` cannot say it,
       because `tEnd` is the last PIVOT and pivots confirm several bars late:
       measured on 15m gold, live corridors were ending 22 bars back and a
       two-bar tolerance called every one of them finished. The renderer uses
       this to decide how far right a corridor may be drawn. */
    if (!auto.channels) {
      chart.setChannels([]);
    } else {
      const now = liveChannels(ownBars, chart.tf, { params: opts });
      for (const ch of now) ch.live = true;
      chart.setChannels(now);
    }
  } catch { chart.setChannels([]); }

  /* NFP, as a vertical mark. Derived from the schedule -- see
     js/chart/newsevents.js for why it is dashed and what would make it solid.
     Built from the DRAWN window rather than the whole series, because a decade
     of monthly marks off-screen costs the same as the twelve on it. */
  try {
    const w = ownBars;
    if (!w.length) chart.setNewsMarks([]);
    else {
      /* The sourced file arrives asynchronously and is cached after the first
         call, so this is one fetch for the whole app. The derived marks are
         drawn either way; the file only upgrades those it covers. */
      loadNews().then((file) => {
        if (chart.symbol !== symbol || chart.tf !== tf) return;
        const a = w[0].t, b = w[w.length - 1].t;
        chart.setNewsMarks(mergeNews(derivedNews(a, b), newsWithin(file, a, b)));
        chart.draw();
      }).catch(() => {});
      chart.setNewsMarks(mergeNews(derivedNews(w[0].t, w[w.length - 1].t), []));
    }
  } catch { chart.setNewsMarks([]); }

  /* Zones come from the chart's own pivots at its own timeframe. They are
     horizontal by definition, so unlike trendlines there is nothing to project
     from a higher frame -- a 4h zone and a 15m zone at the same price are the
     same band, and drawing both would double-count one level. */
  try {
    chart.setZones(auto.zones
      ? liveZones(ownBars, chart.tf, { strengthPivots: opts.strength ?? 3 })
      : []);
  } catch { chart.setZones([]); }

  /* BOS / CHoCH. A close through the last confirmed swing carries ~+4 pp
     against matched candles across three eras -- the second-strongest structural
     finding here. The BOS/CHoCH LABEL, however, does not replicate: CHoCH beat
     BOS in two eras and lost decisively in the third, so the state machine is
     bookkeeping rather than information. Both are drawn; neither is ranked. */
  try {
    if (auto.ms) {
      const r = detectMS(ownBars, { strength: opts.strength ?? 3 });
      /* HOW FAR the close actually went past the level, in ATR.
       *
       * The detector's rule is a close through the last swing with
       * `bufferAtr: 0`, so a close a tenth of a point beyond a level earns the
       * same BOS label as one that displaced a full ATR. Measured on XAUUSD:
       * about a third of BOS marks are closes under 0.25 ATR through, on both
       * M15 and H1, and only 11-22% clear 1.0 ATR.
       *
       * 1.0 ATR is not a new number -- it is `DISPLACEMENT_V1.displacement_atr`,
       * the threshold behind the only cell in this project that ever showed
       * positive expectancy. So the chart can say which of its own marks are
       * the event the backtests trade, rather than implying they all are. */
      const a = atrSeries(ownBars, 14);
      for (const e of r.events) {
        const v = a[e.i];
        e.dispAtr = (v > 0 && ownBars[e.i])
          ? Math.abs(ownBars[e.i].c - e.level) / v : NaN;
      }

      /* INTERNAL vs EXTERNAL STRUCTURE.
       *
       * One structure layer cannot answer "hasn't price already broken that
       * level?", because it has only one notion of which levels count. A break
       * of a three-bar pivot and a break of the swing that defined the last
       * leg are both printed `BOS`, and the reader is left to guess which one
       * the chart meant.
       *
       * EXTERNAL is a break of a swing that survives the MAJOR window -- the
       * same `MAJOR_STRENGTH` that puts rings on swing points, so the two
       * agree by construction: an external break is a break of a RINGED swing.
       * INTERNAL is everything else: real structure inside the leg.
       *
       * The second pass runs on the same `ownBars`, so it inherits the as-of
       * cut. Matching is by the broken LEVEL's bar (`levelI`) rather than by
       * the breaking bar, because the two layers can notice the same break on
       * different bars while agreeing entirely about which swing was taken. */
      if ((opts.strength ?? 3) < MAJOR_STRENGTH) {
        const major = detectMS(ownBars, { strength: MAJOR_STRENGTH });
        const majorLevels = new Set(major.events.map((e) => e.levelI));
        for (const e of r.events) e.external = majorLevels.has(e.levelI);
      } else {
        for (const e of r.events) e.external = true;
      }
      chart.setMsEvents(r.events.slice(-(auto.msMax ?? 12)));
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
    /* TWO PASSES, ONE VOCABULARY.
     *
     * At `fine` every turn of three bars is a swing, and a few hundred
     * identical dots say nothing about which of them the market actually
     * pivoted on. The app already has a word for the difference -- `major`
     * sensitivity is strength 6 -- so a swing is MAJOR when it also survives
     * that window. No new parameter, and the mark means exactly what picking
     * `Major structure` in the menu would show.
     *
     * The second pass runs on the same `ownBars`, so it inherits the as-of cut
     * and cannot see past the bar being drawn. It is cheap next to the
     * trendline walk.
     *
     * At `major` itself the two passes coincide and every swing is major --
     * which is the honest reading of that setting, not a bug.
     */
    const strength = opts.strength ?? 3;
    const swings = auto.swings ? swingPoints(ownBars, { strength }) : [];
    if (swings.length && strength < MAJOR_STRENGTH) {
      const major = new Set(
        swingPoints(ownBars, { strength: MAJOR_STRENGTH }).map((x) => x.i));
      for (const sw of swings) sw.major = major.has(sw.i);
    } else {
      for (const sw of swings) sw.major = true;
    }
    chart.setSwings(swings);
  } catch { chart.setSwings([]); }

  /* Supply/demand zones: the base an impulse departed from. A second, unrelated
     way of finding a zone -- measured at +5.55 / +6.87 / +3.83 pp against the
     same placebo, versus +5.50 / +5.00 / +5.09 for pivot clusters. Two
     detectors that barely overlap converging on the same ~5pp is why both are
     drawn rather than one replacing the other. */
  try {
    chart.setSdZones(auto.sdZones ? liveSDZones(ownBars, chart.tf) : []);
  } catch { chart.setSdZones([]); }

  /* Regime episodes, from the chart's own frame. */
  try {
    chart.setSegments(auto.segments ? buildSegments(ownBars) : []);
  } catch { chart.setSegments([]); }
}

/* THE RULE'S SIGNAL ON THE CHART, from js/chart/donchian.js -- and the ONLY
   thing that shades this chart.

   Everything order-derived has been taken off it: the risk/reward blocks drawn
   from open positions, and the TP bands drawn from a position's stop. Both were
   removed for the same reason -- a suggestion that looks like a filled order is
   the worst confusion this app could offer. Broker positions now get dashed
   lines and nothing else, so a block of colour always means "the rule says".

   The zone appears whether or not anything is open at the broker, because it
   describes what the RULE would risk, not what you hold.

   THE 2R REFERENCE BAND IS GONE. It shaded entry to twice the risk, which was
   the last of the fixed-R ladder still drawing after the rest was removed --
   and on a chart whose forward marks are structural it could only ever
   disagree with them. The levels ahead are marked on the price scale by
   setRuleTargets; the exit is one line from setTrail. */
function paintRuleSignal(chart, sig) {
  if (!sig || !sig.series) {
    chart.setRuleZone(null); chart.setTrail(null); chart.setRuleTargets(null);
    return;
  }
  const held = sig.position;
  const pend = (!held && sig.pending && sig.pending.side !== 0) ? sig.pending : null;
  if (!held && !pend) {
    chart.setRuleZone(null); chart.setTrail(null); chart.setRuleTargets(null);
    return;
  }

  const long = (held ? held.side : pend.side) > 0;
  /* A pending signal has no fill yet: the rule decided on THIS close and the
     order goes in at the next open, which does not exist. The signal close is
     the only price known, and it is what the stop was measured from. */
  const entry = held ? held.entryPrice : pend.signalPrice;
  const stop = held ? held.stop : pend.stop;
  const risk = Math.abs(entry - stop);

  /* THE LEVELS COME FROM THE PANEL, which computed them from the same closed
     bars it walked the rule over. Read BEFORE the zone is set, because the
     zone's green block is drawn to the first of them. */
  const levels = rulePanel.levels || [];

  chart.setRuleZone({
    entry,
    stop,
    /* THE FAR EDGE OF THE GREEN BLOCK IS TP1 ITSELF -- the same price the scale
       is marked at and the panel lists, so the block and the tag can never
       disagree. It was a fixed 2R once and could only ever miss. Zero when
       nothing is ahead, and the block is simply not drawn: clear air is drawn
       as clear air rather than as a band of arbitrary depth. */
    ref: levels.length ? levels[0].price : 0,
    atrMult: (sig.params && sig.params.atrMult) || 2,
    i0: held ? held.entryI : pend.signalI,
    label: held ? (long ? 'RULE holding LONG' : 'RULE holding SHORT')
                : (long ? 'RULE would BUY' : 'RULE would SELL'),
  });

  /* THE MOVING EXIT, TRACED -- ONE LINE, AND IT IS THE ONE THAT WILL FIRE.
   *
   * The trade has two moving exits now: the rule's own channel and the
   * structural trail. This draws the EFFECTIVE one, the tighter of the two at
   * every bar, which is by definition the level that closes the trade. Violet
   * and dashed because it is the trail most of the time; where the channel is
   * tighter the same line traces the channel instead.
   *
   * NOT JUST THE TRAIL. The channel still takes roughly a quarter of the exits,
   * usually early on before any structure has formed behind the trade -- so a
   * line showing only the trail would sit at a level the trade sometimes does
   * not exit at, and draw nothing at the level it does.
   *
   * No label: the Donchian panel names all three levels and marks which binds,
   * and a tag floating over price is the thing that made the zone labels worth
   * removing. The strategy replay still passes one -- there the line has no
   * panel beside it to explain what it is. */
  if (held) {
    const lvl = long ? sig.series.exitLo : sig.series.exitHi;
    const closes = rulePanel.bars ? rulePanel.bars.map((b) => b.c) : [];
    const pts = [];
    let trail = null;
    for (let k = held.entryI; k < sig.bars; k++) {
      if (closes.length) {
        const cand = structuralTrail({
          side: held.side, i: k, view: rulePanel.bars,
          series: sig.series, close: closes,
          /* As in the replay: the break-even floor is measured from the fill,
             so the drawn line must be told which fill it belongs to. */
          entryPrice: held.entryPrice,
        }, { tf: rulePanel.rawTf, cell: `${chart.symbol}|${rulePanel.rawTf}` });
        if (Number.isFinite(cand)) {
          const better = trail === null || (long ? cand > trail : cand < trail);
          if (better) trail = cand;
        }
      }
      const ch = lvl[k];
      let eff = null;
      if (Number.isFinite(ch) && Number.isFinite(trail)) {
        eff = long ? Math.max(ch, trail) : Math.min(ch, trail);
      } else if (Number.isFinite(ch)) eff = ch;
      else if (Number.isFinite(trail)) eff = trail;
      if (Number.isFinite(eff)) pts.push({ i: k, price: eff });
    }
    chart.setTrail(pts.length > 1
      ? { points: pts, color: '#c07cf0', width: 1.4, dash: [4, 3] } : null);
  } else {
    chart.setTrail(null);
  }

  /* THE LEVELS AHEAD, from the bar the rule decided on -- not from the last
     bar. A trade opened 700 bars ago was planned against the structure visible
     THEN, and re-deriving it from today's chart would silently rewrite the plan
     every time a new high printed. */
  /* THE MONEY. Two sources and they mean different things, so the label says
     which: `/signal` gives the lots Python would actually take, and when it has
     not answered -- it returns no size for a position already held -- the value
     falls back to ONE LOT and is marked `/lot`.
   *
   * PER LOT IS NOT A GUESS. It is a property of the CONTRACT: tick value over
   * tick size is what one lot is worth per unit of price, and it needs no
   * equity and no FX rate. That is the whole reason it is a safe fallback,
   * where inventing a position size would not be. */
  /* THE LEVELS, PLUS WHAT THE TAG NEEDS TO PRICE THEM. `entry` is the rule's
     own fill -- both the distance and the money on a tag are measured from it,
     never from the current price -- and the contract decides the money. The
     panel lists the same three numbers from the same two sources, so the tag
     and the row can never disagree. */
  const spec = app.spec;
  chart.setRuleTargets({
    levels,
    entry,
    /* THE STOP TRAVELS WITH THE LEVELS because it is what sizes them: money on
       a tag is now the level's R multiple against a stated account, so a tag
       without a stop distance has no money to print. */
    stop,
    tickSize: spec ? (spec.tick_size || spec.point || 0) : 0,
    tickValue: spec ? (spec.tick_value || 0) : 0,
  });
}

/* THE TARGETS ARE STRUCTURE, NOT R MULTIPLES.
 *
 * They were a 2R / 3.5R / 5R ladder, which has one virtue -- it is computable
 * from any entry -- and one fatal defect: it is the same three numbers on every
 * chart, and it is blind to what is actually in front of the trade. Two longs a
 * hundred points apart got identical targets when one had clear air above it
 * and the other was sitting under a supply zone that had turned price back four
 * times.
 *
 * WHAT REPLACED IT is the first few things price actually has to get through,
 * from js/chart/levels.js: swing highs, S/R levels, supply and demand bases,
 * trendlines projected forward, and the unbroken swing whose break would be the
 * next BOS -- plus the levels earlier BOS and CHoCH events happened at. Nearest
 * first, near-duplicates collapsed, each carrying the name of what it is.
 *
 * CAUSAL, like everything else: every detector sees bars up to the signal bar
 * and no further. A supply zone fitted with tomorrow's bars in view stops
 * today's rally with uncanny precision and nothing about the chart looks wrong.
 *
 * THE RULE STILL HAS NO TAKE-PROFIT, and these are still not one -- it exits on
 * the stop or the moving channel. They are where the trade is going to meet
 * resistance, which is the thing an open R of +7.8 means nothing without. */

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
  /* The rule panel names the unit its money figures are quoted in, and the
     account is the only place that knows it. */
  rulePanel.currency = c;
}

/* REALISED profit for the broker's current day and month.
 *
 * Realised, not floating: this counts CLOSED trades only. The open runner is
 * already reported next door as "Floating", and adding the two together in one
 * number would let an unclosed position flatter a day that has not paid out.
 *
 * Boundaries are the BROKER's midnight, not yours. A trading day is the
 * server's day -- it is what rolls the swap and what the statement will agree
 * with -- and on a +3h server the two disagree for three hours every night,
 * which is exactly when a late New York session is still open.
 *
 * Balance operations are excluded. A deposit arrives as a deal carrying its
 * full amount in `profit`, so counting every deal would report funding the
 * account as a profitable day. Only `buy` and `sell` deals are trades; the
 * bridge maps every other MT5 deal type to its raw number, so this filter
 * drops balance, credit, charge and correction entries without needing to
 * enumerate them.
 */
function realisedWindows() {
  const off = panels.brokerOffsetMs || 0;
  const b = new Date(Date.now() + off);
  return {
    day: Date.UTC(b.getUTCFullYear(), b.getUTCMonth(), b.getUTCDate()) - off,
    month: Date.UTC(b.getUTCFullYear(), b.getUTCMonth(), 1) - off,
  };
}

function paintRealised(deals) {
  const w = realisedWindows();
  let day = 0, month = 0, seen = false;
  for (const d of deals || []) {
    if (d.side !== 'buy' && d.side !== 'sell') continue;
    /* same net as the History tab: gross, minus what the broker took */
    const net = (d.profit || 0) + (d.commission || 0) + (d.swap || 0);
    if (d.time_ms >= w.month) { month += net; seen = true; }
    if (d.time_ms >= w.day) day += net;
  }
  const put = (sel, v) => {
    const node = $(sel);
    if (!node) return;
    node.textContent = signed(v);
    node.className = v > 0 ? 'up' : v < 0 ? 'down' : '';
  };
  /* a month with no closed trades is a real zero, not missing data */
  put('#acDay', day);
  put('#acMonth', seen || day !== 0 ? month : 0);
}

/* ------------------------------------------------------------------ PIN
 *
 * WHAT THIS IS AND IS NOT.
 *
 * It stops someone reading your balance over your shoulder or off a recording.
 * It is NOT security. This is a local page: anyone sitting at this machine can
 * open devtools and clear the class, and no browser-side check survives that.
 * Treating it as protection against someone who HAS the machine would be a lie
 * about what it does, so it is built and described as a screen-share cover.
 *
 * Given that, two things are still worth doing properly:
 *   - the PIN is never stored. A salted SHA-256 goes to localStorage instead,
 *     so a glance at storage does not hand over a number people reuse.
 *   - it never leaves the machine. Nothing here touches the bridge or network.
 */
const PIN_KEY = 'ui.acctPin';
/* Deliberately slow. A four-digit PIN is only ten thousand possibilities, so a
   single SHA-256 -- which is what this used to be -- falls to a script in well
   under a second. PBKDF2 at this count costs roughly a quarter-second per
   guess, which is unnoticeable once when you type it and turns the full sweep
   into hours. It does not make a short PIN strong; it makes it not free. */
const PIN_ITER = 250000;

const hex = (buf) => [...new Uint8Array(buf)]
  .map((b) => b.toString(16).padStart(2, '0')).join('');

/* Storing a PIN you only ever need to CHECK calls for a one-way hash, not
   encryption: anything reversible needs a key, and a key kept beside the
   ciphertext on the same machine protects nobody. The salt is per-PIN, so two
   people choosing 1234 do not produce the same record. */
async function pinHash(pin, salt, iter = PIN_ITER) {
  const enc = new TextEncoder();
  const key = await crypto.subtle.importKey(
    'raw', enc.encode(pin), 'PBKDF2', false, ['deriveBits'],
  );
  const bits = await crypto.subtle.deriveBits(
    { name: 'PBKDF2', salt: enc.encode(salt), iterations: iter, hash: 'SHA-256' },
    key, 256,
  );
  return hex(bits);
}

/* Records written before PBKDF2 landed are plain salted SHA-256. They still
   verify, so an existing PIN keeps working, and are rewritten at the stronger
   setting the moment one is entered correctly. */
async function pinHashLegacy(pin, salt) {
  return hex(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(salt + '|' + pin)));
}

async function pinMatches(pin, rec) {
  if (!rec) return false;
  if (rec.iter) return await pinHash(pin, rec.salt, rec.iter) === rec.hash;
  return await pinHashLegacy(pin, rec.salt) === rec.hash;
}

/* The PIN and its recovery password are stored the same way and kept in the
   same record, so a change to one can never leave the other orphaned. Neither
   is recoverable FROM the record: both are one-way derivations. */
async function pinSave(pin, recovery) {
  const prev = pinStored();
  const salt = hex(crypto.getRandomValues(new Uint8Array(16)));
  const rec = { salt, iter: PIN_ITER, hash: await pinHash(pin, salt) };
  if (recovery) {
    const rsalt = hex(crypto.getRandomValues(new Uint8Array(16)));
    rec.rsalt = rsalt;
    rec.rhash = await pinHash(recovery, rsalt);
  } else if (prev && prev.rhash) {
    /* changing the PIN alone must not silently discard the way back in */
    rec.rsalt = prev.rsalt;
    rec.rhash = prev.rhash;
  }
  save(PIN_KEY, rec);
}

async function recoveryMatches(word, rec) {
  if (!rec || !rec.rhash) return false;
  return await pinHash(word, rec.rsalt, rec.iter || PIN_ITER) === rec.rhash;
}

function pinStored() {
  const v = load(PIN_KEY, null);
  return v && v.salt && v.hash ? v : null;
}

/** Ask for a PIN. Resolves true when it is accepted, false when cancelled. */
function askPin({ setting }) {
  return new Promise((resolve) => {
    const modal = $('#pinModal');
    const input = $('#pinInput');
    const confirm = $('#pinConfirm');
    const recovery = $('#pinRecovery');
    const err = $('#pinErr');
    const forget = $('#pinForget');
    const forgot = $('#pinForgot');

    /* The dialog SWITCHES MODE in place rather than closing and reopening.
       Resetting a forgotten PIN used to close the box, which read as the app
       cancelling on you at the exact moment you were trying to fix something.
       Now "Reset PIN" clears the stored hash and turns this same dialog into
       the set-a-new-one form, so the job you started is the job you finish. */
    let mode = setting;
    /* Three states, not two: enter the PIN, set a PIN, or enter the recovery
       password because the PIN is gone. Recovery reuses the main input rather
       than adding a fourth field -- one box, three meanings, always labelled. */
    let recovering = false;
    /* Changing an EXISTING PIN reads differently from setting the first one:
       there is something to lose, so the dialog says what cancelling does. */
    let changing = false;
    /* PROVING THE PIN IS THE POINT; changing it is a side errand.
       Once the current PIN has been entered correctly the gate has done its
       job, so backing out of the change must not send you round the loop to
       type the same number again. Cancelling then closes as a SUCCESS: the
       figures open, the PIN is untouched. */
    let verified = false;
    const render = (isSetting, isChange = false) => {
      mode = isSetting;
      changing = isChange;
      $('#pinTitle').textContent = recovering ? 'Recovery password'
        : !isSetting ? 'Enter PIN'
          : isChange ? 'Choose a new PIN' : 'Set a PIN';
      $('#pinNote').textContent = recovering
        ? 'Enter your recovery password to set a new PIN. The figures stay '
          + 'covered until the new PIN is saved.'
        : !isSetting
          ? 'Enter your PIN to show the account figures.'
        : isChange
          ? 'Your current PIN still works until a new one is saved — press '
            + 'Escape to keep it and go straight to the figures.'
          : 'Choose a PIN to cover the account figures, and a recovery password '
            + 'in case you forget it. Both stay on this machine and are never '
            + 'sent anywhere. This hides the numbers from the room, not from '
            + 'anyone using this computer.';
      confirm.hidden = !isSetting || recovering;
      /* asked for once, when the PIN is first created -- and again while
         setting a new one if there is still no way back on record */
      const rec = pinStored();
      recovery.hidden = recovering || !isSetting || !!(rec && rec.rhash);
      forget.hidden = isSetting || recovering;
      /* only offer "Forgot PIN?" when a recovery password actually exists */
      forgot.hidden = isSetting || recovering || !(rec && rec.rhash);
      err.hidden = true;
      input.value = ''; confirm.value = ''; recovery.value = '';
      input.type = recovering ? 'text' : 'password';
      input.placeholder = recovering ? 'recovery password' : '••••';
      input.maxLength = recovering ? 64 : 12;
      setTimeout(() => input.focus(), 0);
    };

    render(setting);
    modal.hidden = false;

    const close = (result) => {
      modal.hidden = true;
      input.value = ''; confirm.value = ''; recovery.value = '';
      document.removeEventListener('keydown', onKey, true);
      modal.removeEventListener('mousedown', onBack);
      forget.removeEventListener('click', onForget);
      forgot.removeEventListener('click', onForgot);
      resolve(result);
    };
    const fail = (msg) => {
      err.textContent = msg;
      err.hidden = false;
      /* Retrigger the shake: an animation only restarts if the class actually
         leaves and returns, and a second wrong PIN would otherwise sit still. */
      const box = modal.querySelector('.pin-box');
      box.classList.remove('pin-bad');
      void box.offsetWidth;
      box.classList.add('pin-bad');
    };

    const submit = async () => {
      const pin = input.value.trim();

      /* RECOVERY: prove the fallback, then set a new PIN. It never reveals the
         figures by itself -- it buys you the set-a-PIN form and nothing more,
         so a recovery password overheard once does not open the account
         without a new PIN being chosen and confirmed. */
      if (recovering) {
        const rec = pinStored();
        if (!pin) return fail('Enter your recovery password.');
        if (!await recoveryMatches(pin, rec)) {
          input.value = '';
          return fail('That recovery password does not match.');
        }
        verified = true;
        recovering = false;
        return render(true, true);
      }

      if (mode) {
        /* four digits is the shortest that is not immediately guessable by
           someone watching you type it once */
        if (pin.length < 4) return fail('Use at least 4 characters.');
        if (pin !== confirm.value.trim()) return fail('The two entries do not match.');
        const word = recovery.hidden ? '' : recovery.value.trim();
        /* longer than the PIN on purpose: it is the fallback for the fallback,
           and it is typed rarely enough that length costs nothing */
        if (!recovery.hidden && word.length < 8) {
          return fail('Recovery password: use at least 8 characters.');
        }
        if (!recovery.hidden && word === pin) {
          return fail('The recovery password must differ from the PIN.');
        }
        await pinSave(pin, word || null);
        return close(true);
      }
      const rec = pinStored();
      if (!rec) return close(true);                 // nothing set: nothing to check
      if (await pinMatches(pin, rec)) {
        /* quietly upgrade a legacy record now that the PIN is known good */
        if (!rec.iter) await pinSave(pin);
        return close(true);
      }
      input.value = '';
      fail('That PIN does not match.');
    };

    const onKey = (e) => {
      if (modal.hidden) return;
      if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); cancel(); }
      else if (e.key === 'Enter') {
        e.preventDefault(); e.stopPropagation();
        /* on the set form, Enter in the first box moves to the confirm box
           rather than submitting a half-filled pair */
        if (mode && !recovering && document.activeElement === input && !confirm.value) confirm.focus();
        else if (mode && !recovering && !recovery.hidden
                 && document.activeElement === confirm && !recovery.value) recovery.focus();
        else submit();
      }
    };
    /* Clicking the backdrop cancels; clicking INSIDE the box must not, or
       selecting the text you just typed would close the dialog. */
    const onBack = (e) => { if (e.target === modal) cancel(); };
    /* Cancelling out of a change is a non-event, but a silent one looks like
       the press did not register -- say plainly that nothing changed. */
    const cancel = () => {
      if (changing) toast('PIN unchanged.');
      close(verified);
    };
    /* CHANGING THE PIN COSTS THE OLD ONE.
       This started life as a one-press "Reset PIN" for a forgotten code, which
       quietly made the whole gate pointless: anyone who picked up the machine
       could press it, choose their own PIN and read the balance, without ever
       knowing the original. The easiest bypass must not be a button sitting on
       the lock screen.

       So it verifies against the stored hash first, reusing the PIN already
       typed in the box above -- the field is right there and asking for the
       same number in a second place would be theatre. */
    const onForget = async () => {
      const rec = pinStored();
      if (!rec) { render(true); return; }          // nothing set: nothing to prove
      const pin = input.value.trim();
      if (!pin) return fail('Type your current PIN first, then press Change PIN.');
      if (!await pinMatches(pin, rec)) {
        input.value = '';
        return fail('That PIN does not match.');
      }
      /* THE OLD PIN IS NOT DELETED HERE.
         It used to be, and that was destroy-before-replace: proving the current
         PIN wiped it, so changing your mind and pressing Escape left you with
         no PIN at all and the app asking you to set one. Cancelling must cost
         nothing. The record is only overwritten when a NEW PIN is accepted,
         which pinSave() does in one step. */
      verified = true;
      render(true, true);
    };

    const onForgot = () => {
      const rec = pinStored();
      if (!rec || !rec.rhash) return fail('No recovery password was set.');
      recovering = true;
      render(false);
    };

    document.addEventListener('keydown', onKey, true);
    modal.addEventListener('mousedown', onBack);
    forget.addEventListener('click', onForget);
    forgot.addEventListener('click', onForgot);
  });
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
  $('#autoBtn').classList.toggle('active', activeAuto().on);
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
  /* A symbol you have opened before comes back the way you left it -- its own
     timeframe, type, studies and zoom. Only a symbol with no history inherits
     the focused chart, because "show me GBPUSD" then has nothing better to
     mean than "the way I am looking at things now". */
  const remembered = loadSymbolSettings(sym);
  const from = app.active ? app.active.state() : structuredClone(app.tabs[0]);
  app.tabs.push(remembered
    ? { symbol: sym, tf: remembered.tf, type: remembered.type,
        studies: structuredClone(remembered.studies || []),
        span: remembered.span }
    : { ...structuredClone(from), symbol: sym });
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
  for (const id of ['#rpSym', '#trSym', '#sigSym']) {
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
      ...['ema', 'sma', 'bb', 'vwap', 'donchian'].map((k) => ({ label: INDICATORS[k].label, value: k, checked: c.hasStudy(k) })),
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
    const a = activeAuto();     // the chart in front of you, not a global
    const items = [
      { kind: 'cap', label: 'Algorithmic trendlines' },
      { label: a.on ? 'On' : 'Off', value: 'on', checked: a.on },
      { kind: 'cap', label: 'Sensitivity' },
      { kind: 'sep' },
      { label: 'Adaptive sensitivity', value: 'adaptive',
        checked: a.adaptive, keepOpen: true,
        hint: 'per-instrument' },
      { label: 'Support / resistance', value: 'zones',
        checked: a.zones, keepOpen: true,
        hint: 'price turned repeatedly' },
      { label: 'Channels', value: 'channels',
        checked: a.channels, keepOpen: true,
        hint: 'parallel corridor' },
      { label: 'Regime segments', value: 'segments',
        checked: a.segments, keepOpen: true,
        hint: 'trending / ranging runs' },
      { label: 'Supply / demand zones', value: 'sdzones',
        checked: a.sdZones, keepOpen: true,
        hint: 'impulse origin' },
      { label: 'Swing points (HH/HL/LH/LL)', value: 'swings',
        checked: a.swings, keepOpen: true,
        hint: '' },
      { label: 'BOS / CHoCH', value: 'ms',
        checked: a.ms, keepOpen: true,
        hint: 'structure breaks' },
      { kind: 'sep' },
      ...Object.entries(SENS).map(([k, v]) => ({
        label: v.label, value: 'sens:' + k, keepOpen: true,
        checked: a.sens === k,
      })),
      { kind: 'cap', label: 'Lines per side' },
      ...[2, 3, 4, 6].map((n) => ({ label: String(n), value: 'max:' + n, checked: a.maxLines === n })),
      { kind: 'cap', label: 'Sources' },
      { label: `Own timeframe (${TF_LABEL[c.tf]})`, value: 'own', checked: a.own, keepOpen: true },
      ...higher.map((tf) => ({
        label: `Project from ${TF_LABEL[tf]}`, value: 'htf:' + tf,
        checked: a.htf.includes(tf), keepOpen: true,
      })),
    ];
    if (!higher.length) items.push({ kind: 'cap', label: 'already the highest timeframe' });
    /* No 'Recompute now'. It matched no branch in the handler below and fell
       through to the same `recomputeAll()` that every other menu action ends
       with -- so toggling anything already did it, and the item only ever
       repeated work that had just been done. `recomputeAll()` itself stays:
       the 'l' shortcut and every toggle still call it. */

    openMenu(e.currentTarget, items, (v, item) => {
      /* Every write goes to the ACTIVE chart's instrument. `cur` is what that
         instrument currently resolves to -- its own overrides on top of the
         global defaults -- so a toggle flips what the menu just showed. */
      const sym = app.active && app.active.symbol;
      const tf = app.active && app.active.tf;
      const cur = autoFor(sym, tf);
      const set = (patch) => setAutoFor(sym, tf, patch);

      if (v === 'on') set({ on: !cur.on });
      else if (v === 'own') set({ own: !cur.own });
      else if (v === 'adaptive') set({ adaptive: !cur.adaptive });
      else if (v === 'zones') set({ zones: !cur.zones });
      else if (v === 'channels') set({ channels: !cur.channels });
      else if (v === 'segments') set({ segments: !cur.segments });
      else if (v === 'sdzones') set({ sdZones: !cur.sdZones });
      else if (v === 'swings') set({ swings: !cur.swings });
      else if (v === 'ms') set({ ms: !cur.ms });
      else if (v.startsWith('sens:')) set({ sens: v.slice(5) });
      else if (v.startsWith('max:')) set({ maxLines: Number(v.slice(4)) });
      else if (v.startsWith('htf:')) {
        const tf = v.slice(4);
        set({
          htf: cur.htf.includes(tf)
            ? cur.htf.filter((x) => x !== tf)
            : [...cur.htf, tf],
        });
      }
      syncToolbar();
      if (item && item.keepOpen) $('#autoBtn').click();   // re-render the ticks
      recomputeAll();
    });
  });

  $('#snapBtn')?.addEventListener('click', (e) => openSnapMenu(e.currentTarget));

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

  /* THE EYE COVERS THE ACCOUNT FIGURES, and it now defaults to HIDDEN.
     A balance is nobody else's business and this app is often on a shared or
     recorded screen. An earlier version defaulted to hidden, was changed to
     shown on the argument that how much room the account has left should not be
     something you go looking for, and is now hidden again BY EXPLICIT REQUEST.
     The argument was about which default serves you; the answer is yours.

     The cells are masked rather than emptied (see .acct-hidden in app.css) so
     the status bar keeps its width. A layout that jumps on press announces to
     the room that something was just hidden, which defeats the point. */
  {
    const eye = $('#acctEye');
    const icon = $('#acctEyeIcon');
    const KEY = 'ui.acctHidden';
    const apply = (hidden) => {
      document.body.classList.toggle('acct-hidden', hidden);
      eye.setAttribute('aria-pressed', hidden ? 'true' : 'false');
      icon.textContent = hidden ? '🔒' : '👁';
      eye.title = hidden ? 'Show account figures'
                         : 'Hide account figures (shared screen)';
      /* THE TWO ACCOUNT-BEARING TABS ARE REBUILT, not just restyled. Positions
         and Orders render nothing at all while covered, so the class alone
         cannot bring them back -- and without this the panel kept whatever it
         was showing when the eye was last pressed, which on the covering
         direction is exactly the wrong leftover. */
      panels.render();

      /* ONLY THE COVERED STATE IS REMEMBERED.
         Persisting "revealed" defeated the entire gate: reveal once, and every
         later load showed the balance with no PIN asked -- reloading the page
         WAS the bypass, and a far easier one than the reset button. A reveal is
         now good for this session only; the app always starts covered. */
      save(KEY, true);
    };
    /* HIDING is free; REVEALING costs a PIN.
       Deliberately asymmetric. Covering the screen is the safe direction and
       must never be delayed by a dialog -- someone has just walked up behind
       you. Uncovering is the one that needs the check. */
    eye.addEventListener('click', async () => {
      const hidden = document.body.classList.contains('acct-hidden');
      if (!hidden) { apply(true); return; }
      const ok = await askPin({ setting: !pinStored() });
      if (ok) apply(false);
    });
    apply(true);                        // every load starts covered
  }

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
/**
 * The chart, as a PNG. Canvas only.
 *
 * A side panel was composed beside this for one revision and taken out again:
 * the live chart is a working surface, not a report, and an exported image of
 * it should be the thing you were looking at. The replays compose a panel
 * because half of what they show is HTML; here the picture is on the canvas.
 *
 * WHAT IT DOES NOT CARRY. `_positions` skips broker lines while `_exporting`
 * is set -- "a share should not carry position size or where the stops sit" --
 * so an exported image has no size, no stop and no balance on it, whatever was
 * on screen when it was taken.
 */
function snapName(c) {
  const stamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
  return `${c.symbol}_${TF_LABEL[c.tf] || c.tf}_${stamp}.png`;
}

function download(url, name) {
  const a = el('a', { href: url, download: name });
  document.body.append(a);
  a.click();
  a.remove();
}

/* WHICH SIDE PANELS TRAVEL WITH THE IMAGE.
   Multi-select and remembered, because whoever you send these to wants the
   same context every time -- re-ticking three boxes per share would guarantee
   they eventually go out inconsistent. */
const SNAP_PANELS = [
  { id: 'rulepanel', label: 'Donchian rule' },
  { id: 'trendread', label: 'Trend read' },
  { id: 'signalpanel', label: 'Signal engine' },
];
const SNAP_KEY = 'ui.snapPanels';
const snapSel = () => new Set(load(SNAP_KEY, ['rulepanel', 'trendread', 'signalpanel']));

/* The panels are HTML, the chart is a canvas, and the export has to be ONE
   image. Rather than pull in a DOM-rasteriser, the text is read out of the
   live panels and redrawn -- which also lets the caption use export ink on a
   white card instead of the app's dark palette, and drops the controls (a
   timeframe button means nothing in a PNG). */
function panelLines(id) {
  const host = $('#' + id);
  if (!host) return [];
  const out = [];
  for (const node of host.querySelectorAll('*')) {
    if (node.children.length) continue;                 // leaves only
    if (node.closest('button')) continue;               // controls do not export
    const t = (node.textContent || '').replace(/\s+/g, ' ').trim();
    if (t) out.push(t);
  }
  /* the panels render label/value as adjacent leaves; pair them back up so the
     caption reads as rows rather than a column of orphaned words */
  const rows = [];
  for (let i = 0; i < out.length; i += 2) {
    rows.push(out[i + 1] ? `${out[i]}   ${out[i + 1]}` : out[i]);
  }
  return rows;
}

/**
 * Chart image with the selected side panels drawn beside it.
 * Returns a data URL, or null when the chart could not be captured.
 */
function snapshotWithInfo(c, chosen) {
  const url = c.snapshot({ scale: 2, ink: 'light' });
  if (!url) return Promise.resolve(null);
  const wanted = SNAP_PANELS.filter((p) => chosen.has(p.id));
  if (!wanted.length) return Promise.resolve(url);

  return new Promise((resolve) => {
    const img = new Image();
    img.onload = () => {
      const PAD = 28, COLW = 460, TITLE = 30, LINE = 26;
      const blocks = wanted.map((p) => ({ label: p.label, lines: panelLines(p.id) }));
      const needed = blocks.reduce((a, b) => a + TITLE + b.lines.length * LINE + PAD, PAD);
      const w = img.width + COLW;
      const h = Math.max(img.height, needed);

      const cv = el('canvas');
      cv.width = w; cv.height = h;
      const x = cv.getContext('2d');
      x.fillStyle = '#FFFFFF';
      x.fillRect(0, 0, w, h);
      x.drawImage(img, 0, 0);

      let y = PAD;
      const left = img.width + PAD;
      for (const b of blocks) {
        x.fillStyle = '#0b1a2b';
        x.font = '600 19px "Roboto Mono", ui-monospace, monospace';
        x.fillText(b.label, left, y + 14);
        x.strokeStyle = '#c9d6e4';
        x.beginPath();
        x.moveTo(left, y + 24.5); x.lineTo(w - PAD, y + 24.5);
        x.stroke();
        y += TITLE;
        x.font = '15px "Roboto Mono", ui-monospace, monospace';
        for (const line of b.lines) {
          x.fillStyle = '#33475b';
          /* clip rather than wrap: a caption column that reflows turns a
             two-line panel into a page and pushes the chart out of shape */
          let t = line;
          while (t.length && x.measureText(t).width > COLW - PAD * 2) t = t.slice(0, -1);
          x.fillText(t === line ? t : t.slice(0, -1) + '…', left, y + 14);
          y += LINE;
        }
        y += PAD;
      }
      resolve(cv.toDataURL('image/png'));
    };
    img.onerror = () => resolve(null);
    img.src = url;
  });
}

function snapshot() {
  const c = app.active;
  if (!c) return;
  const url = c.snapshot({ scale: 2, ink: 'light' });
  if (!url) { toast('Could not capture the chart'); return; }
  const name = snapName(c);
  download(url, name);
  toast(`Saved ${name}`);
}

async function snapshotInfo() {
  const c = app.active;
  if (!c) return;
  const chosen = snapSel();
  if (!chosen.size) { toast('Tick at least one panel to include'); return; }
  const url = await snapshotWithInfo(c, chosen);
  if (!url) { toast('Could not capture the chart'); return; }
  const name = snapName(c);
  download(url, name);
  toast(`Saved ${name}`);
}

/* SHARING AN IMAGE, honestly.
 *
 * WhatsApp and Telegram cannot be handed a picture through a link. Their
 * wa.me / t.me URLs carry TEXT only -- there is no parameter for an
 * attachment, and pretending otherwise would send a caption with no chart.
 *
 * So: use the Web Share API when the browser has it, which hands the actual
 * PNG to whichever app the user picks and is the only path that really shares
 * the image. Where it is missing, save the file and open the chat with the
 * caption ready, and SAY that the image has to be attached -- a silent
 * half-share is worse than a clear instruction.
 */
async function shareSnapshot(target) {
  const c = app.active;
  if (!c) return;
  const chosen = snapSel();
  const url = chosen.size
    ? await snapshotWithInfo(c, chosen)
    : c.snapshot({ scale: 2, ink: 'light' });
  if (!url) { toast('Could not capture the chart'); return; }

  const name = snapName(c);
  const caption = `${c.symbol} ${TF_LABEL[c.tf] || c.tf} — DiaNurFx`;

  const blob = await (await fetch(url)).blob();
  const file = new File([blob], name, { type: 'image/png' });
  if (navigator.canShare && navigator.canShare({ files: [file] })) {
    try {
      await navigator.share({ files: [file], text: caption });
      return;                                   // the OS sheet did the rest
    } catch (e) {
      if (e && e.name === 'AbortError') return;  // user closed the sheet
      /* anything else falls through to the link route below */
    }
  }

  download(url, name);
  const link = target === 'telegram'
    ? `https://t.me/share/url?url=${encodeURIComponent(caption)}`
    : `https://wa.me/?text=${encodeURIComponent(caption)}`;
  window.open(link, '_blank', 'noopener');
  toast(`Saved ${name} — attach it in ${target === 'telegram' ? 'Telegram' : 'WhatsApp'}`);
}

function openSnapMenu(anchor) {
  const panelItems = () => {
    const sel = snapSel();
    return [
      { kind: 'cap', label: 'Include from the side panel' },
      ...SNAP_PANELS.map((p) => ({
        label: p.label, value: 'toggle:' + p.id,
        checked: sel.has(p.id), keepOpen: true,
      })),
      { kind: 'sep' },
      { label: 'Save image', value: 'info' },
    ];
  };

  openMenu(anchor, [
    { label: 'Snapshot', value: 'plain', hint: 's' },
    { label: 'Snapshot with info', sub: panelItems },
    { kind: 'sep' },
    {
      label: 'Share',
      sub: [
        { label: 'WhatsApp', value: 'share:whatsapp' },
        { label: 'Telegram', value: 'share:telegram' },
      ],
    },
  ], (v) => {
    if (v === 'plain') { snapshot(); return; }
    if (v === 'info') { snapshotInfo(); return; }
    if (v && v.startsWith('toggle:')) {
      const id = v.slice(7);
      const sel = snapSel();
      if (sel.has(id)) sel.delete(id); else sel.add(id);
      save(SNAP_KEY, [...sel]);
      return;
    }
    if (v && v.startsWith('share:')) shareSnapshot(v.slice(6));
  });
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
      const sym = app.active && app.active.symbol;
      const tf = app.active && app.active.tf;
      const now = !autoFor(sym, tf).on;
      setAutoFor(sym, tf, { on: now });
      syncToolbar();
      recomputeAll();
      toast(`auto trendlines ${now ? 'on' : 'off'}`, 1400);
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
      app.charts.forEach((c) => { c.setPositions(pos); });
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
    /* A tick repaint reads the LIVE bar by definition, so it has nothing to
       say about a chart parked in history -- and running it would quietly
       replace the as-of read with today's, banner and all. */
    if (!asOfCut(app.active).scrolled) {
      trendRead.repaint(app.active.bars);
      panelTick = (panelTick + 1) % 4;
      if (panelTick === 0) signalPanel.repaint(app.active.bars);
      rulePanel.repaint(app.active.bars);
      paintRuleSignal(app.active, rulePanel.sig);
    }
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
    try {
      /* One request serves both. The month figure needs history back to the
         first of the month, the History tab shows a rolling week -- so fetch
         the wider window and give the panel the same 7 days it always had,
         rather than polling the bridge twice every 30 seconds. */
      const span = Math.ceil((Date.now() - realisedWindows().month) / 86400000) + 1;
      const days = Math.max(7, Math.min(365, span));
      const deals = await api.deals(days);
      paintRealised(deals);
      const cut = Date.now() - 7 * 86400000;
      panels.set('deals', days > 7 ? deals.filter((d) => d.time_ms >= cut) : deals);
    } catch { /* skip */ }
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
/* The replay sandboxes borrow this picker rather than building a second overlay;
   see js/ui/search.js. Registering it is the only line that connects them. */
registerSymbolSearch(search);

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

installTips();     // delegated, so panel rows rendered later are covered
installChat();     // the Ask panel; grounded on this app only, see chat.js
/* Debug handle. The workspace is otherwise unreachable from the console, which
   makes "what is actually on this canvas" an unanswerable question -- and that
   is the first question whenever the chart looks wrong. Read-only by
   convention; nothing in the app reads it back. */
window.dnfx = app;

/* The workspace file is read BEFORE boot, and boot is what builds the grid from
   saved state. Nothing above this point may depend on localStorage having its
   final contents -- which is why `app` is populated lazily by boot() rather
   than at module level. */
hydrateWorkspace().then((n) => {
  if (n > 0) {
    // the module-level snapshot was taken from a pre-hydration localStorage
    BOOT_LOCKS.clear();
    for (const k of Object.keys(localStorage)) {
      if (!k.startsWith('dnfx.sym.')) continue;
      try {
        const v = JSON.parse(localStorage.getItem(k) || 'null');
        if (v && v.priceLock) {
          BOOT_LOCKS.set(k.slice('dnfx.sym.'.length), { lock: v.priceLock, tf: v.tf });
        }
      } catch { /* skip a corrupt entry */ }
    }
  }
  boot();
});

// exposed for console poking during development
window.DiaNurFx = { app, api, status, tick, boot };
