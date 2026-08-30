/* replay.js — Elliott Wave Replay, a SANDBOX inside the Backtest tab.
 *
 * WHY IT IS NOT ON THE LIVE CHART. The first version drove the live chart
 * directly: it sliced `chart.bars` to the replay cursor, moved the view and
 * released the price lock, then put everything back on exit. It worked, and it
 * was still the wrong place for it. A tool for deciding whether a strategy is
 * worth anything should not be able to disturb the chart you trade from -- not
 * because the restore was buggy, but because "restores correctly" is a promise
 * that has to be re-earned after every future edit, and the cost of it being
 * broken once is your working layout.
 *
 * So this owns a chart of its own:
 *
 *   - its own `Chart` instance, built into the Backtest tab, never added to
 *     `app.charts`;
 *   - `onChange` is a no-op, so nothing here writes a workspace key. `_persist`
 *     is `this.onChange(this)` and nothing more, which is what makes a bare
 *     Chart genuinely inert;
 *   - its own bar array, fetched separately, so no live path can overwrite it
 *     and no live tick can append to it.
 *
 * Nothing in `js/main.js` or `index.html` knows this file exists.
 *
 * THE FUTURE IS REMOVED, NOT HIDDEN. Stepping re-slices the series to the
 * cursor and hands the slice to `setData`. The overlays, the count and anything
 * else reading the chart see an array whose last bar IS the cursor -- there is
 * no masking flag for a consumer to forget to honour.
 *
 * WHAT IS RECORDED. At every cursor position the belief is logged: the counts,
 * their shares, what each expects next, and the invalidation price that would
 * refute it. The log is the deliverable. A chart showing a wave count is worth
 * very little; a record of what the count claimed 400 bars ago, and whether it
 * was right, is what can settle whether Elliott carries information here.
 */

import { TF_MS as UTIL_TF_MS, el, hhmm, seekBar, ymd, ymdToMs } from '../util.js';
import { openSymbolSearch } from './search.js';
import { toast } from './menu.js';
import { openAudio, pickMime } from './recaudio.js';
import { api } from '../api.js';
import { Chart } from '../chart/engine.js';
import { calibration, countAsOf, scoreBelief, scoreProjection, stability }
  from '../chart/elliott.js';
import { cones, coverage, reachRate, stateSeries } from '../chart/cone.js';

const TFS = ['5m', '15m', '1h', '4h', '1d'];

/* THE HIERARCHY. Elliott is self-similar, so a count is only a claim once you
   say at what degree -- the same three bars are wave 5 of a 15m impulse and
   noise inside a D1 wave 2. Each row reads the SAME instrument at its own
   timeframe, cut to the same instant, and the horizon is the one that timeframe
   can actually speak to.
   `bars` is that horizon expressed in bars of that frame, which is what the
   scorer needs; the wall-clock column is what a person reads. */
const TF_MS = { '5m': 3e5, '15m': 9e5, '1h': 36e5, '4h': 144e5, '1d': 864e5 };
/* Enough bars for the counter to have something to read, plus the reach back to
   the cursor. Capped: past this the fetch costs more than the row is worth. */
const MTF_WARMUP = 900;
const MTF_CAP = 20000;

const HIERARCHY = [
  { tf: '1d', role: 'Macro structure', horizon: '2-8 weeks', bars: 20 },
  { tf: '4h', role: 'Primary wave', horizon: '3-15 days', bars: 24 },
  { tf: '1h', role: 'Scenario', horizon: '8-48 hours', bars: 24 },
  { tf: '15m', role: 'Execution', horizon: '1-4 hours', bars: 12 },
];
const HORIZONS = [12, 24, 48, 96];
/* Play rates. Slow enough at the top end to read the panel between bars, which
   is the whole reason for playing rather than jumping. */
/* Set from the codec comparison in the README, not from habit. */
/* See js/ui/strategyreplay.js: a date jump may need a RANGE fetch, and a range
   on a fast timeframe is unbounded. */
const MAX_FETCH_BARS = 30000;

const FPS = 15;
const BITRATE = 2.5e6;

/** Elapsed recording time as m:ss -- what the file's length will be. */
function recClock(startedMs) {
  const secs = Math.max(0, Math.round((Date.now() - startedMs) / 1000));
  return `${Math.floor(secs / 60)}:${String(secs % 60).padStart(2, '0')}`;
}


const SPEEDS = [
  { label: '0.5x', ms: 800 },
  { label: '1x', ms: 400 },
  { label: '2x', ms: 200 },
  { label: '4x', ms: 100 },
];

export class ElliottReplay {
  constructor() {
    this.symbol = 'XAUUSD.a';
    this.tf = '1h';
    this.horizon = 24;
    this.full = [];            // the complete series; the chart never sees it
    this.i = 0;                // cursor into `full`
    this.beliefs = new Map();  // cursor -> belief, for the loaded series
    this.revealed = false;
    this.chart = null;
    this.host = null;
    this.loading = false;
    this.timer = null;         // the play transport
    this.dir = 1;
    this.speed = 400;
    /* A RECORDING is an ordered list of what was believed at each cursor
       position, in the order the cursor visited them. That is deliberately not
       the same thing as `beliefs`, which is keyed by bar and so loses the
       ORDER and the re-visits -- and the order is the interesting part when the
       question is whether a count repaints. */
    this.rec = null;
    /* How many bars past the cursor are allowed through. Zero while stepping,
       bumped by `Next bar`, and reset by anything that moves the cursor --
       peeking is a question about THIS belief, so it cannot survive the belief
       being replaced. */
    this.peek = 0;
    /* One series per hierarchy row, fetched once and cut by TIME at the cursor.
       By time and not by index: 500 bars back on 15m is not 500 bars back on
       4h, and slicing both by the same count would show a 4h count built from
       bars the 15m chart has not reached. */
    this.mtf = new Map();
    this.mtfDepth = new Map();      // how deep each degree has been fetched
    this.mtfBelief = [];
  }

  /* ---------------------------------------------------------------- mount */

  mount(host) {
    /* A debug handle, the same convenience `window.dnfx` gives the live app.
       Verifying that the two lanes stay locked to one index window is not
       something the DOM can be asked. */
    window.dnfxReplay = this;
    /* REMOUNTING IS NORMAL: the Backtest panel rebuilds its DOM on every render,
       and this same instance is mounted into the new host so the cursor and the
       recorded beliefs survive. Anything bound to the DOCUMENT therefore has to
       be released first -- without this the key handler stacked up one listener
       per render, and `.` stepped two bars while space played and immediately
       stopped itself. */
    this._teardown();
    this.host = host;
    host.innerHTML = '';
    const wrap = el('div', { class: 'ell-wrap' });
    this.bar = el('div', { class: 'ell-bar' });
    /* ONE CHART, TWO HALVES.
     *
     * Left of the dashed marker is what the engine could see; right of it is
     * what happened, washed so the boundary is unmistakable. The count and its
     * projection are drawn from the left half only.
     *
     * The separation that matters is not visual, it is in the data path: the
     * belief is computed from `full.slice(0, cursor + 1)` and never from the
     * array the chart is holding. Drawing the future costs nothing as long as
     * nothing that produces a claim can read it -- and a second chart, which
     * this briefly was, bought the same guarantee at the price of half the
     * height and two price scales to reconcile by eye. */
    this.chartHost = el('div', { class: 'ell-chart' });
    this.panel = el('div', { class: 'ell-panel' });
    wrap.append(this.bar, el('div', { class: 'ell-body' }, this.chartHost, this.panel));
    host.append(wrap);

    this._buildBar();
    this._bindKeys();
    this._paintTransport();
    /* `onChange` deliberately does nothing. It is the single line that keeps
       this chart from writing to the workspace the live charts share. */
    this.chart = new Chart(this.chartHost, {
      symbol: this.symbol, tf: this.tf, type: 'candles',
      onChange: () => {}, onActivate: () => {}, onView: () => {},
    });
    this.chart.view.span = 220;
    this._paintPanel();
    if (!this.full.length) this.load();
    else this._apply();
  }

