/* engine.js — the chart. One canvas per cell, no chart library.
 *
 * Coordinate model
 *   x is driven by BAR INDEX, not time, so weekend/holiday gaps collapse the
 *   way a trading chart is expected to (a time-linear x-axis would leave a hole
 *   over every weekend). `view.right` is a float index of the rightmost visible
 *   slot and may exceed the last bar, which is what gives the chart its
 *   right-hand margin and lets you scroll into empty space.
 *   y is per-pane: price on the main pane, each study pane owning its own scale.
 *
 * Panes are derived from the study list, so adding an oscillator restacks the
 * chart without any layout bookkeeping elsewhere.
 */

import { INDICATORS, runStudy, studyTitle, heikinAshi } from './indicators.js';
import { TF_LABEL, TF_MS, axisTime, clamp, compact, inferDigits, stamp, withZone, zoneLabel } from '../util.js';

/* Right-hand price axis. Sized from the widest label it must actually hold,
   measured at the axis font: a 7-character price ("4659.58", "1.16637") is
   40px and an 8-character one ("53478.90") is 46px, drawn at plot.r + 5. 54
   leaves a couple of pixels of breathing room and hands 10px back to the
   candles -- 64 was inherited from a larger font and never re-measured. */
const AXIS_W = 54;
const TIME_H = 22;        // bottom time axis
const PANE_GAP = 6;
const MIN_SPAN = 12;
/* 4800 rather than 4000 so `fitAll` can still frame a freshly extended chart:
   history doubles on demand and a 4h series reaches ~4800 bars after two
   rounds. Beyond this a bar is under half a pixel and candles stop being
   candles, so the cap is a RENDERING limit, not a data one -- scrolling still
   reaches everything loaded. */
const MAX_SPAN = 4800;

/**
 * 350 bars, on every timeframe.
 *
 * Two earlier versions got the invariant wrong. A fixed 160 bars meant a
 * different WINDOW per frame; fixing the window instead (five days) meant a
 * different CANDLE WIDTH per frame, because width is plot width divided by
 * span -- M1 drew 4800 hair-thin bars and D1 drew 12 fat ones.
 *
 * Holding the span constant holds the candle width constant, and lets the
 * period on screen be the thing that varies: ~1 day on M5, ~5 on M15, ~460 on
 * D1. That is the right way round. A timeframe switch should change how much
 * history one screen holds, not how the chart looks.
 *
 * Takes `tf` although it ignores it, so a future per-frame exception lands here
 * rather than in a new branch at each call site.
 */
export function defaultSpan(tf) {          // eslint-disable-line no-unused-vars
  return clamp(350, MIN_SPAN, MAX_SPAN);
}

/* What the two RESET gestures return to -- the button and the double-click.
   Deliberately separate from `defaultSpan`: a chart OPENS at 350 and RESETS to
   300, which is the one asked for and is the only place the two differ. */
export function resetSpan(tf) {            // eslint-disable-line no-unused-vars
  return clamp(300, MIN_SPAN, MAX_SPAN);
}

const COL = {
  bg: '#02101f',
  grid: 'rgba(13,58,107,.55)',
  gridStrong: 'rgba(13,58,107,.9)',
  text: '#8fa6c0',
  textFaint: '#5d7794',
  up: '#93C90F',
  down: '#E31C79',
  upFill: 'rgba(147,201,15,.85)',
  downFill: 'rgba(227,28,121,.85)',
  cross: 'rgba(217,217,214,.45)',
  crossBg: '#072a55',
  line: '#31c7d6',
  area1: 'rgba(49,199,214,.28)',
  area2: 'rgba(49,199,214,0)',
  axisBg: '#041E42',
  pos: '#FF9E1B',
  sl: '#E31C79',
  tp: '#93C90F',
  draw: '#D9D9D6',
  pink: '#E31C79',
};

/* Ink for a snapshot that will land on a WHITE page.
 *
 * The chart is light-on-dark, so a transparent export washes out over white --
 * pale grid, faint axis text, and a light-grey wordmark that disappears
 * entirely. Transparency cannot fix that; the ink colour is the problem.
 *
 * Every draw site reads COL.<key> at call time rather than closing over the
 * value, so an export can overwrite these keys, paint, and put the originals
 * back. That is why this is a flat override map and not a second palette
 * threaded through forty call sites.
 *
 * Brand hues are kept but darkened to carry on white: #93C90F green is a
 * highlighter at 4.5:1 against paper, #171C8F navy already reads.
 */
const INK_ON_LIGHT = {
  bg: '#FFFFFF',              // label chips only -- the canvas fill is skipped
  grid: 'rgba(23,28,143,.14)',
  gridStrong: 'rgba(23,28,143,.30)',
  text: '#2B3440',
  textFaint: '#63707F',
  up: '#4E7A00',
  down: '#C0135F',
  upFill: 'rgba(78,122,0,.85)',
  downFill: 'rgba(192,19,95,.85)',
  cross: 'rgba(43,52,64,.45)',
  line: '#0E7C8A',
  area1: 'rgba(14,124,138,.22)',
  area2: 'rgba(14,124,138,0)',
  draw: '#3A3F45',
};

export const CHART_TYPES = {
  candles: 'Candles', hollow: 'Hollow candles', bars: 'OHLC bars',
  line: 'Line', area: 'Area', heikin: 'Heikin Ashi', baseline: 'Baseline',
};

export const DRAW_TOOLS = {
  cursor: 'Cursor', hline: 'Horizontal line', trend: 'Trend line',
  ray: 'Ray', rect: 'Rectangle', fib: 'Fib retracement',
};

const FIB = [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1];

/** mm:ss (or h:mm above an hour) left in the bar that opened at `t`. */
function timeLeft(t, tf) {
  const step = TF_MS[tf] || 60e3;
  const ms = t + step - Date.now();
  if (ms <= 0 || ms > step * 1.5) return null;
  const s = Math.floor(ms / 1000);
  const pad = (v) => String(v).padStart(2, '0');
  if (s >= 3600) return `${Math.floor(s / 3600)}:${pad(Math.floor((s % 3600) / 60))}:${pad(s % 60)}`;
  return `${pad(Math.floor(s / 60))}:${pad(s % 60)}`;
}

function niceStep(raw) {
  if (!(raw > 0)) return 1;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const n = raw / mag;
  const step = n <= 1 ? 1 : n <= 2 ? 2 : n <= 2.5 ? 2.5 : n <= 5 ? 5 : 10;
  return step * mag;
}

let uid = 0;
const nextId = () => `d${Date.now().toString(36)}${(uid++).toString(36)}`;

export class Chart {
  constructor(host, opts = {}) {
    this.host = host;
    this.symbol = opts.symbol || 'EURUSD';
    this.tf = opts.tf || '15m';
    this.type = opts.type || 'candles';
    this.studies = opts.studies ? structuredClone(opts.studies) : [];   // bare by default
    this.drawings = opts.drawings ? structuredClone(opts.drawings) : [];
    this.tool = 'cursor';
    this.onChange = opts.onChange || (() => {});
    this.onActivate = opts.onActivate || (() => {});
    /* Fired when the visible RANGE moves. One notifier in the paint path rather
       than a call at each of the six places that mutate `view`, so a new pan,
       zoom or keyboard path cannot forget to announce itself. */
    this.onView = opts.onView || (() => {});
    this._lastViewRight = null;
    this.onSymbolClick = opts.onSymbolClick || (() => {});

    this.bars = [];
    this.digits = 5;
    this.positions = [];
    this.autoLines = [];
    this.channels = [];
    this.trail = null;
    this.ruleZone = null;
    this.zones = [];
    this.segments = [];
    this.sdZones = [];
    this.targets = [];
    this.marks = [];
    this.msEvents = [];
    this.swings = [];
    this.message = 'loading…';
    this.view = { right: 0, span: 160, priceLock: null };
    this.cross = null;
    this.pending = null;          // in-progress drawing
    this.hoverDrawing = null;
    /* Click-selected drawing. Separate from hover: a selection has to
       survive the mouse moving away, which is the whole point of it. */
    this.selectedDrawing = null;
    /* Set only for the duration of a snapshot. Suppresses live-account and
       interaction-state chrome (broker positions, selection accent) so the
       exported PNG is a picture of the MARKET, not of this session. */
    this._exporting = false;
    /* Multiplies the canvas backing store during an export. The CSS size is
       untouched, so layout, font sizes and line widths are all unchanged --
       the same drawing is simply rasterised onto more pixels. */
    this._exportScale = 1;
    /* Backdrop baked into an export. null means leave it transparent. */
    this._exportBg = '#FFFFFF';
    this.panes = [];
    this.dirty = false;

    this._build();
    this._bind();
  }

  /* ------------------------------------------------------------------ DOM */
  _build() {
    this.host.innerHTML = '';
    this.canvas = document.createElement('canvas');
    this.ctx = this.canvas.getContext('2d');
    this.legend = document.createElement('div');
    this.legend.className = 'legend';
    this.msgEl = document.createElement('div');
    this.msgEl.className = 'cell-msg';
    /* Tooltip as DOM, not canvas. A canvas tooltip would have to be redrawn on
       every frame and would clip at the cell edge; a positioned div costs one
       style write on hover and nothing at all otherwise. */
    this.tip = document.createElement('div');
    this.tip.className = 'zone-tip';
    this.tip.hidden = true;

    this.tools = document.createElement('div');
    this.tools.className = 'cell-tools';
    const btn = (txt, title, fn) => {
      const b = document.createElement('button');
      b.textContent = txt; b.title = title;
      b.addEventListener('click', (e) => { e.stopPropagation(); fn(); });
      this.tools.append(b);
    };
    btn('⤢', 'Fit all bars', () => { this.fitAll(); });
    btn('⟳', 'Reset scale', () => { this.resetScale(); });
    btn('⌫', 'Clear drawings', () => { this.drawings = []; this._persist(); this.draw(); });

    this.host.append(this.canvas, this.legend, this.msgEl, this.tip, this.tools);
    this.ro = new ResizeObserver(() => this.resize());
    this.ro.observe(this.host);
    this.resize();
  }

  destroy() { this.ro.disconnect(); this.host.innerHTML = ''; }

  resize() {
    const dpr = (window.devicePixelRatio || 1) * this._exportScale;
    const w = Math.max(80, this.host.clientWidth);
    const h = Math.max(80, this.host.clientHeight);
    this.w = w; this.h = h;
    this.canvas.width = Math.round(w * dpr);
    this.canvas.height = Math.round(h * dpr);
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    this.draw();
  }

  /* ------------------------------------------------------------- data in */
  setData(payload) {
    const bars = (payload && payload.bars) || [];
    const first = !this.bars.length;
    this.bars = bars;
    // the broker's own ticker for what we asked for (EURUSD -> EURUSD.a)
    this.resolved = (payload && payload.symbol) || this.symbol;
    this.digits = payload && payload.digits != null
      ? payload.digits
      : inferDigits(bars.length ? bars[bars.length - 1].c : 1);
    this.message = bars.length ? null : 'no history for this symbol / timeframe';
    if (first || this.view.right === 0) this.resetView();
    else this.view.right = Math.min(this.view.right, bars.length - 1 + this.rightPad());
    /* Older bars arrived (or the series changed): the pending request is
       answered, and the edge is re-clamped against the new extent. */
    this.wantsHistory = false;
    this._recalc();
    this.draw();
  }

  /** Fold a fresh quote into the forming bar so the chart ticks live. */
  applyQuote(q) {
    if (!this.bars.length || !q) return;
    const step = TF_MS[this.tf] || 60e3;
    const price = q.bid && q.ask ? (q.bid + q.ask) / 2 : (q.last || q.bid);
    if (!price) return;
    const last = this.bars[this.bars.length - 1];
    const slot = Math.floor((q.time_ms || Date.now()) / step) * step;
    if (slot > last.t) {
      this.bars.push({ t: slot, o: price, h: price, l: price, c: price, v: 0, ticks: 0 });
      if (this.onNewBar) this.onNewBar(this);
      if (this.view.right >= this.bars.length - 2 + this.rightPad() - 1) {
        this.view.right = this.bars.length - 1 + this.rightPad();
      }
    } else {
      last.c = price;
      last.h = Math.max(last.h, price);
      last.l = Math.min(last.l, price);
      last.ticks = (last.ticks || 0) + 1;
    }
    this.lastQuote = q;
    this._recalc();
    this.draw();
  }

  /** Algorithmic trendlines, each tagged with the tf it was detected on. */
  setChannels(chs) { this.channels = chs || []; }

  setZones(zs) { this.zones = zs || []; }

  /* The Elliott belief for the bar at the right edge; see js/ui/replay.js. */
  setElliott(b) { this.elliott = b || null; }

  /* Where the replay cursor stands, for the lane that shows the future. Without
     it the two lanes are the same picture and the eye has to count columns to
     find the boundary between what was known and what followed. */
  setAsOfMark(i) { this.asOfMark = Number.isFinite(i) ? i : null; }

  setSegments(sg) { this.segments = sg || []; }

  setSdZones(zs) { this.sdZones = zs || []; }
  /* TP bands, from js/chart/targets.js. Reference levels measured in R off
     the live stop -- NOT exits. See that file for why 1.5R is the floor. */
  setTargets(t) { this.targets = t || []; }
  /* Trade events at the bar they happened on. See _marks for why a signal and
     a fill are drawn differently. */
  setMarks(m) { this.marks = m || []; }

  /* A per-bar level traced across a range of bars: the EXIT level of an
     open trade, which for a Donchian is a rolling extreme and therefore
     ratchets rather than slopes. Drawn as a STEP line for that reason -- a
     straight interpolation between bar values would imply the level moved
     continuously, when in fact it holds flat and then jumps to a new
     extreme, and WHEN it jumps is the interesting part. Callers draw(). */
  setTrail(t) { this.trail = t || null; }

  /* The RULE's own risk picture, which is not a position and must never
     look like one. See _ruleZone. */
  setRuleZone(z) { this.ruleZone = z || null; }

  setMsEvents(ev) { this.msEvents = ev || []; }
  setSwings(sw) { this.swings = sw || []; }

  setAutoLines(lines) {
    this.autoLines = lines || [];
    this.draw();
  }

  setPositions(rows) {
    this.positions = (rows || []).filter((p) => p.symbol === this.symbol);
    this.draw();
  }

  setTimeframe(tf) {
    this.tf = tf;
    this.bars = [];
    /* Re-frame for the NEW timeframe. The span used to carry over, so fitting
       all bars on 4h and then switching to M15 kept a 1273-bar window on a
       series that had nothing like that much to show, and the chart opened onto
       empty space. `right = 0` is the flag setData reads to re-position. */
    this.view.span = defaultSpan(tf);
    this.view.right = 0;
    this.view.priceLock = null;
    this.drawings = this._loadDrawings();
    this.onChange(this);
  }
  setType(t) { this.type = t; this._recalc(); this.draw(); this.onChange(this); }
  setTool(t) { this.tool = t; this.pending = null; this.canvas.style.cursor = t === 'cursor' ? 'crosshair' : 'copy'; }

  addStudy(kind, inputs) {
    if (!INDICATORS[kind]) return;
    this.studies.push({ id: nextId(), kind, inputs: inputs || {} });
    this._recalc(); this.draw(); this.onChange(this);
  }
  removeStudy(id) {
    this.studies = this.studies.filter((s) => s.id !== id);
    this._recalc(); this.draw(); this.onChange(this);
  }
  toggleStudy(kind) {
    const found = this.studies.find((s) => s.kind === kind);
    if (found) this.removeStudy(found.id); else this.addStudy(kind);
  }
  hasStudy(kind) { return this.studies.some((s) => s.kind === kind); }

  _loadDrawings() { return []; }   // wired by the workspace via opts.drawings

  _persist() { this.onChange(this); }

  /* --------------------------------------------------------- computation */
  _recalc() {
    this.plotBars = this.type === 'heikin' ? heikinAshi(this.bars) : this.bars;
    this.runs = this.studies.map((s) => runStudy(s, this.plotBars)).filter(Boolean);
  }

  rightPad() { return Math.max(2, Math.round(this.view.span * 0.06)); }

  /**
   * Back to how this chart opens: default zoom, live edge, no price lock.
   *
   * SEPARATE FROM `resetView`, which cannot do this. `setData` calls resetView
   * on the first payload, and main.js applies the symbol's REMEMBERED span
   * before the bars arrive -- so forcing a default span in there would throw
   * away the saved view every time a chart opened.
   *
   * `resetView` keeps the current span on purpose, which is right for the data
   * path and wrong for a button labelled Reset: after fitting 4800 bars it left
   * the reader at 4800 bars wide. This puts the current price back on screen at
   * a readable zoom, which is what the button is for, and persists the result
   * so the symbol reopens that way.
   */
  resetScale() {
    this.view.span = resetSpan(this.tf);
    this.view.right = this.bars.length - 1 + this.rightPad();
    this.view.priceLock = null;
    this.draw();
    this._persist();
  }

