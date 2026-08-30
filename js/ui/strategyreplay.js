/* strategyreplay.js — step the one validated strategy through history.
 *
 * WHY THIS EXISTS. A backtest summary says 207 trades, 36% win rate, PF 1.47.
 * Those numbers are true and they do not tell you what trading it FEELS like:
 * nine losses in a row, four days in a position, two thirds of exits taken by a
 * channel that moves under you every bar. This walks the rule forward one bar at
 * a time so the sequence is visible rather than aggregated.
 *
 * It is a SANDBOX, for the same reason js/ui/replay.js is: it owns its own
 * Chart, its own bars, and an `onChange` that does nothing, so nothing here can
 * write a workspace key or disturb the chart you trade from. Nothing in
 * js/main.js knows this file exists.
 *
 * THE FUTURE IS NOT HIDDEN, IT IS UNREADABLE. The chart is handed the whole
 * series and an as-of mark, because seeing what happened next is the point of a
 * replay. The signal is computed from `full.slice(0, cursor + 1)` and never from
 * the array the chart holds -- one line apart, so it cannot drift.
 *
 * WHAT THE SCORECARD MEANS. Every number in the panel is computed from trades
 * CLOSED AT OR BEFORE THE CURSOR. It is what you would have known standing at
 * that bar, not the run's final statistics shown early. Walking to the end
 * reproduces the backtest; stopping halfway shows you the drawdown you would
 * have been sitting in.
 *
 * THERE IS NO TAKE-PROFIT LINE, and that is not an omission. 138 of 207
 * out-of-sample exits were the trailing 10-bar channel and none were a target;
 * tools/tp_sweep.py measured that capping at 1R turns +43.7 net R into -2.1.
 * The exit level is drawn as what it is -- a level that moves every bar.
 */

import { api } from '../api.js';
import { Chart } from '../chart/engine.js';
import { TF_MS, el, hhmm, px, seekBar, ymd, ymdToMs } from '../util.js';
import { toast } from './menu.js';
import { tip } from './tips.js';
import { openAudio, pickMime } from './recaudio.js';
import { openSymbolSearch } from './search.js';
import { FLAT, LONG, instruction, runRule, tally } from '../chart/rules.js';
import { STATUS_TEXT, STRATEGIES, byKey, coversCell } from '../chart/strategies.js';
import { describeBand, targetBands } from '../chart/targets.js';

const SPEEDS = [{ label: '1x', ms: 400 }, { label: '2x', ms: 200 }, { label: '4x', ms: 100 }];

/* Modelled slippage, in ATR. The same 0.02 sim/core and tools/paper_trade.py
   use -- a replay that estimated fills with a different number would be showing
   a strategy the backtest never ran. */
const SLIP_ATR = 0.02;

/* The traced exit level. Deliberately NOT the SL colour: the stop is fixed
   and this moves, and colouring them alike is how a reader concludes the
   stop trails when it does not. */
const TRAIL_COL = '#31c7d6';

/* Ribbon colours come from the segment renderer's vocabulary, which is named
   for market episodes rather than trade outcomes. The label carries the real
   meaning; only the hue is borrowed. */
const WIN_KIND = 'trending_up';
const LOSS_KIND = 'trending_down';

/* Video rate and bitrate, the same numbers js/ui/replay.js records at. A
   screencast of a chart that only changes when the cursor steps is mostly
   duplicate frames, so 15fps costs nothing and 2.5 Mbps is generous for flat
   colour and thin lines. Matching the Elliott replay matters more than tuning
   either: two recorders in one project that produce different-looking files
   is a difference a viewer has to explain to themselves. */
/* The most bars a date jump is allowed to ask MetaTrader for. `months` mode
   returns a date RANGE, so the count is bars-per-day times days and it grows
   without limit on the fast timeframes -- two years of 1m is three quarters of
   a million bars. The cap is what turns "that date is far back" into a
   sentence instead of a dead tab. */
const MAX_FETCH_BARS = 30000;

const FPS = 15;
const BITRATE = 2.5e6;

/** Elapsed recording time as m:ss -- what the file's length will be. */
function recClock(startedMs) {
  const secs = Math.max(0, Math.round((Date.now() - startedMs) / 1000));
  return `${Math.floor(secs / 60)}:${String(secs % 60).padStart(2, '0')}`;
}

export class StrategyReplay {
  constructor() {
    this.symbol = 'XAUUSD.a';
    this.tf = '4h';
    /* Which rule is loaded. The registry decides what is available;
       adding one there adds it here with no change to this file. */
    this.strategyKey = STRATEGIES[0].key;
    this.full = [];
    this.i = 0;
    this.chart = null;
    this.host = null;
    this.loading = false;
    this.error = null;
    this.timer = null;
    this.dir = 1;                 // which way `play` is running
    this.speed = 400;
    this.digits = 2;
    /* THE FUTURE ARRIVES AS YOU STEP. The chart still holds the whole series
       -- slicing it would reset the view on every step -- but nothing past the
       cursor is drawn, measured or scaled, so a bar appears when the cursor
       reaches it. Walking a rule forward with the answer already on screen is
       not walking it forward: you read the outcome off the shape of the chart
       and then watch the rule catch up. `Reveal` turns it off when the
       question is what happened next rather than what it felt like. */
    this.futureHidden = true;
    /* Bars let through past the cursor without moving it. Zero while stepping;
       `Next bar` bumps it. The rule stays frozen on the bar it decided on, so
       what arrives is the market answering THIS decision rather than a new
       decision arriving with it. */
    this.peek = 0;
    /* A RECORDING, in the shape js/ui/replay.js established: a video of the
       composite frame plus a JSON sidecar of what the rule claimed at every
       cursor position, both written to data/replays/ through the dev server.

       IT MEANS SOMETHING DIFFERENT HERE, and the sidecar says so rather than
       leaving a reader to assume. An Elliott count REPAINTS -- the same bar can
       be wave 3 today and wave 1 tomorrow -- so the order of visits is the
       measurement, and a frame log is the only way to catch it. A rule is a
       pure function of bars[0..i]: step to bar 1804 by any route and it claims
       exactly the same thing. So the frames here are not evidence about the
       rule, they are a record of the SESSION -- what was watched, in what
       order, with which parameters. The deliverable is the trade ledger and
       the equity path written beside them. */
    this.rec = null;
  }

  /* ---------------------------------------------------------------- mount */

  mount(host) {
    window.dnfxStrategy = this;         // a debug handle, like the live app's
    /* Remounting is normal: the Backtest panel rebuilds its DOM on every
       render and this same instance is mounted into the new host, so the cursor
       survives. Document-bound listeners must therefore be released first or
       they stack one per render. */
    this._teardown();
    this.host = host;
    host.innerHTML = '';
    this.bar = el('div', { class: 'sr-bar' });
    this.chartHost = el('div', { class: 'sr-chart' });
    this.panel = el('div', { class: 'sr-panel' });
    host.append(el('div', { class: 'sr-wrap' }, this.bar,
      el('div', { class: 'sr-body' }, this.chartHost, this.panel)));

    this._buildBar();
    this._bindKeys();
    /* `onChange` deliberately does nothing -- the single line that keeps this
       chart from writing to the workspace the live charts share. */
    this.chart = new Chart(this.chartHost, {
      symbol: this.symbol, tf: this.tf, type: 'candles',
      onChange: () => {}, onActivate: () => {}, onView: () => {},
    });
    this.chart.view.span = 200;
    if (!this.full.length) this.load();
    else this._apply();
  }

