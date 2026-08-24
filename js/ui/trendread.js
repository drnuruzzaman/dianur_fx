/* trendread.js — the Trend read panel.
 *
 * One verdict per instrument, built from three timeframes at once. It answers
 * the question you actually ask before placing a trade: is this trending, do
 * the timeframes agree, and where would I be wrong?
 *
 * It is a VIEWER and computes no market logic of its own:
 *   js/chart/regime.js     trending_up / trending_down / range-bound / transition
 *   js/chart/structure.js  the HH/HL/LH/LL swing sequence
 *   js/chart/read.js       the composition: score, invalidation, R:R
 *
 * regime.js and structure.js are ports of sim/tl/*.py compared bar-for-bar in
 * tests/test_structure_parity.py, so those words are the words the backtest
 * reads. read.js has no Python mirror yet and is flagged as such in its own
 * header — its weights are an opinion, not a measurement.
 *
 * The read is DYNAMIC in both axes: it follows the focused chart's instrument
 * and timeframe, reading the chart's own frame plus the two above it. An M15
 * chart is read as 15m/1h/4h and an H1 chart as 1h/4h/1d — the context ladder
 * rises with you rather than staying pinned to the scalping frames.
 */

import { TF_LABEL, el } from '../util.js';
import * as regime from '../chart/regime.js';
import * as structure from '../chart/structure.js';
import { BEAR, BULL, NEUTRAL, WATCH, computeRead } from '../chart/read.js';

/* How many frames to read: the chart's own, plus this many above it. Three is
   the useful number — execution, context, and the frame that decides whether
   the context is itself noise. */
export const LADDER = 3;

/* The context ladder deliberately SKIPS 30m. Context earns a row only if it can
   disagree with the row below it, and adjacent frames rarely do: a 30m read
   beside a 15m read is close to the same series sampled twice, which pads the
   panel to three rows without adding a third opinion. Each step here is a 3-4x
   jump, the spacing at which the frames actually diverge.

   W1 is not a context frame either: at 800 bars a weekly series is fifteen
   years, the regime windows (EMA-50, 40-bar range) span decades, and "trending
   up since 2011" is not a fact anyone trades on. It is still read when it is
   the chart you are looking at — it just never appears as somebody else's
   context. */
const CONTEXT = ['1m', '5m', '15m', '1h', '4h', '1d'];

const RANK = { '1m': 0, '5m': 1, '15m': 2, '30m': 2.5, '1h': 3, '4h': 4, '1d': 5, '1w': 6 };

/**
 * The frames to read for a chart on `tf`: its own first, then upward.
 *
 *   5m  -> 5m  15m 1h
 *   15m -> 15m 1h  4h
 *   1h  -> 1h  4h  1d
 *
 * Near the top there is nothing above to add, so the read EXTENDS DOWNWARD
 * rather than silently shrinking — a D1 chart still gets three frames and
 * therefore still has a quorum. A two-frame panel that looked identical to a
 * three-frame one would quietly weaken every headline.
 */
export function readTfs(tf, ladder = LADDER) {
  const own = RANK[tf] === undefined ? RANK['15m'] : RANK[tf];
  const out = [tf in RANK ? tf : '15m'];
  for (const c of CONTEXT) {
    if (out.length >= ladder) break;
    if (RANK[c] > own) out.push(c);
  }
  for (let k = CONTEXT.length - 1; k >= 0 && out.length < ladder; k--) {
    if (RANK[CONTEXT[k]] < RANK[out[0]]) out.unshift(CONTEXT[k]);
  }
  return out;
}

const REGIME_TEXT = {
  [regime.TRENDING_UP]: 'Trending up',
  [regime.TRENDING_DOWN]: 'Trending down',
  [regime.SIDEWAYS]: 'Range-bound',
  [regime.TRANSITION]: 'Transition',
};

const REGIME_MARK = {
  [regime.TRENDING_UP]: '▲',
  [regime.TRENDING_DOWN]: '▼',
  [regime.SIDEWAYS]: '◆',
  [regime.TRANSITION]: '◆',
};

const REGIME_CLS = {
  [regime.TRENDING_UP]: 'up',
  [regime.TRENDING_DOWN]: 'down',
  [regime.SIDEWAYS]: 'dim',
  [regime.TRANSITION]: 'dim',
};

const BADGE_CLS = { [BULL]: 'bull', [BEAR]: 'bear', [WATCH]: 'watch', [NEUTRAL]: 'neutral' };

/* Brokers suffix their tickers (Pepperstone serves EURUSD.a). The suffix is
   load-bearing everywhere it identifies an instrument -- bridge calls, drawing
   storage, spec lookups -- so it is stripped for DISPLAY only, here, and never
   from the symbol the rest of the app passes around. */
export function shortSymbol(sym) {
  return typeof sym === 'string' ? sym.replace(/\.[a-z]+$/i, '') : sym;
}

export class TrendRead {
  constructor(root, headRoot) {
    this.root = root;
    this.headRoot = headRoot;
    this.symbol = null;
    this.tfs = [];
    this.reads = [];
    this.read = null;
    this.digits = 3;
  }