  resetView() {
    this.view.span = clamp(this.view.span || 160, MIN_SPAN, MAX_SPAN);
    this.view.right = Math.max(this.view.span - 1, this.bars.length - 1 + this.rightPad());
    this.view.priceLock = null;
  }

  /**
   * Clamp the right edge so the LEFT edge cannot leave the data.
   *
   * The old bound was `span * 0.4`, which says "you may always scroll 60% of a
   * SCREEN past the oldest bar". At the default 350-bar span that is 210 empty
   * columns with real candles still beside them, so it read as headroom. After
   * `fitAll` a screen IS the whole dataset, and the same rule bought 60% of the
   * entire history in blank space -- pan left after fitting and the chart
   * emptied out. Measured on 15m gold: `i0` reached -360 with 2120 columns on
   * screen, and on a wider fit it goes past -1200.
   *
   * The bound has to come from the DATA, not from the span. `leftMin` puts the
   * oldest bar at the left edge and stops. When the span is wider than the
   * history there is nothing to the left at all, so it stops immediately --
   * which is correct, everything is already on screen.
   *
   * Pushing INTO that wall is how the reader asks for older bars, so the
   * attempt is recorded rather than silently dropped; main.js turns it into a
   * history fetch. Without that, clamping would have made `fitAll` a dead end:
   * the old fetch trigger needed the right edge to move back, and at the wall
   * it no longer can.
   */
  _clampRight(want) {
    const rightMax = this.bars.length + this.view.span * 0.6;
    const leftMin = Math.min(this.view.span - 1, rightMax);
    if (want < leftMin - 0.5) this.wantsHistory = true;
    return clamp(want, leftMin, rightMax);
  }

  fitAll() {
    if (!this.bars.length) return;
    /* "Fit all bars" did not fit all bars. `span` was the bar count and `right`
       was the last bar PLUS the right pad, so the left edge landed at `pad` and
       the oldest bars fell off the screen -- 72 of them on a 1201-bar 4h chart,
       because the pad is 6% of the span. The span has to carry the pad too.
       The pad is derived from the intended span rather than read back from
       `rightPad()`, which would use the span still being computed. */
    const base = clamp(this.bars.length, MIN_SPAN, MAX_SPAN);
    const pad = Math.max(2, Math.round(base * 0.06));
    this.view.span = clamp(base + pad, MIN_SPAN, MAX_SPAN);
    this.view.right = this.bars.length - 1 + pad;
    this.view.priceLock = null;
    this.draw();
  }

  /** Pane rectangles, derived from which studies are present. */
  _layout() {
    const plotL = 0;
    const plotR = this.w - AXIS_W;
    const top = 0;
    const bottom = this.h - TIME_H;
    const groups = [{ key: 'main', weight: 1, runs: this.runs.filter((r) => r.pane === 'main') }];
    const vol = this.runs.filter((r) => r.pane === 'volume');
    if (vol.length) groups.push({ key: 'volume', weight: 0.17, runs: vol });
    for (const r of this.runs.filter((x) => x.pane === 'own')) {
      groups.push({ key: r.id, weight: 0.24, runs: [r] });
    }
    // Price must keep the lion's share: five oscillators in a quarter-screen
    // cell would otherwise leave candles a 30px sliver.
    const MAIN_MIN = 0.45;
    const subs = groups.slice(1).reduce((a, g) => a + g.weight, 0);
    if (subs && 1 / (1 + subs) < MAIN_MIN) {
      const k = (1 / MAIN_MIN - 1) / subs;
      for (const g of groups.slice(1)) g.weight *= k;
    }
    const totalW = groups.reduce((a, g) => a + g.weight, 0);
    const usable = Math.max(60, bottom - top - PANE_GAP * (groups.length - 1));
    let y = top;
    this.panes = groups.map((g, i) => {
      const h = Math.max(28, (usable * g.weight) / totalW);
      const rect = { x: plotL, y, w: plotR - plotL, h, key: g.key, runs: g.runs, isMain: i === 0 };
      y += h + PANE_GAP;
      return rect;
    });
    this.plot = { l: plotL, r: plotR, t: top, b: bottom, w: plotR - plotL, h: bottom - top };
    this.barW = this.plot.w / this.view.span;
    this.i0 = Math.floor(this.view.right - this.view.span + 1);
    this.i1 = Math.ceil(this.view.right);
    // price range per pane
    for (const p of this.panes) {
      if (p.isMain) {
        let lo = Infinity, hi = -Infinity;
        for (let i = Math.max(0, this.i0); i <= Math.min(this.plotBars.length - 1, this.i1); i++) {
          const b = this.plotBars[i];
          if (this.type === 'line' || this.type === 'area' || this.type === 'baseline') {
            lo = Math.min(lo, b.c); hi = Math.max(hi, b.c);
          } else { lo = Math.min(lo, b.l); hi = Math.max(hi, b.h); }
        }
        for (const r of p.runs) {
          for (const pl of r.plots) {
            for (const key of ['data', 'upper', 'lower']) {
              const arr = pl[key];
              if (!Array.isArray(arr)) continue;
              for (let i = Math.max(0, this.i0); i <= Math.min(arr.length - 1, this.i1); i++) {
                const v = arr[i];
                if (v === null || v === undefined || Number.isNaN(v)) continue;
                lo = Math.min(lo, v); hi = Math.max(hi, v);
              }
            }
          }
        }
        // Deliberately NOT widened to fit position lines or drawings: an SL 300
        // points away would squash the candles into a band. Those levels are
        // clipped to the pane instead, which is what a trading terminal does.
        if (!Number.isFinite(lo) || !Number.isFinite(hi)) { lo = 0; hi = 1; }
        if (this.view.priceLock) {
          p.min = this.view.priceLock.min; p.max = this.view.priceLock.max;
        } else {
          const pad = (hi - lo) * 0.09 || Math.abs(hi) * 0.001 || 1;
          p.min = lo - pad; p.max = hi + pad;
        }
      } else {
        let lo = Infinity, hi = -Infinity, forced = null;
        for (const r of p.runs) {
          for (const pl of r.plots) {
            if (pl.type === 'range') { forced = pl; continue; }
            if (pl.type === 'level') { lo = Math.min(lo, pl.value); hi = Math.max(hi, pl.value); continue; }
            const arr = pl.data;
            if (!Array.isArray(arr)) continue;
            for (let i = Math.max(0, this.i0); i <= Math.min(arr.length - 1, this.i1); i++) {
              const v = arr[i];
              if (v === null || v === undefined || Number.isNaN(v)) continue;
              lo = Math.min(lo, v); hi = Math.max(hi, v);
              if (pl.zeroBase) lo = Math.min(lo, 0);
            }
          }
        }
        if (forced) { lo = forced.min; hi = forced.max; }
        if (!Number.isFinite(lo) || !Number.isFinite(hi) || lo === hi) { lo = 0; hi = hi || 1; }
        const pad = forced ? 0 : (hi - lo) * 0.08;
        p.min = lo - pad; p.max = hi + pad;
      }
    }
    this.main = this.panes[0];
  }

  x(i) { return this.plot.l + (i - this.i0) * this.barW + this.barW / 2; }
  idxAt(px) { return Math.round((px - this.plot.l - this.barW / 2) / this.barW + this.i0); }
  y(pane, v) { return pane.y + pane.h - ((v - pane.min) / (pane.max - pane.min)) * pane.h; }
  valAt(pane, py) { return pane.min + ((pane.y + pane.h - py) / pane.h) * (pane.max - pane.min); }
  paneAt(py) { return this.panes.find((p) => py >= p.y && py <= p.y + p.h) || this.main; }
  tAt(i) {
    const step = TF_MS[this.tf] || 60e3;
    if (!this.bars.length) return Date.now();
    if (i < 0) return this.bars[0].t + i * step;
    if (i >= this.bars.length) return this.bars[this.bars.length - 1].t + (i - this.bars.length + 1) * step;
    return this.bars[i].t;
  }
  idxOfTime(t) {
    const step = TF_MS[this.tf] || 60e3;
    if (!this.bars.length) return 0;
    if (t <= this.bars[0].t) return (t - this.bars[0].t) / step;
    if (t >= this.bars[this.bars.length - 1].t) {
      return this.bars.length - 1 + (t - this.bars[this.bars.length - 1].t) / step;
    }
    let lo = 0, hi = this.bars.length - 1;
    while (hi - lo > 1) {
      const mid = (lo + hi) >> 1;
      if (this.bars[mid].t <= t) lo = mid; else hi = mid;
    }
    return lo;
  }

  /* ---------------------------------------------------------- rendering */
  draw() {
    if (this.dirty) return;
    this.dirty = true;
    requestAnimationFrame(() => { this.dirty = false; this._paint(); });
  }

  _paint() {
    /* Rounded, so sub-pixel drift during a drag does not fire on every frame;
       the consumer debounces anyway, but this keeps the common case free.
       Safe against recursion: the consumer's response sets lines and calls
       draw(), and by then the right edge has not moved again. */
    const vr = Math.round(this.view.right);
    /* The wall is part of the signal. Pressing against it does not move the
       right edge, so a movement-only test would never report the one gesture
       that means "there is nothing here, fetch me more". */
    const wall = !!this.wantsHistory;
    if (vr !== this._lastViewRight || wall !== this._lastWall) {
      this._lastViewRight = vr;
      this._lastWall = wall;
      this.onView(this);
    }
    const ctx = this.ctx;
    ctx.clearRect(0, 0, this.w, this.h);
    /* On export the backdrop comes from _exportBg: a solid colour is BAKED IN
       rather than left transparent. Transparency looked correct in a compositor
       and wrong everywhere else -- most viewers, chat clients and slide tools
       paint their own backing behind a transparent PNG, usually a dark one, so
       a light-ink chart landed on a dark field and appeared unchanged. A white
       fill removes that dependency on whatever is showing the file. */
    const back = this._exporting ? this._exportBg : COL.bg;
    if (back) {
      ctx.fillStyle = back;
      ctx.fillRect(0, 0, this.w, this.h);
    }
    this.msgEl.textContent = this.message || '';
    this.msgEl.style.display = this.message ? 'grid' : 'none';
    if (!this.bars.length) { this.legend.innerHTML = ''; return; }

    this._recalcIfNeeded();
    this._layout();

    for (const pane of this.panes) {
      this._gridY(pane);
    }
    this._gridX();

    for (const pane of this.panes) {
      ctx.save();
      ctx.beginPath();
      ctx.rect(pane.x, pane.y, pane.w, pane.h);
      ctx.clip();
      for (const run of pane.runs) this._plots(pane, run);
      if (pane.isMain) {
        this._segments(pane); this._zones(pane); this._sdZones(pane);
        this._targets(pane);
        this._price(pane); this._foreignPlots(pane);
        this._channels(pane); this._autoLines(pane);
        this._msEvents(pane);
        this._swings(pane);
        this._elliott(pane);
        this._trail(pane);
        this._marks(pane);
        this._asOfMark(pane);
        this._ruleZone(pane);
        this._positions(pane); this._drawings(pane);
      }
      ctx.restore();
      this._axisY(pane);
      this._valueTag(pane);
      if (pane.isMain) this._zoneAxisLabels(pane);
      if (!pane.isMain) this._paneTitle(pane);
    }

    this._axisX();
    this._lastPrice();
    this._crosshair();
    this._watermark();
    this._legend();
  }

  _recalcIfNeeded() { if (!this.runs || this.runs.length !== this.studies.length) this._recalc(); }

  _gridY(pane) {
    const ctx = this.ctx;
    const step = niceStep((pane.max - pane.min) / Math.max(2, Math.floor(pane.h / 46)));
    ctx.strokeStyle = COL.grid;
    ctx.lineWidth = 1;
    ctx.beginPath();
    for (let v = Math.ceil(pane.min / step) * step; v <= pane.max; v += step) {
      const y = Math.round(this.y(pane, v)) + 0.5;
      ctx.moveTo(this.plot.l, y); ctx.lineTo(this.plot.r, y);
    }
    ctx.stroke();
    pane.step = step;
  }

  _gridX() {
    const ctx = this.ctx;
    const step = Math.max(1, Math.ceil(74 / this.barW));
    ctx.strokeStyle = COL.grid;
    ctx.lineWidth = 1;
    ctx.beginPath();
    this.xTicks = [];
    for (let i = Math.ceil(this.i0 / step) * step; i <= this.i1; i += step) {
      const x = Math.round(this.x(i)) + 0.5;
      if (x < this.plot.l || x > this.plot.r) continue;
      ctx.moveTo(x, this.plot.t); ctx.lineTo(x, this.plot.b);
      this.xTicks.push({ i, x });
    }
    ctx.stroke();

    // day separators for intraday timeframes
    if (TF_MS[this.tf] < 864e5) {
      ctx.strokeStyle = COL.gridStrong;
      ctx.beginPath();
      for (let i = Math.max(1, this.i0); i <= Math.min(this.bars.length - 1, this.i1); i++) {
        if (new Date(this.bars[i].t).getUTCDate() !== new Date(this.bars[i - 1].t).getUTCDate()) {
          const x = Math.round(this.x(i) - this.barW / 2) + 0.5;
          ctx.moveTo(x, this.plot.t); ctx.lineTo(x, this.plot.b);
        }
      }
      ctx.stroke();
    }
  }

  _price(pane) {
    const ctx = this.ctx;
    const bars = this.plotBars;
    const bw = Math.max(1, this.barW * 0.7);
    const half = bw / 2;
    const i0 = Math.max(0, this.i0), i1 = Math.min(bars.length - 1, this.i1);

    if (this.type === 'line' || this.type === 'area' || this.type === 'baseline') {
      const pts = [];
      for (let i = i0; i <= i1; i++) pts.push([this.x(i), this.y(pane, bars[i].c)]);
      if (!pts.length) return;
      if (this.type === 'area') {
        const g = ctx.createLinearGradient(0, pane.y, 0, pane.y + pane.h);
        g.addColorStop(0, COL.area1); g.addColorStop(1, COL.area2);
        ctx.fillStyle = g;
        ctx.beginPath();
        ctx.moveTo(pts[0][0], pane.y + pane.h);
        for (const [x, y] of pts) ctx.lineTo(x, y);
        ctx.lineTo(pts[pts.length - 1][0], pane.y + pane.h);
        ctx.closePath(); ctx.fill();
      }
      if (this.type === 'baseline') {
        const base = bars[i0].c;
        const by = this.y(pane, base);
        ctx.save();
        ctx.strokeStyle = 'rgba(177,179,179,.35)';
        ctx.setLineDash([4, 3]);
        ctx.beginPath(); ctx.moveTo(this.plot.l, by); ctx.lineTo(this.plot.r, by); ctx.stroke();
        ctx.restore();
        ctx.lineWidth = 1.6;
        for (let k = 1; k < pts.length; k++) {
          ctx.strokeStyle = pts[k][1] <= by ? COL.up : COL.down;
          ctx.beginPath(); ctx.moveTo(pts[k - 1][0], pts[k - 1][1]); ctx.lineTo(pts[k][0], pts[k][1]); ctx.stroke();
        }
        return;
      }
      ctx.strokeStyle = COL.line;
      ctx.lineWidth = 1.6;
      ctx.beginPath();
      pts.forEach(([x, y], k) => (k ? ctx.lineTo(x, y) : ctx.moveTo(x, y)));
      ctx.stroke();
      return;
    }

    for (let i = i0; i <= i1; i++) {
      const b = bars[i];
      const up = b.c >= b.o;
      const cx = this.x(i);
      const xr = Math.round(cx) + 0.5;
      const yo = this.y(pane, b.o), yc = this.y(pane, b.c);
      const yh = this.y(pane, b.h), yl = this.y(pane, b.l);
      const col = up ? COL.up : COL.down;

      if (this.type === 'bars') {
        ctx.strokeStyle = col; ctx.lineWidth = Math.min(2, Math.max(1, this.barW * 0.16));
        ctx.beginPath();
        ctx.moveTo(xr, yh); ctx.lineTo(xr, yl);
        ctx.moveTo(xr - half, yo); ctx.lineTo(xr, yo);
        ctx.moveTo(xr, yc); ctx.lineTo(xr + half, yc);
        ctx.stroke();
        continue;
      }

      ctx.strokeStyle = col;
      ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(xr, yh); ctx.lineTo(xr, yl); ctx.stroke();

      const top = Math.min(yo, yc);
      const bh = Math.max(1, Math.abs(yc - yo));
      if (bw <= 1.6) continue;                       // too dense for bodies
      const hollow = this.type === 'hollow' && up;
      if (hollow) {
        ctx.strokeRect(Math.round(cx - half) + 0.5, Math.round(top) + 0.5, Math.round(bw), Math.round(bh));
      } else {
        ctx.fillStyle = up ? COL.upFill : COL.downFill;
        ctx.fillRect(Math.round(cx - half), Math.round(top), Math.max(1, Math.round(bw)), Math.round(bh));
      }
    }
  }

