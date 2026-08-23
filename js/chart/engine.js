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
import { TF_LABEL, TF_MS, axisTime, clamp, compact, inferDigits, stamp } from '../util.js';

const AXIS_W = 64;        // right-hand price axis
const TIME_H = 22;        // bottom time axis
const PANE_GAP = 6;
const MIN_SPAN = 12;
const MAX_SPAN = 4000;

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

export const CHART_TYPES = {
  candles: 'Candles', hollow: 'Hollow candles', bars: 'OHLC bars',
  line: 'Line', area: 'Area', heikin: 'Heikin Ashi', baseline: 'Baseline',
};

export const DRAW_TOOLS = {
  cursor: 'Cursor', hline: 'Horizontal line', trend: 'Trend line',
  ray: 'Ray', rect: 'Rectangle', fib: 'Fib retracement',
};

const FIB = [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1];

/* Dash length grows with the source timeframe, so a projected line reads as
   "from higher up" at a glance without reading its label. */
const AUTO_DASH = {
  '1m': [4, 3], '5m': [6, 3], '15m': [8, 4], '30m': [10, 4],
  '1h': [12, 5], '4h': [16, 6], '1d': [20, 7], '1w': [26, 8],
};

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
    this.onSymbolClick = opts.onSymbolClick || (() => {});

    this.bars = [];
    this.digits = 5;
    this.positions = [];
    this.autoLines = [];
    this.message = 'loading…';
    this.view = { right: 0, span: 160, priceLock: null };
    this.cross = null;
    this.pending = null;          // in-progress drawing
    this.hoverDrawing = null;
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

    this.tools = document.createElement('div');
    this.tools.className = 'cell-tools';
    const btn = (txt, title, fn) => {
      const b = document.createElement('button');
      b.textContent = txt; b.title = title;
      b.addEventListener('click', (e) => { e.stopPropagation(); fn(); });
      this.tools.append(b);
    };
    btn('⤢', 'Fit all bars', () => { this.fitAll(); });
    btn('⟳', 'Reset scale', () => { this.resetView(); });
    btn('⌫', 'Clear drawings', () => { this.drawings = []; this._persist(); this.draw(); });

    this.host.append(this.canvas, this.legend, this.msgEl, this.tools);
    this.ro = new ResizeObserver(() => this.resize());
    this.ro.observe(this.host);
    this.resize();
  }

  destroy() { this.ro.disconnect(); this.host.innerHTML = ''; }

  resize() {
    const dpr = window.devicePixelRatio || 1;
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
  setAutoLines(lines) {
    this.autoLines = lines || [];
    this.draw();
  }

  setPositions(rows) {
    this.positions = (rows || []).filter((p) => p.symbol === this.symbol);
    this.draw();
  }

  setSymbol(s) { this.symbol = s; this.bars = []; this.drawings = this._loadDrawings(); this.onChange(this); }
  setTimeframe(tf) { this.tf = tf; this.bars = []; this.drawings = this._loadDrawings(); this.onChange(this); }
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

  resetView() {
    this.view.span = clamp(this.view.span || 160, MIN_SPAN, MAX_SPAN);
    this.view.right = Math.max(this.view.span - 1, this.bars.length - 1 + this.rightPad());
    this.view.priceLock = null;
  }

  fitAll() {
    if (!this.bars.length) return;
    this.view.span = clamp(this.bars.length, MIN_SPAN, MAX_SPAN);
    this.view.right = this.bars.length - 1 + this.rightPad();
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
    const ctx = this.ctx;
    ctx.clearRect(0, 0, this.w, this.h);
    ctx.fillStyle = COL.bg;
    ctx.fillRect(0, 0, this.w, this.h);
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
        this._price(pane); this._foreignPlots(pane); this._autoLines(pane);
        this._positions(pane); this._drawings(pane);
      }
      ctx.restore();
      this._axisY(pane);
      if (!pane.isMain) this._paneTitle(pane);
    }

    this._axisX();
    this._lastPrice();
    this._crosshair();
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

  _positions(pane) {
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

      ctx.save();
      ctx.strokeStyle = col;
      ctx.globalAlpha = (own ? 0.95 : 0.62) * (l.status === 'ACTIVE' ? 1 : 0.82);
      ctx.lineWidth = own ? 1.5 : 2;
      ctx.setLineDash(own ? [] : (AUTO_DASH[l.tf] || [8, 4]));
      ctx.beginPath();
      ctx.moveTo(x1, y1);
      ctx.lineTo(xr, yr);
      ctx.stroke();
      ctx.setLineDash([]);

      // anchors, so it is obvious which swings built the line
      if (own) {
        ctx.globalAlpha = 0.95;
        for (const p of [l.p1, l.p2]) {
          ctx.beginPath();
          ctx.arc(this.x(this.idxOfTime(p.t)), this.y(pane, p.price), 2.5, 0, Math.PI * 2);
          ctx.fillStyle = col;
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
      const col = d === this.hoverDrawing ? COL.pink : (d.color || COL.draw);
      ctx.save();
      ctx.strokeStyle = col;
      ctx.fillStyle = col;
      ctx.lineWidth = d === this.hoverDrawing ? 2 : 1.4;
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

  _axisY(pane) {
    const ctx = this.ctx;
    ctx.fillStyle = COL.axisBg;
    ctx.fillRect(this.plot.r, pane.y, AXIS_W, pane.h);
    ctx.strokeStyle = COL.grid;
    ctx.beginPath();
    ctx.moveTo(this.plot.r + 0.5, pane.y); ctx.lineTo(this.plot.r + 0.5, pane.y + pane.h);
    ctx.stroke();
    ctx.fillStyle = COL.text;
    ctx.font = '10.5px "Roboto Mono", monospace';
    ctx.textAlign = 'left';
    const step = pane.step || niceStep((pane.max - pane.min) / 4);
    const compactAxis = pane.runs.some((r) => r.plots.some((p) => p.fmt === 'compact'));
    for (let v = Math.ceil(pane.min / step) * step; v <= pane.max; v += step) {
      const y = this.y(pane, v);
      if (y < pane.y + 6 || y > pane.y + pane.h - 2) continue;
      const label = compactAxis ? compact(v)
        : pane.isMain ? v.toFixed(this.digits)
          : Math.abs(v) >= 1000 ? compact(v) : v.toFixed(step < 0.01 ? 4 : step < 1 ? 2 : 1);
      ctx.fillText(label, this.plot.r + 6, y + 3.5);
    }
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

  _axisX() {
    const ctx = this.ctx;
    ctx.fillStyle = COL.axisBg;
    ctx.fillRect(0, this.plot.b, this.w, TIME_H);
    ctx.strokeStyle = COL.grid;
    ctx.beginPath();
    ctx.moveTo(0, this.plot.b + 0.5); ctx.lineTo(this.w, this.plot.b + 0.5);
    ctx.stroke();
    ctx.fillStyle = COL.text;
    ctx.font = '10.5px "Roboto Mono", monospace';
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
    ctx.strokeStyle = up ? COL.up : COL.down;
    ctx.setLineDash([3, 3]);
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(this.plot.l, Math.round(y) + 0.5); ctx.lineTo(this.plot.r, Math.round(y) + 0.5);
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = up ? COL.up : COL.down;
    ctx.fillRect(this.plot.r, y - 8, AXIS_W, 16);
    ctx.fillStyle = '#02101f';
    ctx.font = '600 10.5px "Roboto Mono", monospace';
    ctx.fillText(last.c.toFixed(this.digits), this.plot.r + 5, y + 3.5);

    // countdown to the close of the forming bar, directly under the price tag
    const txt = timeLeft(last.t, this.tf);
    if (txt) {
      ctx.font = '600 10.5px "Roboto Mono", monospace';
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
    ctx.font = '10.5px "Roboto Mono", monospace';
    ctx.fillText(pane.isMain ? v.toFixed(this.digits) : (Math.abs(v) >= 1000 ? compact(v) : v.toFixed(2)),
                 this.plot.r + 5, y + 3.5);

    // time tag
    const label = stamp(this.tAt(this.cross.i));
    ctx.font = '10.5px "Roboto Mono", monospace';
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
      drag = { mode: 'pan', x: p.x, y: p.y, right: this.view.right, moved: false };
    });

    c.addEventListener('pointermove', (e) => {
      const p = local(e);
      if (drag && drag.mode === 'pan') {
        drag.moved = true;
        this.view.right = drag.right - (p.x - drag.x) / this.barW;
        this.view.right = clamp(this.view.right, this.view.span * 0.4, this.bars.length + this.view.span * 0.6);
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
      c.style.cursor = this.tool !== 'cursor' ? 'copy' : this.hoverDrawing ? 'pointer' : 'crosshair';
      this.draw();
    });

    const endDrag = () => {
      if (drag && drag.mode === 'pan' && !drag.moved) { /* plain click */ }
      drag = null;
    };
    c.addEventListener('pointerup', endDrag);
    c.addEventListener('pointercancel', endDrag);
    c.addEventListener('pointerleave', () => { this.cross = null; this.draw(); });

    c.addEventListener('wheel', (e) => {
      e.preventDefault();
      const p = local(e);
      if (e.shiftKey) {                                  // shift-wheel pans
        this.view.right = clamp(this.view.right + Math.sign(e.deltaY) * Math.max(1, this.view.span * 0.06),
                                this.view.span * 0.4, this.bars.length + this.view.span * 0.6);
      } else {
        const anchor = this.idxAt(p.x);
        const k = e.deltaY > 0 ? 1.12 : 1 / 1.12;
        const span = clamp(Math.round(this.view.span * k), MIN_SPAN, MAX_SPAN);
        const frac = (anchor - this.i0) / this.view.span;
        this.view.span = span;
        this.view.right = anchor + (1 - frac) * span;
      }
      this.draw();
    }, { passive: false });

    c.addEventListener('dblclick', () => { this.resetView(); this.draw(); });

    c.addEventListener('contextmenu', (e) => {
      const p = local(e);
      const hit = this._hitDrawing(p);
      if (hit) {
        e.preventDefault();
        this.drawings = this.drawings.filter((d) => d !== hit);
        this.hoverDrawing = null;
        this._persist();
        this.draw();
      }
    });

    c.addEventListener('click', () => this.onActivate(this));
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
    };
  }
}
