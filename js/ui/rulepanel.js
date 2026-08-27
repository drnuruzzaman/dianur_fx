/* rulepanel.js — the validated rule's state, beside the price it reads.
 *
 * A VIEWER over js/chart/donchian.js. It computes nothing itself: the channel
 * levels, the position and the exit level all come from `signalsAsOf`, which is
 * the same function tests/test_donchian_parity.py holds against
 * sim/strategies/donchian.py signal for signal. A panel that recomputed the
 * channel with its own rolling max would be a third implementation of a
 * quantity this project has already had diverge once.
 *
 * WHAT THE STATE MEANS, and it is not what a reader will assume. `signalsAsOf`
 * REPLAYS the rule from the first loaded bar. So "LONG" means *the rule would
 * be long, had it been followed across this window* -- it is not your broker
 * position, and nothing here reads your account. The badge says SIMULATED for
 * that reason, and the replay length is shown: load a different number of bars
 * and the simulated position can legitimately differ, because the entry that
 * put the rule in this trade may sit before the left edge.
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
import { DEFAULTS, LONG, instruction, signalsAsOf } from '../chart/donchian.js';
import { VERDICT_TEXT, measuredFor } from '../chart/measured.js';
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

/* EVERY CELL SHOWS ITS OWN RECORD, from js/chart/measured.js.
 *
 * This replaced two earlier mechanisms, and the reason matters. First a
 * hand-written VALIDATED = {XAUUSD, 4h} with a red NOT VALIDATED badge on
 * everything else: true, but nearly useless, because it said a cell had not
 * passed without saying what it HAD done. USDJPY 30m -- which beat all sixty
 * of its own time-shifted controls -- wore the same badge as gold 5m, which
 * lost 0.21 R and came last against every control it ran. Those are not the
 * same finding and must not read the same.
 *
 * Then that list was widened by hand to every timeframe on both symbols, which
 * removed the badge by asserting something the runs contradict. Showing each
 * cell its own numbers settles both problems at once: no timeframe is fenced
 * off, and nothing is claimed that the data does not say.
 *
 * The figures are GENERATED from runs/*.csv by tools/export_measured.py, so
 * the panel cannot quote a number the run disagrees with. Re-run it after a
 * sweep or the panel will quietly go stale.
 */
const VERDICT_CLS = {
  validated: 'rp-v-good',
  failed_oos: 'rp-v-bad',
  no_edge: 'rp-v-bad',
  undersampled: 'rp-v-unknown',
  pending_oos: 'rp-v-partial',
  unmeasured: 'rp-v-unknown',
};

/* Why each verdict says what it says. `undersampled` and `unmeasured` are
   deliberately NOT phrased as failures: an unanswered question and a negative
   answer are different things, and collapsing them is how a cell nobody has
   tested ends up looking disproven. */
const VERDICT_WHY = {
  validated: ['Passed in sample AND out of sample',
    'All four gates in both eras: 200+ trades, beat its own time-shifted '
    + 'controls at the 95th percentile, profitable, and an effect above the '
    + '0.05 R floor. It is the only cell that has done that.'],
  failed_oos: ['Looked real, then did not hold',
    'Passed every gate in sample and failed out of sample. This is the '
    + 'category an in-sample-only report would have handed you as a discovery, '
    + 'which is the whole reason the out-of-sample run exists.'],
  no_edge: ['No edge found on this cell',
    'Failed in sample -- against its own time-shifted controls, on '
    + 'profitability, or on effect size. The rule computes fine here; it just '
    + 'does not pay here.'],
  undersampled: ['Not enough trades to judge',
    'Under the 200-trade floor, so the question was not answerable. That is '
    + 'NOT a negative result: the avg R can look attractive and still be '
    + 'indistinguishable from zero at this sample size.'],
  pending_oos: ['In sample only -- not yet confirmed',
    'Cleared the gates in sample and has not been run out of sample. USDJPY 1h '
    + 'sat in exactly this state until its out-of-sample run came back '
    + 'negative.'],
  unmeasured: ['Never measured',
    'No run in runs/ covers this symbol and timeframe. Nothing is claimed '
    + 'about it in either direction.'],
};