  /* Plots that a study in its OWN pane asked to draw on the price pane. A
     divergence is the case that needs it: the RSI legs belong in the oscillator,
     the price legs belong on the candles, and they are one object. */
  _foreignPlots(mainPane) {
    for (const run of this.runs) {
      if (run.pane === 'main') continue;
      const foreign = run.plots.filter((p) => p.pane === 'main');
      if (foreign.length) this._plots(mainPane, { ...run, plots: foreign });
    }
  }

  _plots(pane, run) {
    const ctx = this.ctx;
    for (const pl of run.plots) {
      if (pl.type === 'range') continue;
      if (pl.pane === 'main' && !pane.isMain) continue;   // drawn by _foreignPlots
      if (pl.type === 'segment') {
        ctx.save();
        for (const sg of (pl.segs || [])) {
          const x1 = this.x(this.idxOfTime(sg.t1));
          const x2 = this.x(this.idxOfTime(sg.t2));
          const y1 = this.y(pane, sg.v1);
          const y2 = this.y(pane, sg.v2);
          if (![x1, x2, y1, y2].every(Number.isFinite)) continue;
          if (x2 < pane.x - 40 || x1 > pane.x + pane.w + 40) continue;
          // A dark casing first: an RSI divergence leg spans only a few points
          // in a pane scaled 0-100, so it lies almost flat against the RSI line
          // and vanishes without contrast behind it.
          ctx.setLineDash([]);
          ctx.strokeStyle = 'rgba(2,16,31,.8)';
          ctx.lineWidth = (sg.width || 1.4) + 2.4;
          ctx.beginPath();
          ctx.moveTo(x1, y1);
          ctx.lineTo(x2, y2);
          ctx.stroke();

          ctx.strokeStyle = sg.color || COL.text;
          ctx.lineWidth = sg.width || 1.4;
          ctx.setLineDash(sg.dash || []);
          ctx.beginPath();
          ctx.moveTo(x1, y1);
          ctx.lineTo(x2, y2);
          ctx.stroke();
          ctx.setLineDash([]);
          // a dot on each anchor, so the two swings being compared are obvious
          ctx.fillStyle = sg.color || COL.text;
          for (const pt of [[x1, y1], [x2, y2]]) {
            ctx.beginPath();
            ctx.arc(pt[0], pt[1], 2.2, 0, Math.PI * 2);
            ctx.fill();
          }
        }
        ctx.restore();
        continue;
      }
      if (pl.type === 'mark') {
        ctx.save();
        for (const m of (pl.points || [])) {
          const x = this.x(this.idxOfTime(m.t));
          const y = this.y(pane, m.v);
          if (!Number.isFinite(x) || !Number.isFinite(y)) continue;
          if (x < pane.x - 10 || x > pane.x + pane.w + 10) continue;
          ctx.fillStyle = m.color || COL.text;
          const r = 3.4;
          ctx.beginPath();
          if (m.up) {
            ctx.moveTo(x, y - r * 1.4); ctx.lineTo(x - r, y + r * 0.6);
            ctx.lineTo(x + r, y + r * 0.6);
          } else {
            ctx.moveTo(x, y + r * 1.4); ctx.lineTo(x - r, y - r * 0.6);
            ctx.lineTo(x + r, y - r * 0.6);
          }
          ctx.closePath();
          ctx.fill();
        }
        ctx.restore();
        continue;
      }
      if (pl.type === 'zone') {
        ctx.fillStyle = pl.color;
        const y1 = this.y(pane, pl.to), y2 = this.y(pane, pl.from);
        ctx.fillRect(pane.x, y1, pane.w, y2 - y1);
        continue;
      }
      if (pl.type === 'level') {
        ctx.save();
        ctx.strokeStyle = pl.color; ctx.lineWidth = 1;
        if (pl.dash) ctx.setLineDash(pl.dash);
        const y = Math.round(this.y(pane, pl.value)) + 0.5;
        ctx.beginPath(); ctx.moveTo(pane.x, y); ctx.lineTo(pane.x + pane.w, y); ctx.stroke();
        /* An optional name, sitting ON the line at the LEFT edge. A horizontal
           in a sub-pane is otherwise just a line: the reader has to count grid
           steps to work out whether it is the 80 or the 50.

           Left rather than right: the right edge is where the RSI trace ends
           and where the axis sits, so a label there competes with the two
           things you are actually reading. The left is empty history.

           `labelColor` exists because the LINE wants to be faint and the TEXT
           does not. Drawing 9px type in the same 40%-alpha colour as the rule
           it names produced a label you had to lean in to read. */
        if (pl.label) {
          ctx.setLineDash([]);
          ctx.font = '9px "Roboto Condensed", sans-serif';
          ctx.fillStyle = pl.labelColor || pl.color;
          ctx.fillText(pl.label, pane.x + 4, y - 3);
        }
        ctx.restore();
        continue;
      }
      if (pl.type === 'band') {
        ctx.fillStyle = pl.color;
        ctx.beginPath();
        let started = false;
        const i0 = Math.max(0, this.i0), i1 = Math.min(pl.upper.length - 1, this.i1);
        for (let i = i0; i <= i1; i++) {
          if (pl.upper[i] === null) continue;
          const x = this.x(i), y = this.y(pane, pl.upper[i]);
          started ? ctx.lineTo(x, y) : (ctx.moveTo(x, y), started = true);
        }
        for (let i = i1; i >= i0; i--) {
          if (pl.lower[i] === null) continue;
          ctx.lineTo(this.x(i), this.y(pane, pl.lower[i]));
        }
        ctx.closePath(); ctx.fill();
        continue;
      }
      if (pl.type === 'histogram') {
        const bw = Math.max(1, this.barW * 0.66);
        const zeroY = this.y(pane, clamp(0, pane.min, pane.max));
        for (let i = Math.max(0, this.i0); i <= Math.min(pl.data.length - 1, this.i1); i++) {
          const v = pl.data[i];
          if (v === null || v === undefined) continue;
          const y = this.y(pane, v);
          ctx.fillStyle = pl.colors ? pl.colors[i] : 'rgba(73,89,255,.5)';
          ctx.fillRect(Math.round(this.x(i) - bw / 2), Math.round(Math.min(y, zeroY)),
                       Math.max(1, Math.round(bw)), Math.max(1, Math.abs(zeroY - y)));
        }
        continue;
      }
      // line
      ctx.strokeStyle = pl.color; ctx.lineWidth = pl.width || 1.3;
      ctx.save();
      if (pl.dash) ctx.setLineDash(pl.dash);
      ctx.beginPath();
      let pen = false;
      for (let i = Math.max(0, this.i0); i <= Math.min(pl.data.length - 1, this.i1); i++) {
        const v = pl.data[i];
        if (v === null || v === undefined || Number.isNaN(v)) { pen = false; continue; }
        const x = this.x(i), y = this.y(pane, v);
        pen ? ctx.lineTo(x, y) : (ctx.moveTo(x, y), pen = true);
      }
      ctx.stroke();
      ctx.restore();
    }
  }

  _trail(pane) {
    const tr = this.trail;
    if (!tr || !tr.points || tr.points.length < 2) return;
    const ctx = this.ctx;
    ctx.save();
    ctx.strokeStyle = tr.color || COL.sl;
    ctx.lineWidth = tr.width || 1.4;
    if (tr.dash) ctx.setLineDash(tr.dash);
    ctx.beginPath();
    let started = false;
    let prevY = null;
    for (const pt of tr.points) {
      if (!Number.isFinite(pt.price)) continue;
      const x0 = this.x(pt.i - 0.5);
      const x1 = this.x(pt.i + 0.5);
      const y = Math.round(this.y(pane, pt.price)) + 0.5;
      if (!started) { ctx.moveTo(x0, y); started = true; }
      else if (prevY !== null && y !== prevY) { ctx.lineTo(x0, y); }
      ctx.lineTo(x1, y);
      prevY = y;
    }
    if (started) ctx.stroke();
    ctx.setLineDash([]);

    /* Label at the RIGHT end, at the cursor: the level's CURRENT value is the
       one being decided on, not the one the trade opened with. */
    const last = [...tr.points].reverse().find((p) => Number.isFinite(p.price));
    if (last && tr.label) {
      const y = Math.round(this.y(pane, last.price)) + 0.5;
      if (y >= pane.y && y <= pane.y + pane.h) {
        ctx.font = '10px "Roboto Mono", monospace';
        const text = `${tr.label} ${last.price.toFixed(this.digits)}`;
        const w = ctx.measureText(text).width + 8;
        const x = Math.min(this.x(last.i) + 6, pane.x + pane.w - w - 4);
        ctx.fillStyle = 'rgba(2,16,31,.85)';
        ctx.fillRect(x, y - 13, w, 12);
        ctx.fillStyle = tr.color || COL.sl;
        ctx.fillText(text, x + 4, y - 3.5);
      }
    }
    ctx.restore();
  }

  /* WHAT THE RULE WOULD RISK, drawn as an area, and the ONLY shading on this
   * chart. Broker positions get lines (see _positions); a block of colour here
   * always means "the Donchian rule says", never "you are holding".
   *
   * THE RED BLOCK IS REAL. Entry to the 2-ATR stop is the rule's actual risk,
   * fixed at entry, and it is the one level here you could place.
   *
   * THE GREEN BLOCK IS NOT A TARGET. Donchian has none: 138 of 207
   * out-of-sample exits were the trailing channel and none were a target, and
   * capping at 1R was measured to turn +43.7 net R into -2.1. It is drawn to a
   * REFERENCE R multiple and HATCHED so the eye reads it as a measurement
   * rather than a level. The real exit is the moving channel line drawn by
   * _trail, which sits BELOW a long's entry early on and ratchets up -- a shape
   * no static box can show.
   *
   * NO TEXT. The bands carried three prose labels and they crowded the price
   * action they were drawn over. The meaning moved to the Donchian panel, which
   * states the stop, the moving exit level and "no take-profit" in words. */
  _ruleZone(pane) {
    const z = this.ruleZone;
    if (!z || !(z.entry > 0)) return;
    const ctx = this.ctx;
    const x0 = Math.max(this.plot.l, this.x(z.i0 ?? this.i0));
    const x1 = this.plot.r;
    if (x1 - x0 < 4) return;
    const yE = this.y(pane, z.entry);

    ctx.save();
    if (z.stop > 0) {
      const yS = this.y(pane, z.stop);
      ctx.fillStyle = 'rgba(227,28,121,.11)';
      ctx.fillRect(x0, Math.min(yE, yS), x1 - x0, Math.abs(yS - yE));
      ctx.strokeStyle = 'rgba(227,28,121,.6)';
      ctx.setLineDash([4, 3]);
      ctx.lineWidth = 1;
      ctx.strokeRect(x0 + 0.5, Math.min(yE, yS) + 0.5,
                     x1 - x0 - 1, Math.abs(yS - yE) - 1);
    }
    if (z.ref > 0) {
      const yR = this.y(pane, z.ref);
      const top = Math.min(yE, yR);
      const h = Math.abs(yR - yE);
      ctx.fillStyle = 'rgba(147,201,15,.07)';
      ctx.fillRect(x0, top, x1 - x0, h);
      ctx.strokeStyle = 'rgba(147,201,15,.30)';
      ctx.setLineDash([2, 5]);
      ctx.beginPath();
      for (let y = top; y < top + h; y += 7) {
        ctx.moveTo(x0, y); ctx.lineTo(x1, y);
      }
      ctx.stroke();
    }
    ctx.setLineDash([5, 4]);
    ctx.strokeStyle = COL.pos || '#e8eef6';
    ctx.beginPath();
    ctx.moveTo(x0, Math.round(yE) + 0.5);
    ctx.lineTo(x1, Math.round(yE) + 0.5);
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.restore();
  }

  _positions(pane) {
    /* LINES ONLY for broker positions. A risk/reward SHADING was added here and
       removed again: shaded areas are now reserved for the Donchian rule's own
       prediction (_ruleZone), so a block of colour on this chart always means
       "the rule says", never "you are holding". With a dozen positions open the
       shading was also drawing a dozen overlapping blocks over the price action.

       Entry/SL/TP lines are live-account state. They are deliberately absent
       from an exported image -- a snapshot gets shared, and a share should not
       carry position size or where the stops sit. */
    if (this._exporting) return;
    const ctx = this.ctx;
    for (const p of this.positions) {
      const rows = [
        [p.price_open, COL.pos, `${p.side.toUpperCase()} ${p.volume}`],
        [p.sl, COL.sl, 'SL'], [p.tp, COL.tp, 'TP'],
      ];
      for (const [v, col, label] of rows) {
        if (!v) continue;
        const y = Math.round(this.y(pane, v)) + 0.5;
        if (y < pane.y || y > pane.y + pane.h) continue;
        ctx.save();
        ctx.strokeStyle = col; ctx.lineWidth = 1; ctx.setLineDash([5, 4]);
        ctx.beginPath(); ctx.moveTo(pane.x, y); ctx.lineTo(pane.x + pane.w, y); ctx.stroke();
        ctx.setLineDash([]);
        ctx.font = '10px "Roboto Mono", monospace';
        const text = `${label} ${v.toFixed(this.digits)}`;
        const w = ctx.measureText(text).width + 8;
        ctx.fillStyle = 'rgba(2,16,31,.85)';
        ctx.fillRect(pane.x + 4, y - 13, w, 12);
        ctx.fillStyle = col;
        ctx.fillText(text, pane.x + 8, y - 3.5);
        ctx.restore();
      }
    }
  }

  /* Algorithmic trendlines. Each carries the timeframe it was FOUND on, so a
     projected H4 line is visually distinct from a local one: dash length grows
     with the source timeframe and the label names it. Support/resistance keep
     the app's up/down colours. Lines are anchored in time and mapped through
     idxOfTime, which is what lets a higher timeframe's geometry land correctly
     on a lower timeframe's x-axis. */
  /* Regime episodes as a thin strip along the TOP of the price pane, not as a
     full-height tint. A full-height band behind the candles would fight the
     zones and channels already there, and the episode boundary -- the thing the
     strip exists to show -- would be the least visible part of it. A strip
     puts the boundaries on one line where they read as a sequence. */
  _segments(pane) {
    if (!this.segments || !this.segments.length) return;
    const ctx = this.ctx;
    const H = 3;
    const COLOR = {
      trending_up: COL.up, trending_down: COL.down,
      sideways: COL.textFaint || COL.text, transition: COL.line,
    };
    ctx.save();
    for (const sg of this.segments) {
      const x0 = this.x(this.idxOfTime(sg.t0));
      const x1 = this.x(this.idxOfTime(sg.t1));
      if (!Number.isFinite(x0) || !Number.isFinite(x1)) continue;
      if (x1 < pane.x || x0 > pane.x + pane.w) continue;
      const a = Math.max(pane.x, x0), b = Math.min(pane.x + pane.w, x1);
      ctx.globalAlpha = sg.closed ? 0.55 : 0.85;
      ctx.fillStyle = COLOR[sg.kind] || COL.line;
      ctx.fillRect(a, pane.y + 1, Math.max(1, b - a), H);
      /* Only label an episode wide enough to hold its own name. A clipped
         "Downtr…" is worse than no label: it reads as a different word. */
      if (b - a > 62) {
        ctx.font = '8.5px "Roboto Mono", monospace';
        ctx.globalAlpha = 0.7;
        ctx.fillText(sg.label.toUpperCase(), a + 3, pane.y + H + 9);
      }
    }
    ctx.restore();
  }