  _bindKeys() {
    this._keys = (e) => {
      if (!this.host || e.target?.matches?.('input,select,textarea')) return;
      if (e.key === '.') { e.preventDefault(); this.stop(); this.step(1); }
      if (e.key === ',') { e.preventDefault(); this.stop(); this.step(-1); }
      if (e.key === ' ') { e.preventDefault(); if (this.timer) this.stop(); else this.play(1); }
      /* `n` for the next bar: one letter from what it does, and beside `.` */
      if (e.key === 'n') { e.preventDefault(); this.peekAhead(1); }
    };
    document.addEventListener('keydown', this._keys);
  }

  _teardown() {
    this.stop();
    if (this._keys) document.removeEventListener('keydown', this._keys);
    this._keys = null;
  }

  unmount() {
    this._teardown();
    /* STOP THE RECORDER, do not just drop it. `mount`/`unmount` also fire on a
       plain re-render, so `_teardown` deliberately leaves `rec` alone -- but a
       real unmount takes the chart away, and a MediaRecorder left running
       against a dead canvas produces a file of one frozen frame and never
       tells you. Stopping writes what was actually captured. */
    if (this.rec) this.stopRecording();
    this.host = null;
  }

  /* ----------------------------------------------------------- transport */

  step(n) {
    if (!this.full.length) return;
    const last = this.full.length - 1;
    const next = Math.min(last, Math.max(0, this.i + n));
    if (next === this.i) { this.stop(); return; }
    this.i = next;
    this.peek = 0;                 // a new bar means a new decision to judge
    this._apply();
  }

  /**
   * Let ONE more bar past the cursor through, without moving the cursor.
   *
   * WHAT STEPPING CANNOT SHOW YOU. Step forward and the rule re-walks: the
   * channel moves, the exit level moves, the scorecard updates. So by the time
   * the next bar is on screen the thing it was going to judge has already
   * changed. Holding the cursor still and letting the next bar arrive shows
   * the stop and the moving exit exactly where they were WHEN THE DECISION WAS
   * MADE, against the bar that resolved it -- which is the moment the whole
   * panel exists to make legible.
   */
  peekAhead(n = 1) {
    if (!this.full.length) return;
    this.stop();
    this.peek = Math.max(0, Math.min(this.full.length - 1 - this.i, this.peek + n));
    this._apply({ keepPeek: true });
  }

  /**
   * Run the cursor on its own, forward or back.
   *
   * BACKWARD IS NOT AN UNDO. It re-walks the rule from the start of the series
   * to the new cursor, exactly as forward does -- `runRule` is a pure function
   * of the bars before the cursor, so stepping back to bar 900 shows what was
   * knowable at bar 900 and not a rewind of what you just watched. That is
   * what makes it useful for going over the bar a trade opened on.
   */
  play(dir = 1) {
    this.stop();
    this.dir = dir >= 0 ? 1 : -1;
    this.timer = setInterval(() => this.step(this.dir), this.speed);
    this._paintBar();
  }

  stop() {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
    this._paintBar();
  }