  _bindKeys() {
    /* Single stepping, which the transport no longer has a button for. Bound on
       the document and removed on unmount, so it cannot outlive the panel. */
    this._keys = (e) => {
      /* `e.target` is not always an Element -- a key event delivered to the
         document has the document as its target, and `document.matches` does
         not exist. The optional call is not defensive noise: without it the
         handler threw and the step keys silently did nothing. */
      if (!this.host || e.target?.matches?.('input,select,textarea')) return;
      if (e.key === '.') { e.preventDefault(); this.stop(); this.step(1); }
      if (e.key === ',') { e.preventDefault(); this.stop(); this.step(-1); }
      /* `n` for the next bar, next to `.` on the keyboard and one letter from
         what it does. */
      if (e.key === 'n') { e.preventDefault(); this.peekAhead(1); }
      if (e.key === ' ') { e.preventDefault(); this.timer ? this.stop() : this.play(1); }
    };
    document.addEventListener('keydown', this._keys);
  }

  /* Everything this instance owns outside its host: the play timer and the
     document key handler. Both outlive the DOM if they are not released -- a
     running transport kept stepping a chart nobody could see. */
  _teardown() {
    if (this._keys) { document.removeEventListener('keydown', this._keys); this._keys = null; }
    this.stop();
  }

  unmount() {
    this._teardown();
    if (this.chart) { this.chart.destroy(); this.chart = null; }
    this.host = null;
  }

  /* ----------------------------------------------------------------- data */

  /* The hierarchy's series, fetched once per symbol. Failures are recorded as
     an empty series rather than thrown: a missing 1d feed should cost that row,
     not the whole panel. */
  async loadHierarchy() {
    this.mtf = new Map();
    await this._fetchHierarchy();
  }

  /**
   * Fetch each degree with enough depth to REACH THE CURSOR.
   *
   * A fixed 3000 bars is 31 days of 15m and 12 years of D1, so a cursor two
   * months back had a full daily series and an empty 15m one -- the execution
   * row simply read "no count", which looks like the counter having nothing to
   * say rather than the data not going back far enough. The depth is therefore
   * derived: the distance from the cursor to now in bars of that frame, plus a
   * warm-up for the counter itself.
   */
  async _fetchHierarchy() {
    const asOfT = this.full.length ? this.full[Math.min(this.i, this.full.length - 1)].t : Date.now();
    const nowT = this.full.length ? this.full[this.full.length - 1].t : Date.now();
    await Promise.all(HIERARCHY.map(async (h) => {
      const step = TF_MS[h.tf] || 36e5;
      const need = Math.min(MTF_CAP,
        Math.ceil((nowT - asOfT) / step) + MTF_WARMUP);
      const have = this.mtfDepth?.get(h.tf) || 0;
      if (have >= need && this.mtf.has(h.tf)) return;      // already deep enough
      try {
        const p = await api.bars(this.symbol, h.tf, need);
        this.mtf.set(h.tf, p.bars || []);
        (this.mtfDepth ||= new Map()).set(h.tf, need);
      } catch { if (!this.mtf.has(h.tf)) this.mtf.set(h.tf, []); }
    }));
  }

  /**
   * The count at each degree, as of the cursor's INSTANT.
   *
   * Every row is cut to `t <= asOfT`, so the D1 row cannot include a day that
   * has not finished by the moment the 1H cursor is standing on. That cut is
   * the only thing making a hierarchy honest: without it the macro row quietly
   * reads tomorrow.
   */
  _readHierarchy(asOfT) {
    const out = [];
    for (const h of HIERARCHY) {
      const bars = this.mtf.get(h.tf) || [];
      const cut = bars.filter((b) => b.t <= asOfT);
      if (cut.length < 120) { out.push({ ...h, empty: true }); continue; }
      const b = countAsOf(cut, { upto: cut.length - 1 });
      out.push({ ...h, belief: b, bars: h.bars, asOfI: cut.length - 1, series: cut });
    }
    return out;
  }

  /**
   * @param {object}  opts
   * @param {number}  opts.months  fetch a date RANGE this many months back
   * @param {number}  opts.seekTo  epoch ms to put the cursor on once loaded
   */
  async load({ months = 0, seekTo = null } = {}) {
    this.stop();
    this.loading = true;
    this.error = null;
    this.months = months;
    this._paintBar();
    try {
      const payload = await api.bars(this.symbol, this.tf, months ? 60000 : 3000,
                                     months);
      this.full = payload.bars || [];
      this.digits = payload.digits;
      this.beliefs = new Map();
      this.revealed = false;
      /* Start 60% in: far enough back to have something to reveal, near enough
         that the counter has bars to work with. */
      this.i = Math.max(120, Math.floor(this.full.length * 0.6));
      /* A range fetch happens BECAUSE a date was asked for, so honour it rather
         than leaving the cursor at 60% of a window whose size just changed. */
      if (seekTo != null && this.full.length) {
        this.i = Math.max(Math.min(120, this.full.length - 1),
                          Math.min(this.full.length - 1, seekBar(this.full, seekTo)));
      }
      /* The per-bar state, computed ONCE for the series. It is the expensive
         half of the cone and it does not depend on the cursor, so recomputing it
         per step would make stepping cost what loading costs. */
      this.coneState = stateSeries(this.full);
      await this.loadHierarchy();
    } catch (err) {
      this.full = [];
      this.error = String(err.message || err);
    }
    this.loading = false;
    if (this.chart) this._apply();
    this._paintBar();
  }


  /**
   * Put the cursor on a date, fetching older history when the date is not in
   * the window that is loaded. See js/ui/strategyreplay.js for the reasoning;
   * the only difference here is the floor, which is the 120 bars the counter
   * needs before it will produce a count at all.
   */
  async gotoDate(text) {
    const ms = ymdToMs(text);
    if (!Number.isFinite(ms)) { this._paintBar(); return; }
    this.stop();

    if (this.full.length && ms < this.full[0].t) {
      const need = (Date.now() - ms) / (UTIL_TF_MS[this.tf] || 60e3) + 320;
      if (need > MAX_FETCH_BARS) {
        this.status.textContent =
          `${text} is ${Math.round(need / 1000)}k bars back on ${this.tf} —`
          + ` past the ${Math.round(MAX_FETCH_BARS / 1000)}k cap. Use a higher timeframe.`;
        return;
      }
      const months = Math.ceil((Date.now() - ms) / (30.44 * 864e5)) + 1;
      await this.load({ months, seekTo: ms });
      return;
    }
    this._seekTo(ms);
  }

  /** Move the cursor to the first bar at or after `ms`, honouring the warmup. */
  _seekTo(ms) {
    if (!this.full.length) return;
    const want = seekBar(this.full, ms);
    const floor = Math.min(120, this.full.length - 1);
    this.i = Math.max(floor, Math.min(this.full.length - 1, want));
    this.peek = 0;
    this._apply();
    if (want < floor) {
      this.status.textContent =
        `${ymd(this.full[this.i].t)} — the first bar the counter has 120 bars of history for.`;
    }
  }

  step(n) {
    if (!this.full.length) return;
    const want = Math.max(120, Math.min(this.full.length - 1, this.i + n));
    if (want === this.i) { this.stop(); return; }    // ran off an end
    this.i = want;
    this.peek = 0;                 // a new bar means a new belief to judge
    this._apply();
  }

  /**
   * Let ONE more bar past the cursor through, without moving the cursor.
   *
   * The count stays exactly as it is. That is the point and it is what
   * stepping cannot do: stepping recomputes the belief, so by the time you see
   * whether the last one was right it has already been replaced by a different
   * one -- and an Elliott count REPAINTS, so the replacement is frequently a
   * different story about the same bars. Holding the belief still and feeding
   * it the future one bar at a time is the only way to watch a specific claim
   * meet the specific bars that settle it.
   */
  peekAhead(n = 1) {
    if (!this.full.length) return;
    this.stop();
    this.peek = Math.max(0, Math.min(this.full.length - 1 - this.i, this.peek + n));
    this._apply({ keepPeek: true });
  }

  /** Run the cursor at `this.speed`, one bar per tick, until it is stopped. */
  play(dir = 1) {
    if (!this.full.length) return;
    this.stop();
    this.dir = dir;
    this.timer = setInterval(() => this.step(dir), this.speed);
    this._paintTransport();
  }