  /* Zones are drawn UNDER the candles, unlike lines and channels. A band is
     the backdrop a bar prints against -- covering the wick that tested it would
     hide the very evidence the zone is claiming. Everything else in this
     renderer sits above price because it is an annotation ON the bars; a zone
     is the region they moved through.

     Colour is by ROLE AT THE CURRENT PRICE, not by the pivots that built it, so
     a band flips from pink to green the moment price closes above it. That flip
     is the whole idea behind "old resistance becomes support", and a zone
     permanently coloured by its origin could never show it. */
  _zones(pane) {
    if (!this.zones || !this.zones.length) return;
    const ctx = this.ctx;
    const last = this.bars.length ? this.bars[this.bars.length - 1].c : NaN;
    for (const z of this.zones) {
      const yHi = this.y(pane, z.high);
      const yLo = this.y(pane, z.low);
      if (!Number.isFinite(yHi) || !Number.isFinite(yLo)) continue;
      if (yLo < pane.y - 40 || yHi > pane.y + pane.h + 40) continue;
      const role = z.roleAt(last);
      const col = role === 'support' ? COL.up : COL.down;
      /* A one-pixel band is invisible on a tight zone, and a tight zone is the
         strongest kind -- so the drawn height has a floor while the EDGES stay
         truthful, marked by the two boundary strokes. */
      const h = Math.max(3, yLo - yHi);

      ctx.save();
      ctx.globalAlpha = 0.055 + 0.055 * (z.strength / 100);
      ctx.fillStyle = col;
      ctx.fillRect(pane.x, yHi, pane.w, h);

      ctx.globalAlpha = 0.35 + 0.3 * (z.strength / 100);
      ctx.strokeStyle = col;
      ctx.lineWidth = 1;
      ctx.setLineDash([2, 3]);
      for (const y of [yHi, yLo]) {
        ctx.beginPath(); ctx.moveTo(pane.x, y); ctx.lineTo(pane.x + pane.w, y); ctx.stroke();
      }
      ctx.setLineDash([]);

      /* SUPPORT / RESISTANCE, not DEMAND / SUPPLY. This renderer draws
         pivot-cluster zones -- a horizontal level price has TURNED AT
         repeatedly -- while _sdZones draws impulse-origin zones, a base price
         LEFT IN A HURRY. Two unrelated detectors both labelled DEMAND made the
         chart look like it was reporting the same thing twice. The suffix
         disambiguates too: `×N` is a touch count, `●` on an s/d zone means
         fresh. */
      const label = `${role === 'support' ? 'SUPPORT' : 'RESISTANCE'} ×${z.touches}`;
      ctx.font = '9px "Roboto Mono", monospace';
      ctx.globalAlpha = 0.8;
      ctx.fillStyle = col;
      ctx.fillText(label, pane.x + 6, clamp(yHi - 3, pane.y + 9, pane.y + pane.h - 3));
      ctx.restore();
    }
  }