  /**
   * Put the cursor on a date, fetching older history when the date is not in
   * the window that is loaded.
   *
   * WHY IT CAN NEED A FETCH AT ALL. The replay loads a fixed count of recent
   * bars, so "3000 bars" is nine months on 4h and ten days on 5m -- the reach
   * of the picker is not a property of the picker, it is a property of the
   * timeframe. Refusing dates outside the window would make the control lie
   * about what the data can do; silently clamping to the oldest loaded bar
   * would be worse, because you would think you were standing somewhere you
   * are not. So it goes and gets them, and says so while it does.
   *
   * AND WHY THERE IS A CEILING. `months` mode asks MetaTrader for a date
   * RANGE, and a range is bars-per-day times days: two years of 1m is about
   * three quarters of a million bars, which is not a slow request, it is a
   * request that ends the tab. The estimate is made before asking and a date
   * beyond reach is refused with the reason rather than attempted.
   */
  async gotoDate(text) {
    const ms = ymdToMs(text);
    if (!Number.isFinite(ms)) { this._paintBar(); return; }
    this.stop();

    if (this.full.length && ms < this.full[0].t) {
      const need = (Date.now() - ms) / (TF_MS[this.tf] || 60e3) + this.floorBars() + 200;
      if (need > MAX_FETCH_BARS) {
        this.error = null;
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

  /** Bars the rule needs before it can say anything on this timeframe. */
  floorBars() {
    const rule = byKey(this.strategyKey);
    return rule.warmup(this._params(rule)) + 40;
  }

  /** Move the cursor to the first bar at or after `ms`, honouring the warmup. */
  _seekTo(ms) {
    if (!this.full.length) return;
    const want = seekBar(this.full, ms);
    const floor = Math.min(this.floorBars(), this.full.length - 1);
    this.i = Math.max(floor, Math.min(this.full.length - 1, want));
    this.peek = 0;
    this._apply();
    if (want < floor) {
      /* Said rather than silently corrected: a rule with no channel yet is not
         the rule, and a cursor that quietly sat somewhere other than the date
         you picked is the kind of thing you find out about much later. */
      this.status.textContent =
        `${ymd(this.full[this.i].t)} — the first bar with a full `
        + `${this.floorBars()}-bar warmup on ${this.tf}.`;
    }
  }

  /* --------------------------------------------------------------- data */

  /**
   * @param {object}  opts
   * @param {number}  opts.months  fetch a date RANGE this many months back
   *                               instead of the recent 3000 bars
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
      this.digits = payload.digits ?? 2;
      /* The spread floor and the point size come from the CONTRACT, so the
         estimated fill below is the one the simulator would charge rather than
         a round number. A failed spec is not fatal: the fill row is dropped and
         the geometry rows still stand. */
      try { this.spec = await api.spec(this.symbol); }
      catch { this.spec = null; }
      /* Start far enough in that the rule has warmed up and there is history
         to have lived through, but with most of the series still ahead. */
      const rule = byKey(this.strategyKey);
      /* warmup off the params this timeframe will ACTUALLY run, not the flat
         defaults -- a 3.3-day channel on 5m is 950 bars, and starting the
         replay 40 bars in would step a rule that has no channel yet. */
      const warm = rule.warmup(this._params(rule)) + 40;
      this.i = Math.min(this.full.length - 1,
                        Math.max(warm, Math.floor(this.full.length * 0.35)));
      /* A range fetch happens BECAUSE a date was asked for, so honour it here
         rather than leaving the cursor at the default 35% of a window whose
         size just changed. */
      if (seekTo != null && this.full.length) {
        this.i = Math.max(Math.min(warm, this.full.length - 1),
                          Math.min(this.full.length - 1, seekBar(this.full, seekTo)));
      }
    } catch (err) {
      this.full = [];
      this.error = String(err.message || err);
    }
    this.loading = false;
    if (this.chart) this._apply();
    this._paintBar();
  }

  /** The params this rule resolves to on the current timeframe. */
  _params(rule) {
    return rule.paramsFor ? { ...rule.defaults, ...rule.paramsFor(this.tf) }
                          : rule.defaults;
  }

  /* -------------------------------------------------------------- render */

  _apply({ keepPeek = false } = {}) {
    if (!this.chart) return;
    if (!this.full.length) { this.chart.setData({ bars: [] }); return; }
    if (!keepPeek) this.peek = 0;

    /* THE CHART HOLDS THE WHOLE SERIES; the SIGNAL is computed from the slice.
       One line apart so the separation cannot drift. */
    const slice = this.full.slice(0, this.i + 1);
    this.chart.symbol = this.symbol;
    this.chart.tf = this.tf;
    this.chart.setData({ bars: this.full, symbol: this.symbol, digits: this.digits });
    this.chart.setAsOfMark(this.i);
    this.chart.setFutureHidden(this.futureHidden);
    this.chart.setFuturePeek(this.peek);
    const rule = byKey(this.strategyKey);
    /* the tf goes in so the replay steps the SAME rule the live panel draws
       and the same one Python measured. Without it this walked rule.defaults
       -- a flat 20/10 on every timeframe. */
    const sig = runRule(slice, rule, { upto: slice.length - 1, tf: this.tf });
    this.sig = sig;

    /* Closed trades as ribbons, coloured by outcome and labelled with the R
       they actually returned -- the sequence is the thing a summary hides. */
    this.chart.setSegments(sig.trades.map((t) => ({
      t0: t.entryTime, t1: t.exitTime,
      kind: t.r > 0 ? WIN_KIND : LOSS_KIND,
      closed: true,
      label: `${t.side === LONG ? 'L' : 'S'} ${t.r >= 0 ? '+' : ''}${t.r.toFixed(1)}R`,
    })));

    /* SIGNAL and FILL are marked separately, one bar apart. The rule fires on
       a CLOSE and the order fills at the NEXT OPEN, so marking only the fill
       would imply the strategy could act on the bar it was reading. */
    const marks = [];
    for (const t of sig.trades) {
      if (Number.isFinite(t.signalI)) {
        marks.push({ i: t.signalI, price: t.signalPrice, kind: 'signal',
                     side: t.side, toI: t.entryI, toPrice: t.entryPrice, toX: 1 });
      }
      marks.push({ i: t.entryI, price: t.entryPrice, kind: 'entry', side: t.side });
      marks.push({ i: t.exitI, price: t.exitPrice, kind: 'exit',
                   side: t.side, win: t.r > 0 });
    }
    if (sig.position) {
      const p0 = sig.position;
      if (Number.isFinite(p0.signalI)) {
        marks.push({ i: p0.signalI, price: p0.signalPrice, kind: 'signal',
                     side: p0.side, toI: p0.entryI, toPrice: p0.entryPrice, toX: 1 });
      }
      marks.push({ i: p0.entryI, price: p0.entryPrice, kind: 'entry', side: p0.side });
    }
    /* A signal on THIS bar has no fill yet -- the next open does not exist.
       Marked without a joining line, because there is nothing to join it to. */
    if (sig.pending && sig.pending.side !== FLAT
        && Number.isFinite(sig.pending.signalI)) {
      marks.push({ i: sig.pending.signalI, price: sig.pending.signalPrice,
                   kind: 'signal', side: sig.pending.side });
    }
    this.chart.setMarks(marks);

    /* TP bands off the live stop. Reference only -- this strategy has no
       take-profit and capping it at 1R was measured to lose money. */
    this.bands = sig.position ? targetBands({
      side: sig.position.side, entry: sig.position.entryPrice, stop: sig.position.stop,
    }) : [];
    this.chart.setTargets(this.bands);

    /* THE EXIT LEVEL, TRACED. This is the closest thing the rule has to a take
       profit and it is the only one of the three levels that MOVES, so a static
       row in the panel was the wrong way to show it: two thirds of exits are
       taken by this line, and the thing worth seeing is it ratcheting up under
       a long until price finally closes through it. Traced from the entry bar
       to the cursor, so stepping forward shows each jump as it happens. */
    this.trail = null;
    if (sig.position) {
      const long = sig.position.side === LONG;
      const lvl = long ? sig.series.exitLo : sig.series.exitHi;
      const pts = [];
      for (let k = sig.position.entryI; k <= this.i; k++) {
        if (Number.isFinite(lvl[k])) pts.push({ i: k, price: lvl[k] });
      }
      if (pts.length > 1) {
        this.trail = { points: pts, label: 'exit', color: TRAIL_COL, width: 1.4 };
      }
    }
    this.chart.setTrail(this.trail);

    /* The open trade's entry and stop, drawn by the same renderer the live
       chart uses for real positions. `tp` is deliberately null: this strategy
       has none, and putting the moving exit level in a slot labelled TP would
       misrepresent the rule the panel exists to state correctly.

       A PENDING signal is drawn too, dashed and at the level it WOULD take.
       Without it the decision bar -- the one moment you are actually being
       asked something -- was the only bar with no levels on the chart at all. */
    const held = sig.position;
    const pend = (!held && sig.pending && sig.pending.side !== FLAT)
      ? sig.pending : null;
    this.chart.setPositions(held ? [{
      symbol: this.symbol,
      side: held.side === LONG ? 'buy' : 'sell',
      volume: 1,
      price_open: held.entryPrice,
      sl: held.stop,
      tp: null,
    }] : (pend ? [{
      symbol: this.symbol,
      side: pend.side === LONG ? 'buy' : 'sell',
      volume: 1,
      // the fill does not exist yet; the signal close is the only price known
      price_open: pend.signalPrice,
      sl: pend.stop,
      tp: null,
    }] : []));

    /* Park the window on the cursor with a little of the future showing, so a
       step forward reveals the next bar rather than scrolling an unchanged
       view. `view.right` is the property the renderer reads -- an earlier
       version set `view.iEnd`, which does not exist, so the chart stayed at the
       end of the series and every level the panel quoted was off-scale.

       The price lock has to be cleared on EVERY step: a dragged price axis pins
       the vertical range, and walking the cursor back then takes the bars
       straight out of it and draws nothing. */
    const pad = this.chart.rightPad();
    /* WHILE STEPPING, park the cursor a couple of dozen bars from the right
       edge: the space is reserved so a step forward drops the next candle into
       it rather than scrolling the whole chart sideways.

       ON REVEAL, widen it. A window that only reaches 24 bars past the cursor
       has almost nothing to reveal -- the button would flip a flag and change
       the picture by one screen-inch, which reads as broken rather than as
       "there is little to see". Half a span forward puts the cursor in the
       middle, so what the rule was standing on and what followed it are in the
       same frame. */
    const ahead = this.futureHidden
      ? Math.max(pad, 24, this.peek + 4)
      : Math.max(pad, Math.round(this.chart.view.span * 0.5));
    this.chart.view.right = Math.min(this.full.length - 1 + pad, this.i + ahead);
    this.chart.view.priceLock = null;
    this.chart.draw();
    /* BEFORE _paintBar, which reads the frame count off `rec` -- otherwise the
       button lags a step behind what has actually been captured. */
    this._frame();
    this._paintBar();
    this._paintPanel();
  }

  _buildBar() {
    this.bar.innerHTML = '';
    const btn = (label, title, fn, cls = '') => {
      const b = el('button', { class: 'rp-btn' + (cls ? ' ' + cls : ''), title }, label);
      b.addEventListener('click', fn);
      return b;
    };
    const sel = (opts, value, fn) => {
      const x = el('select', { class: 'rp-sel' }, ...opts.map((o) =>
        el('option', { value: String(o), selected: String(o) === String(value) || null },
           String(o))));
      x.addEventListener('change', () => fn(x.value));
      return x;
    };

    /* The symbol is a BUTTON, not a text field. Typing a ticker means knowing
       the broker's exact suffix -- XAUUSD.a, not XAUUSD -- and getting it wrong
       returned an empty series with no hint as to why. This opens the same
       picker the live chart uses, which searches the broker's real list. */
    this.symBtn = btn(this.symbol, 'Change symbol — searches the broker list',
      () => {
        const ok = openSymbolSearch((sym) => {
          this.symbol = sym;
          this.symBtn.textContent = sym;
          this.full = [];
          this.load();
        });
        /* No picker registered means this is running without the live app, so
           say so rather than appearing to do nothing. */
        if (!ok) this.status.textContent = 'symbol picker unavailable';
      }, 'rp-symbtn');

    /* TWO PLAY BUTTONS, one per direction, each of which is also its own stop.
       A single toggle could not say WHICH way it was running, and a separate
       stop button is a third thing to aim at for something the running button
       can do itself. Clicking the other direction turns around rather than
       stopping, which is the gesture you actually want when you have overshot
       the bar a trade opened on. */
    const runner = (dir) => () => {
      if (this.timer && this.dir === dir) this.stop();
      else this.play(dir);
    };
    this.playBtn = btn('▶', 'Play forward  (space) — press again to stop',
                       runner(1), 'rp-tp');
    this.backBtn = btn('◀', 'Play backward — press again to stop',
                       runner(-1), 'rp-tp');
    /* A DATE, not a bar number. "Start at bar 1050" is meaningless -- it moves
       every time the window is refetched -- whereas a date is the thing you
       actually remember about a move you want to look at again. It doubles as
       a readout: `_paintBar` writes the cursor's own date into it on every
       step, so the control always says where you are. */
    this.dateInput = el('input', {
      type: 'date', class: 'rp-date',
      title: 'Jump the cursor to this date. Older than the loaded window and '
        + 'the history is fetched first.',
    });
    this.dateInput.addEventListener('change', () => this.gotoDate(this.dateInput.value));
    this.futBtn = btn('Reveal',
      'Show the bars after the cursor. They are hidden by default so the '
      + 'replay generates as it goes — seeing what happened next tells you '
      + 'the answer before the rule does.',
      () => {
        this.futureHidden = !this.futureHidden;
        this._apply();
      });
    this.peekBtn = btn('Next bar ›',
      'Reveal ONE more bar after the cursor without moving it  (n). The rule '
      + 'stays frozen on the bar it decided on, so the stop and the moving '
      + 'exit stay where they were when the decision was made.',
      () => this.peekAhead(1));
    this.recBtn = btn('⏺ Rec',
      'Record the walk — video of the chart and panel, plus a JSON ledger, '
      + 'written to data/replays/. Press again to stop and save.',
      () => this.toggleRecord());
    this.pngBtn = btn('⤓ PNG',
      'Save the chart AND this panel as one image',
      () => this.snapshot());
    this.status = el('span', { class: 'rp-status' });

    this.bar.append(
      sel(STRATEGIES.map((x) => x.label), byKey(this.strategyKey).label, (v) => {
        const picked = STRATEGIES.find((x) => x.label === v);
        if (!picked) return;
        this.strategyKey = picked.key;
        /* Bars are unchanged -- only the rule is -- so this re-walks rather
           than refetching. Stepping position is kept: comparing two rules at
           the SAME bar is the reason to have a selector at all. */
        this.stop();
        this._apply();
      }),
      this.symBtn,
      /* 1m and 5m included even though both measured deeply negative on the
         long windows (gold 5m -0.1812 at percentile 20.0, USDJPY 5m -0.4274
         at 8.3). Stepping through a rule that loses is worth more than not
         being able to look at it -- the replay is where you see WHY, and the
         per-cell verdict badge already says the measurement. */
      sel(['1m', '5m', '15m', '1h', '4h', '1d'], this.tf,
          (v) => { this.tf = v; this.full = []; this.load(); }),
      /* Beside the timeframe, because the two answer one question together:
         WHICH BARS am I looking at. The timeframe picks their size and the
         date picks where they start, and a fetch is what both of them cause.
         The transport that follows is a different kind of control -- it moves
         within the window these two chose. */
      this.dateInput,
      /* Mirror-symmetric about the middle: step back, play back, play
         forward, step forward, run to the end. The single-bar steps sit
         OUTSIDE the play pair so the two transports never sit adjacent --
         they are different gestures and a misclick between them costs you
         the bar you were standing on. */
      btn('◀◀', 'One bar back  (,)', () => { this.stop(); this.step(-1); }, 'rp-tp'),
      this.backBtn,
      this.playBtn,
      btn('▶▶', 'One bar forward  (.)', () => { this.stop(); this.step(1); }, 'rp-tp'),
      btn('▶▶|', 'Run to the end', () => { this.stop(); this.step(this.full.length); }, 'rp-tp'),
      sel(SPEEDS.map((x) => x.label), SPEEDS[0].label, (v) => {
        this.speed = (SPEEDS.find((x) => x.label === v) || SPEEDS[0]).ms;
        if (this.timer) this.play();
      }),
      el('span', { class: 'rp-sep' }),
      /* After the transport and its separator, before the bar counter. The
         counter is a readout that grows and shrinks as the cursor moves; a
         button placed after it would shift sideways while you step, which is
         the one thing a button you aim at should not do. */
      this.futBtn,
      this.peekBtn,
      this.recBtn,
      this.pngBtn,
      this.status);
    this._paintBar();
  }

  _paintBar() {
    if (!this.status) return;
    /* The RUNNING button shows the stop glyph, so the transport says what it
       is doing rather than only what it can do. */
    if (this.playBtn) {
      const fwd = !!this.timer && this.dir > 0;
      const back = !!this.timer && this.dir < 0;
      this.playBtn.textContent = fwd ? '■' : '▶';
      this.playBtn.classList.toggle('on', fwd);
      this.backBtn.textContent = back ? '■' : '◀';
      this.backBtn.classList.toggle('on', back);
    }
    if (this.futBtn) {
      this.futBtn.textContent = this.futureHidden ? 'Reveal' : 'Hide future';
      this.futBtn.classList.toggle('on', !this.futureHidden);
    }
    if (this.peekBtn) {
      /* The count of what has been let through, on the button that let it
         through. Peeking four bars ahead and forgetting is how you conclude a
         rule was right about a move it never saw. */
      this.peekBtn.textContent = this.peek ? `Next bar › +${this.peek}`
                                           : 'Next bar ›';
      this.peekBtn.classList.toggle('on', !!this.peek);
      this.peekBtn.disabled = !this.futureHidden;
    }
    /* The frame count lives ON the Rec button. A recorder with no readout is
       one you discover is still running when you find the file, and the count
       is also the only confirmation that stepping is being captured at all. */
    if (this.recBtn) {
      this.recBtn.classList.toggle('rec', !!this.rec);
      this.recBtn.textContent = this.rec
        ? `⏺ ${recClock(this.rec.startedMs)}` : '⏺ Rec';
      if (this.rec) {
        this.recBtn.title = `Recording — ${this.rec.frames.length} bar`
          + `${this.rec.frames.length === 1 ? '' : 's'} captured. Press to stop and save.`;
      }
    }
    if (this.dateInput && this.full.length) {
      const b = this.full[this.i];
      if (b) this.dateInput.value = ymd(b.t);
      this.dateInput.min = ymd(this.full[0].t);
      this.dateInput.max = ymd(this.full[this.full.length - 1].t);
    }
    if (this.loading) {
      this.status.textContent = this.months
        ? `loading ${this.months} months of ${this.tf} bars…` : 'loading bars…';
      return;
    }
    if (this.error) { this.status.textContent = 'bridge: ' + this.error; return; }
    if (!this.full.length) { this.status.textContent = 'no bars'; return; }
    const b = this.full[this.i];
    /* THE DISPLAY ZONE, like the axis, the legend and the date picker sitting
       two controls to the left -- not UTC. The bar the axis calls 28 May opens
       at 21:00Z on the 27th, so a UTC status line beside a broker-time picker
       disagreed with it by three hours and a date, which reads as a bug in the
       seek rather than as two clocks. Exports stay UTC on purpose: an image
       outlives the session that made it. */
    const when = b ? `${ymd(b.t)} ${hhmm(b.t)}` : '';
    this.status.textContent = `bar ${this.i + 1} of ${this.full.length} · ${when}`
      + ' · , and . step, space plays';
  }

  /**
   * One PNG of the chart AND the panel beside it.
   *
   * The same shape as the Elliott replay's: the live chart's own snapshot is
   * canvas-only because the whole picture is on the canvas, but here half the
   * picture is HTML. An image of a trade sequence without the levels and the
   * scorecard that produced it is a picture of some candles.
   *
   * The panel is REDRAWN rather than screenshotted -- there is no DOM-to-image
   * in this project and adding one to satisfy a caption would be a dependency
   * for a caption. What the image says is therefore what the code believes.
   *
   * GROSS IS CARRIED INTO THE IMAGE. The replay charges no spread, slippage or
   * swap, so its scorecard reads about 19% higher than the backtest on gold 4h
   * (+0.2147 against +0.1799). On screen that caveat sits beside the numbers;
   * an exported image outlives its context and gets shared without it, so the
   * word GROSS goes in the picture rather than being left to the sender.
   */
  snapshot() {
    if (!this.chart || !this.full.length) { toast('Nothing to capture yet'); return; }
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
      this._drawSnapPanel(ctx, img.width, cv.height, panelW, scale);
      const stamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
      const name = `strategy_${this.strategyKey}_${this.symbol}_${this.tf}`
        + `_bar${this.i}_${stamp}.png`;
      const a = el('a', { href: cv.toDataURL('image/png'), download: name });
      document.body.append(a);
      a.click();
      a.remove();
      toast(`Saved ${name}`);
    };
    img.onerror = () => toast('Could not compose the snapshot');
    img.src = url;
  }

