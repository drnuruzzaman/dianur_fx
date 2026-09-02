/* rulepanel.js — the validated rule's state, beside the price it reads.
 *
 * A VIEWER over js/chart/donchian.js. It computes nothing itself: the channel
 * levels, the position and the exit level all come from `signalsAsOf`, which is
 * the same function tests/test_strategy_parity.py holds against
 * sim/strategies/donchian.py signal for signal. A panel that recomputed the
 * channel with its own rolling max would be a third implementation of a
 * quantity this project has already had diverge once.
 *
 * WHAT THE STATE MEANS, and it is not what a reader will assume. `signalsAsOf`
 * REPLAYS the rule from the first loaded bar. So "IN POSITION" means *the rule
 * would be holding, had it been followed across this window* -- it is not your
 * broker position, and nothing here reads your account. Load a different number
 * of bars and the simulated position can legitimately differ, because the entry
 * that put the rule in this trade may sit before the left edge.
 *
 * THAT WARNING IS ON THE BADGE'S TOOLTIP, not on a chip beside it. There were
 * two chips -- SIMULATED and the cell's verdict -- and they were asked for as
 * clutter once the badge grew from `LONG` to `ENTER AT NEXT OPEN`. What they
 * said is not clutter, so it moved rather than going away: hovering the badge
 * answers "is this real, and does the rule work on this cell?" together, which
 * is one place instead of two and is where a reader looks anyway.
 *
 * NO TAKE-PROFIT, so there is no target row. The rule's exits are the fixed
 * 2-ATR stop and a MOVING channel level, and the panel labels the level as
 * moving on every render. tools/tp_sweep.py measured what capping it costs: a
 * 1R take-profit turns +43.7 net R into -2.1. Drawing a "TP" here would show a
 * rule that loses money and was never the validated one.
 *
 * The distance-to-trigger row is the one genuinely forward-looking number: how
 * far price is from the level that would fire. It is quoted in price and in
 * ATR, because 40 points means nothing without the day's range beside it.
 */

import { api } from '../api.js';
import { el, px } from '../util.js';
import { tip } from './tips.js';
import { LONG, donchianRule, instruction, signalsAsOf, strategyForTf }
  from '../chart/donchian.js';
import { displayLevels } from '../chart/levels.js';
import { VERDICT_TEXT, measuredFor } from '../chart/measured.js';
import { trailOption } from '../chart/trailmode.js';
import { SIDEWAYS, TRANSITION, TRENDING_DOWN, TRENDING_UP, latest as regimeNow }
  from '../chart/regime.js';
import { asOfBanner, shortSymbol } from './trendread.js';

/* THE BADGE IS COLOURED BY DIRECTION, NOT BY ACTION.
 *
 * It used to key off the action, which produced three wrong readings: a SELL
 * entry came out GREEN (because `enter` mapped to the bullish class regardless
 * of side), a CLOSE came out red as though it were bearish, and LONG and SHORT
 * were both plain grey -- so the one thing a glance should answer, which way
 * the rule is pointing, was the one thing the colour did not say.
 *
 * Colour now means direction and fill means urgency: a filled badge is
 * something to act on now, an outlined one is a position already open. CLOSE
 * is neither long nor short, so it takes the orange used elsewhere for "a
 * decision is pending", not the down colour. */
function badgeClass(ins) {
  if (ins.action === 'exit') return 'rp-close';
  const side = ins.side;
  const dir = (side === 'BUY' || side === 'LONG') ? 'bull'
    : (side === 'SELL' || side === 'SHORT') ? 'bear' : null;
  if (!dir) return 'rp-quiet';
  // filled when there is something to do, outlined when merely holding
  return ins.action === 'hold' ? `rp-${dir}-held` : `rp-${dir}`;
}

