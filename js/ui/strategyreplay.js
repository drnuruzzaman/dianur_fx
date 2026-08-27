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
import { el, px } from '../util.js';
import { toast } from './menu.js';
import { tip } from './tips.js';
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
    this.speed = 400;
    this.digits = 2;
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
      if (e.key === ' ') { e.preventDefault(); this.timer ? this.stop() : this.play(); }
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
    this.host = null;
  }

  /* ----------------------------------------------------------- transport */

  step(n) {
    if (!this.full.length) return;
    const last = this.full.length - 1;
    const next = Math.min(last, Math.max(0, this.i + n));
    if (next === this.i) { this.stop(); return; }
    this.i = next;
    this._apply();
  }

  play() {
    this.stop();
    this.timer = setInterval(() => this.step(1), this.speed);
    this._paintBar();
  }

  stop() {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
    this._paintBar();
  }

  /* --------------------------------------------------------------- data */

  async load() {
    this.stop();
    this.loading = true;
    this.error = null;
    this._paintBar();
    try {
      const payload = await api.bars(this.symbol, this.tf, 3000);
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
      const warm = rule.warmup(rule.defaults) + 40;
      this.i = Math.min(this.full.length - 1,
                        Math.max(warm, Math.floor(this.full.length * 0.35)));
    } catch (err) {
      this.full = [];
      this.error = String(err.message || err);
    }
    this.loading = false;
    if (this.chart) this._apply();
    this._paintBar();
  }

  /* -------------------------------------------------------------- render */

  _apply() {
    if (!this.chart) return;
    if (!this.full.length) { this.chart.setData({ bars: [] }); return; }

    /* THE CHART HOLDS THE WHOLE SERIES; the SIGNAL is computed from the slice.
       One line apart so the separation cannot drift. */
    const slice = this.full.slice(0, this.i + 1);
    this.chart.symbol = this.symbol;
    this.chart.tf = this.tf;
    this.chart.setData({ bars: this.full, symbol: this.symbol, digits: this.digits });
    this.chart.setAsOfMark(this.i);
    const rule = byKey(this.strategyKey);
    const sig = runRule(slice, rule, { upto: slice.length - 1 });
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
    this.chart.view.right = Math.min(this.full.length - 1 + pad, this.i + Math.max(pad, 24));
    this.chart.view.priceLock = null;
    this.chart.draw();
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

    this.playBtn = btn('▶', 'Play forward  (space)', () => (this.timer ? this.stop() : this.play()), 'rp-tp');
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
      btn('◀', 'One bar back  (,)', () => { this.stop(); this.step(-1); }, 'rp-tp'),
      this.playBtn,
      btn('▶▶', 'One bar forward  (.)', () => { this.stop(); this.step(1); }, 'rp-tp'),
      btn('⏭', 'Run to the end', () => { this.stop(); this.step(this.full.length); }, 'rp-tp'),
      sel(SPEEDS.map((x) => x.label), SPEEDS[0].label, (v) => {
        this.speed = (SPEEDS.find((x) => x.label === v) || SPEEDS[0]).ms;
        if (this.timer) this.play();
      }),
      el('span', { class: 'rp-sep' }),
      /* After the transport and its separator, before the bar counter. The
         counter is a readout that grows and shrinks as the cursor moves; a
         button placed after it would shift sideways while you step, which is
         the one thing a button you aim at should not do. */
      this.pngBtn,
      this.status);
    this._paintBar();
  }

  _paintBar() {
    if (!this.status) return;
    if (this.playBtn) {
      this.playBtn.textContent = this.timer ? '■' : '▶';
      this.playBtn.classList.toggle('on', !!this.timer);
    }
    if (this.loading) { this.status.textContent = 'loading bars…'; return; }
    if (this.error) { this.status.textContent = 'bridge: ' + this.error; return; }
    if (!this.full.length) { this.status.textContent = 'no bars'; return; }
    const b = this.full[this.i];
    const when = b ? new Date(b.t).toISOString().replace('T', ' ').slice(0, 16) : '';
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

  /** The panel, laid out for a white page. Same content as the live one. */
  _drawSnapPanel(ctx, x0, h, w, scale) {
    const sig = this.sig;
    const d = this.digits;
    const C = { bg: '#F4F6F8', rule: '#D6DBE0', head: '#111', sub: '#666',
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