  /* ------------------------------------------------------------- record */

  /**
   * Record the whole walk: a video of chart AND panel, plus a JSON sidecar.
   *
   * WHY BOTH HALVES ARE IN THE FRAME. This panel exists because a summary --
   * 207 trades, 36% win rate, PF 1.47 -- does not tell you what trading the
   * rule feels like. A video of the candles alone reproduces exactly that
   * failure: it shows price moving and not the nine-loss run, the four days in
   * a position, or the exit level ratcheting up underneath. The composite is
   * chart, panel and header in one frame, so what is being claimed is always
   * beside what price did.
   *
   * MP4 IF THE BROWSER WILL, WEBM OTHERWISE. H.264 in MediaRecorder is recent
   * and not universal; asking for a container the browser cannot make throws
   * at start(), so the type is chosen with isTypeSupported and the extension
   * follows what was actually negotiated. A file named .mp4 holding a WebM
   * stream is worse than a .webm.
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
    const dpr = window.devicePixelRatio || 1;
    const src0 = this.chart.canvas;
    const cv = document.createElement('canvas');
    /* EVEN DIMENSIONS. H.264 chroma is subsampled 2x2, so an odd width or
       height makes the encoder pad or refuse -- and it refuses by silently
       failing to start, not by throwing. */
    cv.width = (Math.round(src0.width / dpr) + panelW) & ~1;
    cv.height = (Math.round(src0.height / dpr) + headH) & ~1;
    const ctx = cv.getContext('2d');