/* THE PER-CELL VERDICT, ON THE FLOOR OF THE PANEL RATHER THAN IN ITS HEADER.
 *
 * It was a chip beside the badge, then the badge's tooltip, and both were
 * removed as clutter -- correctly: the header is where the eye lands and it now
 * holds two labels already. But the verdict is the one line that distinguishes
 * a cell the rule was VALIDATED on from one it has never been measured on, and
 * without it both draw the same confident levels. So it comes back at the
 * bottom, in a row of its own, where it is read after the levels rather than
 * competing with them.
 *
 * Words only, no colour code and no tooltip. The figures behind it are still
 * generated from runs/*.csv into js/chart/measured.js by
 * tools/export_measured.py, and runs/ still holds the runs.
 */

/* Enough bars for the channel, the 14-bar ATR and a couple of trades to exist.
   Below this the panel says so instead of drawing a confident nothing.

   IT DEPENDS ON THE TIMEFRAME, because the channel does. A 3.3-day channel is
   20 bars on 4h and 950 on 5m, and a flat floor of 60 would let 5m through
   with an all-NaN channel -- which does not look broken, it looks FLAT. The
   panel would quietly report "no signal" forever on the timeframe where the
   rule is least intuitive. */
function minBars(tf) {
  return donchianRule.warmup(donchianRule.paramsFor(tf)) + 40;
}

export class RulePanel {
  constructor(root, headRoot) {
    this.root = root;
    this.headRoot = headRoot;
    this.symbol = null;
    this.tf = null;
    this.sig = null;
    this.digits = 2;
  }

  update(symbol, tf, bars, opts = {}) {
    this.symbol = symbol;
    this.tf = tf;                          // display label, e.g. 'H4'
    this.rawTf = opts.tf || tf;            // bridge vocabulary, e.g. '4h'
    this.asOf = opts.asOf || null;
    this.live = opts.live !== false;
    if (opts.digits != null) this.digits = opts.digits;
    this.bars = this._closed(bars || []);
    /* NAME THE TIMEFRAME, do not spread DEFAULTS.
       An explicit `entry`/`exit` beats the horizon map by design -- that is
       what lets a sweep pin its own parameters -- so passing DEFAULTS here
       forced the flat 20/10 onto every timeframe and made the map dead code.
       On 15m that is a five-hour channel measured at -0.0756 R rather than the
       3.3-day one that passed its gates. The tf is all this panel knows and
       all it should say. */
    this.sig = (this.bars.length >= minBars(this.rawTf))
      ? signalsAsOf(this.bars, { tf: this.rawTf, ...this._exitOpts() }) : null;
    this.render();
    if (this.live) this._maybeFetch();
  }

  /** Recompute from bars already in hand. Cheap: one pass, no walk-forward. */
  repaint(bars) {
    if (!this.symbol || !bars || bars.length < minBars(this.rawTf)) return;
    this.live = true;
    this.bars = this._closed(bars);
    this.sig = signalsAsOf(this.bars, { tf: this.rawTf, ...this._exitOpts() });
    this.render();
    this._maybeFetch();
  }

  /**
   * THE LAST BAR FROM /bars IS STILL FORMING, and the rule decides on a CLOSE.
   *
   * Acting on it is the live equivalent of look-ahead: the close moves, so the
   * trigger comparison `close > upper` can be true at 09:12 and false at 09:58,
   * and the panel would flip state inside one bar. tools/paper_trade.py and the
   * bridge's /signal both drop it; this used to not, which is why the panel and
   * /signal disagreed about which bar they were describing.
   *
   * Only when LIVE. A chart scrolled back hands over a slice that already ends
   * on a closed bar, and dropping one there would report the wrong bar.
   */
  _closed(bars) {
    return (this.live && bars.length > 1) ? bars.slice(0, -1) : bars;
  }

  /** The panel's own last-closed bar, in the string form /signal reports. */
  _barKey() {
    const b = this.bars[this.bars.length - 1];
    if (!b) return null;
    return new Date(b.t).toISOString().slice(0, 19).replace('T', ' ');
  }