  stop() {
    clearInterval(this.timer);
    this.timer = null;
    this._paintTransport();
  }

  /* The running direction is shown on the button that is running, so the
     transport says what it is doing rather than only what it can do. */
  _paintTransport() {
    if (!this.playBtn) return;
    const fwd = !!this.timer && this.dir > 0;
    const back = !!this.timer && this.dir < 0;
    this.playBtn.textContent = fwd ? '■' : '▶';
    this.playBtn.classList.toggle('on', fwd);
    this.backBtn.textContent = back ? '■' : '◀';
    this.backBtn.classList.toggle('on', back);
    if (this.futBtn) {
      this.futBtn.textContent = this.revealed ? 'Hide future' : 'Reveal';
      this.futBtn.classList.toggle('on', !!this.revealed);
    }
    if (this.peekBtn) {
      /* The count of what has been let through, on the button that let it
         through. Peeking four bars ahead and forgetting is how you conclude a
         count was right about a move it never saw. */
      this.peekBtn.textContent = this.peek ? `Next bar › +${this.peek}`
                                           : 'Next bar ›';
      this.peekBtn.classList.toggle('on', !!this.peek);
      this.peekBtn.disabled = !!this.revealed;
    }
    if (this.recBtn) {
      this.recBtn.classList.toggle('rec', !!this.rec);
      this.recBtn.textContent = this.rec
        ? `⏺ ${recClock(this.rec.startedMs)}` : '⏺ Rec';
      if (this.rec) {
        this.recBtn.title = `Recording — ${this.rec.frames.length} belief`
          + `${this.rec.frames.length === 1 ? '' : 's'} captured. Press to stop and save.`;
      }
    }
  }

  /**
   * Show what followed -- WITHOUT losing the bar you were standing on.
   *
   * The first version jumped the view to the live edge, which is the one place
   * the comparison cannot be made: the count, its invalidation line and its
   * target zone were all a thousand bars off screen. Reveal parks the right
   * edge just past the scoring horizon instead, so the belief and the bars that
   * settled it are in the same picture.
   */
  /* Reveal does two things, and both are the point: it UNHIDES the bars after
     the cursor -- which are not drawn while you step, so the replay generates
     as it goes -- and it widens the window to the full scoring horizon so the
     belief and the bars that settle it are in one picture. Stepping again
     re-hides them; `_apply` clears `revealed` on every move. */
  reveal() {
    if (!this.full.length) return;
    this.stop();
    /* A TOGGLE, not a one-shot. Hiding again is the more common half of the
       gesture: you reveal to check whether the count was right, and then you
       want the chart back the way it was to carry on stepping. Without a way
       back the only route was to step, which throws away the belief you were
       just looking at. */
    if (this.revealed) {
      this.revealed = false;
      this.peek = 0;
      this._apply({ keepPeek: true });
      this._paintBar();
      this._paintTransport();
      return;
    }
    this.revealed = true;
    this.chart.setFutureHidden(false);
    /* Far enough right to cover BOTH the scoring horizon and the projected path,
       so the forecast and the bars that settle it are in the same picture. */
    const c = this.belief && this.belief.counts[0];
    const reach = c && c.projection && c.projection.length
      ? c.projection[c.projection.length - 1].ahead : 0;
    const after = Math.round(Math.max(this.horizon * 1.4, reach + 4));
    this.chart.view.right = Math.min(this.full.length - 1 + this.chart.rightPad(),
      this.i + after);
    this.chart.view.priceLock = null;
    this.chart.draw();
    this._paintBar();
    this._paintTransport();
    this._paintOutcome();
  }

  /** What the belief at the cursor claimed, beside what price actually did. */
  _paintOutcome() {
    const b = this.belief;
    if (!b || !b.counts.length) return;
    const s = scoreBelief(b, this.full, { horizon: this.horizon });
    if (!s) return;
    const c = b.counts[0];
    const proj = scoreProjection(b, this.full);
    const card = el('div', { class: `ell-outcome ${s.hit ? 'ok' : 'no'}` },
      el('div', {}, `claimed ${s.expected} · actual ${s.actual}`),
      el('div', {}, `price moved ${s.moveAtrActual >= 0 ? '+' : ''}`
        + `${s.moveAtrActual.toFixed(2)} ATR in the count's direction `
        + `over ${s.horizon} bars`),
      el('div', {}, s.invalidated
        ? `the count was INVALIDATED — price traded through ${c.invalidation}`
        : 'the invalidation level held'),
    );
    if (proj) {
      /* The path error, which is a harder question than the direction and is
         reported separately for that reason. A count can call the direction and
         still be nowhere near the prices it projected. */
      card.append(el('div', {},
        `projected path off by ${proj.meanAbsAtr.toFixed(2)} ATR on average`
        + ` (${proj.covered}/${proj.of} points reached), terminal `
        + `${proj.terminalAtr >= 0 ? '+' : ''}${proj.terminalAtr.toFixed(2)} ATR`));
    }
    this.panel.prepend(card);
  }

  _apply({ keepPeek = false } = {}) {
    if (!this.chart) return;
    if (!this.full.length) { this.chart.setData({ bars: [] }); return; }
    const slice = this.full.slice(0, this.i + 1);
    if (!keepPeek) this.peek = 0;
    /* `revealed` is deliberately NOT cleared here any more. It used to be, so
       every step re-hid the future -- which is right when Reveal is a one-shot
       and wrong now that it is a mode you are in. A toggle that silently turns
       itself off on the next click of another button is a toggle nobody can
       trust the state of. */
    this.chart.symbol = this.symbol;
    this.chart.tf = this.tf;
    /* THE CHART HOLDS THE WHOLE SERIES; the BELIEF is computed from `slice`.
       That is the entire separation, and it is one line apart on purpose so it
       cannot drift: whatever the chart is showing, `countAsOf` is handed bars
       that stop at the cursor. */
    this.chart.setData({ bars: this.full, symbol: this.symbol, digits: this.digits });
    /* Hidden until `Reveal` asks for it, and re-hidden by the line above that
       clears `revealed` on every step. The count is computed from the bars
       before the cursor either way -- what changes is whether you can read the
       answer off the chart before the count commits to one. */
    this.chart.setFutureHidden(!this.revealed);
    this.chart.setFuturePeek(this.peek);
    this.chart.setAsOfMark(this.i);
    /* ROOM FOR THE FORECAST. The projected path is drawn past the last bar, so
       the view has to hold empty space for it -- parked at the last bar the
       forecast would be off screen, which is the one thing it exists to not be.
       Computed before the view is set, from the count this same call produces. */
    const peek = countAsOf(slice, { upto: slice.length - 1 });
    const reach = peek.counts?.[0]?.projection?.length
      ? peek.counts[0].projection[peek.counts[0].projection.length - 1].ahead
      : 0;
    /* Room for the forecast AND for the bars that settle it, so the projection
       and the outcome are in the same picture without pressing Reveal. */
    const after = Math.max(this.chart.rightPad(), reach + 4, this.peek + 4,
                          Math.round(this.horizon * 1.2));
    this.chart.view.right = Math.min(this.full.length - 1 + this.chart.rightPad(),
      this.i + after);
    /* The price lock has to go on every step. A dragged price axis pins the
       vertical range, and walking the cursor back takes the bars straight out
       of it -- measured on the first build: the lock held 4569-4714 while the
       replay bar closed at 4365 and the chart drew nothing at all. */
    this.chart.view.priceLock = null;

    /* The cone: measured on the history available AT THE CURSOR, attached here
       rather than inside `countAsOf` because it is a property of the series and
       not of the count. */
    if (this.coneState) {
      const reach = peek.counts?.[0]?.projection?.length
        ? peek.counts[0].projection[peek.counts[0].projection.length - 1].ahead : 24;
      const c = cones(this.full, { upto: this.i, precomputed: this.coneState,
        steps: Math.max(12, Math.min(48, Math.max(reach, this.horizon))) });
      /* THE UNCONDITIONAL BAND IS THE ONE DRAWN, and that is a measurement
         rather than a preference: matched on trend, range position, momentum and
         volatility regime, the conditional cone came out within a point of the
         unconditional one on coverage and within 0.06 ATR on width at every
         horizon. Same answer from fewer samples is the worse of the two. */
      peek.cone = c ? { bands: c.unconditional, conditional: c.conditional,
        k: c.k, candidates: c.candidates } : null;
    }

    const belief = peek;
    belief.symbol = this.symbol;
    belief.tf = this.tf;
    this.beliefs.set(this.i, belief);
    this.belief = belief;
    this.mtfBelief = this._readHierarchy(belief.asOfT);
    /* A row that came up short is a row whose series does not reach this far
       back. Deepen it once and repaint -- guarded so a genuinely empty feed does
       not re-request on every step. */
    if (this.mtfBelief.some((r) => r.empty) && !this._deepening) {
      this._deepening = true;
      this._fetchHierarchy().then(() => {
        this._deepening = false;
        if (!this.belief) return;
        this.mtfBelief = this._readHierarchy(this.belief.asOfT);
        this._paintPanel();
      });
    }
    this.chart.setElliott(belief);
    this.chart.draw();
    this._frame();
    this._paintBar();
    this._paintTransport();      // the frame counter lives on the Rec button
    this._paintPanel();
  }