  /* BOS / CHoCH marks: a short horizontal stub at the level that broke,
     running from the swing that made it to the bar that took it out, with the
     label above or below depending on direction.

     Drawn as a LEVEL rather than a marker on the candle because that is what
     the event is about -- the price that gave way. A triangle on the breaking
     bar (which is how the break markers were first drawn, and then removed for
     being unreadable) says an event happened; this says what it happened TO.

     CHoCH is drawn brighter than BOS because it is the one that flips the bias,
     not because it predicts better -- measured, CHoCH beat BOS in two eras out
     of three and lost decisively in the third, so the distinction is
     bookkeeping. Both carry about +4 pp against matched candles. */
  /* Swing highs and lows with their HH / HL / LH / LL label.
   *
   * The dot sits ON the extreme; the label sits outside it -- above a high,
   * below a low -- so the marker never covers the wick it is pointing at.
   *
   * Colour is the LABEL, not the swing kind: HH and HL are both bullish
   * structure and both take the up colour, LH and LL the down colour. Colouring
   * by high-vs-low instead would make every chart half green and half red and
   * say nothing.
   *
   * Density is handled by dropping labels, never dots. At strength 3 on M15
   * there are thousands of pivots; when they crowd, the shape of the sequence
   * is still readable from the dots alone, whereas overlapping text is not
   * readable at all.
   */
  /**
   * The wave count for the bar at the right edge.
   *
   * ONLY THE PRIMARY COUNT IS DRAWN. Three counts on one chart is three
   * polylines through the same pivots, and the picture stops meaning anything
   * -- the alternates live in the panel where they can be read as arguments
   * rather than seen as geometry. The invalidation level is drawn WITH the
   * count because it is the half that can be wrong: a wave label is a story, a
   * price that refutes it is a claim.
   */
  _elliott(pane) {
    const b = this.elliott;
    if (!b || !b.counts || !b.counts.length) return;
    const c = b.counts[0];
    const ctx = this.ctx;
    const names = c.kind === 'impulse' ? ['0', '1', '2', '3', '4', '5'] : ['0', 'A', 'B', 'C'];

    ctx.save();

    /* THE CONE FIRST, behind everything. It is the widest object on the chart
       and the least certain, so it belongs at the bottom of the stack -- and it
       is drawn before the count so the count reads as a line THROUGH a
       distribution rather than as a line with decoration around it.

       Two bands, 50% and 80%, because a single band invites reading its edge as
       a limit. The median is drawn as a line: it is where price ended up half
       the time from states like this one, which is a fact, not a target. */
    if (b.cone && b.cone.bands && b.cone.bands.length) {
      const band = (lo, hi, alpha) => {
        ctx.globalAlpha = alpha;
        ctx.fillStyle = COL.line;
        ctx.beginPath();
        ctx.moveTo(this.x(b.asOfI), this.y(pane, b.close));
        for (const s of b.cone.bands) ctx.lineTo(this.x(b.asOfI + s.ahead), this.y(pane, s.q[hi]));
        for (let k = b.cone.bands.length - 1; k >= 0; k--) {
          const s = b.cone.bands[k];
          ctx.lineTo(this.x(b.asOfI + s.ahead), this.y(pane, s.q[lo]));
        }
        ctx.closePath();
        ctx.fill();
      };
      band(0.1, 0.9, 0.07);
      band(0.25, 0.75, 0.09);
      ctx.globalAlpha = 0.5;
      ctx.strokeStyle = COL.line;
      ctx.lineWidth = 1;
      ctx.setLineDash([2, 3]);
      ctx.beginPath();
      ctx.moveTo(this.x(b.asOfI), this.y(pane, b.close));
      for (const s of b.cone.bands) ctx.lineTo(this.x(b.asOfI + s.ahead), this.y(pane, s.q[0.5]));
      ctx.stroke();
      ctx.setLineDash([]);
    }

    ctx.lineWidth = 1.5;
    ctx.strokeStyle = COL.line;
    ctx.globalAlpha = 0.9;
    ctx.beginPath();
    c.pivots.forEach((p, k) => {
      const x = this.x(this.idxOfTime(p.t));
      const y = this.y(pane, p.price);
      if (k === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    });
    ctx.stroke();

    ctx.font = '600 11px var(--sans, sans-serif)';
    ctx.textAlign = 'center';
    c.pivots.forEach((p, k) => {
      const x = this.x(this.idxOfTime(p.t));
      const y = this.y(pane, p.price);
      if (!Number.isFinite(x) || !Number.isFinite(y)) return;
      const above = k > 0 && p.price > c.pivots[k - 1].price;
      ctx.fillStyle = COL.line;
      ctx.fillText(names[k] || String(k), x, above ? y - 8 : y + 16);
    });

    /* The wave in progress is drawn as an open leg to the current close, so the
       label on screen is the one the panel is talking about. */
    const last = c.pivots[c.pivots.length - 1];
    if (last && Number.isFinite(b.close)) {
      const x0 = this.x(this.idxOfTime(last.t));
      const x1 = this.x(b.asOfI);
      ctx.setLineDash([4, 4]);
      ctx.globalAlpha = 0.7;
      ctx.beginPath();
      ctx.moveTo(x0, this.y(pane, last.price));
      ctx.lineTo(x1, this.y(pane, b.close));
      ctx.stroke();
      ctx.setLineDash([]);
    }

    if (Number.isFinite(c.invalidation)) {
      const y = this.y(pane, c.invalidation);
      if (y > pane.y && y < pane.y + pane.h) {
        ctx.globalAlpha = 0.8;
        ctx.strokeStyle = COL.down;
        ctx.setLineDash([2, 3]);
        ctx.beginPath();
        ctx.moveTo(pane.x, y); ctx.lineTo(pane.x + pane.w, y);
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.fillStyle = COL.down;
        ctx.textAlign = 'left';
        ctx.fillText('invalidation', pane.x + 6, y - 4);
      }
    }

    /* THE PROJECTED PATH, drawn into the empty space to the right of the last
       bar -- the forecast, before the bars that settle it exist. Same colour
       family as the measured count and dashed throughout, because a projected
       leg is a different kind of object from a leg price has actually traded.
       Each point is labelled with the wave it would complete. */
    if (c.projection && c.projection.length && Number.isFinite(b.close)) {
      ctx.globalAlpha = 0.95;
      ctx.strokeStyle = COL.pos;
      ctx.setLineDash([5, 4]);
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.moveTo(this.x(b.asOfI), this.y(pane, b.close));
      for (const pt of c.projection) {
        ctx.lineTo(this.x(b.asOfI + pt.ahead), this.y(pane, pt.price));
      }
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = COL.pos;
      ctx.textAlign = 'center';
      for (const pt of c.projection) {
        const px = this.x(b.asOfI + pt.ahead);
        const py = this.y(pane, pt.price);
        if (!Number.isFinite(px) || !Number.isFinite(py)) continue;
        ctx.beginPath();
        ctx.arc(px, py, 2.5, 0, Math.PI * 2);
        ctx.fill();
        ctx.fillText(pt.label, px, c.dir > 0 ? py - 8 : py + 16);
      }
    }

    if (c.target && c.target.every(Number.isFinite)) {
      const y0 = this.y(pane, c.target[0]);
      const y1 = this.y(pane, c.target[1]);
      ctx.globalAlpha = 0.12;
      ctx.fillStyle = COL.up;
      const x = this.x(b.asOfI);
      ctx.fillRect(x, Math.min(y0, y1), Math.max(20, pane.x + pane.w - x),
        Math.abs(y1 - y0));
    }
    ctx.restore();
  }

  /* The as-of boundary: everything right of this line is what the other lane
     was not allowed to see. */
  _asOfMark(pane) {
    if (!Number.isFinite(this.asOfMark)) return;
    const x = this.x(this.asOfMark);
    if (!Number.isFinite(x) || x < pane.x - 2 || x > pane.x + pane.w + 2) return;
    const ctx = this.ctx;
    ctx.save();
    /* The future half is dimmed rather than the boundary merely drawn: a line
       says where the cursor is, a wash says which half is the answer. */
    ctx.fillStyle = 'rgba(255,158,27,.07)';
    ctx.fillRect(x, pane.y, pane.x + pane.w - x, pane.h);
    ctx.strokeStyle = COL.pos;
    ctx.globalAlpha = 0.85;
    ctx.setLineDash([3, 3]);
    ctx.beginPath();
    ctx.moveTo(x, pane.y);
    ctx.lineTo(x, pane.y + pane.h);
    ctx.stroke();
    ctx.setLineDash([]);
    if (pane.isMain) {
      ctx.font = '600 9.5px var(--sans, sans-serif)';
      ctx.fillStyle = COL.pos;
      ctx.textAlign = 'left';
      ctx.fillText('as of', x + 4, pane.y + 11);
    }
    ctx.restore();
  }

  /* Trade events, at the bar each one happened on.
   *
   * THREE GLYPHS, because three different things happened and conflating them
   * is how a chart starts lying about what was knowable:
   *
   *   signal  a hollow triangle on the bar whose CLOSE triggered the rule. This
   *           is where the decision was made, and no price here was traded.
   *   entry   a filled dot at the price actually filled, on the NEXT bar. It is
   *           one bar to the right of its own signal, always, and seeing that
   *           gap is the point -- a chart that marks only the fill implies the
   *           rule could act on the bar it was reading.
   *   exit    a cross, coloured by whether the trade made money.
   *
   * A faint line joins a signal to its fill so the pair reads as one event.
   */
  _marks(pane) {
    if (!this.marks || !this.marks.length) return;
    const ctx = this.ctx;
    const i0 = Math.floor(this.i0) - 2, i1 = Math.ceil(this.i1) + 2;
    ctx.save();
    for (const m of this.marks) {
      if (m.i < i0 || m.i > i1) continue;
      const x = this.x(m.i);
      const y = this.y(pane, m.price);
      if (!Number.isFinite(x) || !Number.isFinite(y)) continue;
      if (y < pane.y - 8 || y > pane.y + pane.h + 8) continue;
      const up = m.side > 0;
      const col = m.kind === 'exit'
        ? (m.win ? COL.up : COL.down)
        : (up ? COL.up : COL.down);
      ctx.globalAlpha = 0.95;
      ctx.strokeStyle = col;
      ctx.fillStyle = col;
      ctx.lineWidth = 1.4;

      if (m.kind === 'signal') {
        // hollow triangle, pointing the way the trade will face
        const r = 4.5;
        const dir = up ? -1 : 1;
        ctx.beginPath();
        ctx.moveTo(x, y + dir * r);
        ctx.lineTo(x - r, y - dir * r * 0.8);
        ctx.lineTo(x + r, y - dir * r * 0.8);
        ctx.closePath();
        ctx.stroke();
        if (Number.isFinite(m.toX)) {
          ctx.globalAlpha = 0.35;
          ctx.setLineDash([2, 2]);
          ctx.beginPath();
          ctx.moveTo(x, y);
          ctx.lineTo(this.x(m.toI), this.y(pane, m.toPrice));
          ctx.stroke();
          ctx.setLineDash([]);
        }
      } else if (m.kind === 'entry') {
        ctx.beginPath();
        ctx.arc(x, y, 3.2, 0, Math.PI * 2);
        ctx.fill();
      } else {
        const r = 3.4;
        ctx.beginPath();
        ctx.moveTo(x - r, y - r); ctx.lineTo(x + r, y + r);
        ctx.moveTo(x + r, y - r); ctx.lineTo(x - r, y + r);
        ctx.stroke();
      }
    }
    ctx.restore();
  }

  _swings(pane) {
    if (!this.swings || !this.swings.length) return;
    const ctx = this.ctx;
    const i0 = Math.floor(this.i0) - 2, i1 = Math.ceil(this.i1) + 2;
    // Label only when there is room for one: 26px of text plus a gap.
    const roomy = this.barW >= 5;
    let lastLabelX = -Infinity;

    ctx.save();
    ctx.textAlign = 'center';

    /* MAJOR SWINGS ARE DRAWN FIRST, and that ordering is the whole point.
       Labels are rationed by horizontal spacing -- first come, first served --
       so drawing in index order let a minor pivot three bars earlier take the
       slot its major neighbour needed. Majors claim their labels before minors
       are considered. */
    const ordered = [...this.swings].sort(
      (a, b) => (b.major === true) - (a.major === true));

    for (const s of ordered) {
      if (s.i < i0 || s.i > i1) continue;
      const x = this.x(s.i);
      const y = this.y(pane, s.price);
      if (!Number.isFinite(x) || !Number.isFinite(y)) continue;
      if (y < pane.y - 6 || y > pane.y + pane.h + 6) continue;

      const bull = s.label === 'HH' || s.label === 'HL';
      const col = s.label ? (bull ? COL.up : COL.down) : COL.textFaint;
      const major = s.major === true;

      ctx.fillStyle = col;
      ctx.globalAlpha = major ? 1 : 0.45;
      ctx.beginPath();
      ctx.arc(x, y, major ? 3.4 : 1.8, 0, Math.PI * 2);
      ctx.fill();
      /* A ring, not just a bigger dot: size alone is hard to judge against a
         candle wick, and the hollow centre survives being drawn over one. */
      if (major) {
        ctx.strokeStyle = col;
        ctx.lineWidth = 1;
        ctx.globalAlpha = 0.55;
        ctx.beginPath();
        ctx.arc(x, y, 6, 0, Math.PI * 2);
        ctx.stroke();
      }

      if (!s.label || !roomy) continue;
      // minors never outbid a major for a label slot
      const gap = major ? 26 : 34;
      if (x - lastLabelX < gap && !major) continue;
      if (major && this._labelTaken && this._labelTaken.some(
        (lx) => Math.abs(lx - x) < 22)) continue;
      if (!major && this._labelTaken && this._labelTaken.some(
        (lx) => Math.abs(lx - x) < 30)) continue;
      (this._labelTaken = this._labelTaken || []).push(x);
      lastLabelX = x;
      ctx.font = major ? 'bold 9px "Roboto Mono", monospace'
        : '8.5px "Roboto Mono", monospace';
      ctx.globalAlpha = major ? 1 : 0.6;
      ctx.fillText(s.label, x, s.isHigh ? y - (major ? 10 : 6)
        : y + (major ? 17 : 13));
    }
    this._labelTaken = null;
    ctx.restore();
  }

  _msEvents(pane) {
    if (!this.msEvents || !this.msEvents.length) return;
    const ctx = this.ctx;
    const i0 = Math.floor(this.i0), i1 = Math.ceil(this.i1);
    for (const e of this.msEvents) {
      if (e.i < i0 - 5 || e.levelI > i1 + 5) continue;
      const y = this.y(pane, e.level);
      if (!Number.isFinite(y) || y < pane.y - 20 || y > pane.y + pane.h + 20) continue;
      const xa = this.x(e.levelI);
      const xb = this.x(e.i);
      if (!Number.isFinite(xa) || !Number.isFinite(xb)) continue;
      const bull = e.direction === 'bullish';
      const col = bull ? COL.up : COL.down;
      const choch = e.kind === 'choch';

      /* DISPLACEMENT DECIDES THE WEIGHT.
       *
       * A break is a break by the detector's rule -- a close through the level
       * -- but a close 0.16 ATR past it and one 1.4 ATR past it are not the
       * same event, and printing both as `BOS` says they are. Marks that clear
       * `Chart.DISPLACEMENT_ATR` draw at full weight with a bolder label;
       * marginal ones stay on the chart at roughly half, because they ARE
       * structure, just weak structure.
       *
       * Nothing is hidden and nothing is filtered: the label a reader has been
       * looking at for months still appears in the same place. What changes is
       * that the ones worth acting on stop looking identical to the ones that
       * are not. */
      const disp = Number.isFinite(e.dispAtr) ? e.dispAtr : 0;
      const strong = disp >= Chart.DISPLACEMENT_ATR;
      /* TWO INDEPENDENT AXES, and they are deliberately not merged into one
         score. Displacement asks how HARD the level broke; external asks how
         much the LEVEL was worth. A hard break of an internal pivot and a
         gentle break of a major swing are different events, and a single
         combined weight would render them identically. */
      const external = e.external === true;
      const w8 = (strong ? 1 : 0.45) * (external ? 1 : 0.7);

      ctx.save();
      ctx.strokeStyle = col;
      ctx.globalAlpha = (choch ? 0.9 : 0.55) * w8;
      ctx.lineWidth = (choch ? 1.4 : 1) * (strong ? 1 : 0.8);
      ctx.setLineDash(choch ? [] : [4, 3]);
      ctx.beginPath();
      ctx.moveTo(Math.max(pane.x, xa), y);
      ctx.lineTo(Math.min(pane.x + pane.w, xb), y);
      ctx.stroke();
      ctx.setLineDash([]);

      ctx.font = strong ? 'bold 8.5px "Roboto Mono", monospace'
        : '8.5px "Roboto Mono", monospace';
      ctx.fillStyle = col;
      ctx.globalAlpha = (choch ? 0.95 : 0.7) * w8;
      /* The prefix is the cheapest possible way to carry the distinction, and
         it survives being read in greyscale or by someone who never learns what
         the weights mean. */
      const label = (external ? '' : 'i') + (choch ? 'CHoCH' : 'BOS');
      const w = ctx.measureText(label).width;
      const lx = clamp(xb - w - 2, pane.x + 2, pane.x + pane.w - w - 2);
      ctx.fillText(label, lx, bull ? y - 3 : y + 9);
      ctx.restore();
    }
  }

  /* Supply/demand zones (js/chart/supplydemand.js) are drawn UNDER the candles
     like pivot-cluster zones, but they start at the bar they were CONFIRMED and
     run right rather than spanning the whole chart. That is the honest shape:
     the zone did not exist before its impulse finished, and drawing it across
     earlier bars would imply it was available to trade then.

     A FRESH zone (never revisited) is drawn solid; a used one is dashed and
     fainter. Fresh beat tested in all three eras measured (+6.16 vs +4.93,
     +7.21 vs +6.53, +4.28 vs +3.39), so the distinction is real -- but the gap
     is about one percentage point, so it is a hint in the styling rather than a
     different colour. */
  /* TP bands.
   *
   * Drawn from the LAST BAR rightward, not across the whole chart: a target is
   * a claim about where price might go, and painting it back over history it
   * never reached reads as though it had. Deliberately thin, unfilled and
   * dashed -- the supply/demand renderer above fills its zones because a zone
   * is a region price moved through, whereas these are lines price has not
   * reached yet, and giving them the same visual weight as observed structure
   * would overstate them.
   */
  _targets(pane) {
    if (!this.targets || !this.targets.length) return;
    const ctx = this.ctx;
    /* Anchor at the AS-OF bar when there is one, not at the end of the array.
       A replay hands the chart the whole series and marks how far knowledge
       reaches, so `bars.length - 1` is 1,950 bars past the cursor and 12,418px
       off the right edge -- the bands computed correctly and drew nothing at
       all. "From where knowledge stops, rightward" is the right anchor in both
       contexts: the live chart has no mark and falls back to its last bar. */
    const anchor = Number.isFinite(this.asOfMark) ? this.asOfMark : this.bars.length - 1;
    const x0 = Math.max(pane.x, this.x(anchor));
    const x1 = pane.x + pane.w;
    if (x1 <= x0) return;
    ctx.save();
    ctx.font = '9px "Roboto Mono", monospace';
    for (const b of this.targets) {
      const yHi = this.y(pane, b.high);
      const yLo = this.y(pane, b.low);
      if (!Number.isFinite(yHi) || !Number.isFinite(yLo)) continue;
      if (yLo < pane.y - 40 || yHi > pane.y + pane.h + 40) continue;
      ctx.globalAlpha = 0.07;
      ctx.fillStyle = COL.up;
      ctx.fillRect(x0, yHi, x1 - x0, Math.max(2, yLo - yHi));
      ctx.globalAlpha = 0.55;
      ctx.strokeStyle = COL.up;
      ctx.lineWidth = 1;
      ctx.setLineDash([4, 4]);
      for (const y of [yHi, yLo]) {
        ctx.beginPath(); ctx.moveTo(x0, y); ctx.lineTo(x1, y); ctx.stroke();
      }
      ctx.setLineDash([]);
      ctx.globalAlpha = 0.9;
      ctx.fillStyle = COL.up;
      /* The CENTRE is the level; the band is only there to be visible at a
         glance. Labelling the range implied the whole span mattered. */
      const label = `${b.key} ${b.r}R`;
      ctx.fillText(label, x0 + 4,
        clamp((yHi + yLo) / 2 + 3, pane.y + 9, pane.y + pane.h - 3));
    }
    ctx.restore();
  }

  _sdZones(pane) {
    if (!this.sdZones || !this.sdZones.length) return;
    const ctx = this.ctx;
    for (const z of this.sdZones) {
      const yHi = this.y(pane, z.high);
      const yLo = this.y(pane, z.low);
      if (!Number.isFinite(yHi) || !Number.isFinite(yLo)) continue;
      if (yLo < pane.y - 40 || yHi > pane.y + pane.h + 40) continue;
      const x0 = Math.max(pane.x, this.x(this.idxOfTime(z.tConfirmed)));
      const x1 = pane.x + pane.w;
      if (x1 <= x0) continue;
      const col = z.kind === 'demand' ? COL.up : COL.down;
      const h = Math.max(3, yLo - yHi);

      ctx.save();
      ctx.globalAlpha = (z.fresh ? 0.10 : 0.055) + 0.05 * (z.strength / 100);
      ctx.fillStyle = col;
      ctx.fillRect(x0, yHi, x1 - x0, h);

      ctx.globalAlpha = z.fresh ? 0.75 : 0.4;
      ctx.strokeStyle = col;
      ctx.lineWidth = 1;
      ctx.setLineDash(z.fresh ? [] : [3, 3]);
      for (const y of [yHi, yLo]) {
        ctx.beginPath(); ctx.moveTo(x0, y); ctx.lineTo(x1, y); ctx.stroke();
      }
      ctx.setLineDash([]);

      const label = `${z.kind === 'demand' ? 'DEMAND' : 'SUPPLY'}`
        + `${z.fresh ? ' ●' : ' ×' + z.touches}`;
      ctx.font = '9px "Roboto Mono", monospace';
      ctx.globalAlpha = 0.85;
      ctx.fillStyle = col;
      ctx.fillText(label, x0 + 4, clamp(yHi - 3, pane.y + 9, pane.y + pane.h - 3));
      ctx.restore();
    }
  }

  /* A channel is drawn as a FILLED corridor with a dashed median, not as two
     more lines. Two extra lines would compete with the trendlines already on
     the chart and the reader would have to work out which pair belongs
     together; a tinted band says "price lives in here" at a glance, which is
     the entire claim the object makes.

     The fill is deliberately faint. It sits under the candles in z-order and
     under the trendlines, because it is context for them, not a competitor. */
  /* How far past its own data a channel may be extrapolated, as a fraction of
     its measured length. A corridor is a claim about the bars it was fitted to;
     beyond them it is a guess, and the guess should not outgrow the evidence.
     Measured before this cap existed, every channel on a 15m gold chart ran
     54-108% past its own span -- a 98-bar channel extended another 106 bars. */
  static PROJECT_FRACTION = 0.25;

  /* The close-beyond-level, in ATR, at which a structure break is drawn as a
     real one. Not a taste: it is `DISPLACEMENT_V1.displacement_atr` from
     sim/tl/experiments.py, the threshold behind the only positive-expectancy
     cell this project has measured. Changing it here would decouple the chart
     from the spec, which is the whole point of it being the same number. */
  static DISPLACEMENT_ATR = 1.0;

  /*
   * Which colour each channel gets, when two of them cover the same prices.
   *
   * Colour normally encodes DIRECTION -- ascending green, descending pink. That
   * is useless for telling two channels apart at the moment you most need to,
   * because overlapping corridors are usually overlapping precisely BECAUSE
   * they are the same shape, so both come out the same colour and the reader
   * sees four rails of one hue with no way to pair them up.
   *
   * So inside an overlapping group, colour switches to POSITION: the upper
   * corridor green, the lower one red. Outside a group, direction still rules,
   * because a lone channel has nothing to be confused with.
   *
   * Green now means two things depending on context, which is exactly the sort
   * of quiet ambiguity worth refusing -- so the label says which reading
   * applies, appending UPPER or LOWER whenever position took over. A reader who
   * sees neither word is looking at a direction colour.
   *
   * Bands are compared over the VISIBLE window, not at the last bar: two
   * corridors converging off-screen right are not overlapping anywhere the
   * reader can see, and two that cross mid-screen are, even if they happen to
   * be a hair apart at the edge.
   */
  /**
   * Which overlapping corridor is on top, for colouring.
   *
   * TWO CORRIDORS OVERLAP WHEN THEY SHARE A PRICE AT THE SAME X. The first
   * version compared bounding boxes -- min and max of each corridor's rails
   * across the whole visible window -- which is the classic AABB false
   * positive: two steep bands can each sweep 400 points over the window,
   * "overlap" on paper, and never come within 200 points of each other at any
   * single x. Sampling the pair at the same instants answers the question the
   * eye is actually asking.
   *
   * ORDER IS TAKEN AT THE RIGHT EDGE, not from a window average. Corridors
   * cross: measured on 15m gold, a pair 700 bars back swapped vertical order
   * once mid-window, so an average put one of them on top when it was below for
   * half the chart. The right edge is where the label sits and where the reader
   * is standing, so that is the x the words UPPER and LOWER describe.
   *
   * A pair that crosses inside the view is still labelled from the right edge,
   * and the crossing is visible on its own -- the bands physically swap. What
   * is fixed here is that the label now agrees with at least one definite
   * place, rather than with an average that can match neither end.
   */
  _channelColours(leftT, rightT) {
    const chans = this.channels || [];
    const SAMPLES = 48;
    const span = rightT - leftT;

    /* Every corridor sampled at the SAME instants, so pairs are comparable
       column by column. `mid` is the right edge alone. */
    const track = chans.map((c) => {
      const lo = new Array(SAMPLES + 1);
      const hi = new Array(SAMPLES + 1);
      for (let k = 0; k <= SAMPLES; k++) {
        const t = leftT + (span * k) / SAMPLES;
        lo[k] = c.lowerAt(t);
        hi[k] = c.upperAt(t);
      }
      const rLo = c.lowerAt(rightT), rHi = c.upperAt(rightT);
      if (!Number.isFinite(rLo) || !Number.isFinite(rHi)) return null;
      return { lo, hi, mid: (rLo + rHi) / 2 };
    });

    const sharesAnX = (a, b) => {
      for (let k = 0; k <= SAMPLES; k++) {
        const aL = a.lo[k], aH = a.hi[k], bL = b.lo[k], bH = b.hi[k];
        if (!Number.isFinite(aL) || !Number.isFinite(aH)
            || !Number.isFinite(bL) || !Number.isFinite(bH)) continue;
        if (aL <= bH && bL <= aH) return true;
      }
      return false;
    };

    // Small n (max 3 per timeframe), so pairwise grouping is cheaper than a
    // union-find and easier to read.
    const group = chans.map(() => -1);
    let next = 0;
    for (let i = 0; i < chans.length; i++) {
      if (!track[i]) continue;
      if (group[i] < 0) group[i] = next++;
      for (let j = i + 1; j < chans.length; j++) {
        if (!track[j]) continue;
        if (!sharesAnX(track[i], track[j])) continue;
        if (group[j] < 0) group[j] = group[i];
        else if (group[j] !== group[i]) {           // merge the two groups
          const from = group[j], to = group[i];
          for (let k = 0; k < group.length; k++) if (group[k] === from) group[k] = to;
        }
      }
    }

    const members = new Map();
    group.forEach((g, i) => {
      if (g < 0 || !track[i]) return;
      if (!members.has(g)) members.set(g, []);
      members.get(g).push(i);
    });

    const out = chans.map((c) => ({
      col: c.direction === 'up' ? COL.up : c.direction === 'down' ? COL.down : COL.text,
      role: null,
    }));
    for (const idxs of members.values()) {
      if (idxs.length < 2) continue;                // alone: keep direction
      idxs.sort((a, b) => track[b].mid - track[a].mid);   // highest at the right edge
      idxs.forEach((i, k) => {
        if (k === 0) { out[i].col = COL.up; out[i].role = 'UPPER'; }
        else if (k === idxs.length - 1) { out[i].col = COL.down; out[i].role = 'LOWER'; }
        /* Three or more in one group: only the outermost two get a position
           colour. Anything between them is neither upper nor lower, and naming
           it either would be a lie told in colour. */
        else { out[i].col = COL.text; out[i].role = 'MIDDLE'; }
      });
    }
    return out;
  }

  _channels(pane) {
    if (!this.channels || !this.channels.length) return;
    const ctx = this.ctx;
    const rightT = this.tAt(Math.min(this.i1, this.bars.length - 1 + this.rightPad()));
    const leftT = this.tAt(this.i0);
    const paint = this._channelColours(leftT, rightT);
    for (const [ci, c] of this.channels.entries()) {
      const own = c.timeframe === this.tf;
      const col = paint[ci].col;
      const role = paint[ci].role;

      /* THREE times, not two. The old code drew from the view's left edge to
         the view's right edge, which quietly turned every corridor into an
         infinite one: a channel whose data ended 148 bars ago was still drawn
         to the current price as though it were live.

           t0    where drawing starts   (its own start, or the view edge)
           tMid  where its DATA ends    (c.tEnd -- the last bar it was fitted to)
           t1    where drawing stops    (a bounded projection past tMid) */
      const t0 = Math.max(c.tStart, leftT);
      const tMid = Math.min(c.tEnd, rightT);
      /* The cap is measured in BARS, not wall-clock milliseconds. Gold stops
         over the weekend, so a channel spanning 98 bars can span 298 bars'
         worth of clock -- and a fraction of the clock span projects three times
         too far. Same index-vs-time trap the slope conversion documents.
         `tAt` indexes the bar array, so the index has to be whole. */
      const iEnd = this.idxOfTime(c.tEnd);
      const spanBars = Math.max(0, iEnd - this.idxOfTime(c.tStart));
      const lastT = this.bars.length ? this.bars[this.bars.length - 1].t : rightT;

      /* A corridor that is STILL FORMING runs to the right edge; one that has
         already finished stops at the 25% cap.

         The cap exists so a dead channel cannot pretend to reach current price.
         It has no business shortening a live one: those rails are where the
         next bars would sit if the corridor holds, which is the only forward
         statement a channel makes and the reason to draw it at all.

         `c.live` is set by the detector in main.js, not inferred here. A
         bar-count test on `tEnd` cannot answer it: `tEnd` is the last PIVOT and
         pivots confirm several bars late, so live corridors routinely end 20+
         bars back and any tolerance tight enough to be meaningful calls them
         all finished. */
      const live = c.live === true;
      const t1 = live ? rightT : Math.min(rightT,
        this.tAt(Math.round(iEnd + spanBars * Chart.PROJECT_FRACTION)));
      if (tMid <= t0 && t1 <= t0) continue;     // entirely off-screen left

      const X = (t) => this.x(this.idxOfTime(t));
      const LO = (t) => this.y(pane, c.lowerAt(t));
      const HI = (t) => this.y(pane, c.upperAt(t));

      const weak = c.kind === 'projected';
      ctx.save();

      /* THREE segments, because there are three different claims:
       *
       *   measured     the bars the corridor was fitted to
       *   projected    past the fit, but still over bars that EXIST
       *   future       past the last bar, where there is no data at all
       *
       * Only the third is dashed. Dashing the second read as a different KIND
       * of line rather than the same rail with less behind it -- and this chart
       * already spends dashes on higher-timeframe lines and on the median, so a
       * third pattern over real bars is a vocabulary rather than a hint. Beyond
       * the last bar the dash means something no weight could: there is nothing
       * here yet.
       *
       * A projected channel already has one assumed RAIL and is drawn fainter
       * for it. That is a separate axis and both apply. */
      const seg = (ta, tb, mode) => {
        const solid = mode !== 'future';
        if (!(tb > ta)) return;
        const xa = X(ta), xb = X(tb);
        const loA = LO(ta), hiA = HI(ta), loB = LO(tb), hiB = HI(tb);
        if (![loA, hiA, loB, hiB].every(Number.isFinite)) return;

        const dim = mode === 'measured' ? 1 : mode === 'projected' ? 0.45 : 0.3;
        ctx.globalAlpha = (own ? 0.10 : 0.06) * (weak ? 0.6 : 1) * dim;
        ctx.fillStyle = col;
        ctx.beginPath();
        ctx.moveTo(xa, hiA); ctx.lineTo(xb, hiB);
        ctx.lineTo(xb, loB); ctx.lineTo(xa, loA);
        ctx.closePath();
        ctx.fill();

        ctx.globalAlpha = (own ? 0.75 : 0.5) * (weak ? 0.65 : 1)
          * (mode === 'measured' ? 1 : mode === 'projected' ? 0.55 : 0.7);
        ctx.strokeStyle = col;
        ctx.lineWidth = own ? 1.4 : 1.1;
        ctx.setLineDash(solid ? [] : [5, 4]);
        for (const [ya, yb] of [[hiA, hiB], [loA, loB]]) {
          ctx.beginPath(); ctx.moveTo(xa, ya); ctx.lineTo(xb, yb); ctx.stroke();
        }
        // the median, dashed, as every hand-drawn channel carries
        ctx.globalAlpha *= 0.6;
        ctx.setLineDash([5, 5]);
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(xa, (hiA + loA) / 2); ctx.lineTo(xb, (hiB + loB) / 2);
        ctx.stroke();
        ctx.setLineDash([]);
      };

      const onBars = (t) => Math.min(t, lastT);   // clip to where data exists
      seg(t0, onBars(tMid), 'measured');
      seg(Math.max(t0, onBars(tMid)), onBars(t1), 'projected');
      seg(Math.max(t0, lastT), t1, 'future');    // dashed: no bars out here

      const name = c.direction === 'up' ? 'ASCENDING' : c.direction === 'down' ? 'DESCENDING' : 'RANGE';
      const label = `${TF_LABEL[c.timeframe] || c.timeframe} ${name} CHANNEL`
        + `${weak ? ' (proj)' : ''}${role ? ` · ${role}` : ''}`;
      ctx.font = '9px "Roboto Mono", monospace';
      ctx.globalAlpha = 0.75;
      ctx.fillStyle = col;
      // anchored to where the drawing actually STOPS, which is no longer the
      // right edge of the pane
      const tLab = Math.max(t0, t1);
      const xLab = X(tLab);
      const my = (HI(tLab) + LO(tLab)) / 2;
      const w = ctx.measureText(label).width;
      if (Number.isFinite(my)) {
        ctx.fillText(label, Math.max(pane.x + 6, xLab - w - 8),
                     clamp(my - 4, pane.y + 10, pane.y + pane.h - 6));
      }
      ctx.restore();
    }
  }

  _autoLines(pane) {
    if (!this.autoLines || !this.autoLines.length) return;
    const ctx = this.ctx;
    const rightT = this.tAt(Math.min(this.i1, this.bars.length - 1 + this.rightPad()));
    for (const l of this.autoLines) {
      const col = l.kind === 'support' ? COL.up : COL.down;
      const own = l.tf === this.tf;
      const x1 = this.x(this.idxOfTime(l.p1.t));
      const xr = this.x(this.idxOfTime(rightT));
      const y1 = this.y(pane, l.p1.price);
      const yr = this.y(pane, l.p1.price + l.slope * (rightT - l.p1.t));
      if (!Number.isFinite(y1) || !Number.isFinite(yr)) continue;

      /* `offered` marks a line the engine would hand a strategy (quality >= its
         measured threshold). Lines below it are still drawn -- they are real
         structure and hiding them left charts blank -- but at half weight, so
         the eye separates "this is a level" from "this is a level worth
         trading" without reading a single label. */
      const weak = l.offered === false;
      ctx.save();
      ctx.strokeStyle = col;
      ctx.globalAlpha = (own ? 0.95 : 0.62) * (l.status === 'ACTIVE' ? 1 : 0.82)
                        * (weak ? 0.45 : 1);
      ctx.lineWidth = (own ? 1.5 : 2) * (weak ? 0.7 : 1);
      /* EVERY auto line is solid, whatever frame it came from. Dash length
         used to grow with the source timeframe, which made a projected line
         readable as "from higher up" at a glance -- but the line already
         carries its frame in its own label (`M30 S x3`), so the pattern was a
         second encoding of something already on screen, and it cost the
         legibility of the line itself. Frame now shows in weight and alpha
         only: a projected line is dimmer and slightly heavier. */
      ctx.setLineDash([]);
      ctx.beginPath();
      ctx.moveTo(x1, y1);
      ctx.lineTo(xr, yr);
      ctx.stroke();
      ctx.setLineDash([]);

      /* Anchors, so it is obvious WHICH swings built the line -- a trendline
         with no visible pivots is an assertion, and the two dots are the
         evidence for it. Drawn for projected lines too, not just own-timeframe:
         the anchors of an H4 line are exactly what a 15m chart cannot show you
         otherwise, which is the case where they matter most.

         The second anchor is hollow. It is the later of the two, so it is the
         one price is currently working away from, and distinguishing them makes
         the line's direction readable without following it to its end. */
      ctx.globalAlpha = weak ? 0.5 : 0.95;
      ctx.lineWidth = 1.2;
      ctx.strokeStyle = col;
      ctx.fillStyle = col;
      for (const [k, p] of [l.p1, l.p2].entries()) {
        const px = this.x(this.idxOfTime(p.t));
        const py = this.y(pane, p.price);
        if (!Number.isFinite(px) || !Number.isFinite(py)) continue;
        if (px < pane.x - 4 || px > pane.x + pane.w + 4) continue;
        ctx.beginPath();
        ctx.arc(px, py, own ? 3 : 2.5, 0, Math.PI * 2);
        if (k === 1) {
          ctx.fillStyle = COL.bg || '#02101f';
          ctx.fill();
          ctx.stroke();
          ctx.fillStyle = col;
        } else {
          ctx.fill();
        }
      }

      // label rides the right-hand end of the line
      // lifecycle state is the point of the port: CONFIRMED = the market
      // acknowledged the line, ACTIVE = it has been retested since
      const state = l.status === 'ACTIVE' ? ' ●' : l.status === 'CONFIRMED' ? ' ○' : '';
      const label = `${TF_LABEL[l.tf] || l.tf} ${l.kind === 'support' ? 'S' : 'R'}×${l.touches}${state}`;
      ctx.font = '9.5px "Roboto Mono", monospace';
      const w = ctx.measureText(label).width + 8;
      const ly = clamp(yr, pane.y + 8, pane.y + pane.h - 4);
      ctx.globalAlpha = 0.92;
      ctx.fillStyle = 'rgba(2,16,31,.8)';
      ctx.fillRect(xr - w - 4, ly - 11, w, 11);
      ctx.fillStyle = col;
      ctx.fillText(label, xr - w, ly - 2.5);
      ctx.restore();
    }
  }

  _drawings(pane) {
    const ctx = this.ctx;
    const all = this.pending ? [...this.drawings, this.pending] : this.drawings;
    for (const d of all) {
      const sel = !this._exporting && d === this.selectedDrawing;
      const hov = !this._exporting && d === this.hoverDrawing;
      const col = (sel || hov) ? COL.pink : (d.color || COL.draw);
      ctx.save();
      ctx.strokeStyle = col;
      ctx.fillStyle = col;
      ctx.lineWidth = sel ? 2.6 : hov ? 2 : 1.4;
      const p1 = d.p1 ? { x: this.x(this.idxOfTime(d.p1.t)), y: this.y(pane, d.p1.price) } : null;
      const p2 = d.p2 ? { x: this.x(this.idxOfTime(d.p2.t)), y: this.y(pane, d.p2.price) } : null;

      if (d.type === 'hline' && p1) {
        ctx.beginPath(); ctx.moveTo(pane.x, p1.y); ctx.lineTo(pane.x + pane.w, p1.y); ctx.stroke();
        ctx.font = '10px "Roboto Mono", monospace';
        ctx.fillText(d.p1.price.toFixed(this.digits), pane.x + pane.w - 78, p1.y - 4);
      } else if ((d.type === 'trend' || d.type === 'ray') && p1 && p2) {
        let ex = p2.x, ey = p2.y;
        if (d.type === 'ray') {
          const dx = p2.x - p1.x, dy = p2.y - p1.y;
          if (dx !== 0) { const k = (pane.x + pane.w - p1.x) / dx; ex = p1.x + dx * k; ey = p1.y + dy * k; }
        }
        ctx.beginPath(); ctx.moveTo(p1.x, p1.y); ctx.lineTo(ex, ey); ctx.stroke();
      } else if (d.type === 'rect' && p1 && p2) {
        ctx.globalAlpha = 0.14;
        ctx.fillRect(Math.min(p1.x, p2.x), Math.min(p1.y, p2.y), Math.abs(p2.x - p1.x), Math.abs(p2.y - p1.y));
        ctx.globalAlpha = 1;
        ctx.strokeRect(Math.min(p1.x, p2.x), Math.min(p1.y, p2.y), Math.abs(p2.x - p1.x), Math.abs(p2.y - p1.y));
      } else if (d.type === 'fib' && p1 && p2) {
        ctx.font = '10px "Roboto Mono", monospace';
        const lo = Math.min(d.p1.price, d.p2.price), hi = Math.max(d.p1.price, d.p2.price);
        const xa = Math.min(p1.x, p2.x), xb = Math.max(p1.x, p2.x);
        FIB.forEach((f, k) => {
          const v = d.p1.price > d.p2.price ? hi - (hi - lo) * f : lo + (hi - lo) * f;
          const y = this.y(pane, v);
          ctx.globalAlpha = 0.85;
          ctx.setLineDash(k === 0 || k === FIB.length - 1 ? [] : [4, 3]);
          ctx.beginPath(); ctx.moveTo(xa, y); ctx.lineTo(Math.max(xb, xa + 60), y); ctx.stroke();
          ctx.setLineDash([]);
          ctx.fillText(`${(f * 100).toFixed(1)}%  ${v.toFixed(this.digits)}`, xa + 4, y - 3);
        });
      }
      ctx.restore();
    }
  }

  /**
   * The latest value of a tagged series, boxed on the axis.
   *
   * The price pane has had this since the beginning (see _lastPrice); a study
   * pane had nothing, so "what is RSI right now" meant eyeballing the trace
   * against a gridline. It reads the forming bar, so it moves on every tick
   * rather than once a bar.
   *
   * Opt-in per plot via `tag: true`, not automatic: a pane with three series
   * would stack three boxes in one axis column, and the latest value of a MACD
   * histogram bar is not a number anyone reads.
   */
  _valueTag(pane) {
    if (this._exporting) return;
    const ctx = this.ctx;
    for (const run of pane.runs) {
      for (const pl of run.plots) {
        if (!pl.tag || !Array.isArray(pl.data)) continue;
        let v = null;
        for (let i = Math.min(pl.data.length - 1, this.i1); i >= 0; i--) {
          const x = pl.data[i];
          if (x !== null && x !== undefined && !Number.isNaN(x)) { v = x; break; }
        }
        if (v === null) continue;
        const y = this.y(pane, v);
        if (y < pane.y || y > pane.y + pane.h) continue;
        ctx.save();
        ctx.fillStyle = pl.color;
        ctx.fillRect(this.plot.r, y - 8, AXIS_W, 16);
        ctx.fillStyle = '#02101f';
        ctx.font = '600 9.5px "Roboto Mono", monospace';
        ctx.textAlign = 'left';
        ctx.fillText(v.toFixed(pl.tagDigits ?? 1), this.plot.r + 5, y + 3.5);
        ctx.restore();
      }
    }
  }

  _axisY(pane) {
    const ctx = this.ctx;
    ctx.fillStyle = this._exporting ? 'transparent' : COL.axisBg;
    ctx.fillRect(this.plot.r, pane.y, AXIS_W, pane.h);
    ctx.strokeStyle = COL.grid;
    ctx.beginPath();
    ctx.moveTo(this.plot.r + 0.5, pane.y); ctx.lineTo(this.plot.r + 0.5, pane.y + pane.h);
    ctx.stroke();
    ctx.fillStyle = COL.text;
    ctx.font = '9.5px "Roboto Mono", monospace';
    ctx.textAlign = 'left';
    const step = pane.step || niceStep((pane.max - pane.min) / 4);
    const compactAxis = pane.runs.some((r) => r.plots.some((p) => p.fmt === 'compact'));
    /* A zone tag is a specific, named level; a round-number tick is scenery.
       When they collide the tick yields -- drawn underneath, both end up
       unreadable, which is worse than losing one gridline number.

       A LABELLED LEVEL claims its slot the same way. The RSI pane names its own
       bands (overbought 80 / 50 / oversold 20), and the axis was printing "50.0"
       an inch from a line already labelled "50" -- the same number twice, in two
       formats, in a pane 90px tall. The named one wins because it says what the
       level IS and not merely where it sits. */
    const taken = pane.isMain ? this._zoneLabelSlots(pane) : [];
    /* A labelled level and the live value tag both CLAIM an axis slot, for the
       same reason a zone tag does: a round-number tick drawn underneath either
       leaves both unreadable. */
    for (const run of pane.runs) {
      for (const pl of run.plots) {
        if (pl.type === 'level' && pl.label) {
          taken.push({ y: this.y(pane, pl.value) });
        }
        if (pl.tag && Array.isArray(pl.data)) {
          for (let i = Math.min(pl.data.length - 1, this.i1); i >= 0; i--) {
            const v = pl.data[i];
            if (v !== null && v !== undefined && !Number.isNaN(v)) {
              taken.push({ y: this.y(pane, v) });
              break;
            }
          }
        }
      }
    }
    for (let v = Math.ceil(pane.min / step) * step; v <= pane.max; v += step) {
      const y = this.y(pane, v);
      if (y < pane.y + 6 || y > pane.y + pane.h - 2) continue;
      if (taken.some((t) => Math.abs(t.y - y) < 12)) continue;
      const label = compactAxis ? compact(v)
        : pane.isMain ? v.toFixed(this.digits)
          : Math.abs(v) >= 1000 ? compact(v) : v.toFixed(step < 0.01 ? 4 : step < 1 ? 2 : 1);
      ctx.fillText(label, this.plot.r + 6, y + 3.5);
    }
  }

  /* Zone edges, priced on the axis.
   *
   * The bands alone say WHERE a level is but not WHAT it is -- reading a price
   * off one meant hovering the crosshair. Every visible edge now gets its own
   * tag, so the chart answers "what is that level" without an interaction.
   *
   * Three things this has to get right or it becomes noise:
   *   - the LAST-PRICE tag wins. It is drawn later and would paint over a zone
   *     tag anyway; a half-covered number reads as a bug, so anything within
   *     its box (and the countdown under it) is suppressed instead.
   *   - edges closer than a label's height collapse to one tag. Two numbers
   *     three pixels apart are unreadable, and a tight zone -- the strongest
   *     kind -- is exactly where both edges nearly coincide.
   *   - strongest first, so when tags do compete the weaker one drops.
   */
  /** Where the zone tags will sit. Computed separately from drawing them so
   *  the AXIS can consult it and skip its own ticks -- otherwise a round-number
   *  tick renders directly under a zone tag and both become unreadable. */
  _zoneLabelSlots(pane) {
    if (!this.zones || !this.zones.length) return [];
    const last = this.bars.length ? this.bars[this.bars.length - 1].c : NaN;
    if (!Number.isFinite(last)) return [];
    const lastY = this.y(pane, last);
    const LAB_H = 13;

    const wanted = [];
    for (const z of [...this.zones].sort((a, b) => b.strength - a.strength)) {
      /* ONE tag per zone, at the edge price meets FIRST -- not the midpoint.
         The mid is an average of pivot prices and nothing ever turned there;
         the edges are the extremes that formed the cluster, and the near edge
         is the level the detector itself uses for every approach it measures.
         Two tags eight pixels apart also read as two separate levels, which a
         zone is not.
         Price INSIDE the zone is the exception: it can leave either way, so
         both edges are live and both are labelled. */
      const inside = last >= z.low && last <= z.high;
      const edges = inside ? [z.high, z.low] : [last > z.high ? z.high : z.low];
      for (const price of edges) {
        const y = this.y(pane, price);
        if (!Number.isFinite(y) || y < pane.y + 7 || y > pane.y + pane.h - 7) continue;
        /* BOX overlap, not centre distance. The live price tag occupies
           lastY-8..lastY+8 and the countdown lastY+9..lastY+24; a zone tag is
           12px tall centred on its own y. Comparing centres let a tag whose
           centre cleared the region still have its top half clipped by the
           countdown. A level within ~15px of the current price genuinely
           cannot be labelled at its true height -- the price tag has to win --
           so it is dropped rather than nudged, since a number drawn beside the
           wrong line is worse than no number. */
        if (y + 6 > lastY - 8 && y - 6 < lastY + 24) continue;
        if (wanted.some((w) => Math.abs(w.y - y) < LAB_H)) continue;
        wanted.push({ y, price, above: price > last, strength: z.strength });
      }
    }
    return wanted;
  }

  _zoneAxisLabels(pane) {
    const wanted = this._zoneLabelSlots(pane);
    if (!wanted.length) return;
    const ctx = this.ctx;

    ctx.save();
    ctx.font = '9.5px "Roboto Mono", monospace';
    ctx.textAlign = 'left';
    for (const w of wanted) {
      const col = w.above ? COL.down : COL.up;
      ctx.globalAlpha = 0.85;
      ctx.fillStyle = col;
      ctx.fillRect(this.plot.r, w.y - 6, AXIS_W, 12);
      ctx.globalAlpha = 1;
      ctx.fillStyle = '#02101f';
      ctx.fillText(w.price.toFixed(this.digits), this.plot.r + 5, w.y + 3.5);
    }
    ctx.restore();
  }

  /* What the band under the cursor IS.
   *
   * Two detectors draw bands and they answer different questions -- one finds
   * levels price has TURNED AT repeatedly, the other finds bases price LEFT IN
   * A HURRY. The labels say which is which; this says what that means, plus the
   * evidence behind this particular band, so a zone can be judged rather than
   * just noticed.
   *
   * Supply/demand is checked FIRST: its bands are narrower and usually sit
   * inside the broader pivot clusters, so testing the wide one first would
   * always win and the specific object would be unreachable.
   */
  /*
   * A tooltip that explains itself.
   *
   * The numbers a zone is scored on -- ATR width, ATR reaction, a 0-100
   * strength -- are the detector's vocabulary, not a reader's. "0.64 ATR wide"
   * is only meaningful to someone who already knows what ATR is on this
   * instrument; "6.9 pts -- tight" is meaningful to anyone. So every row here
   * carries a LABEL, a value in the instrument's own units, and a plain-word
   * reading, with the raw ratio kept in parentheses for anyone who wants it.
   *
   * The opening line is a full sentence about THIS zone rather than a
   * definition of the zone type, because the first question is always "what is
   * this band telling me", not "what is a supply zone".
   */
  _units(diff) {
    // FX quotes in pips (5- and 3-digit); metals and indices in price points.
    const d = this.digits;
    if (d >= 5) return `${Math.round(diff / 0.0001)} pips`;
    if (d === 3) return `${Math.round(diff / 0.01)} pips`;
    const v = Math.abs(diff);
    return `${v >= 10 ? Math.round(v) : v.toFixed(1)} pts`;
  }

  /* NOT a confidence, and the words are chosen to stop it reading as one.
     Measured over 21,800 approaches: the correlation between this score and
     whether the zone actually held is -0.0097, and the best-scoring quartile
     held 60.5% against the worst quartile's 61.0%. It ranks how cleanly a zone
     is DRAWN -- tight, repeatedly touched, near price. That is a real property
     and it is what decides which six survive the cap; it is not a forecast, and
     "strong" would have said it was. */
  _rating(v) {
    const s = Math.round(v);
    const word = s >= 75 ? 'textbook' : s >= 60 ? 'clean'
      : s >= 45 ? 'rough' : 'marginal';
    return `${s} / 100 &mdash; ${word}`;
  }

  _thickness(widthAtr, diff) {
    const word = widthAtr <= 0.5 ? 'tight, a clear line'
      : widthAtr <= 1.0 ? 'normal'
        : 'wide &mdash; more a region than a line';
    return `${this._units(diff)} &mdash; ${word}`;
  }

  _row(label, value, note) {
    return `<span><em>${label}</em>${value}`
      + (note ? `<small> ${note}</small>` : '') + `</span>`;
  }

  _zoneTip(p) {
    const pane = this.main;
    if (!pane || p.y < pane.y || p.y > pane.y + pane.h) { this.tip.hidden = true; return; }
    const price = this.valAt(pane, p.y);
    const last = this.bars.length ? this.bars[this.bars.length - 1].c : NaN;
    const d = this.digits;
    let html = null;

    for (const z of (this.sdZones || [])) {
      if (price < z.low || price > z.high) continue;
      const buyers = z.kind === 'demand';
      const kind = buyers ? 'DEMAND' : 'SUPPLY';
      const atr = z.atr > 0 ? z.atr : (z.high - z.low) / Math.max(z.widthAtr, 1e-9);
      const imp = z.impulseAtr ?? 0;
      // `kind` IS the direction: supplydemand.js sets DEMAND when the impulse
      // out of the base was up, SUPPLY when it was down. Saying "the move"
      // without naming which way it went throws that away.
      html = `<b>${kind}${z.fresh ? ' &#9679;' : ''}</b>`
        + `<i>Price paused here, then ran ${buyers ? 'UP' : 'DOWN'} hard &mdash; `
        + `${buyers ? 'buyers' : 'sellers'} took control. ${z.fresh
          ? `Nothing has traded back through since, so the ${buyers ? 'buy' : 'sell'} `
            + `orders behind that ${buyers ? 'rally' : 'sell-off'} may still be waiting here.`
          : `Price has been back ${z.touches} time${z.touches === 1 ? '' : 's'} `
            + `since, so some of that ${buyers ? 'buying' : 'selling'} is already used up.`}</i>`
        + this._row('Zone', `${z.low.toFixed(d)} &ndash; ${z.high.toFixed(d)}`)
        + this._row('Thickness', this._thickness(z.widthAtr, z.high - z.low),
          `(${z.widthAtr.toFixed(2)} ATR)`)
        + this._row('Departure',
          `${this._units(imp * atr)} ${buyers ? 'up' : 'down'} in a few bars`,
          `(${imp.toFixed(1)}&times; a normal bar)`)
        + this._row('Shape', this._rating(z.strength));
      break;
    }
    if (!html) {
      for (const z of (this.zones || [])) {
        if (price < z.low || price > z.high) continue;
        const sup = z.roleAt(last) === 'support';
        const atr = z.atr > 0 ? z.atr : (z.high - z.low) / Math.max(z.widthAtr, 1e-9);
        const react = z.reactionAtr;
        html = `<b>${sup ? 'SUPPORT' : 'RESISTANCE'} &times;${z.touches}</b>`
          + `<i>Price has come back to this level ${z.touches} times and turned `
          + `${sup ? 'back up' : 'back down'} each time. `
          + `${sup ? 'Buyers' : 'Sellers'} keep showing up here.</i>`
          + this._row('Zone', `${z.low.toFixed(d)} &ndash; ${z.high.toFixed(d)}`)
          + this._row('Thickness', this._thickness(z.widthAtr, z.high - z.low),
            `(${z.widthAtr.toFixed(2)} ATR)`)
          + (Number.isFinite(react)
            ? this._row('Typical bounce', `${this._units(react * atr)} away`,
              `(${react.toFixed(1)}&times; a normal bar)`)
            : '')
          + this._row('Shape', this._rating(z.strength))
          + `<u>How cleanly the zone is drawn &mdash; not a forecast. Measured `
          + `over 21,800 approaches, a high score holds no more often than a `
          + `low one.</u>`;
        break;
      }
    }
    if (!html) { this.tip.hidden = true; return; }

    this.tip.innerHTML = html;
    this.tip.hidden = false;
    // flip to the other side of the cursor near an edge, or it clips
    const w = this.tip.offsetWidth || 220;
    const h = this.tip.offsetHeight || 60;
    const left = p.x + 14 + w > this.w ? p.x - 14 - w : p.x + 14;
    const top = p.y + 12 + h > this.h ? p.y - 12 - h : p.y + 12;
    this.tip.style.left = `${Math.max(2, left)}px`;
    this.tip.style.top = `${Math.max(2, top)}px`;
  }

  _paneTitle(pane) {
    const ctx = this.ctx;
    ctx.font = '10px "Roboto Mono", monospace';
    let x = pane.x + 8;
    for (const run of pane.runs) {
      const study = this.studies.find((s) => s.id === run.id);
      const text = study ? studyTitle(study) : run.label;
      ctx.fillStyle = COL.textFaint;
      ctx.fillText(text, x, pane.y + 11);
      x += ctx.measureText(text).width + 12;
      const lastVal = run.plots.find((p) => p.type === 'line')?.data;
      if (lastVal) {
        const i = Math.min(lastVal.length - 1, this.cross ? this.cross.i : lastVal.length - 1);
        const v = lastVal[i];
        if (v !== null && v !== undefined) {
          ctx.fillStyle = COL.text;
          const t = Number(v).toFixed(Math.abs(v) > 100 ? 1 : 2);
          ctx.fillText(t, x, pane.y + 11);
          x += ctx.measureText(t).width + 12;
        }
      }
    }
  }

  /* "Trade like a Pro", set into the bottom-right corner of the chart.
   *
   * Anchored to the PLOT, not to a pane: it sits just inside the price axis and
   * just above the time axis, so it keeps the same corner whether the chart is
   * showing three study panes or none. Drawn last, after every pane and the
   * crosshair, so nothing paints over it.
   *
   * Tinted by the CURRENT move -- green while the bar is up on the one before
   * it, pink while it is down -- which makes it a direction read from the
   * corner of the eye as well as a mark. It follows the crosshair when one is
   * up, for the same reason the legend does: hovering a bar should describe
   * THAT bar, not silently keep showing the last one.
   *
   * It IS included in exports -- that was the point of asking for it. The
   * capture stamp claims the bottom-right corner of a snapshot, so on export
   * this lifts a line clear of it rather than the two painting over each other:
   * both are right-aligned at the same baseline and would otherwise collide
   * exactly.
   */
  _watermark() {
    const bars = this.plotBars;
    if (!bars || bars.length < 2) return;
    const i = clamp(this.cross ? this.cross.i : bars.length - 1, 0, bars.length - 1);
    const b = bars[i];
    if (!b) return;
    const prev = bars[i - 1];
    /* first bar has nothing before it: fall back to its own body */
    const chg = prev ? b.c - prev.c : b.c - b.o;

    const ctx = this.ctx;
    ctx.save();
    /* the export is rasterised at 2x and lands on white, so it carries a
       slightly larger, more opaque mark than the on-screen one */
    const exporting = !!this._exporting;
    ctx.font = (exporting ? '600 15px' : '600 13px') + ' "Roboto Mono", monospace';
    ctx.textAlign = 'right';
    ctx.textBaseline = 'alphabetic';
    ctx.globalAlpha = exporting ? 0.75 : 0.5;
    ctx.fillStyle = chg >= 0 ? COL.up : COL.down;
    /* _stampBrand puts the capture time on the bottom line at (r - 8, b - 10);
       clear it by a full line so neither is read through the other */
    const lift = exporting ? 20 : 0;
    ctx.fillText('Trade like a Pro', this.plot.r - 10, this.plot.b - 10 - lift);
    ctx.restore();
  }

  _axisX() {
    const ctx = this.ctx;
    ctx.fillStyle = this._exporting ? 'transparent' : COL.axisBg;
    ctx.fillRect(0, this.plot.b, this.w, TIME_H);
    ctx.strokeStyle = COL.grid;
    ctx.beginPath();
    ctx.moveTo(0, this.plot.b + 0.5); ctx.lineTo(this.w, this.plot.b + 0.5);
    ctx.stroke();
    ctx.fillStyle = COL.text;
    ctx.font = '9.5px "Roboto Mono", monospace';
    ctx.textAlign = 'center';
    for (const tick of (this.xTicks || [])) {
      ctx.fillText(axisTime(this.tAt(tick.i), this.tf), tick.x, this.plot.b + 14);
    }
    ctx.textAlign = 'left';
  }

  _lastPrice() {
    const ctx = this.ctx;
    const pane = this.main;
    const last = this.plotBars[this.plotBars.length - 1];
    if (!last) return;
    const y = this.y(pane, last.c);
    if (y < pane.y || y > pane.y + pane.h) return;
    const up = last.c >= last.o;
    ctx.save();
    /* SOLID. Dashes are this chart's vocabulary for "not a price that traded"
       -- projected trendlines, the rule's stop, position levels. The last price
       is the most concrete line on the chart, so it should not be speaking that
       language. */
    ctx.strokeStyle = up ? COL.up : COL.down;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(this.plot.l, Math.round(y) + 0.5); ctx.lineTo(this.plot.r, Math.round(y) + 0.5);
    ctx.stroke();
    ctx.fillStyle = up ? COL.up : COL.down;
    ctx.fillRect(this.plot.r, y - 8, AXIS_W, 16);
    ctx.fillStyle = '#02101f';
    ctx.font = '600 9.5px "Roboto Mono", monospace';
    ctx.fillText(last.c.toFixed(this.digits), this.plot.r + 5, y + 3.5);

    // countdown to the close of the forming bar, directly under the price tag
    const txt = timeLeft(last.t, this.tf);
    if (txt) {
      ctx.font = '600 9.5px "Roboto Mono", monospace';
      const boxY = y + 9;
      ctx.fillStyle = COL.crossBg;
      ctx.fillRect(this.plot.r, boxY, AXIS_W, 15);
      ctx.strokeStyle = up ? COL.up : COL.down;
      ctx.lineWidth = 1;
      ctx.strokeRect(this.plot.r + 0.5, boxY + 0.5, AXIS_W - 1, 14);
      ctx.fillStyle = '#fff';
      ctx.fillText(txt, this.plot.r + 5, boxY + 11);
    }
    ctx.restore();
  }

  _crosshair() {
    if (!this.cross) return;
    const ctx = this.ctx;
    const { x, y } = this.cross;
    const pane = this.paneAt(y);
    ctx.save();
    ctx.strokeStyle = COL.cross;
    ctx.setLineDash([3, 4]);
    ctx.lineWidth = 1;
    ctx.beginPath();
    const cx = Math.round(this.x(this.cross.i)) + 0.5;
    ctx.moveTo(cx, this.plot.t); ctx.lineTo(cx, this.plot.b);
    ctx.moveTo(this.plot.l, Math.round(y) + 0.5); ctx.lineTo(this.plot.r, Math.round(y) + 0.5);
    ctx.stroke();
    ctx.setLineDash([]);

    // price tag
    const v = this.valAt(pane, y);
    ctx.fillStyle = COL.crossBg;
    ctx.fillRect(this.plot.r, y - 8, AXIS_W, 16);
    ctx.fillStyle = '#fff';
    ctx.font = '9.5px "Roboto Mono", monospace';
    ctx.fillText(pane.isMain ? v.toFixed(this.digits) : (Math.abs(v) >= 1000 ? compact(v) : v.toFixed(2)),
                 this.plot.r + 5, y + 3.5);

    // time tag
    const label = stamp(this.tAt(this.cross.i));
    ctx.font = '9.5px "Roboto Mono", monospace';
    const w = ctx.measureText(label).width + 12;
    ctx.fillStyle = COL.crossBg;
    ctx.fillRect(clamp(cx - w / 2, 0, this.plot.r - w), this.plot.b + 2, w, 16);
    ctx.fillStyle = '#fff';
    ctx.textAlign = 'center';
    ctx.fillText(label, clamp(cx, w / 2, this.plot.r - w / 2), this.plot.b + 13.5);
    ctx.textAlign = 'left';
    ctx.restore();
  }

  _legend() {
    const i = clamp(this.cross ? this.cross.i : this.plotBars.length - 1, 0, this.plotBars.length - 1);
    const b = this.plotBars[i];
    if (!b) { this.legend.innerHTML = ''; return; }
    const prev = this.plotBars[i - 1];
    const chg = prev ? b.c - prev.c : b.c - b.o;
    const pct = prev && prev.c ? (chg / prev.c) * 100 : 0;
    const cls = chg >= 0 ? 'up' : 'down';
    const d = this.digits;
    const f = (v) => Number(v).toFixed(d);

    const studies = this.runs.filter((r) => r.pane === 'main').map((r) => {
      const study = this.studies.find((s) => s.id === r.id);
      const parts = r.plots.filter((p) => p.type === 'line').map((p) => {
        const v = p.data[i];
        return `<i style="color:${p.color}">${v === null || v === undefined ? '—' : Number(v).toFixed(d)}</i>`;
      }).join(' ');
      return `<div><span style="color:#5d7794">${study ? studyTitle(study) : r.label}</span> ${parts}</div>`;
    }).join('');

    // Candle time: the bar under the crosshair, or the forming bar when idle.
    // The forming bar also reports how long it has left, so "what am I looking
    // at and when does it settle" is answerable without hovering the axis.
    const isLast = i === this.plotBars.length - 1;
    const left = isLast ? timeLeft(b.t, this.tf) : null;
    const clock = `<div class="clock"><span>${stamp(b.t)}</span>` +
      (left ? `<span class="cd">closes in ${left}</span>`
            : isLast ? '' : '<span class="dim">closed</span>') + '</div>';

    this.legend.innerHTML =
      `<div class="l1"><b>${this.symbol}</b><span class="tf">${TF_LABEL[this.tf] || this.tf}</span>` +
      `<span class="tf">${CHART_TYPES[this.type]}</span></div>` +
      clock +
      `<div class="ohlc"><span>O <span class="${cls}">${f(b.o)}</span></span>` +
      `<span>H <span class="${cls}">${f(b.h)}</span></span>` +
      `<span>L <span class="${cls}">${f(b.l)}</span></span>` +
      `<span>C <span class="${cls}">${f(b.c)}</span></span>` +
      `<span class="${cls}">${chg >= 0 ? '+' : ''}${f(chg)} (${pct >= 0 ? '+' : ''}${pct.toFixed(2)}%)</span>` +
      (b.v ? `<span>V ${compact(b.v)}</span>` : '') + '</div>' +
      (studies ? `<div class="studies">${studies}</div>` : '');
  }

  /* ------------------------------------------------------------- events */
  _bind() {
    const c = this.canvas;
    c.style.cursor = 'crosshair';
    let drag = null;

    const local = (e) => {
      const r = c.getBoundingClientRect();
      return { x: e.clientX - r.left, y: e.clientY - r.top };
    };

    c.addEventListener('pointerdown', (e) => {
      this.onActivate(this);
      const p = local(e);
      // capture is a nicety (it keeps a drag alive outside the canvas); never
      // let it throw away the rest of the handler
      try { c.setPointerCapture(e.pointerId); } catch { /* no live pointer */ }
      if (p.x > this.plot.r) { drag = { mode: 'scaleY', y: p.y, min: this.main.min, max: this.main.max }; return; }
      if (p.y > this.plot.b) { drag = { mode: 'scaleX', x: p.x, span: this.view.span }; return; }
      if (this.tool !== 'cursor') { this._toolClick(p); return; }
      /* The price range is captured too, so a drag can move the chart UP and
         DOWN as well as left and right. Taken from `this.main` at grab time
         rather than read live, or each frame would pan relative to the range
         the previous frame just set and the chart would accelerate away. */
      drag = {
        mode: 'pan', x: p.x, y: p.y, right: this.view.right, moved: false,
        min: this.main.min, max: this.main.max, movedY: false,
      };
    });

    c.addEventListener('pointermove', (e) => {
      const p = local(e);
      if (drag && drag.mode === 'pan') {
        drag.moved = true;
        this.view.right = this._clampRight(drag.right - (p.x - drag.x) / this.barW);

        /* VERTICAL PAN. The content follows the cursor: drag down and the bars
           move down, which means the visible price WINDOW moves up.

             price(y) = max - (y / h) * (max - min)

           so holding a bar's price at a cursor moved by `dy` needs the window
           shifted by dy * (max - min) / h -- both edges, so the scale is
           unchanged and only the offset moves.

           Setting `priceLock` is what makes it stick: without it the next
           repaint re-fits the range to the bars and the drag springs back. That
           also means a vertical pan turns auto-scaling off, which is what the
           reset gestures exist to undo. */
        const dy = p.y - drag.y;
        if (dy || drag.movedY) {
          const pane = this.main;
          const h = Math.max(1, pane.h);
          const shift = (dy * (drag.max - drag.min)) / h;
          this.view.priceLock = { min: drag.min + shift, max: drag.max + shift };
          drag.movedY = true;
        }
        this.draw();
        return;
      }
      if (drag && drag.mode === 'scaleY') {
        const pane = this.main;
        const mid = (drag.min + drag.max) / 2;
        const k = Math.exp((p.y - drag.y) / 180);
        const half = ((drag.max - drag.min) / 2) * k;
        this.view.priceLock = { min: mid - half, max: mid + half };
        this.draw();
        return;
      }
      if (drag && drag.mode === 'scaleX') {
        const k = Math.exp((drag.x - p.x) / 260);
        this.view.span = clamp(Math.round(drag.span * k), MIN_SPAN, MAX_SPAN);
        this.draw();
        return;
      }
      if (p.x > this.plot.r || p.y > this.plot.b) { this.cross = null; this.draw(); return; }
      this.cross = { x: p.x, y: p.y, i: clamp(this.idxAt(p.x), this.i0, this.i1) };
      if (this.pending && this.pending.p1) {
        const pane = this.main;
        this.pending.p2 = { t: this.tAt(this.cross.i), price: this.valAt(pane, p.y) };
      }
      this.hoverDrawing = this._hitDrawing(p);
      this._zoneTip(p);
      c.style.cursor = this.tool !== 'cursor' ? 'copy' : this.hoverDrawing ? 'pointer' : 'crosshair';
      this.draw();
    });

    const endDrag = () => {
      if (drag && drag.mode === 'pan' && !drag.moved) { /* plain click */ }
      // a finished Y-scale drag is the only way to SET a price lock, so it is
      // also the only place that has to remember one
      // a vertical pan sets a price lock, and a price lock is a saved setting
      if (drag && (drag.mode === 'scaleY' || drag.movedY)) this._persist();
      drag = null;
    };
    c.addEventListener('pointerup', endDrag);
    c.addEventListener('pointercancel', endDrag);
    c.addEventListener('pointerleave', () => {
      this.cross = null;
      this.tip.hidden = true;
      this.draw();
    });

    c.addEventListener('wheel', (e) => {
      e.preventDefault();
      const p = local(e);
      if (e.shiftKey) {                                  // shift-wheel pans
        this.view.right = this._clampRight(
          this.view.right + Math.sign(e.deltaY) * Math.max(1, this.view.span * 0.06));
      } else {
        /* ZOOM PRESERVES THE RIGHT EDGE.
         *
         * It used to anchor on the cursor, which moved the right edge -- and
         * since the AUTO stack now runs AS OF that edge, magnifying the chart
         * silently changed WHEN you were standing. Measured: parked 700 bars
         * back, four notches in moved the edge from 1301 to 1260 and the
         * channel count from 1 to 3; four notches back out landed on 1306, not
         * 1301, so the original picture was unrecoverable by zooming.
         *
         * Keeping the anchor and freezing the as-of point instead would be
         * worse: the chart would draw lines fitted to bar 1301 while showing
         * only up to 1260, which is 41 bars of hindsight on screen -- the exact
         * thing the as-of cut exists to remove.
         *
         * So panning chooses WHEN, zoom chooses how much of it you see. The
         * cost is cursor-anchored zoom, which is a real convenience; panning
         * after the zoom recovers it, and an unstable analysis is the worse
         * trade.
         */
        const k = e.deltaY > 0 ? 1.12 : 1 / 1.12;
        const span = clamp(Math.round(this.view.span * k), MIN_SPAN, MAX_SPAN);
        const atLive = this.view.right >= this.bars.length - 1;
        this.view.span = span;
        // rightPad scales with span, so staying live means re-deriving it
        this.view.right = atLive
          ? this.bars.length - 1 + this.rightPad()
          : this._clampRight(this.view.right);
      }
      this.draw();
    }, { passive: false });

    // the full reset, same as the button: a half-reset that kept the zoom was
    // two different meanings for one gesture
    c.addEventListener('dblclick', () => { this.resetScale(); });

    c.addEventListener('contextmenu', (e) => {
      const p = local(e);
      const hit = this._hitDrawing(p);
      if (hit) {
        e.preventDefault();
        this.drawings = this.drawings.filter((d) => d !== hit);
        this.hoverDrawing = null;
        if (this.selectedDrawing === hit) this.selectedDrawing = null;
    /* Click-selected drawing. Separate from hover: a selection has to
       survive the mouse moving away, which is the whole point of it. */
    this.selectedDrawing = null;
        this._persist();
        this.draw();
      }
    });

    c.addEventListener('click', (e) => {
      this.onActivate(this);
      /* Selecting is a CURSOR-tool action. While a drawing tool is armed the
         click belongs to the tool, or you could never place a second anchor on
         top of an existing line. */
      if (this.tool !== 'cursor') return;
      const hit = this._hitDrawing(local(e));
      this.selectedDrawing = hit || null;
      this.draw();
    });
  }

  /** Remove the selected drawing. Returns false if there was nothing selected. */
  deleteSelected() {
    if (!this.selectedDrawing) return false;
    this.drawings = this.drawings.filter((d) => d !== this.selectedDrawing);
    this.selectedDrawing = null;
    this.hoverDrawing = null;
    this._persist();
    this.draw();
    return true;
  }

  clearSelection() {
    if (!this.selectedDrawing) return;
    this.selectedDrawing = null;
    this.draw();
  }

  /**
   * The chart as a PNG data URL — canvas only, so it captures exactly what is
   * rendered and nothing of the surrounding page.
   */
  /**
   * Brand mark top-left, capture time bottom-right -- drawn straight onto the
   * canvas so they survive into the PNG. Painted AFTER the chart and outside
   * the pane clip, so nothing can cover them.
   *
   * The mark is the four-square logo redrawn in canvas primitives rather than
   * an <img>. Loading an image would make snapshot() async, and a rasterised
   * copy would blur on a HiDPI canvas; five rects cost nothing and stay sharp
   * at any dpr.
   */
  _stampBrand() {
    const ctx = this.ctx;
    const { l, r, b } = this.plot;
    ctx.save();
    ctx.setLineDash([]);
    ctx.globalAlpha = 0.9;

    // --- logo, top-left of the plot area ---
    const M = 28;                                   // mark size in px
    const x = l + 12, y = this.plot.t + 12, u = M / 32;   // 32-unit artboard
    const sq = (rx, ry, fill) => {
      ctx.fillStyle = fill;
      ctx.fillRect(x + rx * u, y + ry * u, 14 * u, 14 * u);
    };
    sq(0, 0, '#171C8F'); sq(18, 0, '#FF9E1B');
    sq(0, 18, '#93C90F'); sq(18, 18, '#E31C79');
    sq(9, 9, '#B1B3B3');

    ctx.font = '700 21px Inter, system-ui, sans-serif';
    ctx.textBaseline = 'middle';
    const tx = x + M + 10, ty = y + M / 2;
    ctx.fillStyle = COL.draw;
    ctx.fillText('DIANUR', tx, ty);
    ctx.fillStyle = COL.pink;
    ctx.fillText('FX', tx + ctx.measureText('DIANUR').width, ty);

    // --- symbol + timeframe, under the mark ---
    ctx.font = '13px "Roboto Mono", monospace';
    ctx.fillStyle = COL.text;
    ctx.fillText(`${this.symbol}  ${TF_LABEL[this.tf] || this.tf}`, x, y + M + 12);

    // --- capture time, bottom-right, above the time axis ---
    const off = -new Date().getTimezoneOffset() / 60;
    const tz = `UTC${off >= 0 ? '+' : ''}${off}`;
    ctx.font = '11px "Roboto Mono", monospace';
    ctx.fillStyle = COL.textFaint;
    ctx.textAlign = 'right';
    ctx.fillText(`${stamp(Date.now())} ${zoneLabel()}`, r - 8, b - 10);
    ctx.restore();
  }

  /**
   * PNG of exactly what is rendered -- candles, studies, drawings, panels --
   * with the brand mark and capture time added and live-account chrome removed.
   * Paints twice: once in export mode for the capture, once normally to put
   * the interactive chart back.
   *
   * _paint() directly, NOT draw(). draw() only schedules a repaint on the next
   * animation frame, so toDataURL would capture the frame BEFORE export mode
   * took effect -- which is exactly how the first version shipped an image
   * with the position lines still in it.
   *
   * `scale` multiplies the backing store on top of the device ratio, so a 2
   * on an already-2x display exports at 4x the CSS pixels. Layout is driven by
   * this.w/this.h, which do not change, so nothing reflows -- text and lines
   * are simply rasterised finer rather than being drawn larger.
   */
  snapshot({ scale = 2, ink = 'light', background = ink === 'light' ? '#FFFFFF' : null,
             zone = 'utc' } = {}) {
    /* An export renders in UTC while the app itself runs on broker time. The
       image outlives the session that made it: "14:00" tells whoever opens the
       file nothing unless the frame is universal, and the axis, the crosshair
       and the corner stamp all have to agree on that frame -- which is why the
       whole paint happens inside withZone rather than the stamp alone. */
    return withZone(zone, () => this._snapshot(scale, ink, background));
  }

  _snapshot(scale, ink, background) {
    const prev = this._exporting;
    const prevBg = this._exportBg;
    this._exportBg = background;
    const saved = {};
    if (ink === 'light') {
      for (const k of Object.keys(INK_ON_LIGHT)) {
        saved[k] = COL[k];
        COL[k] = INK_ON_LIGHT[k];
      }
    }
    this._exporting = true;
    this._exportScale = scale;
    this.resize();                 // re-rasterise at the export resolution
    try {
      this._paint();
      this._stampBrand();
      return this.canvas.toDataURL('image/png');
    } catch {
      return null;
    } finally {
      for (const k of Object.keys(saved)) COL[k] = saved[k];
      this._exporting = prev;
      this._exportBg = prevBg;
      this._exportScale = 1;
      this.resize();               // back to screen resolution; queues a repaint
      this._paint();
    }
  }

  _toolClick(p) {
    const pane = this.main;
    if (p.y > this.plot.b || p.x > this.plot.r) return;
    const point = { t: this.tAt(clamp(this.idxAt(p.x), this.i0, this.i1)), price: this.valAt(pane, p.y) };
    if (this.tool === 'hline') {
      this.drawings.push({ id: nextId(), type: 'hline', p1: point });
      this._persist(); this.draw();
      return;
    }
    if (!this.pending) {
      this.pending = { id: nextId(), type: this.tool, p1: point, p2: point };
    } else {
      this.pending.p2 = point;
      this.drawings.push(this.pending);
      this.pending = null;
      this._persist();
    }
    this.draw();
  }

  cancelTool() { this.pending = null; this.setTool('cursor'); this.draw(); }

  _hitDrawing(p) {
    const pane = this.main;
    const near = 5;
    for (const d of [...this.drawings].reverse()) {
      const y1 = d.p1 ? this.y(pane, d.p1.price) : null;
      if (d.type === 'hline') { if (Math.abs(p.y - y1) <= near) return d; continue; }
      if (!d.p2) continue;
      const x1 = this.x(this.idxOfTime(d.p1.t)), x2 = this.x(this.idxOfTime(d.p2.t));
      const y2 = this.y(pane, d.p2.price);
      if (d.type === 'rect' || d.type === 'fib') {
        const inX = p.x >= Math.min(x1, x2) - near && p.x <= Math.max(x1, x2) + near;
        const onEdge = Math.abs(p.y - y1) <= near || Math.abs(p.y - y2) <= near;
        if (inX && onEdge) return d;
        continue;
      }
      // segment distance
      const dx = x2 - x1, dy = y2 - y1;
      const len2 = dx * dx + dy * dy || 1;
      let t = ((p.x - x1) * dx + (p.y - y1) * dy) / len2;
      if (d.type === 'trend') t = clamp(t, 0, 1); else t = Math.max(0, t);
      const px = x1 + t * dx, py = y1 + t * dy;
      if (Math.hypot(p.x - px, p.y - py) <= near) return d;
    }
    return null;
  }

  /** Serialisable state for the workspace to persist. */
  state() {
    return {
      symbol: this.symbol, tf: this.tf, type: this.type,
      studies: this.studies, span: this.view.span,
      // a hand-set price scale is a setting, not a transient
      priceLock: this.view.priceLock,
    };
  }
}