  /**
   * `series` is a Map of tf -> bars array. Missing timeframes render as pending
   * rather than being skipped, so the panel never silently shows a two-frame
   * verdict as if it were three.
   */
  update(symbol, series, tfs, { lines = [], digits = 3, execTf = null } = {}) {
    this.symbol = symbol;
    this.tfs = tfs;
    /* The chart's OWN frame, passed in explicitly rather than assumed to be
       tfs[0]. Near the top of the ladder the read extends downward, so a 4h
       chart reads 1h/4h/1d and tfs[0] is 1h -- taking price, ATR and the
       heading from tfs[0] meant an H4 chart was labelled H1 and, worse, had its
       invalidation priced off the 1h series. */
    this.execTf = execTf || tfs[0];
    this.digits = digits;
    this.reads = tfs.map((tf) => {
      const bars = series.get(tf);
      if (!bars || bars.length < 60) return { tf, regime: null, structure: null };
      return { tf, regime: regime.latest(bars), structure: structure.latest(bars) };
    });

    /* Price and ATR come from the EXECUTION frame — the one you are looking at
       and would fill on — not from the highest frame read. */
    const exec = series.get(this.execTf) || [];
    const close = exec.length ? exec[exec.length - 1].c : NaN;
    const execRead = this.reads.find((r) => r.tf === this.execTf);
    const atr = execRead && execRead.regime ? execRead.regime.atr : NaN;

    this._lines = lines;          // kept so repaint() can reuse them
    this.read = computeRead(this.reads, lines, close, atr);
    this.render();
  }

  /**
   * Recompute from bars already in hand — no fetch, no trendline walk.
   *
   * The execution frame is the only one that moves between bar closes, so only
   * it is re-read; the context frames keep the reads from the last full update.
   * Lines are reused too: refitting them costs ~19ms and they cannot change
   * until a bar closes, which is what `onNewBar` is for.
   */
  repaint(execBars) {
    if (!this.symbol || !this.tfs.length || !execBars || execBars.length < 60) return;
    const k = this.reads.findIndex((r) => r.tf === this.execTf);
    if (k < 0) return;
    this.reads[k] = {
      tf: this.execTf,
      regime: regime.latest(execBars),
      structure: structure.latest(execBars),
    };
    const close = execBars[execBars.length - 1].c;
    const atr = this.reads[k].regime ? this.reads[k].regime.atr : NaN;
    this.read = computeRead(this.reads, this._lines || [], close, atr);
    this.render();
  }

  render() {
    const r = this.root;
    if (!r) return;
    r.textContent = '';

    if (this.headRoot) {
      /* The chart's own frame, not the whole ladder: the ladder is already
         spelled out one row per line below, and the heading is a control now —
         it has to name the thing clicking it will change. */
      this.headRoot.textContent = this.symbol
        ? `${shortSymbol(this.symbol)} · ${TF_LABEL[this.execTf] || this.execTf}`
        : '—';
    }
    if (!this.symbol || !this.read) {
      r.appendChild(el('div', { class: 'tr-empty dim' }, '—'));
      return;
    }
    const v = this.read;

    /* ---- headline: the call on the left, its conviction on the right ---- */
    const head = el('div', { class: 'tr-head' });
    head.appendChild(el('b', { class: `tr-verdict tr-${BADGE_CLS[v.badge]}` },
                        v.loading ? 'READING…' : v.badge + (v.arrow ? ` ${v.arrow}` : '')));
    head.appendChild(el('span', {
      class: `tr-score mono ${v.score >= 45 ? '' : 'dim'}`,
      title: 'conviction 0-100 — regime agreement, structure, geometry',
    }, v.loading ? '—' : String(v.score)));
    r.appendChild(head);

    /* The note carries WHY, and says out loud when geometry capped the score:
       a reader who sees 35 with no explanation assumes a weak read, when in
       fact the read was strong and the trade is simply badly placed. */
    const note = [v.theme, v.session, v.capped].filter(Boolean).join(' · ');
    r.appendChild(el('div', { class: 'tr-note dim' }, note));

    /* ---- per-timeframe evidence ---------------------------------------- */
    const rows = el('div', { class: 'tr-rows' });
    for (const read of this.reads) {
      const row = el('div', { class: 'tr-row' });
      row.appendChild(el('span', { class: 'tr-tf mono' }, TF_LABEL[read.tf] || read.tf));
      if (!read.regime) {
        row.appendChild(el('span', { class: 'tr-state dim' }, 'loading…'));
      } else {
        const k = read.regime.regime;
        const state = el('span', { class: `tr-state ${REGIME_CLS[k] || 'dim'}` });
        state.appendChild(el('i', { class: 'tr-mark' }, REGIME_MARK[k] || '·'));
        state.appendChild(document.createTextNode(REGIME_TEXT[k] || k));
        row.appendChild(state);
      }
      rows.appendChild(row);
    }
    r.appendChild(rows);

    /* ---- the numbers that decide whether the read is actionable -------- */
    const facts = el('div', { class: 'tr-facts' });
    const px = (x) => (Number.isFinite(x) ? x.toFixed(this.digits) : '—');

    facts.appendChild(this._fact('Structure', v.structText,
      v.structText === 'HH + HL' ? 'up' : v.structText === 'LH + LL' ? 'down' : 'dim',
      'from the highest frame read — an HH+HL on 5m inside an H1 downtrend is a retracement'));

    facts.appendChild(this._fact('Invalidation', px(v.invalidation), 'mono',
      'nearest level on the wrong side — the read is simply wrong through here'));

    /* R:R is coloured against 1:1, not against a preference: below 1 the first
       thing in the way is nearer than the stop, which is a fact about the chart
       rather than an opinion about the trade. */
    const rrTxt = Number.isFinite(v.rr) ? `${v.rr.toFixed(2)}:1` : '—';
    facts.appendChild(this._fact('R:R to first zone', rrTxt,
      !Number.isFinite(v.rr) ? 'dim' : v.rr >= 1 ? 'up mono' : 'down mono',
      'reward to the first opposing line over risk to invalidation'));

    r.appendChild(facts);
  }

  _fact(label, value, cls, title) {
    const row = el('div', { class: 'tr-fact', title: title || '' });
    row.appendChild(el('span', { class: 'dim' }, label));
    row.appendChild(el('span', { class: `tr-val ${cls || ''}` }, value));
    return row;
  }
}