  /* How often the count's own target has been reached, historically, from bars
     like this one. Null when the count has no target -- a correction in
     progress projects a retrace, not a level to travel to. */
  _reach(c) {
    if (!c.target || !this.coneState || !this.belief) return null;
    const atr = this.belief.atr;
    if (!(atr > 0)) return null;
    const dist = Math.abs(c.target[0] - this.belief.close) / atr;
    return reachRate(this.full, { upto: this.i, horizon: this.horizon,
      distAtr: dist, dir: c.dir, precomputed: this.coneState });
  }

  log() {
    return [...this.beliefs.values()].sort((a, b) => a.asOfI - b.asOfI);
  }

  /**
   * Record the replay as VIDEO.
   *
   * `MediaRecorder` over `canvas.captureStream()`, which needs one canvas
   * holding the whole picture -- and half of this view is HTML. So a composite
   * canvas is drawn on every animation frame: the chart's own canvas copied in,
   * the belief panel painted beside it by the same routine the PNG snapshot
   * uses. Recording the chart canvas alone would have been one line and would
   * have produced a video of some candles with no count beside them.
   *
   * MP4 IF THE BROWSER WILL, WEBM OTHERWISE. H.264 in MediaRecorder is recent
   * and not universal; asking for a container the browser cannot make yields an
   * exception at `start()`, so the type is chosen by `isTypeSupported` and the
   * extension follows what was actually negotiated rather than what was asked
   * for. A file named .mp4 holding a WebM stream is worse than a .webm.
   */
  /* async because the soundtrack has to be opened before the codec can be
     chosen. The click's user-gesture privilege carries across this await
     because the await IS the playback call; callers ignore the promise. */
  async toggleRecord() {
    if (this.rec) return this.stopRecording();
    if (!window.MediaRecorder || !this.chart?.canvas?.captureStream) {
      toast('This browser cannot record canvas video');
      return undefined;
    }
    /* THE SOUNDTRACK IS OPENED FIRST, because the codec depends on whether
       there is one: a video-only mime with an audio track attached records the
       picture and silently DROPS the sound, and the first you learn of it is
       on playback. Opening it here also keeps it inside the click -- an
       AudioContext built outside a user gesture starts suspended and would
       render a track of pure silence. It is never routed to the speakers; see
       js/ui/recaudio.js. Null when there is no soundtrack on disk, which means
       a silent recording rather than no recording. */
    const sound = await openAudio();
    const mime = pickMime(!!sound);
    if (!mime) {
      sound?.stop();
      toast('No recordable video format in this browser');
      return undefined;
    }

    const scale = 1;                       // video, not print
    const panelW = 300;
    const headH = 34;
    const src = this.chart.canvas;
    const dpr = window.devicePixelRatio || 1;
    const cv = document.createElement('canvas');
    /* EVEN DIMENSIONS. H.264 chroma is subsampled 2x2, and an odd width or
       height makes the encoder pad or refuse -- measured as a silent failure to
       start on a 1579px composite. */
    cv.width = (Math.round(src.width / dpr) + panelW) & ~1;
    cv.height = (Math.round(src.height / dpr) + headH) & ~1;
    const ctx = cv.getContext('2d');

    const paint = () => {
      if (!this.rec) return;
      ctx.fillStyle = '#02101f';
      ctx.fillRect(0, 0, cv.width, cv.height);
      this._drawHeader(ctx, cv.width, headH);
      ctx.drawImage(src, 0, headH, cv.width - panelW, cv.height - headH);
      ctx.save();
      ctx.translate(0, headH);
      this._drawPanel(ctx, cv.width - panelW, cv.height - headH, panelW, scale, true);
      ctx.restore();
      this.rec.raf = requestAnimationFrame(paint);
    };

    /* A screencast of a chart that changes when the cursor steps: most frames
       are duplicates of the one before. Rate and bitrate are set from the
       measurement in the README rather than from habit. */
    const stream = cv.captureStream(FPS);
    if (sound) stream.addTrack(sound.track);
    const mr = new MediaRecorder(stream, { mimeType: mime, videoBitsPerSecond: BITRATE });
    const chunks = [];
    mr.ondataavailable = (e) => { if (e.data && e.data.size) chunks.push(e.data); };
    mr.onstop = () => this._writeVideo(new Blob(chunks, { type: mime }), mime);

    this.rec = { started: new Date().toISOString(), startedMs: Date.now(),
                 mr, cv, mime, sound, frames: [], raf: 0, tick: 0,
                 audio: sound ? sound.name : null };
    /* The clock has to drive itself: _paintTransport only runs when the cursor
       moves, and a recording left running while you read the panel would show
       0:00 for as long as you sat there. */
    this.rec.tick = setInterval(() => this._paintTransport(), 1000);
    this._frame();                       // the belief sidecar starts here too
    paint();
    mr.start(1000);                      // a chunk a second, so a crash loses one
    this._paintTransport();
    toast(`Recording ${mime.split(';')[0]} — press Rec again to stop and save`);
    return undefined;
  }

  stopRecording() {
    const rec = this.rec;
    if (!rec) return;
    cancelAnimationFrame(rec.raf);
    clearInterval(rec.tick);
    /* The music stops with the picture; nothing else would ever end it. */
    rec.sound?.stop();
    try { rec.mr.stop(); } catch { /* already stopped */ }
    /* `rec` is kept until onstop fires -- the last chunks arrive after this
       returns, and clearing it here would drop the tail of the recording. */
    this._paintTransport();
  }

  async _writeVideo(blob, mime) {
    const rec = this.rec;
    this.rec = null;
    this._paintTransport();
    const ext = mime.startsWith('video/mp4') ? 'mp4' : 'webm';
    const stamp = (rec?.started || new Date().toISOString()).replace(/[:.]/g, '-').slice(0, 19);
    const base = `${this.symbol}_${this.tf}_${stamp}`;
    try {
      const res = await fetch(`/record?name=${encodeURIComponent(`${base}.${ext}`)}`, {
        method: 'POST', headers: { 'Content-Type': mime }, body: blob,
      });
      const out = await res.json();
      if (!res.ok || out.error) throw new Error(out.error || `HTTP ${res.status}`);
      toast(`Saved ${out.saved} (${(out.bytes / 1048576).toFixed(1)} MB)`, 5000);
      /* The belief sidecar goes with it, same basename. It is 25KB against a
         video's several MB, and it is the half a later analysis can query -- a
         video cannot tell you what the count claimed at bar 1804. */
      if (rec && rec.frames.length) this._writeSidecar(rec, base);
    } catch (err) {
      toast(`Could not save the video: ${err.message}`, 6000);
    }
  }

  /* One frame per cursor position. Trimmed to what a later analysis actually
     reads: the full belief object carries every rejected count and its
     evidence strings, which would make a 400-step recording several megabytes
     of text nobody queries. */
  _frame() {
    if (!this.rec || !this.belief) return;
    const b = this.belief;
    this.rec.frames.push({
      i: b.asOfI,
      t: b.asOfT,
      close: b.close,
      atr: b.atr,
      scenario: b.scenario,
      counts: b.counts.map((c) => ({
        label: c.label, kind: c.kind, dir: c.dir, waveNow: c.waveNow,
        share: c.share, outlook: c.outlook, invalidation: c.invalidation,
        target: c.target,
        projection: c.projection,
        evidence: c.evidence.map((e) => ({ key: e.key, value: e.value, ok: e.ok })),
      })),
    });
  }

