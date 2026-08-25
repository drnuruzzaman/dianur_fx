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

import { TF_LABEL, el, stamp } from '../util.js';
import { atrSeries } from '../chart/tlengine.js';
import { tip } from './tips.js';
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

/* Plain-language glosses. Kept as data next to the words they explain, rather
   than inline at each render site, so the panel's wording and its explanation
   cannot drift apart. */
const VERDICT_TIP = {
  [BULL]: ['The overall call is UP.',
    'Most of the timeframes below agree price is working higher, and the '
    + 'swing structure backs it. This is a lean, not a trade: it says which '
    + 'side of the market has the easier path right now.'],
  [BEAR]: ['The overall call is DOWN.',
    'Most of the timeframes below agree price is working lower, and the '
    + 'swing structure backs it. This is a lean, not a trade: it says which '
    + 'side of the market has the easier path right now.'],
  [WATCH]: ['Something is changing.',
    'The timeframes disagree, or the structure has just broken the other way. '
    + 'A move may be starting, but there is not enough agreement yet to call a '
    + 'side.'],
  [NEUTRAL]: ['No call.',
    'The timeframes do not line up and the structure is not making progress '
    + 'either way. Ranging conditions, or simply not enough evidence yet.'],
};

const REGIME_TIP = {
  [regime.TRENDING_UP]: 'Highs and lows are both moving up, and price is '
    + 'making real progress rather than chopping about.',
  [regime.TRENDING_DOWN]: 'Highs and lows are both moving down, and price is '
    + 'making real progress rather than chopping about.',
  [regime.SIDEWAYS]: 'Price is rotating between the same two areas. Moves die '
    + 'at the edges instead of continuing.',
  [regime.TRANSITION]: 'Between states. The old direction has stopped working '
    + 'but a new one has not established itself.',
};

const BADGE_CLS = { [BULL]: 'bull', [BEAR]: 'bear', [WATCH]: 'watch', [NEUTRAL]: 'neutral' };

/* Brokers suffix their tickers (Pepperstone serves EURUSD.a). The suffix is
   load-bearing everywhere it identifies an instrument -- bridge calls, drawing
   storage, spec lookups -- so it is stripped for DISPLAY only, here, and never
   from the symbol the rest of the app passes around. */
export function shortSymbol(sym) {
  return typeof sym === 'string' ? sym.replace(/\.[a-z]+$/i, '') : sym;
}

/**
 * ATR on the execution frame, for the noise floor in `geometry()`.
 *
 * This used to read `regime.atr`, WHICH DOES NOT EXIST -- `regime.latest()`
 * returns regime, direction, rangePos, energy and emaSepAtr, and nothing else.
 * So the value passed in was `undefined`, and the guard it feeds reads
 * `atr > 0 && risk < atr * 0.1`, which is simply false when atr is undefined.
 * The floor never fired, in any read, ever.
 *
 * What that produced was the panel quoting R:R off invalidations sitting a hair
 * from spot: measured on XAUUSD 4h, an invalidation 2.24 away with ATR 41.05 --
 * a stop 0.05 ATR wide -- reported as 18.2:1. Not a good trade found by the
 * engine, an arithmetic artefact of dividing by almost zero, and exactly the
 * case the floor was written to suppress.
 */
function execAtr(bars) {
  if (!bars || bars.length < 20) return NaN;
  try {
    const a = atrSeries(bars, 14);
    const v = a[a.length - 1];
    return Number.isFinite(v) && v > 0 ? v : NaN;
  } catch { return NaN; }
}

/**
 * The "this is not now" bar.
 *
 * Scrolled back, the chart re-runs every detector as of the bar at its right
 * edge, and these panels follow it. A reader glancing at a BULL 76 has no way
 * to tell whether that is today's call or one from three weeks ago, and the
 * panel looks identical either way -- so it says so, in the one place the eye
 * lands first, and the row is styled as a warning rather than as data.
 */