  /**
   * Ask Python for the fill and the size. Throttled: /signal reads MT5 bars,
   * the open positions and the account, which measured 0.55s -- far too slow
   * for the 4s repaint tick. One fetch per new closed bar is all the answer can
   * change on anyway, with a 60s floor so a stalled bar still refreshes size
   * against moving equity.
   */
  _maybeFetch() {
    const key = this._barKey();
    if (!key || !this.symbol) return;
    const now = Date.now();
    const sameBar = this._fetchedBar === key;
    if (sameBar && this._fetchedAt && now - this._fetchedAt < 60_000) return;
    this._fetchedBar = key;
    this._fetchedAt = now;
    const want = `${this.symbol}|${this.rawTf}`;
    api.signalNow(this.symbol, this.rawTf)
      .then((doc) => {
        // a slower fetch for a symbol you have navigated away from must not
        // repaint the panel with someone else's numbers
        if (want !== `${this.symbol}|${this.rawTf}`) return;
        this.remote = doc;
        this.render();
      })
      .catch((err) => {
        if (want !== `${this.symbol}|${this.rawTf}`) return;
        this.remote = { error: String(err.message || err) };
        this.render();
      });
  }

  _px(v) {
    return Number.isFinite(v) ? v.toFixed(this.digits) : '—';
  }

  /** The cell this panel is standing in: instrument AND timeframe. */
  cell() {
    return `${this.symbol}|${this.rawTf}`;
  }

  /**
   * The structural trailing exit, which this panel now runs.
   *
   * NOT THE VALIDATED RULE ANY MORE, and the note under the levels is the only
   * thing that says so. It prints unconditionally for that reason: there is no
   * switch here and therefore no state in which the warning could be quiet.
   *
   * `structuralTrail` memoises on the bar's TIME, so the ~390 ms first walk over
   * a fresh window is paid once and every 4-second repaint after it costs about
   * 5 ms. That matters here in a way it did not in the replay: this panel
   * redraws on a timer.
   */
  _exitOpts() {
    if (!this.symbol || !this.bars || !this.bars.length) return {};
    try {
      const t = trailOption(this.cell(), this.rawTf);
      return t ? { exitTrail: t } : {};
    } catch { return {}; }
  }

  /**
   * A PIP IS TEN POINTS on every instrument here: fractional FX quotes five
   * decimals and the pip is the fourth, JPY quotes three and it is the second,
   * gold quotes two and it is the first. One rule covers all three.
   *
   * A MAGNITUDE, because every level in this list is ahead of the trade by
   * construction -- so its distance is always in the profitable direction, and
   * `price - entry` is NEGATIVE for a short. A minus sign against a profit is
   * the price direction leaking into a number about the account.
   */
  _pips(diff) {
    if (!Number.isFinite(diff)) return '';
    const pip = Math.pow(10, -(this.digits - 1));
    const v = Math.abs(diff) / pip;
    return `${v >= 100 ? Math.round(v) : v.toFixed(1)} pips`;
  }

  /**
   * What that move is worth at the SMALLEST position a broker accepts.
   *
   * `tick_value / tick_size` is what one lot is worth per unit of price -- a
   * property of the contract, needing no equity and no FX rate -- taken at 0.01
   * lots. A fixed unit, so every row on every instrument answers the same
   * question; scaling by a live position size would move the number whenever
   * equity did and make two charts incomparable.
   *
   * Empty when the contract is unknown, which is the honest degradation: the
   * row still shows the price and the distance.
   */
  _cash(diff) {
    const sp = this.spec;
    if (!sp || !Number.isFinite(diff)) return '';
    const ts = sp.tick_size || sp.point || 0;
    const tv = sp.tick_value || 0;
    if (!(ts > 0 && tv > 0)) return '';
    return `$${Math.round(Math.abs(diff) * (tv / ts) * 0.01)}`;
  }

  /**
   * A price with a small tag saying whether it is the level that binds.
   *
   * Returns the bare string when there is no tag, so a row that has nothing to
   * say about which exit is live stays a plain value rather than growing an
   * empty box -- an always-present badge that is sometimes blank reads as a
   * rendering fault.
   */
  _badged(text, tag) {
    if (!tag) return text;
    return el('span', {}, text, el('span', {
      class: 'rp-live' + (tag === 'LIVE' ? ' rp-live-on' : ''), text: tag,
    }));
  }