/**
 * The cell's numbers as a SENTENCE, for the verdict tooltip.
 *
 * The figures used to sit on the face of the panel as a table of avg R, PF and
 * control percentile. They are still here, because hiding the evidence behind
 * a one-word verdict would be worse than showing too much of it -- but they
 * are written out in words, on hover, where someone who wants them can read
 * them and someone deciding on a trade is not made to parse them first.
 */
function measuredSentence(cell) {
  const say = (m, era) => {
    if (!m) return `Out of sample: not run yet.`;
    const dir = m.avgR >= 0 ? 'made' : 'lost';
    return `${era}: ${m.trades} trades over ${m.span.replace('..', ' to ')}, `
      + `${dir} ${Math.abs(m.avgR).toFixed(4)} R per trade on average `
      + `(profit factor ${m.pf.toFixed(2)}). It scored better than `
      + `${m.pct.toFixed(0)}% of 60 shuffled copies of itself.`;
  };
  if (!cell.is && !cell.oos) return '';
  const bits = [];
  if (cell.is) bits.push(say(cell.is, 'In sample'));
  bits.push(say(cell.oos, 'Out of sample'));
  return '\n\nTHE NUMBERS. ' + bits.join(' ');
}

/* Enough bars for the 20-bar channel, the 14-bar ATR and a couple of trades to
   exist. Below this the panel says so instead of drawing a confident nothing. */
const MIN_BARS = 60;

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
    this.sig = (this.bars.length >= MIN_BARS)
      ? signalsAsOf(this.bars, DEFAULTS) : null;
    this.render();
    if (this.live) this._maybeFetch();
  }

  /** Recompute from bars already in hand. Cheap: one pass, no walk-forward. */
  repaint(bars) {
    if (!this.symbol || !bars || bars.length < MIN_BARS) return;
    this.live = true;
    this.bars = this._closed(bars);
    this.sig = signalsAsOf(this.bars, DEFAULTS);
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
        this.symbol ? `needs ${MIN_BARS}+ bars` : '—'));
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
    const badge = el('span', { class: `rp-badge ${badgeClass(ins)}` },
      ins.action === 'enter' ? ins.side
        : ins.action === 'exit' ? 'CLOSE'
          : ins.action === 'hold' ? ins.side : 'NO SIGNAL');
    head.appendChild(badge);
    const cell = measuredFor(this.symbol, this.rawTf || this.tf);
    const [why, whyBody] = VERDICT_WHY[cell.verdict] || VERDICT_WHY.unmeasured;
    head.appendChild(tip(
      el('span', { class: `rp-verdict ${VERDICT_CLS[cell.verdict]}` },
        VERDICT_TEXT[cell.verdict] || cell.verdict),
      why, whyBody + measuredSentence(cell)));
    head.appendChild(tip(el('span', { class: 'rp-sim' }, 'SIMULATED'),
      'Not your broker position',
      'This is what the rule WOULD be holding, replayed over the '
      + `${sig.bars} bars loaded on this chart. It does not read your account, `
      + 'and it cannot place an order. Load a different number of bars and this '
      + 'can legitimately change, because the entry that opened the trade may '
      + 'sit before the left edge of the window.'));
    r.appendChild(head);

    if (ins.note) r.appendChild(el('div', { class: 'rp-note' }, ins.note));

    /* ---- levels ---- */
    const rows = [];
    if (ins.stop != null) {
      rows.push(['stop', this._px(ins.stop),
        `fixed at entry, ${p.atrMult} ATR`]);
    }
    if (sig.exitLevel != null) {
      const side = sig.position && sig.position.side === LONG;
      rows.push(['exit level', this._px(sig.exitLevel),
        `closes on a close ${side ? 'below' : 'above'} it — MOVES each bar`]);
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

    /* ---- no take-profit, said rather than implied by absence ---- */
    r.appendChild(tip(el('div', { class: 'rp-notp' }, 'no take-profit'),
      'The rule does not take profit',
      'It leaves on the stop or on a close back through the '
      + `${p.exit}-bar channel, and that level moves every bar. Capping the `
      + 'winners was measured: a 1R take-profit lifts the win rate to 49% and '
      + 'turns +43.7 net R into -2.1. The few trades that run for weeks are '
      + 'what makes the arithmetic close at a 36% win rate.'));

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