    const paint = () => {
      if (!this.rec) return;
      /* READ THE CANVAS EACH FRAME rather than closing over it. The Backtest
         panel rebuilds its DOM on every render and mount() then constructs a
         NEW Chart, so a captured reference goes stale and the video freezes on
         the last frame before the remount -- while still recording, which is
         the worst of both. */
      const src = this.chart?.canvas;
      ctx.fillStyle = '#02101f';
      ctx.fillRect(0, 0, cv.width, cv.height);
      this._drawHeader(ctx, cv.width, headH);
      if (src) ctx.drawImage(src, 0, headH, cv.width - panelW, cv.height - headH);
      ctx.save();
      ctx.translate(0, headH);
      this._drawSnapPanel(ctx, cv.width - panelW, cv.height - headH, panelW,
                          scale, true);
      ctx.restore();
      this.rec.raf = requestAnimationFrame(paint);
    };

    const stream = cv.captureStream(FPS);
    if (sound) stream.addTrack(sound.track);
    const mr = new MediaRecorder(stream, { mimeType: mime, videoBitsPerSecond: BITRATE });
    const chunks = [];
    mr.ondataavailable = (e) => { if (e.data && e.data.size) chunks.push(e.data); };
    mr.onstop = () => this._writeVideo(new Blob(chunks, { type: mime }), mime);

