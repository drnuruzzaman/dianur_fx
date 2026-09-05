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
 * THERE IS NO TAKE-PROFIT, AND THERE WAS ONE FOR A WHILE. It was asked for,
 * built five ways -- structural and fitted, whole position and half -- wired
 * into this panel and then into the live chart, and then withdrawn from both.
 * The reason it went is in logs/tp_struct_eval.txt: across twelve cells out of
 * sample, no target beat the trailing exit on net R, and the full structural
 * cap was the worst of them. The walker can still execute a target and the
 * study can still be re-run; no surface in the app trades one.
 *
 * WHAT REMAINS OPTIONAL is the STOP WIDTH, off by default, fitted per cell and
 * per side from the heat a favourable path takes. It is a different kind of
 * change from a target -- it moves where the trade starts rather than capping
 * where it ends -- and it has its own toggle and its own warning.
 *
 * THE FIT IS CAUSAL, WHICH IS THE ONLY REASON IT IS ALLOWED HERE AT ALL. It is
 * measured from `full.slice(0, cursor + 1)`, the same slice the signal is, and
 * re-measured on every step. A fit taken once over the loaded window would put
 * a number from the future onto a chart whose whole purpose is not to have one
 * -- and it would be invisible, because a stop fitted on bars you have not
 * reached looks exactly like one fitted on bars you have.
 */

import { api } from '../api.js';
import { Chart } from '../chart/engine.js';
import { AUTO_DEFAULTS, BAR_COUNT, TF, TF_MS, resolveAuto, el, hhmm, px, seekBar, ymd, ymdToMs } from '../util.js';
import { toast } from './menu.js';
import { tip } from './tips.js';
import { openAudio, pickMime } from './recaudio.js';
import { openSymbolSearch } from './search.js';
import { FLAT, LONG, instruction, runRule, tally } from '../chart/rules.js';
import { latestDimensions } from '../chart/regime.js';
import { STATUS_TEXT, STRATEGIES, byKey, coversCell } from '../chart/strategies.js';
import {
  STOP_ATR, ruleVerdictNote, setStopEnabled, stopEnabled, stopMultiples,
  stopOption,
} from '../chart/stopmode.js';
import { displayLevels } from '../chart/levels.js';
import { trailOption } from '../chart/trailmode.js';
import { structuralTrail } from '../chart/exittrail.js';
import { detect as detectMS } from '../chart/marketstructure.js';
import { swingPoints } from '../chart/structure.js';
import { atrSeries, liveLines } from '../chart/tlengine.js';
import { liveZones } from '../chart/zones.js';
import { liveSDZones } from '../chart/supplydemand.js';
import { liveChannels } from '../chart/channels.js';
import { build as buildSegments } from '../chart/segments.js';
import { derived as derivedNews, loadSourced as loadNews, merge as mergeNews,
         upTo as newsUpTo, within as newsWithin } from '../chart/newsevents.js';
import { SENSITIVITY } from '../chart/trendlines.js';

const SPEEDS = [{ label: '1x', ms: 400 }, { label: '2x', ms: 200 }, { label: '4x', ms: 100 }];

/* Modelled slippage, in ATR. The same 0.02 sim/core and tools/paper_trade.py
   use -- a replay that estimated fills with a different number would be showing
   a strategy the backtest never ran. */
const SLIP_ATR = 0.02;

/* The traced exit. Deliberately NOT the SL colour: the stop is fixed and this
   moves, and colouring them alike is how a reader concludes the stop trails
   when it does not. Violet and dashed because it is the structural trail most
   of the time -- and dashed for the same reason the target bands are: a level
   that moves every bar should not carry the weight of one that does not. */