  render() {
    const r = this.root;
    if (!r) return;
    r.innerHTML = '';
    if (this.headRoot) {
      this.headRoot.textContent = this.symbol
        ? `${shortSymbol(this.symbol)} ${this.tf || ''}`.trim() : '—';
    }
    if (!this.sig) {
      r.appendChild(el('div', { class: 'rp-empty' },
        this.symbol ? `needs ${minBars(this.rawTf)}+ bars` : '—'));
      return;
    }
    if (this.asOf) r.appendChild(asOfBanner(this.asOf));

    const sig = this.sig;
    const p = sig.params;
    const ins = instruction(sig);
    const last = this.bars[sig.bars - 1];
    const price = last ? last.c : NaN;

    /* ---- the headline: what the rule says, and that it is simulated ---- */
    const head = el('div', { class: 'rp-head' });
    /* THE BADGE SAYS WHAT TO DO AND WHEN, in the same words the strategy
       replay uses. It used to say `LONG` / `SELL` / `CLOSE` -- the DIRECTION --
       which left the timing implicit, and the timing is the part that is easy
       to get wrong: a signal fires on a CLOSE and fills at the NEXT OPEN, so a
       badge reading `SELL` invites acting on a bar the rule has not finished
       reading. Two panels describing one rule should not use two vocabularies
       for it either.

       DIRECTION IS NOW CARRIED BY COLOUR ALONE -- `badgeClass` keys off the
       side, filled for something to act on and outlined for a position already
       held. That is the trade this file already made deliberately for the
       action/direction split; it is worth knowing it is now the only signal. */
    const ACT = { enter: 'ENTER AT NEXT OPEN', exit: 'CLOSE AT NEXT OPEN',
                  hold: 'IN POSITION', wait: 'NO SIGNAL' };
    const badge = el('span', { class: `rp-badge ${badgeClass(ins)}` },
      ACT[ins.action] || ACT.wait);
    head.appendChild(badge);
    /*
    /* THE SECOND LABEL: WHICH WAY, OR WHAT THE MARKET IS DOING INSTEAD.
     *
     * `IN POSITION` is a state and says nothing about direction, which is the
     * first thing a glance wants. The badge's COLOUR carries it, and colour
     * alone turned out not to be enough -- so the direction is written out.
     *
     * WHEN THERE IS NO POSITION there is no direction to write, and a blank
     * chip beside `NO SIGNAL` would be a box that means nothing. The market's
     * own regime goes there instead, from js/chart/regime.js -- the same
     * reading the segment renderer uses, computed from EMA separation, where
     * price sits in its range, and whether the range is expanding. It answers
     * the question a flat panel actually raises: not "which way am I" but "why
     * is nothing happening".
     *
     * THE TWO ARE STYLED APART ON PURPOSE. A direction is a fact about YOUR
     * trade and is filled; a regime is a claim about the MARKET and is
     * outlined. Reading `SIDEWAYS` as "I am flat" or `LONG` as "the market is
     * rising" would each be a different mistake, and one shared style is how a
     * panel invites both. */
    const dir = (sig.position && sig.position.side)
      || (sig.pending && sig.pending.side)
      || 0;
    if (dir) {
      head.appendChild(el('span', {
        class: `rp-state ${dir === LONG ? 'rp-state-long' : 'rp-state-short'}`,
      }, dir === LONG ? 'LONG' : 'SHORT'));
    } else {
      const reg = this.bars.length >= 60 ? regimeNow(this.bars) : null;
      const WORD = {
        [TRENDING_UP]: 'TRENDING UP',
        [TRENDING_DOWN]: 'TRENDING DOWN',
        [SIDEWAYS]: 'SIDEWAYS',
        [TRANSITION]: 'TRANSITION',
      };
      const word = reg && WORD[reg.regime];
      if (word) {
        head.appendChild(el('span', { class: 'rp-state rp-state-regime' }, word));
      }
    }
    r.appendChild(head);

    if (ins.note) r.appendChild(el('div', { class: 'rp-note' }, ins.note));

    /* ---- levels ---- */
    const rows = [];

    /* WHICH WAY, AND FROM WHERE. The badge above says what to DO -- "SHORT",
       "CLOSE" -- which is an action; these two say what is HELD. Without them
       the panel described a trade whose entry price appeared nowhere, so
       nothing on it could be checked against a broker ticket, and `open R`
       below had no anchor a reader could see.

       `side` carries the direction colour for the reason js/ui/strategyreplay.js
       gives: the state badge is coloured by direction already, but a reader
       scanning the rows should not have to look back up to it. */
    if (sig.position) {
      /* NO `side` ROW. It repeated what the badge's colour already says, and
         with the badge now reading IN POSITION rather than LONG the pair read
         as two different facts rather than one said twice. The direction is in
         the badge's colour, and in the geometry directly below: a stop ABOVE
         the entry is a short. */
      rows.push(['entry', this._px(sig.position.entryPrice),
        `${sig.position.side === LONG ? 'long' : 'short'} from a simulated `
        + `fill, bar ${sig.position.entryI + 1}`]);
    }

    /* WHICH EXIT IS LIVE. The trade now has THREE levels that can end it and
       only one can be reached first, so listing them as equals leaves the reader
       to work out which is actually holding the position -- which is the whole
       question, and the one thing a set of prices cannot answer by itself.

       For a long, price falls to whichever level is HIGHEST: the tightest binds
       and the rest are inert behind it. Early in a trend trade the channel is
       usually the loosest of the three, which is why it can look absurdly far
       away for the first few bars.

       THE CHART DRAWS ONLY THE WINNER -- one line, the effective exit. This is
       the one place all three are written down. */
    const stopNow = ins.stop != null ? ins.stop
      : (sig.position ? sig.position.stop : null);
    const trailNow = sig.position ? sig.position.trail : null;
    let liveStop = null;
    let liveExit = null;
    let liveTrail = null;
    if (sig.position && Number.isFinite(sig.exitLevel)) {
      const long = sig.position.side === LONG;
      const tighter = (a, b) => {
        if (!Number.isFinite(a)) return b;
        if (!Number.isFinite(b)) return a;
        return long ? Math.max(a, b) : Math.min(a, b);
      };
      const effective = tighter(sig.exitLevel, trailNow);
      if (ins.action === 'exit') {
        /* Already fired. Nothing is "waiting" any more: the exit is spent and
           the stop is the only thing still able to act, for the one bar until
           the close order fills. */
        liveExit = 'triggered';
        liveStop = 'until fill';
      } else if (Number.isFinite(stopNow)) {
        const exitBinds = long ? effective > stopNow : effective < stopNow;
        liveStop = exitBinds ? 'behind' : 'LIVE';
        const mark = (v) => (!Number.isFinite(v) ? null
          : (exitBinds && v === effective ? 'LIVE' : 'behind'));
        liveExit = mark(sig.exitLevel);
        liveTrail = mark(trailNow);
      }
    }

    /* THE STOP STAYS UP UNTIL THE FILL, including on the bar the exit fires.
       `instruction()` reports no stop for an 'exit' action, which is right about
       the DECISION and wrong about the RISK: the close order fills at the next
       open, and until it does the position is still open and the stop can still
       take it. */
    if (stopNow != null) {
      rows.push(['stop', this._badged(this._px(stopNow), liveStop),
        `fixed ${p.atrMult} ATR`]);
    }
    if (Number.isFinite(trailNow)) {
      /* WHERE IT IS, NOT WHAT IT PROMISES. The note carried "— RATCHETS, never
         retreats" and that half came off by request; the ratchet is still what
         the code does (js/chart/exittrail.js), it is just no longer asserted on
         the face of the panel. */
      rows.push(['trail', this._badged(this._px(trailNow), liveTrail),
        'nearest S/R or swing behind price']);
    }
    if (sig.exitLevel != null) {
      /* NO NOTE. It read "closes on a close above it — MOVES each bar" and came
         off by request. The line under the badge already says the exit moves,
         and the LIVE / behind badge on this row says whether this is the level
         that would fire -- which was the part the sentence could not tell you
         and the row can. */
      rows.push(['exit level', this._badged(this._px(sig.exitLevel), liveExit)]);
    }

    /* HOW LONG, AND HOW FAR. `open R` is the number the whole panel exists to
       make readable: 685 bars and +7.79 R is a different situation from 3 bars
       and +0.1, and neither is visible from a set of price levels. Both are
       SIMULATED, like everything else here -- the badge above says so. */
    if (sig.position) {
      rows.push(['bars held', String(sig.bars - 1 - sig.position.entryI),
        `since bar ${sig.position.entryI + 1} of ${sig.bars}`]);
      if (Number.isFinite(price) && sig.position.risk > 0) {
        const openR = (price - sig.position.entryPrice)
          * sig.position.side / sig.position.risk;
        rows.push(['open R', (openR >= 0 ? '+' : '') + openR.toFixed(2),
          'at the last closed price']);
      }
    }

    const i = sig.bars - 1;
    const up = sig.series.hi[i];
    const dn = sig.series.lo[i];
    const atr = sig.series.atr[i];
    if (Number.isFinite(up)) {
      rows.push([`upper ${p.entry}`, this._px(up),
        this._gap(price, up, atr, 'above')]);
    }
    if (Number.isFinite(dn)) {
      rows.push([`lower ${p.entry}`, this._px(dn),
        this._gap(price, dn, atr, 'below')]);
    }

    /* THE EXECUTION NUMBERS COME FROM PYTHON, via /signal. Only the fill and
       the size do, and only those: sizing needs an FX rate, and the browser's
       rate disagrees with the one the backtest used by enough to move a whole
       lot step. The geometry above is computed here because it is FX-free and
       exactly reproducible; anything that depends on money is not. */
    const live = this.remote;
    const fresh = live && !live.error && live.bar_time === this._barKey();
    if (live && live.error) {
      rows.push(['size', 'unavailable', live.error]);
    } else if (fresh && live.action !== 'hold') {
      if (live.est_entry != null) {
        rows.push(['est. fill', '~' + this._px(live.est_entry),
          'next bar open, not known yet']);
      }
      if (live.stop_points != null) {
        rows.push(['stop distance',
          `${this._px(live.stop_distance)}  ${Math.round(live.stop_points)} pts`,
          `${(live.params && live.params.atr_mult) || ''} ATR`]);
      }
      if (live.lots != null) {
        rows.push(['size', `${live.lots.toFixed(2)} lots`,
          live.lots > 0
            ? `risks ${(live.risk_acct || 0).toFixed(2)} of ${
              (live.equity || 0).toFixed(0)}`
            : 'ROUNDS TO ZERO — see below']);
      }
    }

    const tbl = el('div', { class: 'rp-rows' });
    for (const [k, v, note] of rows) {
      const row = el('div', { class: 'rp-row' });
      row.appendChild(el('span', { class: 'rp-k' }, k));
      /* `v` may be an element -- a badged price, or the coloured side -- so it
         is passed as a CHILD rather than through `text`, which would stringify
         it to [object HTMLSpanElement]. */
      row.appendChild(el('span', { class: 'rp-v mono' }, v));
      row.appendChild(el('span', { class: 'rp-n' }, note));
      tbl.appendChild(row);
    }
    r.appendChild(tbl);

    /* An account that cannot take the trade is the case a UI most wants to
       paper over, so it gets the arithmetic rather than a bare 0.00. */
    if (fresh && live.lots === 0 && live.min_lot_risk_pct != null) {
      r.appendChild(tip(el('div', { class: 'rp-untradeable' },
        `NOT TRADEABLE at ${live.params.risk_pct}% risk`),
      'The minimum lot is larger than the risk budget',
      `The smallest position the broker accepts (${live.min_lot_min} lots) `
      + `risks ${live.min_lot_risk_acct}, which is ${live.min_lot_risk_pct}% `
      + `of equity — ${(live.min_lot_risk_pct / live.params.risk_pct).toFixed(1)}x `
      + 'the fraction the backtest measured. Gold ATR has roughly tripled since '
      + 'the sample, so the 2-ATR stop is far wider in price than it was. This '
      + 'is arithmetic, not a suggestion to raise risk: nothing in runs/ was '
      + `measured at ${live.min_lot_risk_pct}% per trade.`));
    }

    /* ---- what is in the way ----
     *
     * COMPUTED HERE AND NOWHERE ELSE. The chart draws these too, and it reads
     * them off `this.levels` rather than deriving its own: two independent
     * derivations of "the next resistance" is how a panel ends up naming a
     * price the chart does not draw a line at. `paintRuleSignal` in js/main.js
     * runs AFTER `update()` for that reason.
     *
     * NOT TAKE-PROFITS -- the rule has none, and the heading says so. They are
     * the levels price has to get through, named by what each one IS, which is
     * the thing a bare R multiple could never say.
     */
    this.levels = [];
    this.levelsCleared = false;
    if (sig.position || (sig.pending && sig.pending.side)) {
      const held2 = sig.position;
      const anchor2 = held2 ? held2.entryPrice : sig.pending.signalPrice;
      const at = held2 ? held2.entryI : sig.pending.signalI;
      const sd = (held2 ? held2.side : sig.pending.side) > 0 ? 1 : -1;
      /* MORE THAN ARE SHOWN, because some are about to be discarded. */
      const found = displayLevels(this.bars, {
        side: sd, from: anchor2, upto: at, tf: this.rawTf, max: 8,
      });
      /* A LEVEL PRICE HAS ALREADY REACHED IS SPENT, and drawing it is worse
         than drawing nothing: it sits behind the trade looking like something
         still to come. The levels are chosen once, at the signal bar, and then
         price walks through them -- so each is checked against every bar SINCE
         that decision, and the first one still ahead becomes TP.
         `high`/`low` rather than the close: a target is filled by a touch. */
      const reached = (price) => {
        for (let k = at; k < this.bars.length; k++) {
          const b = this.bars[k];
          if (sd > 0 ? b.h >= price : b.l <= price) return true;
        }
        return false;
      };
      const ahead2 = found.filter((lv) => !reached(lv.price));

      /* THREE, NOT FOUR. The fourth was removed by request and the cap is
         `displayLevels`'s own default now -- this slice is the belt to that
         braces, kept because the panel is the surface that would show a fourth
         if the default ever moved. */
      this.levels = ahead2.slice(0, 3)
        /* NUMBERED FROM ONE, with no bare `TP`. The first level used to be
           called just `TP` and the rest `TP1`, `TP2` -- which reads as though
           the bare one were the target and the numbered ones were something
           else, when they are four of the same kind of thing. `TP1` is the
           first thing in the way; there is no zeroth. */
        .map((lv, k) => ({ ...lv, key: `TP${k + 1}` }));
      /* SPENT IS NOT THE SAME AS NONE FOUND, and the panel has to be able to
         say which. A trade that has run through everything that was in front of
         it is in clear air -- the best thing that can happen to a trend trade
         and the reason this rule has no take-profit -- while a trade the
         detectors found nothing for is a different situation entirely. Showing
         an empty space for both would make the good case look broken. */
      this.levelsCleared = found.length > 0 && ahead2.length === 0;
      this.levelsSide = sd;
    }
    if (this.levels.length) {
      /* NO HEADING. It read "in the way — not targets · AUD at 0.01 lots" and
         was removed by request, taking the currency note with it: the money
         column is the account's currency at the broker minimum, and the panel
         no longer says so anywhere. The rows are TP1/TP2/TP3 and are the only
         numbered pair-and-price list here, so they do not need announcing.

         A RULE INSTEAD OF A HEADING. With the words gone the levels ran
         straight on from `open R` and `upper/lower`, which are readings about
         the trade rather than prices ahead of it. The hairline is the same one
         the cell verdict below already uses -- see `.rp-rule` -- so the panel
         has one divider, used twice, rather than a new decoration. */
      const bt = el('div', { class: 'rp-rows rp-rule' });
      /* THE DISTANCE AND THE MONEY LIVE HERE NOW. They were on the chart, in a
         tag over the newest bars, and the chart only marks the price on the
         axis. This is the list that has room for them.

         BOTH MEASURED FROM THE FILL, never from the current price: a level's
         defining pair is how far it is from where you got in and what it pays
         when it gets there, and both are fixed for the life of the trade. An
         earlier version mixed the two anchors in one label -- distance from
         now, money from entry -- which nothing about the label revealed. */
      const from = sig.position ? sig.position.entryPrice
        : (sig.pending ? sig.pending.signalPrice : NaN);
      for (const lv of this.levels) {
        const row = el('div', { class: 'rp-row' });
        row.appendChild(el('span', { class: 'rp-k' }, lv.key));
        row.appendChild(el('span', { class: 'rp-v mono' }, this._px(lv.price)));
        const bits = [];
        const away = this._pips(lv.price - from);
        if (away) bits.push(away);
        const cash = this._cash(lv.price - from);
        if (cash) bits.push(cash);
        /* DISTANCE AND MONEY ONLY. The row used to end with what the level IS
           -- "demand zone, fresh", "LH high" -- and that was removed by
           request. The kind still decides WHICH prices are listed; it is just
           no longer printed beside them. */
        row.appendChild(el('span', { class: 'rp-n' }, bits.join('  ')));
        bt.appendChild(row);
      }
      r.appendChild(bt);
    } else if (this.levelsCleared) {
      r.appendChild(el('div', { class: 'rp-clear' },
        `all reached — clear air ${this.levelsSide > 0 ? 'above' : 'below'}`));
    }

    /* THE VERDICT, LAST. For the rule ACTUALLY RUNNING here, which on a
       horizon-matched timeframe is not the one runs/ measured. */
    const rawTf2 = this.rawTf || this.tf;
    const rec = measuredFor(this.symbol, rawTf2, strategyForTf(rawTf2));
    /* THE VERDICT IS ABOUT THE CELL, NOT ABOUT WHAT IS RUNNING. Those came
       apart when the structural trail was added -- `VALIDATED` was earned on
       the channel exit alone -- and nothing on this panel says so any more.
       That is a deliberate call, made twice; the record of what the exit
       actually is lives in js/chart/trailmode.js and logs/exit_trail_eval.txt. */
    r.appendChild(el('div', { class: 'rp-cell' },
      el('span', { class: 'rp-cell-k' }, 'this cell'),
      el('span', { class: 'rp-cell-v' },
        VERDICT_TEXT[rec.verdict] || rec.verdict)));

    /* NO STATISTICS ON THE FACE OF THE PANEL.
     *
     * This used to print the cell's in-sample and out-of-sample avg R, PF,
     * trade count and control percentile. Accurate, and the wrong thing to put
     * in front of someone deciding whether to take a trade: "avg +0.1649 R, 35
     * tr, PF 1.28, pct 68.3" is research notation, and a reader who has to
     * decode it is not being helped by it. The verdict chip above already says
     * the same thing in words -- TOO FEW TRADES, NO EDGE FOUND, VALIDATED --
     * and the numbers behind it are one hover away in its tooltip, so nothing
     * is hidden, it is just no longer shouted.
     *
     * The per-cell figures still live in js/chart/measured.js and runs/*.csv.
     */
  }

  /** Distance from price to a channel edge, in price and in ATR. */
  _gap(price, level, atr, dir) {
    if (!Number.isFinite(price) || !Number.isFinite(level)) return '';
    const d = Math.abs(price - level);
    const inAtr = (Number.isFinite(atr) && atr > 0)
      ? ` (${(d / atr).toFixed(2)} ATR)` : '';
    const beyond = dir === 'above' ? price > level : price < level;
    if (beyond) return `price is already ${dir} it`;
    return `${px(d, this.digits)} away${inAtr}`;
  }
}