    this.rec = { started: new Date().toISOString(), startedMs: Date.now(),
                 mr, cv, mime, sound, frames: [], raf: 0, tick: 0,
                 fromI: this.i, strategyKey: this.strategyKey,
                 symbol: this.symbol, tf: this.tf,
                 audio: sound ? sound.name : null };
    /* The clock has to drive itself. _paintBar only runs when the cursor
       moves, and a recording left running while you read the panel would show
       0:00 for as long as you sat there. */
    this.rec.tick = setInterval(() => this._paintBar(), 1000);
    this._frame();                       // the sidecar starts on this bar
    paint();
    mr.start(1000);                      // a chunk a second, so a crash loses one
    this._paintBar();
    toast(`Recording ${mime.split(';')[0]} \u2014 press Rec again to stop and save`);
    return undefined;
  }

  stopRecording() {
    const rec = this.rec;
    if (!rec) return;
    cancelAnimationFrame(rec.raf);
    clearInterval(rec.tick);
    /* The music stops with the picture. It is a separate object from the
       recorder and nothing else would ever end it -- a paused-and-forgotten
       <audio> keeps playing until it is collected, so the room would carry on
       hearing a recording that finished. */
    rec.sound?.stop();
    try { rec.mr.stop(); } catch { /* already stopped */ }
    /* rec is kept until onstop fires: the last chunks arrive after this
       returns, and clearing it here would drop the tail. */
    this._paintBar();
  }

  async _writeVideo(blob, mime) {
    const rec = this.rec;
    this.rec = null;
    this._paintBar();
    const ext = mime.startsWith('video/mp4') ? 'mp4' : 'webm';
    const stamp = (rec?.started || new Date().toISOString())
      .replace(/[:.]/g, '-').slice(0, 19);
    const base = `strategy_${rec?.strategyKey || this.strategyKey}`
      + `_${rec?.symbol || this.symbol}_${rec?.tf || this.tf}_${stamp}`;
    try {
      const res = await fetch(`/record?name=${encodeURIComponent(`${base}.${ext}`)}`, {
        method: 'POST', headers: { 'Content-Type': mime }, body: blob,
      });
      const out = await res.json();
      if (!res.ok || out.error) throw new Error(out.error || `HTTP ${res.status}`);
      toast(`Saved ${out.saved} (${(out.bytes / 1048576).toFixed(1)} MB)`, 5000);
      /* The sidecar goes with it under the same basename. It is a few tens of
         KB against a video's several MB, and it is the half that can be
         QUERIED -- a video cannot tell you what R the fourth trade returned. */
      if (rec) await this._writeSidecar(rec, base);
    } catch (err) {
      toast(`Could not save the video: ${err.message}`, 6000);
    }
  }

  /**
   * One frame per cursor position: what the rule claimed standing on that bar.
   *
   * EVERY NUMBER IS AS OF THE CURSOR, never the run's final statistics shown
   * early -- sig.trades holds only what had closed by then, which is the whole
   * reason this panel is worth stepping. Writing the final tally into every
   * frame would turn a record of what you knew into a record of what you found
   * out later.
   */
  _frame() {
    if (!this.rec || !this.sig) return;
    const sig = this.sig;
    const b = this.full[this.i];
    if (!b) return;
    const ins = instruction(sig);
    const pos = sig.position;
    const t = tally(sig.trades);
    this.rec.frames.push({
      i: this.i,
      t: b.t,
      close: b.c,
      action: ins.action,
      side: ins.side || null,
      stop: ins.stop != null ? ins.stop : (pos ? pos.stop : null),
      exitLevel: Number.isFinite(sig.exitLevel) ? sig.exitLevel : null,
      position: pos ? {
        side: pos.side,
        entryI: pos.entryI,
        entryPrice: pos.entryPrice,
        stop: pos.stop,
        risk: pos.risk,
        barsHeld: this.i - pos.entryI,
        openR: (b.c - pos.entryPrice) * pos.side / pos.risk,
      } : null,
      /* the scorecard AS OF THIS BAR, which is what the panel was showing */
      closed: t.n ? { n: t.n, winPct: t.winPct, avgR: t.avgR, netR: t.netR,
                      pf: Number.isFinite(t.pf) ? t.pf : null,
                      maxDDr: t.maxDDr, worstStreak: t.worstStreak } : null,
    });
  }

  /**
   * Write the sidecar to data/replays/ through the dev server.
   *
   * A browser download lands wherever the browser puts downloads, which is not
   * the project -- and the point of a recorded replay is that it sits beside
   * the runs it will be compared with.
   *
   * WHAT MAKES IT SELF-CONTAINED. The resolved parameters go in, not just the
   * strategy's name. donchian on 15m is a 317-bar channel and on 4h a 20-bar
   * one -- the same key, two different rules -- so a file saying only
   * "donchian" cannot be read back six months later. The cell's status and the
   * GROSS caveat travel with it for the same reason a snapshot carries them:
   * an artefact outlives its context and gets shared without it.
   */
  async _writeSidecar(rec, base) {
    const rule = byKey(rec.strategyKey) || byKey(this.strategyKey);
    const sig = this.sig;
    const t = sig ? tally(sig.trades) : { n: 0 };
    const covered = coversCell(rule, rec.symbol, rec.tf);
    const payload = {
      kind: 'strategy_replay',
      symbol: rec.symbol,
      tf: rec.tf,
      strategy: { key: rule.key, label: rule.label, summary: rule.summary,
                  status: rule.status, cells: rule.cells || [],
                  coversThisCell: covered },
      /* the rule AS RUN, which on a horizon-matched timeframe is not its
         defaults -- see js/chart/donchian.js */
      params: this._params(rule),
      started: rec.started,
      saved: new Date().toISOString(),
      audio: rec.audio || null,
      cursor: { from: rec.fromI, to: this.i, bars: this.full.length },
      firstBarT: this.full[0]?.t ?? null,
      lastBarT: this.full[this.full.length - 1]?.t ?? null,
      note: 'GROSS: no spread, slippage or swap is charged in this replay. On '
        + 'XAUUSD 4h that reads about 19% above the backtest engine (+0.2147 '
        + 'vs +0.1799 R per trade). A rule is a pure function of the bars '
        + 'before the cursor, so `frames` records the session that was '
        + 'watched, not evidence about the rule -- `trades` is the result.',
      /* the ledger as of the cursor, which is the artefact worth keeping */
      score: t.n ? { n: t.n, winPct: t.winPct, avgR: t.avgR, netR: t.netR,
                     pf: Number.isFinite(t.pf) ? t.pf : null,
                     maxDDr: t.maxDDr, worstStreak: t.worstStreak,
                     byReason: t.byReason } : null,
      trades: sig ? sig.trades.map((x) => ({
        side: x.side, tag: x.tag, reason: x.reason,
        signalI: x.signalI, signalPrice: x.signalPrice,
        entryI: x.entryI, entryTime: x.entryTime, entryPrice: x.entryPrice,
        stop: x.stop, risk: x.risk,
        exitI: x.exitI, exitTime: x.exitTime, exitPrice: x.exitPrice,
        r: x.r,
      })) : [],
      frames: rec.frames,
    };
    try {
      const res = await fetch('/record', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: `${base}.json`, payload }),
      });
      const out = await res.json();
      if (!res.ok || out.error) throw new Error(out.error || `HTTP ${res.status}`);
    } catch { /* the video is the artefact; a missing sidecar is not a toast */ }
  }

  /**
   * The band above the chart in a recording.
   *
   * It sits ABOVE the plot rather than over it because the top-left of the
   * plot is where the chart draws its own legend, and a logo there covers the
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

    /* The RULE is named here, not just the symbol. A viewer scrubbing the
       video is watching entries appear and needs to know which rule produced
       them -- and with three rules in the picker, a file that does not say is
       a file that gets attributed to the wrong one. */
    ctx.font = '11px "Roboto Mono", monospace';
    ctx.fillStyle = '#8fa6c0';
    const rule = byKey(this.strategyKey);
    ctx.fillText(`${this.symbol}  ${this.tf}  \u00b7  ${rule.label}`, tx + 96, ty);

    const b = this.full[this.i];
    const right = `bar ${this.i + 1}/${this.full.length}`
      + (b ? `  ${new Date(b.t).toISOString().slice(0, 16).replace('T', ' ')}Z` : '');
    ctx.textAlign = 'right';
    ctx.fillStyle = '#5d7794';
    ctx.fillText(right, w - 12, ty);
    ctx.restore();
  }

  /**
   * The panel, redrawn on a canvas. Same content as the live one.
   *
   * TWO PALETTES, ONE LAYOUT, for the reason js/ui/replay.js gives: the PNG is
   * composed for a white page and the video is a recording of the app, so it
   * has to look like the app. Two layouts would make the panel in the video a
   * different object from the panel on screen, and then a discrepancy between
   * them is something a viewer has to reason about rather than read.
   */
  _drawSnapPanel(ctx, x0, h, w, scale, dark = false) {
    const sig = this.sig;
    const d = this.digits;
    const C = dark
      ? { bg: '#02101f', rule: '#0a2f57', head: '#d9d9d6', sub: '#5d7794',
          body: '#8fa6c0', ok: '#93C90F', no: '#FF9E1B', warn: '#FF9E1B' }
      : { bg: '#F4F6F8', rule: '#D6DBE0', head: '#111', sub: '#666',
          body: '#555', ok: '#2f7d18', no: '#b0402a', warn: '#b0402a' };
    ctx.fillStyle = C.bg;
    ctx.fillRect(x0, 0, w, h);
    const pad = 14 * scale;
    let y = 24 * scale;

    const line = (text, { size = 10.5, colour = C.body, gap = 5, bold = false } = {}) => {
      ctx.font = `${bold ? '700 ' : ''}${size * scale}px Roboto Condensed, Arial, sans-serif`;
      ctx.fillStyle = colour;
      const max = w - pad * 2;
      const words = String(text).split(' ');
      let ln = '';
      for (const word of words) {
        const test = ln ? ln + ' ' + word : word;
        if (ctx.measureText(test).width > max && ln) {
          ctx.fillText(ln, x0 + pad, y);
          y += (size + 3) * scale;
          ln = word;
        } else { ln = test; }
      }
      if (ln) { ctx.fillText(ln, x0 + pad, y); y += (size + gap) * scale; }
    };
    const kv = (k, v) => {
      ctx.font = `${10 * scale}px Roboto Condensed, Arial, sans-serif`;
      ctx.fillStyle = C.sub;
      ctx.fillText(k, x0 + pad, y);
      ctx.font = `${10 * scale}px Roboto Mono, monospace`;
      ctx.fillStyle = C.head;
      const tw = ctx.measureText(String(v)).width;
      ctx.fillText(String(v), x0 + w - pad - tw, y);
      y += 14 * scale;
    };
    const rule = () => {
      ctx.strokeStyle = C.rule;
      ctx.lineWidth = scale;
      ctx.beginPath();
      ctx.moveTo(x0 + pad, y - 8 * scale);
      ctx.lineTo(x0 + w - pad, y - 8 * scale);
      ctx.stroke();
      y += 4 * scale;
    };

    const r = byKey(this.strategyKey);
    line(`${r.label}`, { size: 14, colour: C.head, bold: true, gap: 3 });
    line(`${this.symbol}  ${this.tf}  —  bar ${this.i} of ${this.full.length - 1}`,
         { size: 10, colour: C.sub, gap: 10 });

    if (sig) {
      const ins = instruction(sig);
      const ACT = { enter: 'ENTER AT NEXT OPEN', exit: 'CLOSE AT NEXT OPEN',
                    hold: 'IN POSITION', wait: 'NO SIGNAL' };
      line(ACT[ins.action] || 'NO SIGNAL',
           { size: 12, bold: true, gap: 8,
             colour: ins.action === 'wait' ? C.sub : C.head });
      if (ins.side) kv('side', ins.side);
      if (sig.position) kv('entry', px(sig.position.entryPrice, d));
      const stopNow = (ins.stop != null) ? ins.stop
        : (sig.position ? sig.position.stop : null);
      if (stopNow != null) kv('stop loss', px(stopNow, d));
      if (Number.isFinite(sig.exitLevel)) kv('exit level', px(sig.exitLevel, d));
      if (sig.position) {
        kv('bars held', String(this.i - sig.position.entryI));
        const openR = (this.full[this.i].c - sig.position.entryPrice)
          * sig.position.side / sig.position.risk;
        kv('open R', (openR >= 0 ? '+' : '') + openR.toFixed(2));
      }
      rule();

      const t = tally(sig.trades);
      line('AS OF THIS BAR — GROSS', { size: 10, colour: C.sub, gap: 6, bold: true });
      if (t && t.n) {
        kv('closed trades', String(t.n));
        kv('win rate', t.winPct.toFixed(1) + '%');
        kv('avg R', (t.avgR >= 0 ? '+' : '') + t.avgR.toFixed(4));
        kv('profit factor', Number.isFinite(t.pf) ? t.pf.toFixed(3) : '\u221e');
        kv('worst drawdown', t.maxDDr.toFixed(1) + 'R');
      } else {
        line('no closed trades yet', { size: 10, colour: C.sub });
      }
      rule();
      line('No spread, slippage or swap is charged in this replay. On XAUUSD 4h '
           + 'these read about 19% higher than the backtest engine '
           + '(+0.2147 vs +0.1799 R per trade).',
           { size: 9, colour: C.warn, gap: 10 });
    }

    rule();
    const covered = coversCell(r, this.symbol, this.tf);
    line(STATUS_TEXT[r.status] || r.status,
         { size: 10, bold: true, gap: 5,
           colour: r.status === 'validated' && covered ? C.ok : C.no });
    if (!covered) {
      line(`Measured on ${r.cells.join(', ')} — NOT on ${this.symbol} `
           + `${this.tf}. Nothing here has been validated on this cell.`,
           { size: 9, colour: C.no, gap: 8 });
    }
    line(r.summary, { size: 9, colour: C.body, gap: 8 });

    ctx.font = `${8.5 * scale}px Roboto Condensed, Arial, sans-serif`;
    ctx.fillStyle = C.sub;
    ctx.fillText('DiaNurFx — strategy replay', x0 + pad, h - 12 * scale);
  }

  _paintPanel() {
    const p = this.panel;
    if (!p) return;
    p.innerHTML = '';
    const sig = this.sig;
    if (!sig) return;
    const d = this.digits;
    const ins = instruction(sig);

    /* WHAT TO DO NEXT. Phrased as an action at the next open, because that is
       when it can be acted on -- the entry price does not exist yet. */
    const ACT = {
      enter: ['sr-act-enter', 'ENTER AT NEXT OPEN'],
      exit: ['sr-act-exit', 'CLOSE AT NEXT OPEN'],
      hold: ['sr-act-hold', 'IN POSITION'],
      wait: ['sr-act-wait', 'NO SIGNAL'],
    };
    const [cls, head] = ACT[ins.action] || ACT.wait;
    const rows = [];
    /* The side gets the direction colour. The badge above says what to DO
       ("IN POSITION"), which is a state and not a direction -- it was green for
       a short as readily as for a long, so nothing on the panel answered
       "which way" at a glance. */
    if (ins.side) {
      const dir = (ins.side === 'BUY' || ins.side === 'LONG') ? 'sr-up'
        : (ins.side === 'SELL' || ins.side === 'SHORT') ? 'sr-down' : '';
      rows.push(['side', el('span', { class: dir, text: ins.side })]);
    }

    /* THE ESTIMATED FILL, on an entry bar only. The rule fires on a close and
       fills at the NEXT open, so the entry price does not exist yet: this is
       what the simulator WOULD charge -- the close plus the contract's spread
       and 0.02 ATR of slippage, on the side that pays each. Against ticks that
       model came out 100% conservative on gold, over-charging ~+0.0128 R, so
       the real fill has been slightly better than this rather than worse. */
    const atrNow = sig.series && sig.series.atr ? sig.series.atr[this.i] : NaN;
    if (ins.action === 'enter' && Number.isFinite(atrNow) && this.full[this.i]) {
      const close = this.full[this.i].c;
      const long = ins.side === 'BUY';
      const point = this.spec ? (this.spec.point || 0) : 0;
      const spread = (this.spec ? (this.spec.spread_points_now || 0) : 0) * point;
      const est = close + (long ? spread + SLIP_ATR * atrNow : -SLIP_ATR * atrNow);
      rows.push(['est. fill', '~' + px(est, d)]);
    }

    if (sig.position) rows.push(['entry', px(sig.position.entryPrice, d)]);

    /* THE STOP STAYS UP UNTIL THE FILL, including on the bar the exit fires.
       `instruction()` reports no stop for an 'exit' action, which is right about
       the DECISION and wrong about the RISK: the close order fills at the next
       open, and until it does the position is still open and the stop can still
       take it. A gap through it overnight is exactly the case where the stop
       matters most, so the row disappearing on that bar hid the level at the
       one moment it was the only thing standing under the trade. */
    const stopNow = (ins.stop !== null && ins.stop !== undefined)
      ? ins.stop
      : (sig.position ? sig.position.stop : null);
    if (stopNow !== null && stopNow !== undefined) {
      rows.push(['stop loss', px(stopNow, d)]);
    }

    /* HOW WIDE THE RISK IS, which is what decides whether a trade is even
       placeable. Quoted in price, in points and in ATR: 7641 points means
       nothing on its own, and the same 2 ATR is $76 on gold today and was $23
       eighteen months ago. */
    const anchor = sig.position ? sig.position.entryPrice
                                : (this.full[this.i] ? this.full[this.i].c : NaN);
    if (stopNow != null && Number.isFinite(anchor)) {
      const dist = Math.abs(anchor - stopNow);
      const pt = this.spec && this.spec.point ? this.spec.point : 0;
      const bits = [px(dist, d)];
      if (pt > 0) bits.push(`${Math.round(dist / pt)} pts`);
      if (Number.isFinite(atrNow) && atrNow > 0) {
        bits.push(`${(dist / atrNow).toFixed(2)} ATR`);
      }
      rows.push(['stop distance', bits.join('  ')]);
    }

    /* WHICH EXIT IS LIVE. The trade has two, and only one of them can be
       reached first, so showing both as equals leaves the reader to work out
       which is actually holding the position -- which is the whole question.

       For a long, price falls to whichever level is HIGHER: if the channel sits
       below the stop, the stop is hit before any close can print below the
       channel, and the channel is inert until it ratchets above it. Early in a
       trend trade that is the normal state, and it is why the exit level can
       look absurdly far away for the first few bars. */
    if (sig.exitLevel !== null && Number.isFinite(sig.exitLevel)) {
      const long = sig.position && sig.position.side === LONG;
      const stop = stopNow;
      let liveStop = null;
      let liveExit = null;
      if (ins.action === 'exit') {
        /* Already fired. Neither level is "waiting" any more: the exit is
           spent and the stop is the only thing still able to act, for the one
           bar until the close order fills. */
        liveExit = 'triggered';
        liveStop = 'until fill';
      } else if (Number.isFinite(stop) && sig.position) {
        const exitBinds = long ? sig.exitLevel > stop : sig.exitLevel < stop;
        liveExit = exitBinds ? 'LIVE' : 'not yet';
        liveStop = exitBinds ? 'behind' : 'LIVE';
      }
      // rewrite the stop row now that we know which one binds
      const si = rows.findIndex((r) => r[0] === 'stop loss');
      if (si >= 0 && liveStop) rows[si] = ['stop loss', px(stop, d), liveStop];
      rows.push(['exit level', px(sig.exitLevel, d), liveExit]);
    }
    if (sig.position) {
      const bars = this.i - sig.position.entryI;
      rows.push(['bars held', String(bars)]);
      const openR = (this.full[this.i].c - sig.position.entryPrice)
        * sig.position.side / sig.position.risk;
      rows.push(['open R', (openR >= 0 ? '+' : '') + openR.toFixed(2)]);
    }

    p.append(
      el('div', { class: 'sr-act ' + cls }, head),
      el('div', { class: 'sr-note', text: ins.note || '' }),
      el('table', { class: 'sr-kv' }, ...rows.map(([k, v, note]) =>
        el('tr', {}, el('td', { text: k }),
           el('td', { class: 'mono' }, v,
              note ? el('span', {
                class: 'sr-live' + (note === 'LIVE' ? ' sr-live-on' : ''),
                text: note,
              }) : null)))));

    if (this.bands && this.bands.length) {
      p.append(el('div', { class: 'sr-h' }, 'target bands (reference)'));
      p.append(el('table', { class: 'sr-kv' }, ...this.bands.map((b) =>
        el('tr', {},
          el('td', { text: `${b.key}  ${b.r}R` }),
          el('td', { class: 'mono', text: px(b.price, d) })))));
    }

    if (sig.exitLevel !== null && Number.isFinite(sig.exitLevel)) {
      p.append(el('div', { class: 'sr-warn' },
        'The exit level is NOT a take-profit — it is the 10-bar channel and '
        + 'it moves every bar. Capping this strategy at 1R was measured to turn '
        + '+43.7 net R into −2.1.'));
    }

    /* NO LOT SIZE HERE, and the omission is deliberate rather than unfinished.
       Sizing needs live equity and an FX rate, and the two available rates do
       not agree: MetaTrader's tick_value carries the broker's live rate while
       sim/fx.py builds AUDJPY from bar closes. On USDJPY those differ by 0.095%
       -- tiny, but enough to flip a whole 0.01 lot step in 30 of 420 tested
       cases, and the browser's answer came out LARGER every time. Both rates
       are defensible; a size that silently disagrees with the one the backtest
       used is not. So the number comes from one place. */
    if (ins.action === 'enter' || sig.position) {
      p.append(el('div', { class: 'sr-note' },
        'Lot size is not shown here: it needs live equity and an FX rate, and '
        + 'the rate in the browser differs from the one the backtest used by '
        + 'enough to move a lot step. Run tools/signal_now.py for the size '
        + 'that matches.'));
    }

    /* THE SCORECARD, from trades closed at or before the cursor only. */
    const t = tally(sig.trades);
    p.append(el('div', { class: 'sr-h' }, 'as of this bar — GROSS'));
    /* SAID OUT LOUD, because the number is otherwise indistinguishable from a
       gate result. js/chart/rules.js fills at open[i] exactly: no spread, no
       slippage, no swap. On gold 4h 2018-2026 that reads +0.2147 R against the
       engine's +0.1799 -- about 19% high -- because the engine paid 430 of
       spread and 579 of slippage over the same window and this does not. */
    /* THE WINDOW, SAID AS PLAINLY AS THE COSTS.
       Cost drag is ~19%, and the scorecard routinely sits further from the
       validated figures than that -- because it is also a DIFFERENT WINDOW.
       The replay loads the last 3000 bars (about 1.4 years at 4h), while the
       verdict badge above quotes eras of 5 and 6 years. Reading +0.46 R here
       against a stated +0.19 and concluding the rule has improved is the
       mistake this line exists to prevent: it is a short, recent, gross sample,
       and the trade count says how short. */
    const first = this.full[0], atCursor = this.full[this.i];
    if (first && atCursor) {
      const day = (b) => new Date(b.t).toISOString().slice(0, 10);
      p.append(el('div', { class: 'sr-note' },
        `window ${day(first)} → ${day(atCursor)} · ${t.n} closed trade`
        + `${t.n === 1 ? '' : 's'} — a shorter, more recent sample than the `
        + 'eras quoted above, so expect it to differ by more than the cost drag.'));
    }
    p.append(tip(el('div', { class: 'sr-warn' },
      'These are GROSS. The replay charges no spread, slippage or swap — '
      + 'on gold 4h it reads about 19% higher than the backtest.'),
    'Why the replay is optimistic',
    'The chart-side rule exists to show you the SEQUENCE of trades, and it '
    + 'deliberately does not carry a second copy of the cost model -- this '
    + 'project has been bitten before by one quantity having two '
    + 'implementations. The net figures come from runs/ via the verdict badge '
    + 'above. Measured drag on XAUUSD 4h 2018-2026: engine +0.1799 R per trade, '
    + 'replay +0.2147.'));
    if (!t.n) {
      p.append(el('div', { class: 'sr-note', text: 'no closed trades yet' }));
    } else {
      const pf = Number.isFinite(t.pf) ? t.pf.toFixed(3) : '∞';
      p.append(el('table', { class: 'sr-kv' },
        ...[['closed trades', String(t.n)],
            ['win rate', t.winPct.toFixed(1) + '%'],
            ['avg R', (t.avgR >= 0 ? '+' : '') + t.avgR.toFixed(4)],
            ['net R', (t.netR >= 0 ? '+' : '') + t.netR.toFixed(1)],
            ['profit factor', pf],
            ['worst drawdown', t.maxDDr.toFixed(1) + 'R'],
            ['longest losing run', String(t.worstStreak)]].map(([k, v]) =>
          el('tr', {}, el('td', { text: k }), el('td', { class: 'mono', text: v })))));
      const by = Object.entries(t.byReason)
        .sort((a, b) => b[1] - a[1]).map(([k, v]) => `${k} ${v}`).join(' · ');
      p.append(el('div', { class: 'sr-note', text: 'exits: ' + by }));
    }

    /* WHAT THIS RULE IS, and whether the numbers behind it apply HERE. A
       dropdown that lists a failed strategy beside a validated one as equals
       would erase the only finding this project has, so the status and the cell
       match are stated every time, not buried in a tooltip. */
    const rule = byKey(this.strategyKey);
    const covered = coversCell(rule, this.symbol, this.tf);
    p.append(el('div', { class: 'sr-h' }, 'the rule'));
    p.append(el('div', { class: 'sr-badge sr-st-' + rule.status },
      STATUS_TEXT[rule.status] || rule.status));
    if (!covered) {
      p.append(el('div', { class: 'sr-warn' },
        `That record was measured on ${rule.cells.join(', ')} — NOT on `
        + `${this.symbol} ${this.tf}. The same rule on a different cell is a `
        + 'different hypothesis, and this project has watched that distinction '
        + 'collapse. Nothing below has been validated here.'));
    }
    p.append(el('div', { class: 'sr-note', text: rule.summary }));
    const rec = rule.record || {};
    const keys = Object.keys(rec);
    if (keys.length) {
      p.append(el('table', { class: 'sr-kv' }, ...keys.map((k) => {
        const r = rec[k];
        const bits = [];
        if (r.trades != null) bits.push(`${r.trades} trades`);
        if (r.avgR != null) bits.push(`avgR ${r.avgR >= 0 ? '+' : ''}${r.avgR}`);
        if (r.pf != null) bits.push(`PF ${r.pf}`);
        return el('tr', {}, el('td', { text: k }),
                  el('td', { class: 'mono', text: bits.join(' · ') }));
      })));
    }
    p.append(el('div', { class: 'sr-note', text: rule.notes || '' }));
  }
}