  /**
   * Write the recording to `data/replays/` through the dev server.
   *
   * A browser download would land wherever the browser puts downloads, which is
   * not the project -- and the point of a recorded replay is that it sits beside
   * the runs it will be compared with.
   */
  async _writeSidecar(rec, base) {
    const name = `${base}.json`;
    /* The scoring is written WITH the beliefs, so a recording is self-contained:
       whoever opens it does not have to re-derive the outcome, and cannot
       re-derive it differently. */
    const score = this.scoreAll();
    const payload = {
      symbol: this.symbol, tf: this.tf,
      started: rec.started, saved: new Date().toISOString(),
      audio: rec.audio || null,
      bars: this.full.length,
      firstBarT: this.full[0]?.t, lastBarT: this.full[this.full.length - 1]?.t,
      horizon: this.horizon,
      note: 'share is an uncalibrated relative weight, not a probability',
      score: { n: score.n, accuracy: score.accuracy, baseline: score.baseline,
               invalidated: score.invalidated, byOutcome: score.byOutcome,
               pathMedianAtr: score.pathMedianAtr },
      frames: rec.frames,
    };
    try {
      const res = await fetch('/record', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, payload }),
      });
      const out = await res.json();
      if (!res.ok || out.error) throw new Error(out.error || `HTTP ${res.status}`);
    } catch { /* the video is the artefact; a missing sidecar is not worth a toast */ }
  }

  /**
   * Score every recorded belief against what actually followed.
   *
   * Only beliefs with a FULL horizon of real bars after them are scored.
   * Grading the last few against however many bars happen to exist would score
   * them on a shorter horizon than they were asked about, which flatters or
   * punishes at random.
   */
  scoreAll({ horizon = this.horizon } = {}) {
    const rows = [];
    for (const b of this.log()) {
      if (b.asOfI + horizon > this.full.length - 1) continue;
      const s = scoreBelief(b, this.full, { horizon });
      if (s) rows.push({ asOfI: b.asOfI, t: b.asOfT, ...s, proj: scoreProjection(b, this.full) });
    }
    return summarise(rows, horizon);
  }

  /**
   * Score EVERY bar in the series, not only the ones stepped through.
   *
   * Clicking `▶ Next bar` two hundred times is a fine way to watch the count
   * evolve and a poor way to measure it: the sample is wherever you happened to
   * stop. This walks the whole series on a fixed stride, which is the number
   * worth quoting.
   */
  scoreSweep({ horizon = this.horizon, stride = 5 } = {}) {
    const rows = [];
    const beliefs = [];
    for (let i = 200; i + horizon < this.full.length; i += stride) {
      const b = countAsOf(this.full, { upto: i });
      if (!b.counts.length) continue;
      beliefs.push(b);
      const s = scoreBelief(b, this.full, { horizon });
      if (s) {
        rows.push({ asOfI: i, t: b.asOfT, ...s,
          /* carried for calibration: the claimed distribution, not just the pick */
          scenario: b.scenario,
          proj: scoreProjection(b, this.full) });
      }
    }
    /* CONE COVERAGE, on the same sweep. A cone is only worth drawing if its 80%
       band contains the outcome about 80% of the time, and that is a different
       question from whether the COUNT was right -- the cone can be honest while
       the count is worthless, which is in fact what the numbers say. */
    const coneRows = [];
    if (this.coneState) {
      for (let i = 200; i + horizon < this.full.length; i += stride * 4) {
        const c = cones(this.full, { upto: i, precomputed: this.coneState, steps: horizon });
        if (c) coneRows.push({ bands: c.unconditional, close: c.close, atr: c.atr, asOfI: i });
      }
    }
    return { ...summarise(rows, horizon), stride, swept: true,
             calibration: calibration(rows), stability: stability(beliefs, { stride }),
             coverage: coneRows.length ? coverage(coneRows, this.full) : null };
  }

  /**
   * One PNG of the chart AND the belief beside it.
   *
   * The live chart's snapshot is canvas-only, which is right there -- the whole
   * picture is on the canvas. Here half the picture is HTML, and an image of a
   * wave count without the count that produced it is a picture of some candles.
   *
   * The panel is REDRAWN onto the canvas rather than screenshotted. There is no
   * DOM-to-image in this project and adding one for this would be a dependency
   * to satisfy a caption; the panel's content is three arrays this file already
   * holds, so it is cheaper and more honest to lay them out directly. What the
   * image says is therefore what the code believes, not what a rasteriser made
   * of the stylesheet.
   */
  snapshot() {
    if (!this.chart || !this.full.length) return;
    const scale = 2;
    const url = this.chart.snapshot({ scale, ink: 'light' });
    if (!url) { toast('Could not capture the chart'); return; }
    const panelW = 300 * scale;
    const img = new Image();
    img.onload = () => {
      const cv = document.createElement('canvas');
      cv.width = img.width + panelW;
      cv.height = img.height;
      const ctx = cv.getContext('2d');
      ctx.fillStyle = '#FFFFFF';
      ctx.fillRect(0, 0, cv.width, cv.height);
      ctx.drawImage(img, 0, 0);
      this._drawPanel(ctx, img.width, cv.height, panelW, scale);
      const stamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
      const name = `elliott_${this.symbol}_${this.tf}_bar${this.i}_${stamp}.png`;
      const a = el('a', { href: cv.toDataURL('image/png'), download: name });
      document.body.append(a);
      a.click();
      a.remove();
      toast(`Saved ${name}`);
    };
    img.onerror = () => toast('Could not compose the snapshot');
    img.src = url;
  }

  /**
   * The brand header, drawn into every video frame.
   *
   * A recording gets shared, and a shared frame has to say what it is and where
   * it came from -- a chart with a wave count on it and no header is a chart
   * from anywhere. The mark is drawn from the same 32-unit artboard the app's
   * favicon and the chart's export stamp use, so the three cannot drift.
   *
   * It sits in a band ABOVE the chart rather than over it: the top-left of the
   * plot is where the count's own labels appear, and a logo there covers the
   * thing the video exists to show.
   */
  _drawHeader(ctx, w, h) {
    ctx.save();
    ctx.fillStyle = '#041E42';
    ctx.fillRect(0, 0, w, h);
    ctx.fillStyle = 'rgba(13,58,107,.9)';
    ctx.fillRect(0, h - 1, w, 1);

    const M = 20;                       // mark size
    const x = 12, y = (h - M) / 2, u = M / 32;
    const sq = (rx, ry, fill) => {
      ctx.fillStyle = fill;
      ctx.fillRect(x + rx * u, y + ry * u, 14 * u, 14 * u);
    };
    sq(0, 0, '#171C8F'); sq(18, 0, '#FF9E1B');
    sq(0, 18, '#93C90F'); sq(18, 18, '#E31C79');
    sq(9, 9, '#B1B3B3');

    ctx.textBaseline = 'middle';
    ctx.font = '700 15px "Roboto Condensed", Arial, sans-serif';
    const tx = x + M + 9, ty = h / 2;
    ctx.fillStyle = '#D9D9D6';
    ctx.fillText('DiaNur', tx, ty);
    ctx.fillStyle = '#E31C79';
    ctx.fillText('Fx', tx + ctx.measureText('DiaNur').width, ty);

    ctx.font = '11px "Roboto Mono", monospace';
    ctx.fillStyle = '#8fa6c0';
    const mid = `${this.symbol}  ${this.tf}  ·  Elliott replay`;
    ctx.fillText(mid, tx + 96, ty);

    /* The as-of bar, right-aligned. It is the one number a viewer scrubbing the
       video needs and cannot otherwise read off a moving chart. */
    const b = this.belief;
    const right = `bar ${this.i + 1}/${this.full.length}`
      + (b && b.asOfT ? `  ${new Date(b.asOfT).toISOString().slice(0, 16).replace('T', ' ')}Z` : '');
    ctx.textAlign = 'right';
    ctx.fillStyle = '#5d7794';
    ctx.fillText(right, w - 12, ty);
    ctx.restore();
  }

  /* Light ink, because the chart half is exported for a white page and two
     halves in different palettes is not one image. */
  _drawPanel(ctx, x0, h, w, scale, dark = false) {
    const b = this.belief;
    /* Two palettes, one layout. The PNG is exported for a white page; the video
       is a recording of the app and has to look like the app. Anything else
       means the panel in the video is a different object from the panel on
       screen. */
    const C = dark
      ? { bg: '#02101f', rule: '#0a2f57', head: '#d9d9d6', sub: '#5d7794',
          body: '#8fa6c0', ok: '#93C90F', no: '#5d7794', warn: '#FF9E1B' }
      : { bg: '#F4F6F8', rule: '#D6DBE0', head: '#111', sub: '#666',
          body: '#555', ok: '#2f7d18', no: '#999', warn: '#b0402a' };
    const pad = 14 * scale;
    let y = 22 * scale;
    const px = (v) => (Number.isFinite(v) ? v.toFixed(this.digits ?? 2) : '—');
    const line = (text, { size = 10.5, colour = C.body, gap = 5, bold = false } = {}) => {
      ctx.font = `${bold ? '700 ' : ''}${size * scale}px Roboto Condensed, Arial, sans-serif`;
      ctx.fillStyle = colour;
      /* Wrapped by measurement rather than by character count: the panel text is
         proportional and a fixed break lands mid-word on half the lines. */
      const max = w - pad * 2;
      let cur = '';
      for (const word of String(text).split(' ')) {
        const test = cur ? `${cur} ${word}` : word;
        if (ctx.measureText(test).width > max && cur) {
          ctx.fillText(cur, x0 + pad, y);
          y += (size + 3) * scale;
          cur = word;
        } else { cur = test; }
      }
      if (cur) { ctx.fillText(cur, x0 + pad, y); y += (size + gap) * scale; }
    };

    ctx.fillStyle = C.bg;
    ctx.fillRect(x0, 0, w, h);
    ctx.strokeStyle = C.rule;
    ctx.lineWidth = 1 * scale;
    ctx.beginPath();
    ctx.moveTo(x0 + 0.5 * scale, 0); ctx.lineTo(x0 + 0.5 * scale, h);
    ctx.stroke();

    line(`${this.symbol} · ${this.tf} · Elliott replay`, { size: 13, bold: true, colour: C.head });
    line(`bar ${this.i + 1} of ${this.full.length}`
      + (b && b.asOfT ? ` · ${new Date(b.asOfT).toISOString().slice(0, 16).replace('T', ' ')} UTC` : ''),
    { size: 9.5, colour: C.sub, gap: 10 });

    if (!b || !b.counts.length) { line('no admissible count at this bar', { colour: C.sub }); return; }

    b.counts.forEach((c, k) => {
      line(`${k === 0 ? 'PRIMARY' : `ALT ${String.fromCharCode(65 + k)}`}  ${Math.round(c.share * 100)}%`,
        { size: 9.5, bold: true, colour: C.sub, gap: 2 });
      line(c.label, { size: 11.5, bold: k === 0, colour: C.head, gap: 2 });
      line(`expects ${c.outlook} · invalid ${c.dir > 0 ? 'below' : 'above'} ${px(c.invalidation)}`
        + (c.target ? ` · target ${px(c.target[0])}–${px(c.target[1])}` : ''),
      { size: 9.5, colour: C.body, gap: k === 0 ? 4 : 10 });
      if (k === 0) {
        for (const e of c.evidence) {
          line(`${e.ok ? '✓' : '✗'} ${e.text}`, { size: 9.5, gap: 2,
            colour: e.ok ? C.ok : C.no });
        }
        y += 6 * scale;
      }
    });

    const sc = b.scenario;
    line(`continuation ${Math.round(sc.continuation * 100)}%   `
      + `correction ${Math.round(sc.correction * 100)}%   `
      + `reversal ${Math.round(sc.reversal * 100)}%`,
    { size: 10, colour: C.head, gap: 8 });

    /* The caveat travels WITH the image. A screenshot outlives the session that
       made it, and a percentage in a picture reads as a probability unless the
       picture says otherwise. */
    line('Share is an uncalibrated relative weight from this scoring function, '
      + 'not a probability.', { size: 9, colour: C.sub });
    if (this.revealed) {
      line('FUTURE REVEALED — bars right of the count were not visible to it.',
        { size: 9, colour: C.warn });
    }
  }

  /* ------------------------------------------------------------------- UI */

  _buildBar() {
    const btn = (label, title, fn) => {
      const b = el('button', { class: 'rp-btn', title }, label);
      b.addEventListener('click', fn);
      return b;
    };
    const sel = (opts, value, fn) => {
      const s = el('select', { class: 'rp-sel' });
      for (const o of opts) {
        const opt = el('option', { value: String(o) }, String(o));
        if (String(o) === String(value)) opt.selected = true;
        s.append(opt);
      }
      s.addEventListener('change', () => fn(s.value));
      return s;
    };

    /* A BUTTON that opens the shared picker, not a text field. Typing a
       ticker means knowing the broker's exact suffix -- XAUUSD.a, not XAUUSD --
       and getting it wrong loaded an empty series with nothing to explain why.
       The picker searches the broker's real symbol list. */
    this.symInput = btn(this.symbol, 'Change symbol — searches the broker list',
      () => {
        const ok = openSymbolSearch((sym) => {
          this.symbol = sym;
          this.symInput.textContent = sym;
          this.load();
        });
        if (!ok) this.status.textContent = 'symbol picker unavailable';
      });
    this.symInput.classList.add('rp-symbtn');

    this.status = el('span', { class: 'rp-status' });
    /* A TRANSPORT, not a set of nudge buttons. Watching the count evolve is the
       thing this view is for, and clicking `next bar` two hundred times is not
       watching it -- play runs the cursor forward on its own and you read the
       panel as it changes. Single stepping is still there on `,` and `.`, which
       is where it belongs: it is the fine adjustment, not the main gesture. */
    /* THE SAME TRANSPORT AS THE STRATEGY REPLAY, in the same order: step back,
       play back, play forward, step forward, run to the end. Two panels that
       do the same job with different controls is two things to learn for one
       gesture, and the muscle memory does not transfer.

       Each play button is also its own stop, which is why the separate ■ is
       gone: it was a third thing to aim at for something the running button
       can do itself. Clicking the opposite direction turns around rather than
       stopping. */
    const runner = (dir) => () => {
      if (this.timer && this.dir === dir) this.stop();
      else this.play(dir);
    };
    this.stepBackBtn = btn('◀◀', 'One bar back  (,)',
                           () => { this.stop(); this.step(-1); });
    this.backBtn = btn('◀', 'Play backward — press again to stop', runner(-1));
    this.playBtn = btn('▶', 'Play forward  (space) — press again to stop', runner(1));
    this.stepFwdBtn = btn('▶▶', 'One bar forward  (.)',
                          () => { this.stop(); this.step(1); });
    this.endBtn = btn('▶▶|', 'Run to the end',
                      () => { this.stop(); this.step(this.full.length); });
    for (const b of [this.stepBackBtn, this.backBtn, this.playBtn,
                     this.stepFwdBtn, this.endBtn]) b.classList.add('rp-tp');
    /* A DATE, not a bar number: a bar index moves every time the window is
       refetched, and a date is what you remember about the move you want to
       look at again. It doubles as a readout of where the cursor is. */
    this.dateInput = el('input', {
      type: 'date', class: 'rp-date',
      title: 'Jump the cursor to this date. Older than the loaded window and '
        + 'the history is fetched first.',
    });
    this.dateInput.addEventListener('change', () => this.gotoDate(this.dateInput.value));
    this.futBtn = btn('Reveal',
      'Show every bar after the cursor, and widen the window to the scoring '
      + 'horizon. Press again to hide them and carry on stepping.',
      () => this.reveal());
    this.peekBtn = btn('Next bar ›',
      'Reveal ONE more bar after the cursor without moving it  (n). The count '
      + 'stays exactly as it is, so you watch this belief meet the bars that '
      + 'settle it instead of it being replaced by a new one.',
      () => this.peekAhead(1));
    this.bar.append(
      this.symInput,
      sel(TFS, this.tf, (v) => { this.tf = v; this.load(); }),
      this.dateInput,
      this.stepBackBtn, this.backBtn, this.playBtn, this.stepFwdBtn, this.endBtn,
      sel(SPEEDS.map((x) => x.label), SPEEDS[1].label, (v) => {
        this.speed = (SPEEDS.find((x) => x.label === v) || SPEEDS[1]).ms;
        if (this.timer) this.play(this.dir);        // re-arm at the new rate
      }),
      this.futBtn,
      this.peekBtn,
      (this.recBtn = btn('⏺ Rec', 'Record every step; press again to write it '
        + 'to data/replays/', () => this.toggleRecord())),
      btn('⤓ PNG', 'Save the chart AND this panel as one image',
        () => this.snapshot()),
      el('span', { class: 'rp-sep' }),
      el('span', { class: 'rp-lbl' }, 'horizon'),
      sel(HORIZONS, this.horizon, (v) => { this.horizon = Number(v); }),
      btn('Score stepped', 'Score only the bars you stepped through',
        () => this._paintScore(this.scoreAll())),
      btn('Score whole series', 'Walk every 5th bar of the series and score it',
        () => this._paintScore(this.scoreSweep())),
      this.status,
    );
  }

  _paintBar() {
    if (!this.status) return;
    if (this.dateInput && this.full.length) {
      const b = this.full[this.i];
      if (b) this.dateInput.value = ymd(b.t);
      this.dateInput.min = ymd(this.full[0].t);
      this.dateInput.max = ymd(this.full[this.full.length - 1].t);
    }
    if (this.loading) {
      this.status.textContent = this.months
        ? `loading ${this.months} months of ${this.tf} bars…` : 'loading…';
      return;
    }
    if (!this.full.length) {
      this.status.textContent = this.error || 'no history';
      return;
    }
    /* Display zone, matching the axis, the legend and the date picker. A UTC
       status line beside a broker-time picker disagreed with it by three hours
       and a date, which reads as a broken seek rather than as two clocks. The
       video header and the snapshot panel stay UTC on purpose -- they outlive
       the session that made them. */
    const t = this.belief && this.belief.asOfT
      ? `${ymd(this.belief.asOfT)} ${hhmm(this.belief.asOfT)}` : '';
    this.status.textContent = `bar ${this.i + 1} / ${this.full.length}`
      + (t ? ` · ${t}` : '') + (this.revealed ? ' · future revealed' : '')
      + (this.rec ? ` · REC ${this.rec.frames.length}` : '');
  }

  _paintPanel() {
    const host = this.panel;
    if (!host) return;
    const b = this.belief;
    host.innerHTML = '';
    if (!b || !b.counts.length) {
      host.append(el('div', { class: 'ell-empty' }, 'no admissible count at this bar'));
      return;
    }
    const px = (v) => (Number.isFinite(v) ? v.toFixed(this.digits ?? 2) : '—');

    host.append(el('div', { class: 'ell-note' },
      `The % is the target's REACH RATE: how often price actually travelled that `
      + `far in that direction within ${this.horizon} bars, measured on windows `
      + 'that closed before this bar. It is not the count’s confidence — that '
      + 'number was measured and had none.'));

    b.counts.forEach((c, k) => {
      const row = el('div', { class: `ell-count ${k === 0 ? 'primary' : ''}` });
      /* THE NUMBER ON THE RIGHT IS THE TARGET'S REACH RATE, not the count's
         share. The share was one scoring function's weight with nothing behind
         it -- measured, its confidence was inverted, and weighting a cone by it
         cost 33 points of coverage. The reach rate is the same visual slot
         filled with something checkable: how often price has actually travelled
         that far, in that direction, within the horizon. */
      const reach = this._reach(c);
      row.append(
        el('div', { class: 'ell-head' },
          el('b', {}, k === 0 ? 'Primary' : `Alt ${String.fromCharCode(65 + k)}`),
          el('span', { class: 'ell-label' }, c.label),
          el('span', { class: 'ell-share', title: reach
            ? `price reached this target within ${this.horizon} bars in `
              + `${reach.n} of the comparable windows before this bar`
            : 'no target to measure' },
          reach ? pct(reach.rate) : '—')),
        el('div', { class: 'ell-line' },
          `expects ${c.outlook} · invalidated ${c.dir > 0 ? 'below' : 'above'} ${px(c.invalidation)}`
          + (c.target ? ` · target ${px(c.target[0])}–${px(c.target[1])}` : '')),
      );
      if (k === 0) {
        const ev = el('div', { class: 'ell-ev' });
        for (const e of c.evidence) {
          ev.append(el('div', { class: e.ok ? 'ok' : 'no' }, `${e.ok ? '✓' : '✗'} ${e.text}`));
        }
        row.append(ev);
      }
      host.append(row);
    });

    const s = b.scenario;
    host.append(el('div', { class: 'ell-scen' },
      el('div', {}, `continuation ${pct(s.continuation)}`),
      el('div', {}, `correction ${pct(s.correction)}`),
      el('div', {}, `reversal ${pct(s.reversal)}`)));

    this._paintHierarchy();
  }

  /**
   * The count at each degree: macro down to execution.
   *
   * WHAT THIS DELIBERATELY DOES NOT PRINT is a line like `Next 1H -> bullish
   * 67%`. That number was measured on this instrument at these timeframes and
   * it has no skill: fitted walk-forward and honestly calibrated, the forecast
   * collapses onto the base rate and still scores a shade WORSE than ignoring
   * the chart (Brier skill -2.6% on 15m, -3.6% on 1H, -5.2% on 4H). Printing a
   * confident percentage next to a direction would be inventing the one thing
   * this whole harness exists to test.
   *
   * So each row shows what the count IS -- direction, wave, the level that
   * refutes it -- and the weight is labelled as a weight. The direction is
   * still worth reading: it is the count's claim, and it is falsifiable at the
   * invalidation price. The percentage is not.
   */
  _paintHierarchy() {
    const rows = this.mtfBelief || [];
    if (!rows.length) return;
    const host = this.panel;
    host.append(el('div', { class: 'ell-sec' }, 'Structure by degree'));
    const tbl = el('table', { class: 'ell-tbl ell-mtf' });
    tbl.append(el('tr', {},
      el('th', {}, 'tf'), el('th', {}, 'reads'), el('th', {}, 'bias'), el('th', {}, 'reach')));
    for (const r of rows) {
      if (r.empty || !r.belief || !r.belief.counts.length) {
        tbl.append(el('tr', {},
          el('td', {}, r.tf), el('td', { class: 'thin' }, 'no count'),
          el('td', {}, '—'), el('td', {}, '—')));
        continue;
      }
      const c = r.belief.counts[0];
      const up = c.dir > 0;
      tbl.append(el('tr', {},
        el('td', {}, r.tf),
        el('td', { class: 'wide' }, `${c.kind === 'impulse' ? 'wave' : ''} ${c.waveNow} ${c.outlook}`.trim()),
        el('td', { class: up ? 'ok' : 'no' }, up ? 'up' : 'down'),
        /* Each degree measures its target against ITS OWN series, which is the
           only comparison that means anything -- a 4h target is not a 15m
           target expressed in different bars. */
        el('td', { class: 'thin' }, (() => {
          if (!c.target || !r.series) return '—';
          const a = r.belief.atr;
          if (!(a > 0)) return '—';
          const d = Math.abs(c.target[0] - r.belief.close) / a;
          const rr = reachRate(r.series, { upto: r.asOfI, horizon: r.bars,
            distAtr: d, dir: c.dir });
          return rr ? pct(rr.rate) : '—';
        })())));
    }
    host.append(tbl);
    /* Agreement across degrees is the one aggregate worth showing, because it
       is a fact about the readings rather than a forecast: it says whether the
       structure tells one story or four. */
    const dirs = rows.filter((r) => r.belief && r.belief.counts.length)
      .map((r) => r.belief.counts[0].dir);
    const up = dirs.filter((d) => d > 0).length;
    host.append(el('div', { class: 'ell-line' },
      dirs.length
        ? `${up} of ${dirs.length} degrees read up`
          + (up === dirs.length || up === 0 ? ' — aligned' : ' — divided')
        : 'no counts at any degree'));
    host.append(el('div', { class: 'ell-note' },
      'Reach is measured per degree against its own series and its own horizon. '
      + 'The count’s own confidence is not shown: fitted walk-forward and '
      + 'calibrated it has no measured skill on XAUUSD at 15m, 1H or 4H — see '
      + 'tools/elliott_fit.mjs. The DIRECTION, the invalidation price and the '
      + 'reach rate are the parts that can be checked.'));
  }

  _paintScore(s) {
    const host = this.panel;
    host.innerHTML = '';
    if (!s.n) {
      host.append(el('div', { class: 'ell-empty' },
        `nothing to score — a belief needs ${s.horizon} bars after it before the `
        + 'outcome is known.'));
      return;
    }
    const edge = s.accuracy - s.baseline;
    host.append(
      el('div', { class: 'ell-note' },
        `${s.n} beliefs${s.swept ? ` (every ${s.stride}th bar of the series)`
          : ' (the bars you stepped through)'}, `
        + `${s.horizon}-bar horizon, outcome bucketed at ±0.75 ATR.`),
      el('div', { class: 'ell-score' },
        el('div', {}, `accuracy ${pct(s.accuracy)}`),
        el('div', {}, `baseline ${pct(s.baseline)}`),
        el('div', { class: edge > 0 ? 'ok' : 'no' },
          `edge ${edge > 0 ? '+' : ''}${(edge * 100).toFixed(1)}pp`)),
      el('div', { class: 'ell-line' },
        `invalidated within horizon ${pct(s.invalidated)} · `
        + Object.entries(s.byOutcome).map(([k, v]) => `${k} ${v}`).join(' · ')),
      s.pathMedianAtr != null
        ? el('div', { class: 'ell-line' },
          `projected path off by ${s.pathMedianAtr.toFixed(2)} ATR (median over `
          + `${s.pathN} paths) — the direction and the path are separate questions, `
          + 'and a count can call one while missing the other')
        : el('div', {}),
      el('div', { class: 'ell-note' }, edge > 0
        ? 'Above baseline on this sample. One instrument and one window is not a '
          + 'result — check the other timeframes before believing it.'
        : 'AT OR BELOW the baseline: naming the most common outcome every time '
          + 'would have done as well or better. That is the finding, not a bug.'),
      el('div', { class: 'ell-note' },
        'Baseline = always naming the most common outcome in this sample.'),
    );
    if (s.coverage) this._paintCoverage(s.coverage);
    if (s.calibration) this._paintCalibration(s.calibration);
    if (s.stability) this._paintStability(s.stability);
  }

  /* Does the cone mean what it says? A nominal 80% band should contain the
     outcome about 80% of the time. Width is reported beside it because coverage
     alone can be bought by widening -- a band from -10 to +10 ATR covers
     everything and says nothing. */
  _paintCoverage(rows) {
    const host = this.panel;
    host.append(
      el('div', { class: 'ell-sec' }, 'Cone coverage'),
      el('div', { class: 'ell-note' },
        'A nominal 80% band should hold the outcome ~80% of the time. Width is in '
        + 'ATR, so it is comparable across instruments.'),
    );
    const tbl = el('table', { class: 'ell-tbl' });
    tbl.append(el('tr', {},
      el('th', {}, 'bars'), el('th', {}, 'n'),
      el('th', {}, '80%'), el('th', {}, '50%'), el('th', {}, 'w80')));
    for (const r of rows) {
      const off = Math.abs(r.cover80 - 0.8);
      tbl.append(el('tr', {},
        el('td', {}, `+${r.horizon}`),
        el('td', {}, String(r.n)),
        el('td', { class: off <= 0.05 ? 'ok' : 'no' }, pct(r.cover80)),
        el('td', { class: Math.abs(r.cover50 - 0.5) <= 0.05 ? 'ok' : 'no' }, pct(r.cover50)),
        el('td', { class: 'thin' }, `${r.width80Atr.toFixed(1)}`)));
    }
    host.append(tbl);
  }

  /* Accuracy says how often the top pick was right. Calibration says whether the
     NUMBER meant anything, which is the question that decides if `share` may
     ever be printed as a probability. */
  _paintCalibration(c) {
    const host = this.panel;
    host.append(
      el('div', { class: 'ell-sec' }, 'Calibration'),
      el('div', { class: 'ell-note' },
        'Does a 70% forecast happen 70% of the time? Bucketed by the confidence '
        + 'the forecast claimed for the class it named.'),
    );
    const tbl = el('table', { class: 'ell-tbl' });
    tbl.append(el('tr', {},
      el('th', {}, 'claimed'), el('th', {}, 'n'),
      el('th', {}, 'said'), el('th', {}, 'happened'), el('th', {}, 'gap')));
    for (const b of c.buckets) {
      tbl.append(el('tr', {},
        el('td', {}, `${Math.round(b.lo * 100)}–${Math.round(Math.min(b.hi, 1) * 100)}%`),
        el('td', {}, String(b.n)),
        el('td', {}, pct(b.claimed)),
        el('td', {}, pct(b.happened)),
        /* A bucket under 30 samples is a coincidence, not a measurement, and is
           greyed rather than dropped -- hiding it would make the table look
           more settled than the data is. */
        el('td', { class: b.n < 30 ? 'thin' : (b.gap >= -0.05 ? 'ok' : 'no') },
          `${b.gap >= 0 ? '+' : ''}${Math.round(b.gap * 100)}pp`)));
    }
    host.append(tbl);
    host.append(el('div', { class: 'ell-line' },
      `Brier ${c.brier.toFixed(4)} vs climatology ${c.brierBase.toFixed(4)}`
      + ` · skill ${c.skill >= 0 ? '+' : ''}${(c.skill * 100).toFixed(1)}%`));
    host.append(el('div', { class: 'ell-note' }, c.skill > 0
      ? 'Beats a forecast that ignores the chart and always predicts this '
        + 'sample’s base rates.'
      : 'WORSE THAN CLIMATOLOGY: always predicting this sample’s base rates '
        + 'would have scored better. The shares carry no information yet, which '
        + 'is why the panel does not call them probabilities.'));
  }

  /* The repainting question, which a finished chart cannot answer because a
     finished chart always looks right -- the count was fitted to it. */
  _paintStability(st) {
    this.panel.append(
      el('div', { class: 'ell-sec' }, 'Stability / repainting'),
      el('div', { class: 'ell-line' },
        `primary reading changed on ${pct(st.flipRate)} of steps`),
      el('div', { class: 'ell-line' },
        `changed DIRECTION on ${pct(st.dirFlipRate)} — the change that would have `
        + 'reversed a trade rather than relabelled one'),
      el('div', { class: 'ell-line' },
        `median run ${st.medianRunBars} bars · longest ${st.longestRunBars} bars`),
      el('div', { class: 'ell-note' },
        `Sampled every ${st.stride} bars, so a run is measured to that resolution.`),
    );
  }
}

const pct = (x) => `${Math.round((x || 0) * 100)}%`;

function summarise(rows, horizon) {
  const n = rows.length;
  const hits = rows.filter((r) => r.hit).length;
  const dead = rows.filter((r) => r.invalidated).length;
  /* The BASELINE is what always naming the most common outcome would score. An
     accuracy figure without it is unreadable: 55% is strong against a 40%
     baseline and worthless against 60%. */
  const byOutcome = {};
  for (const r of rows) byOutcome[r.actual] = (byOutcome[r.actual] || 0) + 1;
  const majority = n ? Math.max(...Object.values(byOutcome)) : 0;
  /* PATH ERROR is reported as a MEDIAN, not a mean: a handful of counts project
     into a gap and miss by twenty ATR, and an average would be a report on those
     few rather than on the typical case. */
  const errs = rows.map((r) => r.proj && r.proj.meanAbsAtr)
    .filter(Number.isFinite).sort((a, b) => a - b);
  return {
    n, hits, accuracy: n ? hits / n : null, baseline: n ? majority / n : null,
    invalidated: n ? dead / n : null, byOutcome, horizon, rows,
    pathN: errs.length,
    pathMedianAtr: errs.length ? errs[Math.floor(errs.length / 2)] : null,
  };
}