const TRAIL_COL = '#c07cf0';

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
    /* Structure settings, taken from the SAME constant the live chart's auto
       defaults are built from -- see AUTO_DEFAULTS in js/util.js. The replay
       offers no menu for these; matching an unchanged live chart is the point.
       `_htf` caches higher-frame bars per load so stepping costs no fetch. */
    this.sens = AUTO_DEFAULTS.sens;
    this._htf = new Map();
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
   * bars -- BAR_COUNT per frame, the same table the live chart loads with, so
   * that both draw trendlines fitted on the same history -- so the window is
   * nine months on 4h and about eight days on 5m: the reach
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
   *                               instead of the recent BAR_COUNT bars
   * @param {number}  opts.seekTo  epoch ms to put the cursor on once loaded
   */
  async load({ months = 0, seekTo = null } = {}) {
    this.stop();
    this.loading = true;
    this.error = null;
    this.months = months;
    this._paintBar();
    try {
      /* THE SAME WINDOW THE LIVE CHART LOADS, by request, so the two surfaces
         draw the same trendlines.
       *
         It was a flat 3000. Settings, sources, history for the higher frames
         and the distance filter were all matched and the support lines still
         disagreed: with 3000 own-frame bars the replay found 5m lines that a
         2500-bar chart does not, and those extra lines deduped away the 15m
         ones the chart was drawing. Trendline fitting is not local -- change
         how far back the walk starts and you change which anchors win -- so
         the window itself has to match or nothing downstream can.
       *
         THE COST IS A SHORTER WALK on the fast frames: 5m goes from 3000 bars
         to 2500, about eight days instead of ten. `months` mode is untouched,
         so asking for a date range still reaches as far as it ever did. */
      const payload = await api.bars(this.symbol, this.tf,
                                     months ? 60000 : (BAR_COUNT[this.tf] || 3000),
                                     months);
      this.full = payload.bars || [];
      this.digits = payload.digits ?? 2;
      /* The spread floor and the point size come from the CONTRACT, so the
         estimated fill below is the one the simulator would charge rather than
         a round number. A failed spec is not fatal: the fill row is dropped and
         the geometry rows still stand. */
      try { this.spec = await api.spec(this.symbol); }
      catch { this.spec = null; }
      /* OPEN ON THE CURRENT BAR, by request.
       *
       * This started at 35% of the window, so the replay opened with most of
       * the series still ahead to step through. That is the right default for
       * a stepping exercise and the wrong one for the question people actually
       * arrive with: what is the rule saying about the market NOW. Landing on a
       * date eight months ago made the panel read as a historical curiosity,
       * and getting to today meant holding a button.
       *
       * Nothing is lost, because the transport runs both ways: stepping BACK
       * from the live edge reaches every bar the old default could, and the
       * date field still jumps anywhere. What IS true is that there is no
       * future left to reveal at the last bar -- `Reveal` and `Next bar` have
       * nothing to show until you step back, which is the honest state of
       * affairs when the cursor is on the most recent close.
       */
      const rule = byKey(this.strategyKey);
      /* warmup off the params this timeframe will ACTUALLY run, not the flat
         defaults -- a 3.3-day channel on 5m is 950 bars, and opening the
         replay 40 bars in would show a rule that has no channel yet. */
      const warm = rule.warmup(this._params(rule)) + 40;
      this.i = Math.max(Math.min(warm, this.full.length - 1),
                        this.full.length - 1);
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
    /* BUILT ONCE PER LOAD. The schedule is a function of the window, not of the
       cursor, so regenerating it on every step would recompute a decade of
       dates to draw the same marks. `_news` holds everything in the window;
       `_apply` filters it to the cursor. */
    /* HIGHER FRAMES FOR THE TRENDLINES, fetched once per load rather than per
       step: a projection needs the source series, and refetching it on every
       bar would make stepping a network operation. They are cut to the cursor
       at draw time, not here. */
    this._htf = new Map();
    if (this.full.length) {
      const rank = TF.indexOf(this.tf);
      const want = resolveAuto(this.symbol, this.tf, AUTO_DEFAULTS).htf
        || AUTO_DEFAULTS.htf;
      await Promise.all(want
        .filter((src) => TF.indexOf(src) > rank)
        .map(async (src) => {
          try {
            /* The SAME count js/main.js uses via getSeries -- see BAR_COUNT
               in js/util.js for why matching it matters. */
            const p = await api.bars(this.symbol, src,
                                     Math.min(BAR_COUNT[src] || 1000, 1200));
            if (p && p.bars && p.bars.length) this._htf.set(src, p.bars);
          } catch { /* a missing higher frame just contributes no lines */ }
        }));
    }

    this._news = [];
    if (this.full.length) {
      try {
        const from = this.full[0].t, to = this.full[this.full.length - 1].t;
        this._news = mergeNews(derivedNews(from, to),
                               newsWithin(await loadNews(), from, to));
      } catch { this._news = []; }
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

  /**
   * The cell this replay is standing in: instrument AND timeframe.
   *
   * One string, built in one place, because it is the key every per-cell
   * setting is stored under and a second spelling of it would silently give the
   * take-profit switch a different scope from the evidence that justifies it.
   * The same shape `strategies.js` uses for `cells`.
   */
  cell() {
    return `${this.symbol}|${this.tf}`;
  }

  /**
   * Turn the fitted take-profit on or off FOR THIS CELL, and say what it did.
   *
   * The re-walk is free -- `runRule` is a pure function of the slice -- so the
   * cursor stays exactly where it is and the whole ledger behind it is recut
   * under the new rule. That is the point of putting the switch here: the
   * comparison worth making is the same bar under both configurations, not two
   * separate runs you have to hold in your head.
   *
   * THE EVIDENCE IS QUOTED ON THE WAY IN, not on the way out. Turning a target
   * on is the moment to be told it is reached 51% of the time and pays
   * -0.02 R [-0.07, +0.05] gross; being told when you turn it off is too late
   * to matter. It costs a thousand bootstrap resamples a side, which is why it
   * happens on a click and never in `render`.
   */
  /**
   * Turn the fitted stop on or off FOR THIS CELL.
   *
   * Separate from the target because it is a bigger change than one: the stop
   * sets R, so moving it moves every number on the panel including the ones
   * describing trades that were already closed, and it decides which signals
   * are placeable at all. `stopMultiples` reports the survival its width
   * actually delivers, which is the claim being made and the thing to say.
   */
  toggleStop() {
    const cell = this.cell();
    const on = !stopEnabled(cell);
    setStopEnabled(cell, on);
    this.stop();
    this._apply();
    if (!on) { toast(`Stop back to ${STOP_ATR} ATR for ${cell} — as validated`); return; }
    const slice = this.full.slice(0, this.i + 1);
    const m = stopMultiples(slice, cell);
    if (m.source !== 'measured') {
      toast(`Fitted stop on for ${cell}, but ${slice.length} bars is too few to `
            + `measure one — still ${STOP_ATR} ATR`, 6000);
      return;
    }
    const pct = (x) => (Number.isFinite(x) ? `${(x * 100).toFixed(0)}%` : '—');
    toast(`Fitted stop on for ${cell} — long ${m[1]} ATR (survives `
          + `${pct(m.survival[1])} of favourable paths), short ${m['-1']} ATR `
          + `(${pct(m.survival['-1'])}). R now means something different here.`, 9000);
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

    /* THE PLAN, FITTED ON THE SLICE AND NOT ON THE SERIES. `slice` is the bars
       up to and including the cursor -- the same array the signal is walked
       over, passed one line apart from it so the two cannot drift. Handing
       `this.full` here instead would fit the target on bars the replay is
       pretending not to have seen, and would look identical on screen.

       Both come back `undefined` when their mode is off or the slice is too
       short to measure, and an absent option is the validated rule: a 2.0 ATR
       stop and no target. `atrMult` is spread conditionally because `undefined`
       would OVERRIDE the rule's default rather than fall through to it -- the
       three-layer merge in runRule takes the last value, not the last defined
       one. */
    const cell = this.cell();
    /* THE STOP WIDTH IS THE ONLY THING THIS CAN VARY NOW.
     *
     * There was a take-profit here -- five modes, structural and fitted, whole
     * and half -- withdrawn from every surface, and then from the walker
     * itself. The replay was the last to keep the switch, on the argument that
     * a sandbox should run what the live chart will not; the answer was to
     * remove it here too, and then to remove the machinery entirely. What
     * survives is the measurement it went on the strength of:
     * logs/tp_struct_eval.txt, twelve cells out of sample, no target beating
     * the trailing exit on net R. */
    const atrMult = stopOption(slice, cell);
    const exitTrail = trailOption(cell, this.tf);
    const sig = runRule(slice, rule, {
      upto: slice.length - 1, tf: this.tf,
      ...(atrMult ? { atrMult } : {}),
      ...(exitTrail ? { exitTrail } : {}),
    });
    this.sig = sig;
    /* Kept for the panel and the sidecar so neither re-fits: both want to say
       WHICH numbers this walk used, and computing them a second time is how a
       screen ends up quoting a stop the walker did not use. */
    this.plan = {
      cell,
      stop: atrMult ? atrMult.multiples : null,
      trail: !!exitTrail,
      /* ONLY THE FITTED STOP STILL ANNOUNCES ITSELF. The trail's note was
         removed by request from both surfaces; its measurement is recorded in
         js/chart/trailmode.js and logs/exit_trail_eval.txt instead. */
      notes: [ruleVerdictNote(slice, cell)].filter(Boolean),
    };

    /* THE STRUCTURE THE LIVE CHART DRAWS, ON THE CAUSAL SLICE.
     *
     * Trendlines, S/R zones, BOS/CHoCH and swing points, from the SAME modules
     * the live chart calls -- marketstructure.js, structure.js, tlengine.js,
     * zones.js and the sensitivity presets -- so the replay and the chart
     * cannot disagree about what a swing or a band is. The rule's own decisions were already computed on this
     * slice; drawing structure from the full series beside them would put
     * tomorrow's swing next to today's signal, which is the one thing this
     * surface exists to prevent.
     *
     * ONE TIMEFRAME, unlike the live chart. The chart projects lines down from
     * higher frames because it can fetch them; the replay loads one series and
     * fetching more mid-walk would make each step a network call. A line here
     * is therefore always this frame's own.
     *
     * MAJOR IS THE SAME WORD IT IS EVERYWHERE ELSE: a swing that also survives
     * strength 6, which is what picking `Major structure` in the live menu
     * shows. Both passes run on `slice`, so both inherit the as-of cut.
     */
    this._drawStructure(slice);

    /* SCHEDULED RELEASES, up to the cursor and no further. Marking a print the
       walk has not reached yet would put tomorrow's news beside today's signal,
       which is the same violation as drawing tomorrow's swing. */
    this.chart.setNewsMarks(newsUpTo(this._news, slice[slice.length - 1].t));

    /* Closed trades as ribbons, coloured by outcome and labelled with the R
       they actually returned -- the sequence is the thing a summary hides. */
    /* TWO RIBBONS. Row 0 is the REGIME EPISODE strip the live chart draws --
       RANGE, TRANSITION, UPTREND -- and row 1 is this replay's own trades.
       They shared one array before, so the replay's trades silently replaced
       the episodes and the two surfaces showed different things at the top of
       the chart. Episodes are built on the causal slice like everything else
       here, so the strip ends at the cursor rather than describing bars the
       walk has not reached. */
    let episodes = [];
    try {
      const auto = resolveAuto(this.symbol, this.tf, AUTO_DEFAULTS);
      episodes = (auto.segments !== false && slice.length >= 60)
        ? buildSegments(slice).map((sg) => ({ ...sg, row: 0 }))
        : [];
    } catch { episodes = []; }

    this.chart.setSegments(episodes.concat(sig.trades.map((t) => ({
      t0: t.entryTime, t1: t.exitTime,
      kind: t.r > 0 ? WIN_KIND : LOSS_KIND,
      closed: true,
      row: 1,
      label: `${t.side === LONG ? 'L' : 'S'} ${t.r >= 0 ? '+' : ''}${t.r.toFixed(1)}R`,
    }))));

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

    /* WHAT IS IN THE WAY -- THE SAME OBJECT THE LIVE CHART DRAWS.
     *
     * This was a 2R / 3.5R / 5R ladder from a `targetBands` helper, labelled
     * "reference
     * only" -- js/chart/targets.js, now deleted along with the parity test that
     * pinned it to sim/targets.py. It was the last fixed-R ladder left in the
     * app, and it
     * had the defect the live chart's version was removed for: three numbers
     * computed from the stop alone, identical in shape on every chart, blind to
     * what is actually in front of the trade. On a sandbox whose entire job is
     * to show what the rule does, a ladder that is the same on every symbol is
     * the one thing on screen that cannot be wrong and cannot be informative.
     *
     * Now it is `displayLevels` -- swing points, S/R, supply/demand bases,
     * trendlines and the unbroken swing whose break would be the next BOS --
     * derived from the SLICE, not from `this.full`, so the levels are the ones
     * visible at the cursor and not the ones today's chart would produce.
     *
     * THEY ARE NOT TARGETS ON THIS SURFACE EITHER. The rule has no
     * take-profit; it exits on the stop or on the trail traced below. These say
     * how much room the trade has, which is the question an entry raises.
     */
    this.levels = [];
    this.levelsCleared = false;
    {
      const held0 = sig.position;
      const pend0 = (!held0 && sig.pending && sig.pending.side !== FLAT)
        ? sig.pending : null;
      if (held0 || pend0) {
        const sd = ((held0 ? held0.side : pend0.side) > 0) ? 1 : -1;
        const anchor = held0 ? held0.entryPrice : pend0.signalPrice;
        const at = held0 ? held0.entryI : pend0.signalI;
        /* MORE THAN ARE SHOWN, because some are about to be discarded as
           spent -- the same over-fetch the live panel does. */
        const found = displayLevels(slice, {
          side: sd, from: anchor, upto: at, tf: this.tf, max: 8,
        });
        /* A LEVEL PRICE HAS ALREADY TOUCHED IS SPENT. Checked against every bar
           from the decision to the CURSOR -- never past it, which is the whole
           discipline of this file: `slice` ends at `this.i`, so a level the
           future clears cannot be discarded early. */
        const reached = (price) => {
          for (let k = at; k < slice.length; k++) {
            const b = slice[k];
            if (sd > 0 ? b.h >= price : b.l <= price) return true;
          }
          return false;
        };
        const ahead = found.filter((lv) => !reached(lv.price));
        this.levels = ahead.slice(0, 3).map((lv, k) => ({ ...lv, key: `TP${k + 1}` }));
        this.levelsCleared = found.length > 0 && ahead.length === 0;
      }
    }
    this.chart.setRuleTargets(this.levels.length ? {
      levels: this.levels,
      entry: sig.position ? sig.position.entryPrice
        : (sig.pending ? sig.pending.signalPrice : NaN),
      /* THE STOP SIZES THE MONEY -- see js/chart/engine.js ACCOUNT. Without it
         the tags fall back to prices only. */
      stop: sig.position ? sig.position.stop
        : (sig.pending ? sig.pending.stop : NaN),
      tickSize: this.spec ? (this.spec.tick_size || this.spec.point || 0) : 0,
      tickValue: this.spec ? (this.spec.tick_value || 0) : 0,
    } : null);

    /* THE EXIT, TRACED -- ONE LINE, AND IT IS THE ONE THAT WILL FIRE.
     *
     * There were two for a while: the channel solid in cyan and the structural
     * trail dashed in violet, on the argument that seeing which binds is the
     * whole question. It is -- but that question has a written answer in the
     * panel now, where both levels sit with a LIVE/behind badge, and two moving
     * lines over the same price action asked the reader to do the comparison by
     * eye instead of reading it.
     *
     * So the chart draws the EFFECTIVE exit: the tighter of the two at every
     * bar, which is by definition the level that will close the trade. It is
     * still dashed violet -- the trail's colour -- because the trail is what it
     * is most of the time; where the channel is tighter the same line simply
     * traces the channel instead. What it must never be is a line the trade
     * does NOT exit at, which is what drawing only the trail would have been:
     * the channel still takes about a quarter of the exits, usually early on
     * before any structure has formed behind the trade.
     *
     * Traced from the entry bar to the cursor, so stepping forward shows it
     * ratchet -- which is the thing worth watching and the reason this is a
     * line and not a row. */
    this.trail = null;
    if (sig.position) {
      const long = sig.position.side === LONG;
      const lvl = long ? sig.series.exitLo : sig.series.exitHi;
      const closes = slice.map((b) => b.c);
      const pts = [];
      let held = null;
      for (let k = sig.position.entryI; k <= this.i; k++) {
        if (exitTrail) {
          const cand = structuralTrail({
            side: sig.position.side, i: k, view: slice,
            series: sig.series, close: closes,
            /* The walker measures the break-even floor from this; a line drawn
               without it would sit somewhere the trade never exits. */
            entryPrice: sig.position.entryPrice,
          }, { tf: this.tf, cell });
          if (Number.isFinite(cand)) {
            const better = held === null || (long ? cand > held : cand < held);
            if (better) held = cand;
          }
        }
        const ch = lvl[k];
        let eff = null;
        if (Number.isFinite(ch) && Number.isFinite(held)) {
          eff = long ? Math.max(ch, held) : Math.min(ch, held);
        } else if (Number.isFinite(ch)) eff = ch;
        else if (Number.isFinite(held)) eff = held;
        if (Number.isFinite(eff)) pts.push({ i: k, price: eff });
      }
      if (pts.length > 1) {
        this.trail = { points: pts, label: 'exit', color: TRAIL_COL,
                       width: 1.4, dash: [4, 3] };
      }
    }
    this.chart.setTrail(this.trail);

    /* THE PLAN: ENTRY, THE RISK BLOCK, AND THE ROOM AHEAD.
     *
     * This used to go through `setPositions` -- the renderer for REAL BROKER
     * ROWS -- with a synthetic row carrying volume 1 and a null tp. It drew the
     * right two lines for the wrong reason, and it put a simulated trade
     * through the one code path in the app that is supposed to mean "you hold
     * this". `setRuleZone` is the renderer that means "the rule says", which is
     * all the replay ever shows, and it is what the live chart uses. Both
     * surfaces now draw the same object from the same call.
     *
     * A PENDING signal is drawn too, at the level it WOULD take. Without it the
     * decision bar -- the one moment you are actually being asked something --
     * was the only bar with no plan on the chart at all. Its `ref` still works:
     * the levels above are computed from the signal close when there is no
     * fill, because the next open does not exist yet.
     *
     * `ref` IS TP1 AND NOTHING ELSE. Zero when nothing is ahead, and the block
     * is simply not drawn -- clear air drawn as clear air. */
    const held = sig.position;
    const pend = (!held && sig.pending && sig.pending.side !== FLAT)
      ? sig.pending : null;
    this.chart.setPositions([]);
    if (held || pend) {
      const long = (held ? held.side : pend.side) > 0;
      this.chart.setRuleZone({
        entry: held ? held.entryPrice : pend.signalPrice,
        stop: held ? held.stop : pend.stop,
        ref: this.levels.length ? this.levels[0].price : 0,
        i0: held ? held.entryI : pend.signalI,
        label: held ? (long ? 'RULE holding LONG' : 'RULE holding SHORT')
                    : (long ? 'RULE would BUY' : 'RULE would SELL'),
      });
    } else {
      this.chart.setRuleZone(null);
    }

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
                       runner(1), 'rp-trans');
    this.backBtn = btn('◀', 'Play backward — press again to stop',
                       runner(-1), 'rp-trans');
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
    /* TWO SWITCHES, PER CELL, ON THE BAR RATHER THAN IN A MENU. The take-profit
       changes what the walker does on every bar you are about to step through,
       so it belongs beside the transport that steps it -- a mode you have to go
       and look for is a mode you forget is on. They sit AFTER the recorder's
       separator, away from the transport, because a misclick between "step one
       bar" and "re-run the whole walk under a different rule" is expensive. */
    this.stopBtn = btn('SL fit',
      'Fitted stop for THIS symbol and timeframe. On: the stop is the 75th '
      + 'percentile of the heat a favourable path takes, per side. Off: '
      + `${STOP_ATR} ATR, the width the rule was validated with. Changing it `
      + 'changes which trades are placeable at all, so it is off by default.',
      () => this.toggleStop());
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
      btn('◀◀', 'One bar back  (,)', () => { this.stop(); this.step(-1); }, 'rp-trans'),
      this.backBtn,
      this.playBtn,
      btn('▶▶', 'One bar forward  (.)', () => { this.stop(); this.step(1); }, 'rp-trans'),
      btn('▶▶|', 'Run to the end', () => { this.stop(); this.step(this.full.length); }, 'rp-trans'),
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
      this.stopBtn,
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
    /* THE MODE BUTTONS SAY WHICH CELL THEY ARE ANSWERING FOR. The setting is
       per instrument and per timeframe, so the same button is lit on 4h and
       dark on 15m of the same symbol -- which reads as a bug unless the label
       says whose switch it is. `_apply` has already fitted the plan, so "on but
       unmeasured" is a state the button can show rather than one you discover
       by wondering why nothing changed. */
    if (this.stopBtn) {
      const fitted = !!(this.plan && this.plan.stop);
      this.stopBtn.classList.toggle('on', fitted);
      this.stopBtn.textContent = fitted
        ? `SL ${this.plan.stop[1]}/${this.plan.stop['-1']} ATR`
        : (stopEnabled(this.cell()) ? 'SL —' : `SL ${STOP_ATR}`);
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
      /* AND THE MODES, because they are not part of `params` and change what
         the trades below mean. Null for both is the validated configuration;
         anything else is a rule `measured.js` never scored, and a ledger that
         did not say so would be read six months later as evidence about the one
         that was. `fittedOn` is the cursor, not the window: the numbers were
         measured causally and a reader has to be able to check that. */
      plan: this.plan ? {
        stopAtr: this.plan.stop,
        fittedOnBars: this.i + 1,
        notes: this.plan.notes,
        trail: this.plan.trail,
      } : null,
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
      /* Above the verdict badge it contradicts, in the export as on the screen.
         This is the caption that stops a shared image being read as evidence
         about the rule that was measured. */
      for (const n of (this.plan ? this.plan.notes : [])) {
        line(n, { size: 9, colour: C.warn, gap: 10 });
      }
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

  /**
   * Trendlines, BOS/CHoCH and swings for the bars up to the cursor.
   *
   * Every detector is wrapped: a frame one of them cannot read should cost the
   * chart that one overlay, never the whole repaint. The live chart takes the
   * same precaution for the same reason.
   */
  _drawStructure(slice) {
    /* THE LIVE CHART'S OWN EFFECTIVE SETTINGS for this symbol and frame, not a
       constant: global defaults, then the per-instrument override, then the
       per-timeframe one. Reading the same saved state is the only way the two
       surfaces stay in step once anyone touches the AUTO TL menu. */
    const auto = resolveAuto(this.symbol, this.tf, AUTO_DEFAULTS);
    this.sens = auto.sens || AUTO_DEFAULTS.sens;
    const sens = SENSITIVITY[this.sens] || SENSITIVITY.normal;
    const strength = sens.strength;
    const minDraw = auto.minDraw ?? AUTO_DEFAULTS.minDraw;
    const maxLines = auto.maxLines ?? AUTO_DEFAULTS.maxLines;
    const sources = auto.htf || AUTO_DEFAULTS.htf;
    const MAJOR = SENSITIVITY.major.strength;

    try {
      const r = (auto.ms !== false && slice.length >= 40)
        ? detectMS(slice, { strength }) : null;
      if (r && r.events && r.events.length) {
        /* `external` marks a break that the MAJOR pass also saw -- the same
           second-pass trick main.js uses, matched on the broken level's bar
           rather than the breaking bar, because the two passes can notice one
           break a bar apart while agreeing which swing was taken. */
        if (strength < MAJOR) {
          const major = detectMS(slice, { strength: MAJOR });
          const levels = new Set(major.events.map((e) => e.levelI));
          for (const e of r.events) e.external = levels.has(e.levelI);
        } else {
          for (const e of r.events) e.external = true;
        }
        this.chart.setMsEvents(r.events.slice(-12));
      } else {
        this.chart.setMsEvents([]);
      }
    } catch { this.chart.setMsEvents([]); }

    try {
      const sw = (auto.swings !== false && slice.length >= 40)
        ? swingPoints(slice, { strength }) : [];
      if (sw.length && strength < MAJOR) {
        const major = new Set(swingPoints(slice, { strength: MAJOR }).map((x) => x.i));
        for (const x of sw) x.major = major.has(x.i);
      } else {
        for (const x of sw) x.major = true;
      }
      this.chart.setSwings(sw);
    } catch { this.chart.setSwings([]); }

    /* S/R ZONES, from the chart's own pivots at its own timeframe.
     *
     * NO HIGHER-FRAME PROJECTION HERE, and unlike trendlines that is not a
     * limitation of the replay -- the live chart does not project them either.
     * A zone is horizontal by definition, so a 4h band and a 15m band at the
     * same price are the same band, and drawing both would double-count one
     * level.
     *
     * ON THE SURVIVORSHIP QUESTION: `zones.detect` is the detector that once
     * scored levels partly on how they were later respected, which flatters
     * anything measured through it. Running it on `slice` is what makes that
     * safe here rather than merely unlikely -- the array it is handed ENDS at
     * the cursor, so the "later" it could learn from does not exist yet. The
     * same argument covers every detector on this chart, and it is the reason
     * all of them are given the slice rather than the series. */
    try {
      this.chart.setZones(auto.zones !== false && slice.length >= 40
        ? liveZones(slice, this.tf, { strengthPivots: strength })
        : []);
    } catch { this.chart.setZones([]); }

    /* SUPPLY / DEMAND -- the impulse-origin bands, a different detector from
       the pivot clusters above and gated by its own toggle on the live chart.
       Off by default there, which is why the replay showed none while a chart
       with it switched on showed six. */
    try {
      this.chart.setSdZones(auto.sdZones && slice.length >= 40
        ? liveSDZones(slice, this.tf) : []);
    } catch { this.chart.setSdZones([]); }

    /* CHANNELS. What reads on screen as "the missing trendline" is usually
       this: the ASCENDING CHANNEL rails are a corridor, not an auto line, and
       the replay drew none because nothing here called for them. Detected from
       the chart's OWN frame only, as on the live chart -- a corridor projected
       down from 4h onto 15m would be drawn from rails whose containment was
       measured on other bars. */
    try {
      if (auto.channels === false || slice.length < 60) {
        this.chart.setChannels([]);
      } else {
        const now = liveChannels(slice, this.tf, { params: sens });
        for (const ch of now) ch.live = true;
        this.chart.setChannels(now);
      }
    } catch { this.chart.setChannels([]); }

    /* TRENDLINES FROM EVERY SOURCE ABOVE THIS FRAME, exactly as the live chart
       builds them -- `autoSources` there is `own` plus each `htf` ranked higher,
       and this is the same set. It was this frame only, which is why the two
       surfaces disagreed: a 15m replay drew none of the 1h/4h/1d lines the
       chart beside it was drawing.
     *
       EVERY HIGHER FRAME IS CUT TO THE CURSOR'S INSTANT before it is walked.
       A 4h series ending after the 15m cursor would project a line fitted with
       knowledge of bars the replay has not reached -- the exact unfalsifiable
       drawing the live chart's own as-of cut exists to prevent. */
    try {
      const cutoff = slice[slice.length - 1].t;
      const rank = TF.indexOf(this.tf);
      const lines = [];
      if (slice.length >= 60) {
        for (const l of liveLines(slice, this.tf, { params: sens, minDraw })) lines.push(l);
      }
      for (const src of sources) {
        if (TF.indexOf(src) <= rank) continue;
        const raw = this._htf.get(src);
        if (!raw || !raw.length) continue;
        const cut = raw.filter((b) => b.t <= cutoff);
        if (cut.length < 40) continue;
        try {
          for (const l of liveLines(cut, src, { params: sens, minDraw })) lines.push(l);
        } catch { /* one unreadable frame contributes nothing */ }
      }
      /* THE DISTANCE FILTER, WITHOUT WHICH THE TWO SURFACES CANNOT AGREE.
       *
       * js/main.js drops any line sitting more than DRAW_MAX_ATR of the
       * CHART'S OWN atr from price, and dedupes lines arriving within 0.35 ATR
       * of each other. Leaving both out was the whole remaining difference:
       * with identical settings and identical history the replay still drew 4h
       * and 1d lines at score 92 that the live chart had already discarded as
       * unreachable, because a daily line can sit fifty chart-ATRs away and
       * still pass its own proximity test. Score alone then ranked exactly the
       * lines the chart refuses to draw straight to the top.
       *
       * The threshold is the engine's own detection threshold applied in the
       * chart's units, so every source obeys the rule the chart's own frame
       * obeys. When it leaves nothing, nothing is drawn. */
      const DRAW_MAX_ATR = 5;
      const atrSeq = atrSeries(slice, 14);
      const atrNow = atrSeq[atrSeq.length - 1];
      const spot = slice[slice.length - 1].c;
      const near = [];
      for (const l of lines) {
        if (atrNow > 0) {
          const v = l.valueAt(cutoff);
          if (Number.isFinite(v) && Number.isFinite(spot)
            && Math.abs(v - spot) / atrNow > DRAW_MAX_ATR) continue;
          const dup = near.some((k) => k.kind === l.kind
            && Math.abs(k.valueAt(cutoff) - v) / atrNow < 0.35);
          if (dup) continue;
        }
        near.push(l);
      }
      /* Same budget the live chart applies, across ALL sources rather than per
         source -- otherwise four frames quietly put a dozen lines on screen.
         ACTIVE outranks merely CONFIRMED at equal score. */
      const score = (l) => l.score + (l.status === 'ACTIVE' ? 2 : 0);
      near.sort((a, b) => score(b) - score(a));
      const budget = { support: maxLines, resistance: maxLines };
      this.chart.setAutoLines(near.filter((l) => (budget[l.kind]-- > 0)));
    } catch { this.chart.setAutoLines([]); }
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

    /* WHICH EXIT IS LIVE. The trade now has THREE levels that can end it and
       only one of them can be reached first, so listing them as equals leaves
       the reader to work out which is actually holding the position -- which is
       the whole question.

       For a long, price falls to whichever level is HIGHEST: the tightest one
       binds and the others are inert behind it. Early in a trend trade the
       channel is usually the loosest of the three, which is why it can look
       absurdly far away for the first few bars.

       THE CHART DRAWS ONLY THE WINNER. This is the one place both are written
       down, because "which binds" is a fact about the trade rather than a
       picture of it. */
    if (sig.position && Number.isFinite(sig.exitLevel)) {
      const long = sig.position.side === LONG;
      const stop = stopNow;
      const trail = sig.position.trail;
      /* The tighter of two, for this side: a long exits at the HIGHER level. */
      const tighter = (a, b) => {
        if (!Number.isFinite(a)) return b;
        if (!Number.isFinite(b)) return a;
        return long ? Math.max(a, b) : Math.min(a, b);
      };
      const effective = tighter(sig.exitLevel, trail);
      const badge = (v) => {
        if (ins.action === 'exit') return null;
        if (!Number.isFinite(v)) return null;
        return v === effective ? 'LIVE' : 'behind';
      };

      let liveStop = null;
      let liveExit = null;
      let liveTrail = null;
      if (ins.action === 'exit') {
        /* Already fired. Nothing is "waiting" any more: the exit is spent and
           the stop is the only thing still able to act, for the one bar until
           the close order fills. */
        liveExit = 'triggered';
        liveStop = 'until fill';
      } else if (Number.isFinite(stop)) {
        /* The stop only binds when it is TIGHTER than the moving exits -- which
           for a long means higher than both. */
        const exitBinds = long ? effective > stop : effective < stop;
        liveStop = exitBinds ? 'behind' : 'LIVE';
        liveExit = exitBinds ? badge(sig.exitLevel) : 'behind';
        liveTrail = exitBinds ? badge(trail) : 'behind';
      }
      const si = rows.findIndex((r) => r[0] === 'stop loss');
      if (si >= 0 && liveStop) rows[si] = ['stop loss', px(stop, d), liveStop];
      rows.push(['exit level', px(sig.exitLevel, d), liveExit]);
      if (Number.isFinite(trail)) {
        rows.push(['trail', px(trail, d), liveTrail]);
      }
    }

    /* THE TAKE-PROFIT, WHEN THERE IS ONE. Struck off the fill price at entry
       and fixed from then on, which is what makes it a target rather than a
       third trailing level -- and why it is shown with the multiple it was
       given: `2.31R` is the claim, `4,712.85` is only where that claim landed
       for this particular trade.

       The remaining distance is quoted in R too. "18 dollars away" says nothing
       without the risk beside it, and the whole question while a trade is open
       is which of the three levels price reaches first. */
    if (sig.position) {
      const pos = sig.position;
      rows.push(['bars held', String(this.i - pos.entryI)]);
      const openR = (this.full[this.i].c - pos.entryPrice) * pos.side / pos.risk;
      rows.push(['open R', (openR >= 0 ? '+' : '') + openR.toFixed(2)]);
    }

    /* THE REGIME AS OF THIS BAR, from the same js/chart/regime.js the live
       Trend read draws -- causal, so the label is one a reader had here.
       REPORTED, NOT ACTED ON, and the note says which: measured on 15m gold,
       splitting the rule's trades by regime does not beat a random gate of the
       same size (p = 0.087) and does not clear a best-bucket null (p = 0.334),
       so gating on it would be the twelfth entry filter to fail that test.
       What the split DOES show is worth reading bar by bar, which is why the
       row is here at all. */
    const rg = latestDimensions(this.full.slice(0, this.i + 1));
    if (rg) {
      /* THREE AXES, NOT ONE LABEL. The single four-state read put 48.5% of 15m
         bars in `transition`, which is a bucket too broad to say anything.
         Direction, phase and volatility vary independently and are reported
         independently -- see the dimensions() note in js/chart/regime.js.
         REPORTED, NOT ACTED ON: the four-state version was measured as an entry
         gate and did not clear a best-bucket null (p = 0.334 on 15m, 0.254 on
         5m), and nothing about splitting the same readings differently changes
         that until it is measured too. */
      rows.push(['direction', rg.direction,
                 'EMA(21) vs EMA(50) in ATR units, as of this bar']);
      rows.push(['phase', rg.phase, Number.isFinite(rg.giveBackAtr)
        ? `${rg.giveBackAtr.toFixed(2)} ATR back from the 40-bar extreme`
        : 'no direction, so no trend to pull back from']);
      rows.push(['volatility', rg.volatility, 'ATR now against its 56-bar mean']);
      rows.push(['ema sep', `${rg.emaSepAtr >= 0 ? '+' : ''}${rg.emaSepAtr.toFixed(2)} ATR`]);
      rows.push(['range pos', `${Math.round(rg.rangePos * 100)}%`,
                 'where price sits in its own 40-bar range']);
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

    /* WHAT IS IN THE WAY -- A RULE, NOT A HEADING, and no names on the rows.
       Both were removed by request on the live panel and the replay follows, so
       the two surfaces stay one design: the words "in the way — not targets"
       are gone, and so is what each level IS ("demand zone, fresh", "swing
       high"). The kind still decides WHICH prices are listed; it is no longer
       printed beside them.
     *
       The hairline replaces the heading rather than being added to it. Every
       `.sr-h` here draws a border under itself, so dropping the words would
       have dropped the divider too and run TP1 straight on from `open R`. */
    if (this.levels && this.levels.length) {
      p.append(el('table', { class: 'sr-kv sr-rule' }, ...this.levels.map((lv) =>
        el('tr', {},
          el('td', { text: lv.key }),
          el('td', { class: 'mono' }, px(lv.price, d))))));
    } else if (this.levelsCleared) {
      /* SPENT IS NOT THE SAME AS NONE FOUND. A trade that has run through
         everything ahead of it is in clear air -- the best thing that can
         happen to a trend trade -- and showing the same blank for both would
         make the good case look broken. */
      p.append(el('div', { class: 'sr-note sr-rule' }, 'all reached — clear air ahead'));
    }

    if (sig.exitLevel !== null && Number.isFinite(sig.exitLevel)) {
      p.append(el('div', { class: 'sr-warn' },
        'The exit level is NOT a take-profit — it is the 10-bar channel and '
        + 'it moves every bar. Capping this strategy at a fixed 1R was measured '
        + 'to turn +43.7 net R into −2.1.'));
    }

    /* WHAT IS BEING STEPPED, WHEN IT IS NOT THE RULE THAT WAS MEASURED.
       Printed on every bar the mode is on, above the verdict badge it
       contradicts, because a switch whose consequence is only visible in the
       settings that turned it on is a switch that gets left on by accident. */
    for (const n of (this.plan ? this.plan.notes : [])) {
      p.append(el('div', { class: 'sr-warn' }, n));
    }

    /* THE FIT ITSELF, and the sample it came off.
       Two numbers per side and the count behind them, because "2.31R" and
       "2.31R measured on 1,145 samples" are different claims and the panel is
       the only place the difference can be seen. Recomputed nowhere: these are
       the values `_apply` handed the walker, so the panel cannot quote a target
       the trades were not taken against.

       The reach rate is deliberately NOT here. It costs a thousand bootstrap
       resamples a side and this redraws on every step; it is computed once, on
       the click that turns the mode on, and toasted there. */
    if (this.plan && this.plan.stop) {
      p.append(el('div', { class: 'sr-h' },
        `fitted on bars 1–${this.i + 1} (causal)`));
      const fit = [];
      fit.push(['stop width',
                `${this.plan.stop[1]} ATR long  ${this.plan.stop['-1']} ATR short`]);
      const n = (this.plan.stop || {}).n;
      if (n) fit.push(['samples', `${n} per side`]);
      p.append(el('table', { class: 'sr-kv' }, ...fit.map(([k, v]) =>
        el('tr', {}, el('td', { text: k }), el('td', { class: 'mono', text: v })))));
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
    /* NOT "— GROSS" any more; the word came off by request. What it warned
       about is still stated, in full sentences, in the note a few lines below:
       the replay charges no spread, slippage or swap. The SNAPSHOT and the
       video keep the word in their own header, because those leave the app and
       nothing travels with them to explain the numbers. */
    p.append(el('div', { class: 'sr-h' }, 'as of this bar'));
    /* SAID OUT LOUD, because the number is otherwise indistinguishable from a
       gate result. js/chart/rules.js fills at open[i] exactly: no spread, no
       slippage, no swap. On gold 4h 2018-2026 that reads +0.2147 R against the
       engine's +0.1799 -- about 19% high -- because the engine paid 430 of
       spread and 579 of slippage over the same window and this does not. */
    /* THE WINDOW, SAID AS PLAINLY AS THE COSTS.
       Cost drag is ~19%, and the scorecard routinely sits further from the
       validated figures than that -- because it is also a DIFFERENT WINDOW.
       The replay loads the last BAR_COUNT[tf] bars (1200 on 4h, about 200
       days), while the
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