export function asOfBanner(ms) {
  const b = el('div', { class: 'panel-asof' });
  b.appendChild(el('b', {}, 'AS OF'));
  b.appendChild(el('span', { class: 'mono' }, stamp(ms)));
  return tip(b, 'This read is not live',
    'The chart is scrolled back, so every number in this panel was computed on '
    + 'the bar at its right edge, knowing nothing about what came after. That '
    + 'is the point: it is what the engine WOULD have told you standing there, '
    + 'which is the only way to check whether it was any good.',
    'Scroll to the right edge to return to live.');
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
  update(symbol, series, tfs, { lines = [], digits = 3, execTf = null,
                               asOf = null } = {}) {
    this.symbol = symbol;
    this.tfs = tfs;
    /* Non-null when the chart is scrolled into history: the ms of the bar the
       whole read was computed on. See render() -- a historical read that does
       not SAY it is historical is worse than no panel at all. */
    this.asOf = asOf;
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
    const atr = execAtr(exec);

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
    const atr = execAtr(execBars);
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
    if (this.asOf) r.appendChild(asOfBanner(this.asOf));
    const v = this.read;

    /* ---- headline: the call on the left, its conviction on the right ---- */
    const head = el('div', { class: 'tr-head' });
    const vt = VERDICT_TIP[v.badge] || VERDICT_TIP[NEUTRAL];
    head.appendChild(tip(
      el('b', { class: `tr-verdict tr-${BADGE_CLS[v.badge]}` },
         v.loading ? 'READING…' : v.badge + (v.arrow ? ` ${v.arrow}` : '')),
      `Trend read: ${v.badge}`, vt[1],
      `${vt[0]} The rows below show which timeframes agree.`));

    head.appendChild(tip(
      el('span', { class: `tr-score mono ${v.score >= 45 ? '' : 'dim'}` },
         v.loading ? '—' : String(v.score)),
      'Conviction', 'How much the evidence agrees, from 0 to 100. Three things '
      + 'feed it: whether the timeframes tell the same story, whether the swing '
      + 'structure confirms it, and whether price is well placed against the '
      + 'lines. A high number does not promise a big move — it means the read '
      + 'is well supported.',
      /* The capped case needs its own sentence, and used to get the wrong one.
         A capped score is 35 because the trade is badly PLACED, not because the
         evidence is thin -- often the evidence is excellent. Reading "thin
         evidence" off a capped 35 is the opposite of what happened. */
      v.loading ? 'Still reading…'
        : v.capped
          ? `Held at ${v.score}. The read itself may be strong — what is weak `
            + 'is the placement: the first level in the way is nearer than the '
            + 'invalidation. See Risk : reward below.'
          : v.score >= 45 ? `${v.score} — well supported.`
            : `${v.score} — under 45. The read exists, but the evidence behind `
              + 'it is thin.'));
    r.appendChild(head);

    /* The note carries WHY, and says out loud when geometry capped the score:
       a reader who sees 35 with no explanation assumes a weak read, when in
       fact the read was strong and the trade is simply badly placed. */
    /* `v.capped` is NOT shown here any more. It said the same thing as the
       `Risk : reward` row three lines below -- and said it in the detector's
       arithmetic rather than in words, on the one line that is supposed to
       carry the plain-English WHY. The cap still applies to the score; the
       reason for it now lives in the conviction tooltip, next to the number it
       explains. */
    const note = [v.theme, v.session].filter(Boolean).join(' · ');
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
        /* Tip goes on the whole ROW, not just the words: the timeframe label is
           half the information, and someone hovering "H4" wants the same
           explanation as someone hovering "Trending up". */
        tip(row, `${TF_LABEL[read.tf] || read.tf} — ${REGIME_TEXT[k] || k}`,
            REGIME_TIP[k] || 'No reading yet.',
            read.tf === this.execTf
              ? 'This is the timeframe on your chart.'
              : 'A context frame. When frames disagree, the higher one wins.');
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
      'Swing structure', 'A trend is just a pattern of peaks and troughs. '
      + '<b>HH + HL</b> means each peak is higher than the one before it AND '
      + 'each trough is higher too — that is what an uptrend IS. '
      + '<b>LH + LL</b> is the mirror: lower peaks, lower troughs. Anything '
      + 'else means the pattern has broken and price is undecided.',
      v.structText === 'HH + HL'
        ? 'Higher peaks and higher troughs — an intact uptrend.'
        : v.structText === 'LH + LL'
          ? 'Lower peaks and lower troughs — an intact downtrend.'
          : 'Mixed. Read from the highest frame down: an uptrend on M5 inside '
            + 'an H1 downtrend is a bounce, not a turn.'));

    facts.appendChild(this._fact('Invalidation', px(v.invalidation), 'mono',
      'Invalidation price', 'The price that proves this read wrong. It is the '
      + 'nearest level on the losing side — if price trades through it, the '
      + 'reasoning behind the call has failed and there is nothing left to '
      + 'wait for.',
      Number.isFinite(v.invalidation)
        ? `Through ${px(v.invalidation)}, this read is finished.`
        : 'No level close enough to name one.'));

    /* RISK FIRST, which is how the ratio is spoken. `0.16:1` was correct and
       read backwards: a trader seeing it says "risking 0.16 to make 1", the
       flattering reading, when the truth is the reverse. Written `1 : 0.16`
       there is nothing to invert -- the 1 is always what you put up.

       Coloured against 1:1, not against a preference: below 1 the first thing
       in the way is nearer than the stop, which is a fact about the chart
       rather than an opinion about the trade. */
    const rrTxt = Number.isFinite(v.rr) ? `1 : ${v.rr.toFixed(2)}` : '—';
    facts.appendChild(this._fact('Risk : reward', rrTxt,
      !Number.isFinite(v.rr) ? 'dim' : v.rr >= 1 ? 'up mono' : 'down mono',
      'Risk against reward', 'Read it as: risk <b>1</b> to make this much. '
      + 'The 1 is the distance from price down to the Invalidation above &mdash; '
      + 'what it costs to be wrong. The second number is the distance up to the '
      + 'first level in the way &mdash; what is available before something '
      + 'stops the move.',
      !Number.isFinite(v.rr)
        ? 'No usable level nearby, or the invalidation sits inside the noise.'
        : v.rr >= 1 ? `Risk 1 to make ${v.rr.toFixed(2)} — more room ahead than behind.`
          : `Risk 1 to make ${v.rr.toFixed(2)} — the obstacle is nearer than the `
            + 'exit, so the read can be right and still not worth taking from here.'));

    r.appendChild(facts);
  }

  _fact(label, value, cls, tipTitle, tipBody, tipNote) {
    const row = el('div', { class: 'tr-fact' });
    row.appendChild(el('span', { class: 'dim' }, label));
    row.appendChild(el('span', { class: `tr-val ${cls || ''}` }, value));
    return tipTitle ? tip(row, tipTitle, tipBody, tipNote) : row;
  }
}
